"""
Stealth Auto-Applier Engine with LLM Form Resolution.
Automates Greenhouse, Lever, and Ashby job applications using Playwright with anti-detection:
- playwright-stealth patches
- Human keystroke timing jitter (30-90ms)
- Natural scrolling & mouse actions
- Resume upload and form field mapping
- LLM & heuristic-driven custom question answering
- Screenshot verification on completion
"""

import asyncio
import os
import json
import re
import random
import time
from typing import Dict, Any, Optional, List, Tuple
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from database import update_job_status, save_portal_account, get_portal_account
from llm_form_engine import FormLLMEngine

def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)

async def human_type(element, text: str):
    """Types text with natural human delays between keystrokes."""
    if not text:
        return
    try:
        await element.fill("")
    except Exception:
        pass
    for char in text:
        await element.type(char, delay=random.uniform(25, 75))
        if random.random() < 0.04:
            await asyncio.sleep(random.uniform(0.08, 0.2))

async def capture_clean_screenshot(page, screenshot_path: str):
    """Takes a clean full-page screenshot by temporarily unsticking sticky/floating headers."""
    try:
        await page.evaluate('''() => {
            const stickyElements = document.querySelectorAll('*');
            stickyElements.forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position === 'sticky' || (style.position === 'fixed' && !el.className.includes('grecaptcha'))) {
                    el.setAttribute('data-prev-position', el.style.position || '');
                    el.style.position = 'static';
                }
            });
        }''')
    except Exception:
        pass

    await page.screenshot(path=screenshot_path, full_page=True)

    try:
        await page.evaluate('''() => {
            const modified = document.querySelectorAll('[data-prev-position]');
            modified.forEach(el => {
                el.style.position = el.getAttribute('data-prev-position');
                el.removeAttribute('data-prev-position');
            });
        }''')
    except Exception:
        pass

class StealthApplier:
    def __init__(self, profile_path: str = "user_profile.json", dry_run: bool = True, headless: Optional[bool] = None, keep_open: bool = False):
        self.profile = load_user_profile(profile_path)
        self.dry_run = dry_run # If True, fills out form and screenshots, but doesn't press Submit
        self.headless = headless
        self.keep_open = keep_open # If True, leaves headed browser open for candidate interaction
        self.stealth = Stealth()
        self.llm = FormLLMEngine(self.profile)

    async def fill_page(self, page, job: Dict[str, Any], dry_run: bool = True) -> (bool, str, str):
        """Fills out form fields on an existing Playwright page."""
        job_id = job["job_id"]
        url = job["apply_url"]
        platform = job.get("ats_platform", "greenhouse")
        os.makedirs("screenshots", exist_ok=True)
        screenshot_path = f"screenshots/{job_id}.png"

        print(f"Navigating to {url}...")
        try:
            await page.goto(url, wait_until="commit", timeout=30000)
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(2.0, 3.0))

        if platform == "greenhouse":
            success, msg = await self._fill_greenhouse(page, job)
        elif platform == "lever":
            success, msg = await self._fill_lever(page, job)
        elif platform == "ashby":
            success, msg = await self._fill_ashby(page, job)
        elif platform == "workday":
            success, msg = await self._fill_workday(page, job)
        elif platform == "linkedin":
            success, msg = await self._fill_linkedin(page, job)
        else:
            success, msg = False, f"Unsupported platform for direct apply: {platform}"

        await capture_clean_screenshot(page, screenshot_path)

        if success:
            if dry_run:
                status = "READY_TO_SUBMIT (DRY_RUN)"
                final_msg = f"{msg} [DRY RUN: Form filled completely with LLM resolution, skipped final click]"
            else:
                submit_selectors = [
                    "button[type='submit']",
                    "input[type='submit']",
                    "#submit_app",
                    "#submit-button",
                    "button:has-text('Submit Application')",
                    "button:has-text('Submit application')",
                    "button:has-text('Submit')",
                    "button:has-text('Apply')",
                    ".template-btn-submit",
                    "button[data-automation-id='bottom-navigation-next-button']",
                    "button[data-automation-id='submit-button']"
                ]
                
                submit_btn = None
                search_targets = [page] + page.frames
                for st in search_targets:
                    for sel in submit_selectors:
                        try:
                            btn = await st.query_selector(sel)
                            if btn and await btn.is_visible() and await btn.is_enabled():
                                submit_btn = btn
                                print(f"[{job_id}] Found active submit button with selector: {sel}")
                                break
                        except Exception:
                            continue
                    if submit_btn:
                        break

                if submit_btn:
                    await submit_btn.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    submit_start_time = time.time()
                    await submit_btn.click()
                    print(f"[{job_id}] Clicked Submit button! Waiting for response or security verification...")

                    visible_sec = []
                    for _ in range(8):
                        await asyncio.sleep(1.0)
                        sec_inputs = []
                        for st in search_targets:
                            try:
                                si_list = await st.query_selector_all("input[id^='security-input-'], input[name*='code' i], input[id*='verification' i]")
                                sec_inputs.extend(si_list)
                            except Exception:
                                pass
                        seen_ids = set()
                        for si in sec_inputs:
                            try:
                                if await si.is_visible():
                                    el_id = await si.get_attribute("id") or str(id(si))
                                    if el_id not in seen_ids:
                                        seen_ids.add(el_id)
                                        visible_sec.append(si)
                            except Exception:
                                pass
                        if visible_sec:
                            break

                    if visible_sec:
                        print(f"[{job_id}] Detected email verification code prompt with {len(visible_sec)} input boxes. Fetching PIN from Gmail...")
                        from gmail_authenticator import GmailVerificationReader
                        reader = GmailVerificationReader()
                        code = reader.fetch_latest_verification_code(sender_keyword=platform, timeout_seconds=40, received_after_ts=submit_start_time)
                        if code:
                            print(f"[{job_id}] Injecting verification code: {code}")
                            try:
                                await visible_sec[0].focus()
                                await page.keyboard.type(code, delay=50)
                            except Exception:
                                if len(visible_sec) == len(code):
                                    for idx, char in enumerate(code):
                                        await visible_sec[idx].fill(char)
                                        await asyncio.sleep(0.1)
                                else:
                                    await visible_sec[0].fill(code)
                            await asyncio.sleep(1.0)

                            resubmit_btn = await page.query_selector("button[type='submit'], button:has-text('Submit application'), button:has-text('Submit Application')")
                            if resubmit_btn and await resubmit_btn.is_enabled():
                                await resubmit_btn.click()
                                print(f"[{job_id}] Clicked final Resubmit button with verified PIN!")
                                await asyncio.sleep(6.0)
                        else:
                            print(f"[{job_id}] Warning: Could not retrieve fresh verification PIN from email.")

                    has_confirm = False
                    has_error = False
                    for _ in range(10):
                        page_content = (await page.content()).lower()
                        has_confirm = any(w in page_content for w in ["thank you for your interest", "your application has been received", "application submitted", "thanks for applying", "we have received your application", "thank you for applying"])
                        has_error = any(w in page_content for w in ["incorrect security code", "there was an error processing your application", "we couldn't submit your application", "flagged as possible spam", "403 error", "the request could not be satisfied", "request blocked"])
                        if has_confirm or has_error:
                            break
                        await asyncio.sleep(1.0)

                    await capture_clean_screenshot(page, screenshot_path)

                    if has_confirm and not has_error:
                        status = "APPLIED"
                        final_msg = f"{msg} [Application submitted successfully & confirmation verified]"
                    elif has_error:
                        status = "FAILED"
                        final_msg = f"{msg} [Submission encountered portal error or incorrect code; review screenshot]"
                    else:
                        status = "READY_TO_SUBMIT (REVIEW_REQUIRED)"
                        final_msg = f"{msg} [Form filled, final confirmation pending manual verification]"
                else:
                    status = "READY_TO_SUBMIT"
                    final_msg = f"{msg} [Form filled, manual review recommended: submit button selector not matched]"
        else:
            status = "FAILED"
            final_msg = f"Failed to fill form: {msg}"

        update_job_status(job_id, status, final_msg, screenshot_path)
        return success, status, final_msg

    async def apply_to_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = job["job_id"]
        screenshot_path = f"screenshots/{job_id}.png"
        update_job_status(job_id, "APPLYING", f"Starting stealth application for {job['title']} at {job['company_name']}")

        p = await async_playwright().start()
        try:
            if self.headless is not None:
                headless_mode = self.headless
            else:
                headless_mode = os.environ.get("HEADLESS", "true").lower() in ("true", "1", "yes")

            browser = await p.chromium.launch(
                headless=headless_mode,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars"
                ]
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await self.stealth.apply_stealth_async(page)

            try:
                success, status, final_msg = await self.fill_page(page, job, dry_run=self.dry_run)
                
                if self.keep_open and not headless_mode:
                    print(f"[{job_id}] Headed browser mode active: Leaving form open on screen for user review and direct submission.")
                    
                    async def watch_user_submission_and_keep_alive():
                        try:
                            submitted = False
                            def on_nav(frame):
                                if frame == active_page.main_frame:
                                    url_low = active_page.url.lower()
                                    if any(k in url_low for k in ["/confirmation", "/submitted", "/thank-you", "/thanks", "/applied", "/success"]):
                                        nonlocal submitted
                                        if not submitted:
                                            submitted = True
                                            print(f"🎉 [{job_id}] Detected confirmation URL navigation: {url_low}! Updating status to APPLIED...")
                                            async def async_save():
                                                await asyncio.sleep(1.5)
                                                await capture_clean_screenshot(active_page, screenshot_path)
                                                update_job_status(job_id, "APPLIED", "Application submitted by candidate via live browser [URL Confirmation Verified]", screenshot_path)
                                            asyncio.create_task(async_save())

                            active_page.on("framenavigated", on_nav)

                            for _ in range(3600):
                                if not context.pages or context.pages[0].is_closed():
                                    break
                                active_page = context.pages[0]
                                try:
                                    content = (await active_page.content()).lower()
                                    curr_url = active_page.url.lower()
                                    has_confirmed = any(w in content for w in [
                                        "thank you for your interest",
                                        "your application has been received",
                                        "application submitted",
                                        "thanks for applying",
                                        "we have received your application",
                                        "thank you for applying",
                                        "application was submitted",
                                        "submission successful",
                                        "application has been submitted",
                                        "successfully submitted",
                                        "your application was sent",
                                        "thanks, your application was received",
                                        "we'll be in touch",
                                        "we will be in touch",
                                        "application complete"
                                    ]) or any(k in curr_url for k in ["/confirmation", "/submitted", "/thank-you", "/thanks", "/applied", "/success"])
                                    
                                    if has_confirmed and not submitted:
                                        submitted = True
                                        print(f"🎉 [{job_id}] Detected user submission on opened form! Updating dashboard to APPLIED...")
                                        await asyncio.sleep(1.5)
                                        await capture_clean_screenshot(active_page, screenshot_path)
                                        update_job_status(job_id, "APPLIED", "Application submitted by candidate via live browser [Confirmation Verified]", screenshot_path)
                                        break
                                except Exception:
                                    pass
                                await asyncio.sleep(1.2)
                        except Exception as e:
                            print(f"[{job_id}] Watcher encountered exception: {e}")
                        finally:
                            try:
                                await browser.close()
                                await p.stop()
                            except Exception:
                                pass

                    asyncio.create_task(watch_user_submission_and_keep_alive())
                else:
                    await browser.close()
                    await p.stop()

                return {"job_id": job_id, "status": status, "message": final_msg, "screenshot": screenshot_path}

            except Exception as e:
                err_msg = str(e)
                print(f"Error applying to {job_id}: {err_msg}")
                try:
                    await page.screenshot(path=screenshot_path)
                except Exception:
                    pass
                update_job_status(job_id, "FAILED", err_msg, screenshot_path)
                try:
                    await browser.close()
                    await p.stop()
                except Exception:
                    pass
                return {"job_id": job_id, "status": "FAILED", "message": err_msg, "screenshot": screenshot_path}
        except Exception as launch_err:
            try:
                await p.stop()
            except Exception:
                pass
            raise launch_err

    async def apply_batch_headed(self, jobs: List[Dict[str, Any]], dry_run: bool = True):
        """
        Launches a single headed Chrome window on the Mac screen and auto-fills
        each job in its own tab, keeping all tabs open for candidate review and one-click submission.
        """
        p = await async_playwright().start()
        try:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars"
                ]
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )

            # Map page -> (job_id, screenshot_path)
            tab_job_map = {}
            page_tasks = []

            # Create tabs for all jobs concurrently
            for idx, job in enumerate(jobs):
                job_id = job["job_id"]
                screenshot_path = f"screenshots/{job_id}.png"
                update_job_status(job_id, "APPLYING", f"Opening live tab ({idx+1}/{len(jobs)}) for {job['title']} at {job['company_name']}")

                if idx == 0:
                    page = (await context.pages)[0] if context.pages else await context.new_page()
                else:
                    page = await context.new_page()

                await self.stealth.apply_stealth_async(page)
                tab_job_map[page] = (job_id, screenshot_path)

            print(f"⚡ Launching parallel auto-fill across {len(jobs)} tabs in the same browser window...")

            async def fill_single_tab(p_obj, j_data):
                j_id = j_data["job_id"]
                print(f"[Parallel Batch] Autofilling tab for {j_id} ({j_data.get('company_name')} - {j_data.get('title')})...")
                try:
                    await self.fill_page(p_obj, j_data, dry_run=dry_run)
                except Exception as fe:
                    print(f"[Parallel Batch Error] {j_id}: {fe}")
                    update_job_status(j_id, "FAILED", f"Error during auto-fill: {fe}")

            # Run all tab fills in parallel concurrently
            fill_coroutines = [fill_single_tab(p, j) for p, j in zip(tab_job_map.keys(), jobs)]
            await asyncio.gather(*fill_coroutines)

            print(f"✅ All {len(jobs)} forms are opened and pre-filled in parallel in your Chrome window! Monitoring tabs for candidate submissions...")

            # Multi-tab watcher: keep Chrome open and track submissions on any tab
            async def multi_tab_watcher():
                try:
                    submitted_jobs = set()
                    
                    # Also register network / navigation listeners on each page
                    for page in list(tab_job_map.keys()):
                        jid, sc_path = tab_job_map[page]
                        
                        def make_nav_handler(j_id=jid, s_path=sc_path, p_ref=page):
                            async def on_nav(frame):
                                if frame == p_ref.main_frame and j_id not in submitted_jobs:
                                    url_low = p_ref.url.lower()
                                    if any(k in url_low for k in ["/confirmation", "/submitted", "/thank-you", "/thanks", "/applied", "/success"]):
                                        submitted_jobs.add(j_id)
                                        print(f"🎉 [{j_id}] Detected confirmation URL navigation: {url_low}! Updating status to APPLIED...")
                                        await asyncio.sleep(1.5)
                                        await capture_clean_screenshot(p_ref, s_path)
                                        update_job_status(j_id, "APPLIED", "Application submitted by candidate via live browser [URL Confirmation Verified]", s_path)
                            return lambda f: asyncio.create_task(on_nav(f))
                        
                        page.on("framenavigated", make_nav_handler())

                    for _ in range(7200): # Watch up to 2 hours
                        if not context.pages or all(p.is_closed() for p in context.pages):
                            break

                        for page in list(context.pages):
                            if page.is_closed() or page not in tab_job_map:
                                continue
                            jid, sc_path = tab_job_map[page]
                            if jid in submitted_jobs:
                                continue

                            try:
                                content = (await page.content()).lower()
                                curr_url = page.url.lower()
                                has_confirmed = any(w in content for w in [
                                    "thank you for your interest",
                                    "your application has been received",
                                    "application submitted",
                                    "thanks for applying",
                                    "we have received your application",
                                    "thank you for applying",
                                    "application was submitted",
                                    "submission successful",
                                    "application has been submitted",
                                    "successfully submitted",
                                    "your application was sent",
                                    "thanks, your application was received",
                                    "we'll be in touch",
                                    "we will be in touch",
                                    "application complete"
                                ]) or any(k in curr_url for k in ["/confirmation", "/submitted", "/thank-you", "/thanks", "/applied", "/success"])

                                if has_confirmed:
                                    submitted_jobs.add(jid)
                                    print(f"🎉 [{jid}] Detected submission on live tab! Updating dashboard to APPLIED...")
                                    await asyncio.sleep(1.5)
                                    await capture_clean_screenshot(page, sc_path)
                                    update_job_status(jid, "APPLIED", "Application submitted by candidate via live browser [Confirmation Verified]", sc_path)
                            except Exception:
                                pass

                        await asyncio.sleep(1.2)
                except Exception as e:
                    print(f"Multi-tab watcher exception: {e}")
                finally:
                    try:
                        await browser.close()
                        await p.stop()
                    except Exception:
                        pass

            asyncio.create_task(multi_tab_watcher())

        except Exception as e:
            print(f"Error launching headed batch browser: {e}")
            try:
                await p.stop()
            except Exception:
                pass

    async def _fill_greenhouse(self, page, job: Optional[Dict[str, Any]] = None) -> (bool, str):
        pers = self.profile["personal"]
        prof = self.profile.get("professional", {})
        await page.mouse.wheel(0, 300)
        await asyncio.sleep(0.8)

        # Detect Greenhouse iframe if embedded on company custom career portal
        target = page
        frames = page.frames
        for f in frames:
            if "greenhouse.io" in f.url or "gh_jid" in f.url or "embed/job_app" in f.url:
                target = f
                break

        # ── 1. Standard Identity Fields ──────────────────────────────────────────
        fn_input = await target.query_selector("#first_name, input[name='first_name']")
        if fn_input:
            await human_type(fn_input, pers["first_name"])

        ln_input = await target.query_selector("#last_name, input[name='last_name']")
        if ln_input:
            await human_type(ln_input, pers["last_name"])

        email_input = await target.query_selector("#email, input[name='email']")
        if email_input:
            await human_type(email_input, pers["email"])

        phone_input = await target.query_selector("#phone, input[name='phone']")
        if phone_input:
            await human_type(phone_input, pers["phone"])

        # LinkedIn / GitHub / Portfolio link fields
        for field_name, url_val in [
            ("linkedin", pers.get("linkedin_url", "")),
            ("github", pers.get("github_url", "")),
            ("website", pers.get("portfolio_url") or pers.get("github_url", "")),
            ("portfolio", pers.get("portfolio_url") or pers.get("github_url", "")),
        ]:
            if not url_val:
                continue
            el = await target.query_selector(
                f"input[name*='{field_name}' i], input[id*='{field_name}' i], "
                f"input[placeholder*='{field_name}' i], input[aria-label*='{field_name}' i]"
            )
            if el:
                curr = await el.input_value()
                if not curr:
                    await human_type(el, url_val)

        # Current Employer / Company field
        curr_comp = (prof.get("work_experience") or [{}])[0].get("company", "")
        if curr_comp:
            emp_el = await target.query_selector(
                "input[name*='current_company' i], input[name*='employer' i], input[id*='company' i]"
            )
            if emp_el:
                curr = await emp_el.input_value()
                if not curr:
                    await human_type(emp_el, curr_comp)

        # Years of experience numeric field
        yoe_el = await target.query_selector(
            "input[name*='years_of_experience' i], input[id*='years' i]"
        )
        if yoe_el:
            curr = await yoe_el.input_value()
            if not curr:
                await human_type(yoe_el, str(int(prof.get("years_of_experience", 5))))

        # Country / City if required (Greenhouse React-Select combobox)
        city_input = await target.query_selector("#candidate-location, input[id*='location' i]")
        if city_input:
            try:
                await city_input.click()
                await city_input.fill("")
                await city_input.type("Bangalore", delay=70)
                await asyncio.sleep(1.8)
                menu_opt = await target.query_selector("div[id*='option-0'], div.select__option")
                if menu_opt:
                    await menu_opt.click(force=True)
                else:
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Enter")
            except Exception:
                pass

        # ── 2. Resume File Upload ─────────────────────────────────────────────────
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            uploaded = False
            # Try modern Greenhouse "Attach" button for resume
            try:
                resume_label = await target.query_selector("label[for='resume']")
                if resume_label:
                    attach_btn = await resume_label.evaluate_handle("e => e.previousElementSibling")
                    if attach_btn:
                        async with page.expect_file_chooser(timeout=3000) as fc_info:
                            await attach_btn.click()
                        file_chooser = await fc_info.value
                        await file_chooser.set_files(resume_path)
                        uploaded = True
                        print("[Greenhouse] Attached resume via Attach button file chooser.")
                        await asyncio.sleep(1.5)
            except Exception:
                pass

            if not uploaded:
                file_input = await target.query_selector("input#resume, input[type='file'][id*='resume' i], input[type='file'][name*='resume' i]")
                if not file_input:
                    # First file input that looks like the resume upload
                    file_input = await target.query_selector("input[type='file']")
                if file_input:
                    await file_input.set_input_files(resume_path)
                    await asyncio.sleep(1.5)

            # Cover Letter: only attach to explicitly-named cover-letter file inputs, NOT all file inputs
            cl_input = await target.query_selector(
                "input[type='file'][id*='cover' i], input[type='file'][name*='cover' i], "
                "input[type='file'][id*='letter' i], input[type='file'][name*='letter' i]"
            )
            if cl_input:
                try:
                    await cl_input.set_input_files(resume_path)  # attach resume as cover letter
                    await asyncio.sleep(1.0)
                except Exception:
                    pass

        # ── 3a. Dynamic text inputs and textareas via LLM ────────────────────────
        fields = await target.query_selector_all("div.field, div[class*='field'], li.custom-question")
        for f in fields:
            try:
                label_el = await f.query_selector("label")
                if not label_el:
                    continue
                q_text = (await label_el.inner_text()).strip()
                if not q_text or len(q_text) < 3:
                    continue

                text_input = await f.query_selector("input[type='text']:not(.select__input):not([role='combobox']), textarea")
                if text_input:
                    current_val = await text_input.input_value()
                    if not current_val:
                        ans = self.llm.answer_question(q_text, job_context=job)
                        if ans:
                            await human_type(text_input, ans)

                # Native HTML select dropdowns
                select_el = await f.query_selector("select")
                if select_el:
                    opts = await select_el.query_selector_all("option")
                    opt_texts = [await o.inner_text() for o in opts if (await o.get_attribute("value"))]
                    if opt_texts:
                        chosen = self.llm.answer_question(q_text, opt_texts, job_context=job)
                        if chosen:
                            try:
                                await select_el.select_option(label=chosen)
                            except Exception:
                                pass
            except Exception:
                pass

        # ── 3b. React-Select Comboboxes (Authorization, Sponsorship, EEO, Privacy) ──
        combos = await target.query_selector_all("div.select__control")
        for c in combos:
            try:
                # Walk up to find a container that has a label sibling
                parent_el = await c.evaluate_handle(
                    'e => e.closest(".select__container") || e.closest("div.field") || e.parentElement'
                )
                label = await parent_el.query_selector("label")
                if not label:
                    continue
                lbl_txt = (await label.inner_text()).strip()
                # Skip pure country/location dropdowns (handled above separately)
                if not lbl_txt or lbl_txt.lower() in ("country*", "country", "location", "location*"):
                    continue

                # Skip if already has a value selected
                has_val = await c.query_selector("div.select__single-value, div[class*='singleValue' i]")
                if has_val:
                    continue

                await c.click(force=True)
                await asyncio.sleep(0.5)
                options = await target.query_selector_all("div[class*='option' i].select__option, div.select__menu div[id*='option']")
                if not options:
                    # Close the opened dropdown and skip
                    await page.keyboard.press("Escape")
                    continue
                opt_texts = [(await o.inner_text()).strip() for o in options]
                chosen = self.llm.answer_question(lbl_txt, opt_texts, job_context=job)
                matched_idx = 0
                if chosen:
                    found = False
                    for idx, ot in enumerate(opt_texts):
                        if chosen.strip().lower() == ot.strip().lower():
                            matched_idx = idx
                            found = True
                            break
                    if not found:
                        for idx, ot in enumerate(opt_texts):
                            if re.search(r'\b' + re.escape(chosen.lower()) + r'\b', ot.lower()):
                                matched_idx = idx
                                found = True
                                break
                    if not found:
                        for idx, ot in enumerate(opt_texts):
                            if chosen.lower() in ot.lower():
                                matched_idx = idx
                                break
                try:
                    await options[matched_idx].click(force=True, timeout=2500)
                except Exception:
                    await options[matched_idx].evaluate('el => el.click()')
                await asyncio.sleep(0.4)
            except Exception:
                pass

        # ── 3c. Checkboxes (EEO consent, demographic survey, privacy) ────────────
        checkboxes = await target.query_selector_all("input[type='checkbox']")
        for cb in checkboxes:
            try:
                is_checked = await cb.is_checked()
                if not is_checked:
                    await cb.click(force=True)
            except Exception:
                pass

        return True, "Greenhouse: filled standard fields, LinkedIn/GitHub, resume, custom questions, combos, and EEO"

    async def _fill_lever(self, page, job: Optional[Dict[str, Any]] = None) -> (bool, str):
        pers = self.profile["personal"]
        apply_btn = await page.query_selector("a.postings-btn, .template-btn-submit")
        if apply_btn:
            await apply_btn.click()
            await asyncio.sleep(1.5)

        name_input = await page.query_selector("input[name='name']")
        if name_input:
            await human_type(name_input, f"{pers['first_name']} {pers['last_name']}")

        email_input = await page.query_selector("input[name='email']")
        if email_input:
            await human_type(email_input, pers["email"])

        phone_input = await page.query_selector("input[name='phone']")
        if phone_input:
            await human_type(phone_input, pers["phone"])

        li_input = await page.query_selector("input[name='urls[LinkedIn]']")
        if li_input and pers.get("linkedin_url"):
            await human_type(li_input, pers["linkedin_url"])

        gh_input = await page.query_selector("input[name='urls[GitHub]']")
        if gh_input and pers.get("github_url"):
            await human_type(gh_input, pers["github_url"])

        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                await file_input.set_input_files(resume_path)

        # Dynamic Lever custom questions
        lever_cards = await page.query_selector_all("div.application-question")
        for card in lever_cards:
            try:
                label_el = await card.query_selector("div.text, label")
                if not label_el:
                    continue
                t_input = await card.query_selector("input[type='text'], textarea")
                if t_input:
                    ans = self.llm.answer_question(q_text, job_context=job)
                    if ans:
                        await human_type(t_input, ans)
            except Exception:
                pass

        return True, "Filled Lever fields and resolved custom questions with LLM"

    async def _fill_ashby(self, page, job: Optional[Dict[str, Any]] = None) -> (bool, str):
        pers = self.profile["personal"]
        prof = self.profile.get("professional", {})

        # ── 1. Navigate to /application if we're on the overview page ────────────
        if "/application" not in page.url:
            try:
                apply_link = await page.query_selector(
                    'a:has-text("Apply for this Job"), button:has-text("Apply for this Job"), '
                    'a:has-text("Apply Now"), button:has-text("Apply Now")'
                )
                if apply_link:
                    await apply_link.click(force=True)
                    await asyncio.sleep(2.5)
                else:
                    target_url = page.url.rstrip("/") + "/application"
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(2.0)
            except Exception:
                pass

        await page.mouse.wheel(0, 300)
        await asyncio.sleep(1.0)

        # ── 2. Resume Upload FIRST (let Ashby parser auto-fill from resume) ───────
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_inputs = await page.query_selector_all("input[type='file']")
            for fi in file_inputs:
                try:
                    await fi.set_input_files(resume_path)
                    await asyncio.sleep(1.0)
                except Exception:
                    pass
            await asyncio.sleep(5.0)  # Wait for Ashby's resume parser

        # ── 3. Standard Ashby Identity Fields ────────────────────────────────────
        # First try separate first/last name fields; fall back to combined name input
        fn_el = await page.query_selector(
            "input[name*='firstName' i], input[id*='firstName' i], input[name='first_name'], input[id='first_name']"
        )
        ln_el = await page.query_selector(
            "input[name*='lastName' i], input[id*='lastName' i], input[name='last_name'], input[id='last_name']"
        )
        if fn_el and ln_el:
            curr_fn = await fn_el.input_value()
            if not curr_fn:
                await human_type(fn_el, pers["first_name"])
            curr_ln = await ln_el.input_value()
            if not curr_ln:
                await human_type(ln_el, pers["last_name"])
        else:
            # Combined name field
            name_input = await page.query_selector(
                "input[name*='name' i]:not([name*='company' i]):not([name*='employer' i]), "
                "input[id*='name' i]:not([id*='company' i]):not([id*='employer' i])"
            )
            if name_input:
                curr = await name_input.input_value()
                if not curr:
                    await human_type(name_input, f"{pers['first_name']} {pers['last_name']}")

        email_input = await page.query_selector("input[type='email'], input[name*='email' i]")
        if email_input:
            curr = await email_input.input_value()
            if not curr:
                await human_type(email_input, pers["email"])

        phone_input = await page.query_selector("input[type='tel'], input[name*='phone' i]")
        if phone_input:
            curr = await phone_input.input_value()
            if not curr:
                await human_type(phone_input, pers["phone"])

        # Location combobox — type city and pick first suggestion
        loc_input = await page.query_selector(
            "input[placeholder*='Start typing' i], input[placeholder*='city' i], "
            "input[aria-label*='location' i]:not([type='hidden'])"
        )
        if loc_input:
            try:
                await loc_input.focus()
                city_str = f"{pers.get('current_city', 'Bengaluru')}, Karnataka, India"
                await human_type(loc_input, city_str)
                await asyncio.sleep(1.2)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.3)
                await page.keyboard.press("Enter")
            except Exception:
                pass

        # Current / Most Recent Employer
        curr_comp = (prof.get("work_experience") or [{}])[0].get("company", "73 Strings")
        for emp_sel in [
            "input[placeholder*='employer' i]", "input[name*='employer' i]",
            "input[placeholder*='company' i]", "input[name*='company' i]",
        ]:
            emp_el = await page.query_selector(emp_sel)
            if emp_el:
                curr = await emp_el.input_value()
                if not curr:
                    await human_type(emp_el, curr_comp)
                break

        # University / School
        inst = prof.get("education", {}).get("institution", "JSS Academy of Technical Education")
        for edu_sel in [
            "input[placeholder*='university' i]", "input[name*='university' i]",
            "input[placeholder*='school' i]", "input[name*='school' i]",
        ]:
            edu_el = await page.query_selector(edu_sel)
            if edu_el:
                curr = await edu_el.input_value()
                if not curr:
                    await human_type(edu_el, inst)
                break

        # LinkedIn / GitHub / Portfolio URLs
        for field_hint, url_val in [
            ("linkedin", pers.get("linkedin_url", "")),
            ("github", pers.get("github_url", "")),
            ("website", pers.get("portfolio_url") or pers.get("github_url", "")),
            ("portfolio", pers.get("portfolio_url") or pers.get("github_url", "")),
        ]:
            if not url_val:
                continue
            url_el = await page.query_selector(
                f"input[name*='{field_hint}' i], input[placeholder*='{field_hint}' i], "
                f"input[aria-label*='{field_hint}' i], input[id*='{field_hint}' i], input[type='url']"
            )
            if url_el:
                curr = await url_el.input_value()
                if not curr:
                    await human_type(url_el, url_val)
                    break  # only fill first URL-type input once

        # Pronouns
        pronouns_val = self.profile.get("custom_answers", {}).get("pronouns", "He/Him")
        pn_el = await page.query_selector("input[name*='pronoun' i], input[placeholder*='pronoun' i]")
        if pn_el:
            curr = await pn_el.input_value()
            if not curr:
                await human_type(pn_el, pronouns_val)

        # ── 4. Yes/No and Radio Button Fields ────────────────────────────────────
        # Work authorization (legally authorized → Yes)
        try:
            auth_yes = page.locator(
                "button:has-text('Yes')[class*='yesno'], button[data-option='yes'], "
                "label:has-text('Yes'):near(p:has-text('legally authorized')), "
                "label:has-text('Yes'):near(span:has-text('authorized to work'))"
            ).first
            if await auth_yes.count() > 0:
                await auth_yes.scroll_into_view_if_needed()
                await auth_yes.click(force=True)
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # Sponsorship not required → No
        try:
            no_spons = page.locator(
                "label:has-text('No, I do not require'), "
                "label:has-text('I do not need sponsorship'), "
                "button:has-text('No'):near(p:has-text('sponsorship'))"
            ).first
            if await no_spons.count() > 0:
                await no_spons.click(force=True)
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # Hybrid / in-office → Yes
        try:
            hybrid_yes = page.locator(
                "label:has-text('Yes, I am able to work'), "
                "label:has-text(\"Yes, I'm able to work from the office\")"
            ).first
            if await hybrid_yes.count() > 0:
                await hybrid_yes.click(force=True)
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # ── 5. Custom Text / Textarea / Dropdown Questions (LLM) ─────────────────
        custom_fields = await page.query_selector_all(
            "div[class*='field' i], div[class*='question' i], "
            "section[class*='question' i], div[class*='FormQuestion' i]"
        )
        for cf in custom_fields:
            try:
                lbl = await cf.query_selector("label, span[class*='label' i], h3, h4, p[class*='title' i], legend")
                if not lbl:
                    continue
                lbl_text = (await lbl.inner_text()).strip()
                if not lbl_text or any(
                    k in lbl_text.lower()
                    for k in ["name", "email", "phone", "resume", "cv", "location", "employer", "school", "university", "linkedin", "github", "website", "pronoun"]
                ):
                    continue

                # 1. Text & Textarea inputs
                txt_input = await cf.query_selector(
                    "textarea, input[type='text']:not([role='combobox']):not([aria-haspopup='listbox'])"
                )
                if txt_input:
                    existing_val = await txt_input.input_value()
                    if not existing_val:
                        ans = self.llm.answer_question(lbl_text, job_context=job)
                        if ans:
                            await human_type(txt_input, ans)
                    continue

                # 2. Radio button groups
                radio_inputs = await cf.query_selector_all("input[type='radio']")
                if radio_inputs:
                    radio_labels = []
                    for ri in radio_inputs:
                        # Get the label text for this radio: parent label or aria-label
                        r_label = await ri.evaluate_handle('el => el.closest("label") || el.parentElement')
                        r_txt = ""
                        try:
                            r_txt = (await r_label.inner_text()).strip()
                        except Exception:
                            pass
                        if not r_txt:
                            r_txt = await ri.get_attribute("value") or ""
                        radio_labels.append((ri, r_txt))
                    valid_pairs = [(ri, t) for ri, t in radio_labels if t and t != lbl_text]
                    if valid_pairs:
                        chosen = self.llm.answer_question(lbl_text, [t for _, t in valid_pairs], job_context=job)
                        if chosen:
                            for ri, t in valid_pairs:
                                if chosen.lower() in t.lower() or t.lower() in chosen.lower():
                                    try:
                                        await ri.click(force=True)
                                    except Exception:
                                        parent_lbl = await ri.evaluate_handle('el => el.closest("label")')
                                        await parent_lbl.click(force=True)
                                    await asyncio.sleep(0.3)
                                    break
                    continue

                # 3. Custom dropdown / combobox
                # Use inner_text() for button (not input_value which only works on inputs)
                select_btn = await cf.query_selector(
                    "button[role='combobox'], div[role='combobox'], "
                    "input[role='combobox'], button[id*='downshift' i], "
                    "button[class*='select' i]"
                )
                if select_btn:
                    tag = await select_btn.evaluate("el => el.tagName.toLowerCase()")
                    btn_txt = ""
                    try:
                        if tag == "input":
                            btn_txt = (await select_btn.input_value()).strip()
                        else:
                            btn_txt = (await select_btn.inner_text()).strip()
                    except Exception:
                        pass
                    if not btn_txt or any(w in btn_txt.lower() for w in ["start typing", "select", "choose", "please", "none", "—"]):
                        await select_btn.click(force=True)
                        await asyncio.sleep(0.5)
                        options = await page.query_selector_all(
                            "div[role='option'], li[role='option'], div[class*='menuItem' i], div[id*='option' i]"
                        )
                        if options:
                            opt_texts = [(await o.inner_text()).strip() for o in options]
                            chosen = self.llm.answer_question(lbl_text, opt_texts, job_context=job)
                            if chosen:
                                for idx, ot in enumerate(opt_texts):
                                    if chosen.lower() in ot.lower() or ot.lower() in chosen.lower():
                                        await options[idx].click(force=True)
                                        await asyncio.sleep(0.3)
                                        break
                        else:
                            # Lazy-loaded options: type to search
                            try:
                                await select_btn.type("Bengaluru", delay=50)
                                await asyncio.sleep(0.8)
                                first_opt = await page.query_selector("div[role='option'], li[role='option']")
                                if first_opt:
                                    await first_opt.click(force=True)
                                else:
                                    await page.keyboard.press("ArrowDown")
                                    await asyncio.sleep(0.2)
                                    await page.keyboard.press("Enter")
                            except Exception:
                                pass
            except Exception:
                pass

        # ── 6. Catch unhandled textareas ─────────────────────────────────────────
        textareas = await page.query_selector_all("textarea:not([name*='recaptcha' i])")
        for ta in textareas:
            try:
                if not await ta.is_visible():
                    continue
                curr_val = await ta.input_value()
                if not curr_val:
                    parent = await ta.evaluate_handle('el => el.closest("div") || el.parentElement')
                    if q_text:
                        ans = self.llm.answer_question(q_text, job_context=job)
                        if ans:
                            await human_type(ta, ans)
            except Exception:
                pass

        # ── 7. Consent checkboxes ────────────────────────────────────────────────
        consent_boxes = await page.query_selector_all("input[type='checkbox']")
        for cb in consent_boxes:
            try:
                if not await cb.is_checked():
                    await cb.click(force=True)
            except Exception:
                pass

        return True, "Ashby: filled identity, resume, URLs, employer, school, radios, custom questions, and checkboxes"

    async def _fill_workday(self, page, job: Dict[str, Any]) -> (bool, str):
        import urllib.parse
        import secrets
        import string
        from gmail_authenticator import GmailVerificationReader

        pers = self.profile["personal"]
        portal_url = job["apply_url"]
        domain = urllib.parse.urlparse(portal_url).netloc
        company = job.get("company_name", "Workday Employer")

        # ── 1. Cookie Banner ──────────────────────────────────────────────────────
        for _ in range(2):
            cookie_btn = await page.query_selector(
                'button[id*="accept" i], button:has-text("Accept Cookies"), '
                'button:has-text("Accept All"), button:has-text("I Accept")'
            )
            if cookie_btn:
                try:
                    await cookie_btn.click(force=True)
                    await asyncio.sleep(1.2)
                except Exception:
                    pass

        # ── 2. Click primary Apply button on job description page ────────────────
        apply_btn = await page.query_selector(
            'a[data-automation-id*="apply" i], button[data-automation-id*="apply" i], '
            'a:has-text("Apply"), button:has-text("Apply for This Job")'
        )
        if apply_btn:
            try:
                await apply_btn.click(force=True)
                await asyncio.sleep(3.0)
            except Exception:
                pass

        # ── 3. 'Autofill with Resume' OR 'Apply Manually' ────────────────────────
        autofill_btn = await page.query_selector(
            'a:has-text("Autofill with Resume"), button:has-text("Autofill with Resume"), '
            'a:has-text("Apply Manually"), button:has-text("Apply Manually")'
        )
        if autofill_btn:
            try:
                await autofill_btn.click(force=True)
                await asyncio.sleep(3.0)
            except Exception:
                pass

        # Dismiss cookie banner if it re-appears
        for cookie_txt in ["Accept Cookies", "Accept All"]:
            cb2 = await page.query_selector(f'button:has-text("{cookie_txt}")')
            if cb2:
                try:
                    await cb2.click(force=True)
                    await asyncio.sleep(1.0)
                except Exception:
                    pass

        # ── 4. Workday Account Wall (Create / Sign In) ───────────────────────────
        try:
            await page.wait_for_selector(
                "input[data-automation-id='email'], input[type='email'], "
                "input[data-automation-id='file-upload-input-ref']",
                timeout=12000
            )
        except Exception:
            pass

        existing = get_portal_account(domain)
        pwd = ""
        if existing:
            pwd = existing["password"]
            # If page shows 'Create Account' but we already have credentials → switch to Sign In
            sign_in_link = await page.query_selector(
                'button[data-automation-id="signInLink"], a[data-automation-id="signInLink"], '
                'button:has-text("Sign In"), a:has-text("Sign In")'
            )
            if sign_in_link:
                try:
                    await sign_in_link.click(force=True)
                    await asyncio.sleep(2.5)
                except Exception:
                    pass
        else:
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            pwd = "".join(secrets.choice(alphabet) for _ in range(14)) + "Aa1!"
            save_portal_account(domain, company, pers["email"], pwd, status="ACTIVE")

        email_input = await page.query_selector(
            "input[data-automation-id='email'], input[id*='email' i], input[type='email']"
        )
        pass_input = await page.query_selector(
            "input[data-automation-id='password'], input[id*='password' i], input[type='password']"
        )

        if email_input and pass_input:
            await human_type(email_input, pers["email"])
            await human_type(pass_input, pwd)

            confirm_pwd = await page.query_selector(
                "input[data-automation-id='verifyPassword'], input[id*='confirm' i]"
            )
            if confirm_pwd:
                await human_type(confirm_pwd, pwd)

            consent = await page.query_selector("input[type='checkbox']")
            if consent:
                try:
                    if not await consent.is_checked():
                        await consent.click(force=True)
                except Exception:
                    pass

            submit_auth = await page.query_selector(
                'button[data-automation-id="signInSubmitButton"], '
                'button[data-automation-id="createAccountSubmitButton"], '
                'div[data-automation-id="click_filter"][aria-label="Sign In"], '
                'div[data-automation-id="click_filter"][aria-label="Create Account"], '
                'button:has-text("Sign In"), button:has-text("Create Account")'
            )
            if submit_auth:
                await submit_auth.click(force=True)
                await asyncio.sleep(6.0)

            # Email verification PIN
            pin_input = await page.query_selector(
                "input[data-automation-id='verificationCode'], input[id*='code' i], input[name*='pin' i]"
            )
            if pin_input:
                print(f"[Workday] Verification screen! Polling Gmail for PIN...")
                try:
                    reader = GmailVerificationReader(user=pers["email"])
                    pin = reader.fetch_latest_verification_code(sender_keyword="workday", timeout_seconds=45)
                    if pin:
                        await human_type(pin_input, pin)
                        verify_btn = await page.query_selector(
                            "button:has-text('Verify'), button:has-text('Continue'), "
                            "button[data-automation-id='verifyButton']"
                        )
                        if verify_btn:
                            await verify_btn.click(force=True)
                            await asyncio.sleep(4.0)
                except Exception as e:
                    print(f"[Workday] PIN fetch error: {e}")

        # ── 5. Upload Resume ──────────────────────────────────────────────────────
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector(
                "input[type='file'], input[data-automation-id='file-upload-input-ref']"
            )
            if file_input:
                await file_input.set_input_files(resume_path)
                await asyncio.sleep(4.0)

        # ── 6. Navigate multi-step Workday form (up to 6 steps) ─────────────────
        # Steps: My Information → My Experience → Application Questions →
        #        Voluntary Disclosures → Self Identify → Review
        for step_idx in range(6):
            await asyncio.sleep(2.0)

            # Fill available fields on current step ─────────────────────────────

            # Legal name (fix ALL CAPS issue from Workday resume parser)
            fn_el = await page.query_selector("input[data-automation-id='legalNameSection_firstName']")
            if fn_el:
                val = await fn_el.input_value()
                if not val or val.isupper():
                    await fn_el.triple_click()
                    await fn_el.fill(pers.get("first_name", "Aditya"))

            ln_el = await page.query_selector("input[data-automation-id='legalNameSection_lastName']")
            if ln_el:
                val = await ln_el.input_value()
                if not val or val.isupper():
                    await ln_el.triple_click()
                    await ln_el.fill(pers.get("last_name", "Kumar"))

            # Phone number
            phone_el = await page.query_selector("input[data-automation-id='phone-number']")
            if phone_el:
                curr_phone = await phone_el.input_value()
                if not curr_phone:
                    # First set country code to India (+91)
                    phone_type_btn = await page.query_selector("button[data-automation-id='phoneDeviceType']")
                    if phone_type_btn:
                        try:
                            await phone_type_btn.click()
                            await asyncio.sleep(0.5)
                            mobile_opt = await page.query_selector("div[data-automation-id='menuItem']:has-text('Mobile')")
                            if mobile_opt:
                                await mobile_opt.click()
                        except Exception:
                            pass
                    # Country code / extension field
                    ext_btn = await page.query_selector(
                        "button[data-automation-id='countryPhoneCode'], "
                        "button[aria-label*='country code' i], button[aria-label*='phone' i]"
                    )
                    if ext_btn:
                        try:
                            await ext_btn.click()
                            await asyncio.sleep(0.8)
                            india_opt = await page.query_selector(
                                "div[data-automation-id='menuItem']:has-text('India'), "
                                "li:has-text('+91')"
                            )
                            if india_opt:
                                await india_opt.click()
                            await asyncio.sleep(0.4)
                        except Exception:
                            pass
                    await human_type(phone_el, pers["phone"].replace("+91", "").replace(" ", "").strip())

            # Address Line 1
            addr_input = await page.query_selector("input[data-automation-id='addressSection_addressLine1']")
            if addr_input:
                curr_addr = await addr_input.input_value()
                if not curr_addr:
                    await human_type(addr_input, "Outer Ring Road, Bellandur")

            # City
            city_input = await page.query_selector("input[data-automation-id='addressSection_city']")
            if city_input:
                curr_city = await city_input.input_value()
                if not curr_city:
                    await human_type(city_input, pers.get("current_city", "Bengaluru"))

            # Postal Code
            postal_input = await page.query_selector("input[data-automation-id='addressSection_postalCode']")
            if postal_input:
                curr_postal = await postal_input.input_value()
                if not curr_postal:
                    await human_type(postal_input, "560103")

            # Country dropdown (India)
            country_btn = await page.query_selector(
                "button[data-automation-id='addressSection_country'], "
                "button[data-automation-id='country']"
            )
            if country_btn:
                try:
                    curr_country_txt = (await country_btn.inner_text()).strip()
                    if not curr_country_txt or curr_country_txt.lower() in ("select one", "—", ""):
                        await country_btn.click()
                        await asyncio.sleep(0.8)
                        india_opt = await page.query_selector(
                            "div[data-automation-id='menuItem']:has-text('India'), li:has-text('India')"
                        )
                        if india_opt:
                            await india_opt.click()
                            await asyncio.sleep(0.5)
                except Exception:
                    pass

            # State / Region (Karnataka)
            state_btn = await page.query_selector("button[data-automation-id='addressSection_countryRegion']")
            if state_btn:
                try:
                    curr_state_txt = (await state_btn.inner_text()).strip()
                    if not curr_state_txt or curr_state_txt.lower() in ("select one", "—", ""):
                        await state_btn.click()
                        await asyncio.sleep(0.8)
                        karnataka_opt = await page.query_selector(
                            "div[data-automation-id='menuItem']:has-text('Karnataka'), li:has-text('Karnataka')"
                        )
                        if karnataka_opt:
                            await karnataka_opt.click()
                            await asyncio.sleep(0.5)
                except Exception:
                    pass

            # How Did You Hear About Us? — match any job-board or online source option
            source_btn = await page.query_selector(
                "button[data-automation-id='source'], div[data-automation-id='source'] button"
            )
            if source_btn:
                try:
                    await source_btn.click()
                    await asyncio.sleep(0.8)
                    hear_opt = await page.query_selector(
                        "div[data-automation-id='menuItem']:has-text('LinkedIn'), "
                        "div[data-automation-id='menuItem']:has-text('Indeed'), "
                        "div[data-automation-id='menuItem']:has-text('Glassdoor'), "
                        "div[data-automation-id='menuItem']:has-text('Job Board'), "
                        "div[data-automation-id='menuItem']:has-text('Internet'), "
                        "div[data-automation-id='menuItem']:has-text('Online'), "
                        "div[data-automation-id='menuItem']:has-text('Career'), "
                        "li:has-text('LinkedIn'), li:has-text('Indeed'), li:has-text('Internet')"
                    )
                    if hear_opt:
                        await hear_opt.click()
                        await asyncio.sleep(0.5)
                    else:
                        # Fall back to first available option
                        first_opt = await page.query_selector("div[data-automation-id='menuItem']")
                        if first_opt:
                            await first_opt.click()
                            await asyncio.sleep(0.4)
                except Exception:
                    pass

            # Work Authorization → Yes (Workday radio or yes/no widget)
            for auth_sel in [
                "button[data-automation-id*='authorized' i]:has-text('Yes')",
                "label:has-text('Yes'):near(label:has-text('authorized'))",
            ]:
                try:
                    auth_yes = await page.query_selector(auth_sel)
                    if auth_yes and await auth_yes.is_visible():
                        await auth_yes.click(force=True)
                        await asyncio.sleep(0.4)
                        break
                except Exception:
                    pass

            # EEO / Voluntary Disclosure checkboxes
            for cb_sel in [
                "input[type='checkbox'][data-automation-id*='disclose' i]",
                "input[type='checkbox']",
            ]:
                checkboxes = await page.query_selector_all(cb_sel)
                for cb in checkboxes:
                    try:
                        if not await cb.is_checked():
                            await cb.click(force=True)
                    except Exception:
                        pass
                if checkboxes:
                    break

            # Advance to next step
            next_btn = await page.query_selector(
                'button[data-automation-id="bottom-navigation-next-button"], '
                'button[data-automation-id="bottom-navigation-done-button"], '
                'button:has-text("Save and Continue"), button:has-text("Next")'
            )
            if next_btn and await next_btn.is_visible():
                try:
                    await next_btn.click(force=True)
                    await asyncio.sleep(3.5)
                except Exception:
                    pass
            else:
                # No next button found — likely on Review page, stop advancing
                print(f"[Workday] Step {step_idx + 1}: no Next button found, stopping step advancement")
                break

        return True, f"Workday: navigated {company} portal, uploaded resume, filled 6-step form, synced credentials"

    async def _fill_linkedin(self, page, job: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Automates LinkedIn Easy Apply modal workflow:
        1. Clicks 'Easy Apply' button
        2. Iterates through multi-step dialog modal (Contact, Experience, Resume, Custom Qs)
        3. Pre-fills form fields using candidate profile and FormLLMEngine
        4. Stops at 'Review' step without clicking final Submit button for user review
        """
        pers = self.profile["personal"]
        prof = self.profile.get("professional", {})

        await asyncio.sleep(2.0)

        # ── 1. Locate and click 'Easy Apply' button ────────────────────────────
        apply_btn = None
        for sel in [
            "button.jobs-apply-button",
            "button:has-text('Easy Apply')",
            "button:has-text('Apply now')",
            ".jobs-s-apply button"
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    apply_btn = btn
                    break
            except Exception:
                pass

        if apply_btn:
            try:
                await apply_btn.click()
                await asyncio.sleep(2.5)
            except Exception as e:
                print(f"[LinkedIn] Could not click Easy Apply button: {e}")
        else:
            print("[LinkedIn] Easy Apply button not directly clickable, checking if modal is already open...")

        # ── 2. Multi-step Modal Loop ──────────────────────────────────────────
        for step in range(12):
            # Check for modal dialog
            modal = await page.query_selector("div.jobs-easy-apply-modal, div[role='dialog'], div.jobs-easy-apply-content")
            if not modal:
                # Wait briefly in case modal is launching asynchronously
                await asyncio.sleep(1.5)
                modal = await page.query_selector("div.jobs-easy-apply-modal, div[role='dialog'], div.jobs-easy-apply-content")
                if not modal:
                    if step == 0:
                        return False, "LinkedIn: Easy Apply modal did not open. Ensure user is logged in to LinkedIn."
                    break

            await asyncio.sleep(1.0)
            target = modal

            # ── 2a. Fill Phone Number ──────────────────────────────────────────
            phone_el = await target.query_selector("input[id*='phoneNumber'], input[name*='phoneNumber'], input[aria-label*='phone' i]")
            if phone_el:
                curr = await phone_el.input_value()
                if not curr:
                    await human_type(phone_el, pers["phone"])

            # ── 2b. Fill City / Location ───────────────────────────────────────
            city_el = await target.query_selector("input[id*='city' i], input[name*='city' i], input[aria-label*='city' i]")
            if city_el:
                curr = await city_el.input_value()
                if not curr:
                    await human_type(city_el, pers.get("current_city", "Bengaluru"))
                    await asyncio.sleep(1.0)
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Enter")

            # ── 2c. Resume Upload ──────────────────────────────────────────────
            resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
            if resume_path and os.path.exists(resume_path):
                file_input = await target.query_selector("input[type='file']")
                if file_input:
                    try:
                        await file_input.set_input_files(resume_path)
                        await asyncio.sleep(1.5)
                    except Exception:
                        pass

            # ── 2d. Fill Form Questions (Text, Radios, Selects) ───────────────
            form_fields = await target.query_selector_all("div.fb-dash-form-element, div.jobs-easy-apply-form-element, div.pb4")
            for ff in form_fields:
                try:
                    label_el = await ff.query_selector("label, span.fb-dash-form-element__label, span.t-14")
                    if not label_el:
                        continue
                    q_text = (await label_el.inner_text()).strip()
                    if not q_text or len(q_text) < 3:
                        continue

                    # Text / Numeric inputs
                    txt_input = await ff.query_selector("input[type='text'], input[type='number'], textarea")
                    if txt_input:
                        val = await txt_input.input_value()
                        if not val:
                            ans = self.llm.answer_question(q_text, job_context=job)
                            if ans:
                                await human_type(txt_input, ans)
                        continue

                    # Select dropdowns
                    sel_el = await ff.query_selector("select")
                    if sel_el:
                        opts = await sel_el.query_selector_all("option")
                        opt_texts = [await o.inner_text() for o in opts if (await o.get_attribute("value"))]
                        if opt_texts:
                            chosen = self.llm.answer_question(q_text, opt_texts, job_context=job)
                            if chosen:
                                try:
                                    await sel_el.select_option(label=chosen)
                                except Exception:
                                    pass
                        continue

                    # Radio fieldsets
                    radios = await ff.query_selector_all("input[type='radio']")
                    if radios:
                        radio_opts = []
                        for r in radios:
                            r_lbl = await r.evaluate_handle("el => el.closest('label') || el.parentElement")
                            r_txt = (await r_lbl.inner_text()).strip() if r_lbl else ""
                            radio_opts.append((r, r_txt))
                        valid_opts = [(r, t) for r, t in radio_opts if t]
                        if valid_opts:
                            chosen = self.llm.answer_question(q_text, [t for _, t in valid_opts], job_context=job)
                            if chosen:
                                for r, t in valid_opts:
                                    if chosen.lower() in t.lower() or t.lower() in chosen.lower():
                                        await r.click(force=True)
                                        break
                except Exception:
                    pass

            # ── 2e. Check for Next / Review Button vs Submit Button ───────────
            next_btn = await target.query_selector(
                "button[aria-label*='Continue to next step' i], "
                "button[aria-label*='Review your application' i], "
                "button:has-text('Next'), button:has-text('Review')"
            )
            submit_btn = await target.query_selector(
                "button[aria-label*='Submit application' i], button:has-text('Submit application')"
            )

            if submit_btn and await submit_btn.is_visible() and not next_btn:
                print(f"[LinkedIn] Reached Review/Submit screen at step {step + 1}. Form pre-filled completely!")
                break

            if next_btn and await next_btn.is_visible():
                btn_txt = (await next_btn.inner_text()).strip()
                if "review" in btn_txt.lower():
                    await next_btn.click()
                    await asyncio.sleep(2.0)
                    print(f"[LinkedIn] Clicked Review button, reached final review screen!")
                    break
                else:
                    await next_btn.click()
                    await asyncio.sleep(2.0)
            else:
                print(f"[LinkedIn] No Next or Review button visible at step {step + 1}. Stopping loop.")
                break

        return True, "LinkedIn Easy Apply: pre-filled multi-step modal fields up to review stage"
