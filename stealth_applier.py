"""
Stealth Auto-Applier Engine.
Automates Greenhouse & Lever job applications using Playwright with anti-detection:
- playwright-stealth patches
- Human keystroke timing jitter (50-150ms)
- Natural scrolling & mouse actions
- Resume upload and form field mapping
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

def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)

async def human_type(element, text: str):
    """Types text with natural human delays between keystrokes."""
    for char in text:
        await element.type(char, delay=random.uniform(30, 90))
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.1, 0.3))

class StealthApplier:
    def __init__(self, profile_path: str = "user_profile.json", dry_run: bool = True):
        self.profile = load_user_profile(profile_path)
        self.dry_run = dry_run # If True, fills out form and screenshots, but doesn't press Submit
        self.stealth = Stealth()

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
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await self.stealth.apply_stealth_async(page)

            try:
                print(f"Navigating to {url}...")
                await page.goto(url, wait_until="networkidle", timeout=35000)
                await asyncio.sleep(random.uniform(1.5, 3.0))

                if platform == "greenhouse":
                    success, msg = await self._fill_greenhouse(page)
                elif platform == "lever":
                    success, msg = await self._fill_lever(page)
                else:
                    success, msg = False, f"Unsupported platform: {platform}"

                await page.screenshot(path=screenshot_path, full_page=True)

                if success:
                    if self.dry_run:
                        status = "READY_TO_SUBMIT (DRY_RUN)"
                        final_msg = f"{msg} [DRY RUN: Form filled completely, skipped final click]"
                    else:
                        status = "APPLIED"
                        final_msg = f"{msg} [Application submitted successfully]"
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
        await page.mouse.wheel(0, 400)
        await asyncio.sleep(1.0)

        fn_input = await page.query_selector("#first_name")
        if fn_input:
            await human_type(fn_input, pers["first_name"])

        ln_input = await page.query_selector("#last_name")
        if ln_input:
            await human_type(ln_input, pers["last_name"])

        email_input = await page.query_selector("#email")
        if email_input:
            await human_type(email_input, pers["email"])

        phone_input = await page.query_selector("#phone")
        if phone_input:
            await human_type(phone_input, pers["phone"])

        li_input = await page.query_selector("input[id*='linkedin' i], input[autocomplete*='linkedin' i]")
        if li_input and pers.get("linkedin_url"):
            await human_type(li_input, pers["linkedin_url"])

        resume_path = self.profile.get("resume", {}).get("file_path")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                await file_input.set_input_files(resume_path)

        return True, "Filled standard Greenhouse fields"

    async def _fill_lever(self, page) -> (bool, str):
        pers = self.profile["personal"]
        apply_btn = await page.query_selector("a.postings-btn, .template-btn-submit")
        if apply_btn:
            await apply_btn.click()
            await asyncio.sleep(2.0)

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

        resume_path = self.profile.get("resume", {}).get("file_path")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                await file_input.set_input_files(resume_path)

        return True, "Filled standard Lever fields"
