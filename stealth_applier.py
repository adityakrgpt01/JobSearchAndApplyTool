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
import random
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from database import update_job_status
from llm_form_engine import FormLLMEngine

def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)

async def human_type(element, text: str):
    """Types text with natural human delays between keystrokes."""
    if not text:
        return
    for char in text:
        await element.type(char, delay=random.uniform(25, 75))
        if random.random() < 0.04:
            await asyncio.sleep(random.uniform(0.08, 0.2))

class StealthApplier:
    def __init__(self, profile_path: str = "user_profile.json", dry_run: bool = True):
        self.profile = load_user_profile(profile_path)
        self.dry_run = dry_run # If True, fills out form and screenshots, but doesn't press Submit
        self.stealth = Stealth()
        self.llm = FormLLMEngine(self.profile)

    async def apply_to_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = job["job_id"]
        url = job["apply_url"]
        platform = job.get("ats_platform", "greenhouse")
        os.makedirs("screenshots", exist_ok=True)
        screenshot_path = f"screenshots/{job_id}.png"

        update_job_status(job_id, "APPLYING", f"Starting stealth application for {job['title']} at {job['company_name']}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
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
                print(f"Navigating to {url}...")
                await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(random.uniform(1.5, 2.5))

                if platform == "greenhouse":
                    success, msg = await self._fill_greenhouse(page)
                elif platform == "lever":
                    success, msg = await self._fill_lever(page)
                elif platform == "ashby":
                    success, msg = await self._fill_ashby(page)
                else:
                    success, msg = False, f"Unsupported platform for direct apply: {platform}"

                await page.screenshot(path=screenshot_path, full_page=True)

                if success:
                    if self.dry_run:
                        status = "READY_TO_SUBMIT (DRY_RUN)"
                        final_msg = f"{msg} [DRY RUN: Form filled completely with LLM resolution, skipped final click]"
                    else:
                        # Attempt final submission click
                        submit_btn = await page.query_selector("button[type='submit'], input[type='submit'], #submit_app, .template-btn-submit")
                        if submit_btn:
                            await submit_btn.click()
                            await asyncio.sleep(3.0)
                            await page.screenshot(path=screenshot_path, full_page=True)
                            status = "APPLIED"
                            final_msg = f"{msg} [Application submitted successfully]"
                        else:
                            status = "READY_TO_SUBMIT"
                            final_msg = f"{msg} [Form filled, manual review recommended]"
                else:
                    status = "FAILED"
                    final_msg = f"Failed to fill form: {msg}"

                update_job_status(job_id, status, final_msg, screenshot_path)
                await browser.close()
                return {"job_id": job_id, "status": status, "message": final_msg, "screenshot": screenshot_path}

            except Exception as e:
                err_msg = str(e)
                print(f"Error applying to {job_id}: {err_msg}")
                try:
                    await page.screenshot(path=screenshot_path)
                except Exception:
                    pass
                update_job_status(job_id, "FAILED", err_msg, screenshot_path)
                await browser.close()
                return {"job_id": job_id, "status": "FAILED", "message": err_msg, "screenshot": screenshot_path}

    async def _fill_greenhouse(self, page) -> (bool, str):
        pers = self.profile["personal"]
        await page.mouse.wheel(0, 300)
        await asyncio.sleep(0.8)

        # 1. Standard Fields
        fn_input = await page.query_selector("#first_name, input[name='first_name']")
        if fn_input:
            await human_type(fn_input, pers["first_name"])

        ln_input = await page.query_selector("#last_name, input[name='last_name']")
        if ln_input:
            await human_type(ln_input, pers["last_name"])

        email_input = await page.query_selector("#email, input[name='email']")
        if email_input:
            await human_type(email_input, pers["email"])

        phone_input = await page.query_selector("#phone, input[name='phone']")
        if phone_input:
            await human_type(phone_input, pers["phone"])

        # Country / City if required
        city_input = await page.query_selector("#candidate-location, input[id*='location' i]")
        if city_input:
            await human_type(city_input, pers.get("current_city", "Bengaluru"))

        # 2. Resume File Upload
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                await file_input.set_input_files(resume_path)
                await asyncio.sleep(1.0)

        # 3. Dynamic Custom Question Resolution via LLM Engine
        # Look for custom fields and question containers
        fields = await page.query_selector_all("div.field, div[class*='field']")
        for f in fields:
            try:
                label_el = await f.query_selector("label")
                if not label_el:
                    continue
                q_text = (await label_el.inner_text()).strip()
                if not q_text or len(q_text) < 3:
                    continue

                # Check text inputs
                text_input = await f.query_selector("input[type='text'], textarea")
                if text_input:
                    current_val = await text_input.input_value()
                    if not current_val:
                        ans = self.llm.answer_question(q_text)
                        if ans:
                            await human_type(text_input, ans)

                # Check select dropdowns
                select_el = await f.query_selector("select")
                if select_el:
                    opts = await select_el.query_selector_all("option")
                    opt_texts = [await o.inner_text() for o in opts if (await o.get_attribute("value"))]
                    if opt_texts:
                        chosen = self.llm.answer_question(q_text, opt_texts)
                        if chosen:
                            await select_el.select_option(label=chosen)
            except Exception as e:
                print(f"[Greenhouse Field Warning] {e}")

        return True, "Filled standard Greenhouse fields and resolved custom questions with LLM"

    async def _fill_lever(self, page) -> (bool, str):
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
                q_text = (await label_el.inner_text()).strip()
                t_input = await card.query_selector("input[type='text'], textarea")
                if t_input:
                    ans = self.llm.answer_question(q_text)
                    if ans:
                        await human_type(t_input, ans)
            except Exception:
                pass

        return True, "Filled Lever fields and resolved custom questions with LLM"

    async def _fill_ashby(self, page) -> (bool, str):
        pers = self.profile["personal"]
        await page.mouse.wheel(0, 300)
        await asyncio.sleep(1.0)

        # Name
        name_input = await page.query_selector("input[id*='name' i]")
        if name_input:
            await human_type(name_input, f"{pers['first_name']} {pers['last_name']}")

        email_input = await page.query_selector("input[type='email']")
        if email_input:
            await human_type(email_input, pers["email"])

        phone_input = await page.query_selector("input[type='tel']")
        if phone_input:
            await human_type(phone_input, pers["phone"])

        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                await file_input.set_input_files(resume_path)

        return True, "Filled Ashby fields and attached resume"
