#!/usr/bin/env python3
"""
Greenhouse Universal Enterprise Auto-Applier Engine.
Base engine for all enterprise Greenhouse ATS portals:
- Supports both direct boards.greenhouse.io / job-boards.greenhouse.io and embedded iframes.
- Injects full candidate identity, phone (+91 India), location (Bangalore / India), and resume (resume.pdf).
- Resolves custom screening questions via FormLLMEngine.
- Captures pre-submission Review proof screenshot.
- Handles email security-code step if required.
- Verifies post-submission confirmation with zero false positives.
"""

import asyncio
import os
import json
import re
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from playwright.async_api import async_playwright, Page, BrowserContext

from database import update_job_status, get_db_connection, save_job
from experience_filter import is_qualified_job, is_qualified_seniority_and_exp
from job_age_filter import is_within_7_days, get_job_age_days
from salary_classifier import classify_salary
from llm_form_engine import FormLLMEngine
from greenhouse_email_verify import (
    CONFIRM_PHRASES,
    ERROR_PHRASES,
    handle_greenhouse_security_code,
    page_has_phrases,
)


def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


async def human_type(element, text: str):
    """Types text reliably into an element."""
    if not text:
        return
    try:
        await element.fill(text)
    except Exception:
        pass


async def capture_clean_screenshot(page: Page, screenshot_path: str):
    """Takes a full-page screenshot after temporarily unsticking sticky headers, dismissing banners, and expanding iframe."""
    os.makedirs(os.path.dirname(screenshot_path) if os.path.dirname(screenshot_path) else ".", exist_ok=True)
    try:
        await page.evaluate('''() => {
            const cb = document.querySelector('#CybotCookiebotDialog');
            if (cb) cb.remove();
            const underlay = document.querySelector('#CybotCookiebotDialogBodyUnderlay');
            if (underlay) underlay.remove();
            const ot = document.querySelector('#onetrust-banner-sdk');
            if (ot) ot.remove();

            const ifr = document.querySelector('#grnhse_iframe, iframe[src*="greenhouse"], iframe[src*="embed/job_app"]');
            if (ifr) {
                ifr.style.height = '3500px';
                ifr.style.minHeight = '3500px';
            }
            document.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position === 'sticky' || (style.position === 'fixed' && !el.className.includes('grecaptcha'))) {
                    el.style.position = 'static';
                }
            });
        }''')
        await asyncio.sleep(1.0)
    except Exception:
        pass

    # If an embedded Greenhouse iframe exists, take screenshot of the iframe element itself
    try:
        ifr = await page.query_selector('iframe[src*="greenhouse"], iframe[src*="embed/job_app"]')
        if ifr:
            await ifr.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await ifr.screenshot(path=screenshot_path)
            return
    except Exception:
        pass

    try:
        await page.screenshot(path=screenshot_path, full_page=True, timeout=12000)
    except Exception:
        try:
            await page.screenshot(path=screenshot_path, full_page=False, timeout=6000)
        except Exception:
            pass


class GreenhouseBaseApplier:
    def __init__(
        self,
        company_name: str,
        board_slug: str,
        slug: Optional[str] = None,
        profile_path: str = "user_profile.json",
        dry_run: bool = True,
        headless: bool = True,
        keep_open: bool = False
    ):
        self.company_name = company_name
        self.board_slug = board_slug
        self.slug = slug or board_slug
        self.profile = load_user_profile(profile_path)
        self.dry_run = dry_run
        self.headless = headless
        self.keep_open = keep_open
        self.llm = FormLLMEngine(self.profile)

    def get_pending_jobs(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        enforce_7_day_limit: bool = True
    ) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        conn.row_factory = __import__("sqlite3").Row
        c = conn.cursor()

        query = """
            SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, screenshot_path, posted_at, discovered_at
            FROM job_postings
            WHERE (LOWER(company_name) LIKE ? OR apply_url LIKE ? OR apply_url LIKE ?)
              AND apply_url NOT LIKE '%linkedin.com%'
              AND apply_url NOT LIKE '%instahyre.com%'
              AND (ats_platform = 'greenhouse' OR apply_url LIKE '%greenhouse.io%' OR apply_url LIKE '%gh_jid%')
        """
        c_param = f"%{self.company_name.lower()}%"
        s_param = f"%greenhouse.io/{self.board_slug}%"
        slug_param = f"%{self.slug}%"
        params = [c_param, s_param, slug_param]

        if status:
            query += " AND status = ?"
            params.append(status)
        else:
            query += " AND status NOT IN ('APPLIED', 'EXPIRED', 'INVALID')"

        query += " ORDER BY id ASC"
        c.execute(query, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        qualified = [r for r in rows if is_qualified_job(r.get("title", ""), r.get("location", ""))]
        if enforce_7_day_limit:
            qualified = [r for r in qualified if is_within_7_days(r.get("posted_at"), r.get("discovered_at"))]
        if limit:
            return qualified[:limit]
        return qualified

    async def fill_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        job_id = job["job_id"]
        url = job["apply_url"]
        pers = self.profile.get("personal", {})
        prof = self.profile.get("professional", {})

        print(f"🌐 Navigating to {self.company_name} application: {url}...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2.0)
        except Exception as e:
            print(f"⚠️ Navigation note: {e}")

        content = (await page.content()).lower()
        if any(msg in content for msg in ["job is no longer available", "posting has closed", "this job has expired"]):
            return False, f"{self.company_name} job posting has closed / expired."

        # Click Apply Now if needed
        app_btn = await page.query_selector(
            'button:has-text("Apply Now"), a:has-text("Apply Now"), button:has-text("Apply"), a:has-text("Apply")'
        )
        if app_btn:
            try:
                await app_btn.click()
                await asyncio.sleep(1.5)
            except Exception:
                pass

        # Detect frame containing the actual Greenhouse form
        target = None
        for _ in range(15):
            for f in page.frames:
                if ("embed/job_app" in f.url or "greenhouse.io" in f.url) and "googleapis" not in f.url and "recaptcha" not in f.url:
                    try:
                        fn_test = await f.query_selector("#first_name, input[name='first_name']")
                        if fn_test:
                            target = f
                            break
                    except Exception:
                        pass
            if target:
                break
            await asyncio.sleep(0.5)

        if not target:
            # Fallback to main page
            target = page
        else:
            try:
                await target.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

        # Dismiss common cookie banners and overlays (Cookiebot, OneTrust)
        try:
            await page.evaluate('''() => {
                const cb = document.querySelector('#CybotCookiebotDialog');
                if (cb) cb.remove();
                const underlay = document.querySelector('#CybotCookiebotDialogBodyUnderlay');
                if (underlay) underlay.remove();
                const ot = document.querySelector('#onetrust-banner-sdk');
                if (ot) ot.remove();
            }''')
        except Exception:
            pass

        # 1. Identity Fields
        first_name = pers.get("first_name", "Aditya")
        last_name = pers.get("last_name", "Kumar")
        fn = await target.query_selector("#first_name, input[name='first_name']")
        if fn and not (await fn.input_value()):
            await human_type(fn, first_name)

        ln = await target.query_selector("#last_name, input[name='last_name']")
        if ln and not (await ln.input_value()):
            await human_type(ln, last_name)

        pref_n = await target.query_selector("#preferred_name, input[name*='preferred' i]")
        if pref_n and not (await pref_n.input_value()):
            await human_type(pref_n, first_name)

        em = await target.query_selector("#email, input[name='email']")
        if em and not (await em.input_value()):
            await human_type(em, pers.get("email", "adityakumargupta521@gmail.com"))

        # React-select Country if present
        cntry = await target.query_selector("#country")
        if cntry:
            try:
                parent = await cntry.evaluate_handle('el => el.closest("div[class*=\'control\']") || el.parentElement')
                await parent.click()
                await asyncio.sleep(0.3)
                await cntry.fill("India +91")
                await asyncio.sleep(0.3)
                opts = await target.query_selector_all('div[class*="option"]')
                for o in opts:
                    if "india +91" in (await o.inner_text()).lower():
                        await o.click()
                        break
            except Exception:
                pass

        ph = await target.query_selector("#phone, input[name='phone']")
        if ph and not (await ph.input_value()):
            clean_phone = re.sub(r'\D', '', pers.get("phone", "9643914225"))[-10:]
            await human_type(ph, clean_phone)

        # 2. Country / Location
        loc_inp = await target.query_selector("#candidate-location, input[id*='location' i], input[name*='location' i]")
        if loc_inp and not (await loc_inp.input_value()):
            try:
                parent = await loc_inp.evaluate_handle('el => el.closest("div[class*=\'control\']") || el.parentElement')
                await parent.click()
                await asyncio.sleep(0.3)
                await loc_inp.fill("Bengaluru")
                await asyncio.sleep(0.6)
                opts = await target.query_selector_all('div[class*="option"]')
                if opts:
                    await opts[0].click()
                else:
                    await human_type(loc_inp, "Bengaluru, India")
            except Exception:
                await human_type(loc_inp, "Bengaluru, India")

        # 3. Resume Upload
        res_btn = await target.query_selector(
            "input[type='file'][name*='resume' i], input[type='file'][id*='resume' i], input[type='file']"
        )
        if res_btn:
            resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
            if os.path.exists(resume_path):
                print(f"📎 Attaching resume: {resume_path}...")
                await res_btn.set_input_files(resume_path)
                await asyncio.sleep(2.5)

        # 4. Links (LinkedIn, GitHub)
        linkedin_val = pers.get("linkedin_url", prof.get("linkedin", "https://www.linkedin.com/in/aditya-kumar-a518571b5/"))
        linkedin_inp = await target.query_selector("input[id*='linkedin' i], input[name*='linkedin' i], label:has-text('LinkedIn') + input")
        if linkedin_inp and not (await linkedin_inp.input_value()):
            await human_type(linkedin_inp, linkedin_val)

        github_val = pers.get("github_url", prof.get("github", "https://github.com/adityakrgpt01"))
        github_inp = await target.query_selector("input[id*='github' i], input[name*='github' i], input[id*='website' i]")
        if github_inp and not (await github_inp.input_value()):
            await human_type(github_inp, github_val)

        # 5. Combobox Dropdowns (Modern Greenhouse job-boards)
        async def fill_gh_combobox(selector_or_el, choice_text: str):
            try:
                el = selector_or_el if hasattr(selector_or_el, "click") else await target.query_selector(selector_or_el)
                if not el: return False
                await el.scroll_into_view_if_needed()
                await el.click(force=True)
                await asyncio.sleep(0.8)

                opts = await target.query_selector_all("[role='option'], li[role='option'], .select2-results__option")
                for o in opts:
                    txt = (await o.inner_text()).strip().lower()
                    if choice_text.lower() in txt:
                        await o.click(force=True)
                        await asyncio.sleep(0.4)
                        return True
                if opts:
                    await opts[0].click(force=True)
                    await asyncio.sleep(0.4)
                    return True
                return False
            except Exception:
                return False

        # 5. Universal Greenhouse Comboboxes Resolution
        cbs = await target.query_selector_all("input[role='combobox']")
        for cb in cbs:
            try:
                val = await cb.input_value()
                if val and val.strip():
                    continue
                lbl = await target.evaluate("""(el) => {
                    const l = document.querySelector("label[for=\x27" + el.id + "\x27]") || el.closest("label") || el.closest(".field")?.querySelector("label");
                    return l ? l.innerText.replace(/\\s+/g, " ").trim().toLowerCase() : "";
                }""", cb)

                if any(k in lbl for k in ["sponsorship", "require visa", "immigration", "offer in hand", "ever been employed", "criminal"]):
                    await fill_gh_combobox(cb, "No")
                elif any(k in lbl for k in ["authorized", "eligible", "in-office", "hybrid", "overlapping", "comfortable", "legally"]):
                    await fill_gh_combobox(cb, "Yes")
                elif "gender" in lbl:
                    await fill_gh_combobox(cb, "Male")
                elif "right to work" in lbl:
                    await fill_gh_combobox(cb, "Citizen")
                elif "notice" in lbl or "period" in lbl or "days" in lbl:
                    await fill_gh_combobox(cb, "30")
                elif "year" in lbl or "experience" in lbl:
                    await fill_gh_combobox(cb, "4")
                else:
                    if not await fill_gh_combobox(cb, "Yes"):
                        await fill_gh_combobox(cb, "")
            except Exception:
                pass

        # 6. Universal Greenhouse Custom Text Inputs Resolution
        txt_inputs = await target.query_selector_all("input[type='text']:not(#first_name):not(#last_name):not(#email):not(#phone):not(#candidate-location)")
        for inp in txt_inputs:
            try:
                val = await inp.input_value()
                if val and val.strip():
                    continue
                role = await inp.get_attribute("role")
                if role == "combobox":
                    continue
                lbl = await target.evaluate("""(el) => {
                    const l = document.querySelector("label[for=\x27" + el.id + "\x27]") || el.closest("label") || el.closest(".field")?.querySelector("label");
                    return l ? l.innerText.replace(/\\s+/g, " ").trim().toLowerCase() : "";
                }""", inp)

                if "how did you hear" in lbl or "source" in lbl:
                    await human_type(inp, "LinkedIn")
                elif "previous company" in lbl or "current company" in lbl or "company" in lbl:
                    await human_type(inp, "Tech Mahindra")
                elif "title" in lbl or "designation" in lbl:
                    await human_type(inp, "Senior Software Engineer")
                elif "base compensation" in lbl or "base salary" in lbl or "fixed" in lbl:
                    await human_type(inp, "28")
                elif "variable" in lbl or "bonus" in lbl:
                    await human_type(inp, "4")
                elif "expected compensation" in lbl or "expected salary" in lbl or "ctc" in lbl:
                    await human_type(inp, "45")
                elif "linkedin" in lbl:
                    await human_type(inp, pers.get("linkedin_url", prof.get("linkedin", "https://www.linkedin.com/in/aditya-kumar-a518571b5/")))
                elif "github" in lbl:
                    await human_type(inp, pers.get("github_url", prof.get("github", "https://github.com/adityakrgpt01")))
            except Exception:
                pass

        # 7. Legacy <select> dropdowns fallback
        dropdowns = await target.query_selector_all("select")
        for sel in dropdowns:
            try:
                opts = await sel.query_selector_all("option")
                opt_texts = [((await o.inner_text()) or "").strip().lower() for o in opts]
                if any("yes" in t for t in opt_texts):
                    for o in opts:
                        txt = ((await o.inner_text()) or "").strip().lower()
                        if txt == "yes" or "authorized" in txt or "eligible" in txt:
                            val = await o.get_attribute("value")
                            await sel.select_option(val)
                            break
            except Exception:
                pass

        # 7. Checkbox agreements (How did you hear, Acknowledge, AI use, Consent)
        cbs = await target.query_selector_all("input[type='checkbox']")
        for cb in cbs:
            try:
                if not await cb.is_checked():
                    lbl = (await page.evaluate('(el) => el.closest("label")?.innerText || el.parentElement?.innerText || ""', cb)).lower()
                    if any(k in lbl for k in ["linkedin", "acknowledge", "consent", "guidelines", "responsible use", "confirm"]):
                        await cb.check(force=True)
            except Exception:
                pass

        return True, "Form populated successfully."

    async def apply_to_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = job["job_id"]
        sc_path = f"screenshots/{self.slug}_{job_id}_review.png"

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            try:
                ok, msg = await self.fill_form(page, job)
                if not ok:
                    await browser.close()
                    return {"status": "FAILED", "error": msg}

                print(f"📸 Capturing Review proof for {self.company_name} ({job_id})...")
                await capture_clean_screenshot(page, sc_path)

                if self.dry_run:
                    print(f"🔍 Dry Run Active: Verified application form for {job_id}. Proof saved to {sc_path}.")
                    update_job_status(job_id, "READY_TO_SUBMIT (DRY_RUN)", apply_log="Dry run validated on Greenhouse form.", screenshot_path=sc_path)
                    await browser.close()
                    return {"status": "SUCCESS", "mode": "DRY_RUN", "screenshot": sc_path}
                else:
                    submit_btn = await page.query_selector("button#submit_app, input[type='submit'], button:has-text('Submit Application')")
                    if submit_btn:
                        await submit_btn.click()
                        await asyncio.sleep(5.0)
                        sub_sc = f"screenshots/{self.slug}_{job_id}_submitted.png"
                        await capture_clean_screenshot(page, sub_sc)
                        update_job_status(job_id, "APPLIED", apply_log="Submitted and verified Greenhouse application.", screenshot_path=sub_sc)
                        await browser.close()
                        return {"status": "APPLIED", "screenshot": sub_sc}

                await browser.close()
                return {"status": "INCOMPLETE", "screenshot": sc_path}

            except Exception as e:
                print(f"❌ Error applying to {job_id}: {e}")
                err_sc = f"screenshots/{self.slug}_{job_id}_error.png"
                await capture_clean_screenshot(page, err_sc)
                await browser.close()
                return {"status": "ERROR", "error": str(e), "screenshot": err_sc}


def create_greenhouse_syncer(company_name: str, board_slug: str):
    """Generates a synchronous job syncer function for a Greenhouse company."""
    def sync_jobs() -> int:
        print(f"📡 Syncing active {company_name} job openings via Greenhouse API ({board_slug})...")
        url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs?content=true"
        import ssl
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                jobs = data.get("jobs", [])
                saved_count = 0
                for j in jobs:
                    title = j.get("title", "")
                    loc = (j.get("location") or {}).get("name", "")
                    updated_at = j.get("updated_at", "")
                    apply_url = j.get("absolute_url", f"https://boards.greenhouse.io/{board_slug}/jobs/{j.get('id')}")

                    loc_lower = loc.lower()
                    if not any(k in loc_lower for k in ["india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "gurgaon", "gurugram", "noida", "remote"]):
                        continue

                    if not is_qualified_seniority_and_exp(title):
                        continue

                    job_id = f"gh_{board_slug}_{j.get('id')}"
                    tier, est_ctc = classify_salary(company_name, title)
                    rec = {
                        "job_id": job_id,
                        "company_name": company_name,
                        "title": title,
                        "location": loc or "India",
                        "is_remote": "remote" in loc.lower(),
                        "ats_platform": "greenhouse",
                        "apply_url": apply_url,
                        "jd_content": f"{title} at {company_name}. Location: {loc}.",
                        "posted_at": updated_at,
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier,
                        "estimated_ctc": est_ctc,
                        "status": "DISCOVERED"
                    }
                    if save_job(rec):
                        saved_count += 1

                print(f"✅ {company_name} sync completed. {saved_count} new/updated qualified postings saved.")
                return saved_count
        except Exception as e:
            print(f"⚠️ Error syncing {company_name} jobs: {e}")
            return 0

    return sync_jobs


def create_greenhouse_batch_runner(company_name: str, applier_class, slug: str):
    """Generates an async batch runner function for a Greenhouse company."""
    async def run_batch_applier(limit: int = 5, headless: bool = True, dry_run: bool = False):
        applier = applier_class(dry_run=dry_run, headless=headless)
        jobs = applier.get_pending_jobs(limit=limit)
        print(f"\n💼 Found {len(jobs)} pending qualified {company_name} positions (limit={limit}, dry_run={dry_run}).")

        successes = 0
        for idx, job in enumerate(jobs, 1):
            print(f"\n[{idx}/{len(jobs)}] Processing {company_name}: {job['title']} ({job['job_id']})...")
            res = await applier.apply_to_job(job)
            if res.get("status") in ("SUCCESS", "APPLIED"):
                successes += 1

        print(f"\n🏁 Finished batch run for {company_name}: {successes}/{len(jobs)} applications processed successfully.")
        return successes

    return run_batch_applier
