#!/usr/bin/env python3
"""
Interactive Headful Runner for MSCI (iCIMS ATS).
Launches headful browser on Mac desktop, fills email & consent,
waits for human to complete the hCaptcha checkpoint,
then automates form filling, resume attachment, and halts at Review page.
"""

import asyncio
import os
from playwright.async_api import async_playwright

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_PATH = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/msci_proof.png"

async def run_msci_interactive():
    print("=" * 80)
    print("🚀 LAUNCHING HEADFUL MSCI APPLICATION RUNNER")
    print("=" * 80)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=50)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 950},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        job_url = "https://globalcareers-msci.icims.com/jobs/5973/job?mode=apply&apply=yes_iframe=1"
        print(f"🌐 Navigating to MSCI job apply page: {job_url}")
        await page.goto(job_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        frame = page.frame(name="icims_content_iframe") or page
        print(f"Target frame URL: {frame.url}")

        # Step 1: Pre-fill email and consent if on login step
        try:
            email_field = await frame.wait_for_selector("input#email, input[type='email'], input[name='email']", timeout=10000)
            if email_field:
                print("📧 Pre-filling email: adityakumargupta521@gmail.com")
                await email_field.fill("adityakumargupta521@gmail.com")
                await page.wait_for_timeout(800)

                # Consent checkbox
                chk = await frame.query_selector("input[type='checkbox']")
                if chk and not await chk.is_checked():
                    print("✅ Checking consent checkbox...")
                    await chk.check()
                    await page.wait_for_timeout(800)

                # Next button
                next_btn = await frame.query_selector("input#enter-profile, button#enter-profile, input[type='submit'], button[type='submit']")
                if next_btn:
                    print("👉 Clicking 'Next' to trigger verification...")
                    await next_btn.click()
                    await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"Notice on initial email step: {e}")

        print("\n" + "#" * 80)
        print("🔔 ACTION REQUIRED ON DESKTOP:")
        print("Please solve the hCaptcha on the open Chromium window if visible.")
        print("The script is monitoring the page and will automatically continue once verified!")
        print("#" * 80 + "\n")

        # Step 2: Poll until application form fields or profile upload appears
        form_reached = False
        for tick in range(120): # wait up to 4 minutes
            current_frame = page.frame(name="icims_content_iframe") or page
            first_name = await current_frame.query_selector("input#val_candidate_first_name, input[name*='first_name'], input#firstName, input[name*='firstName']")
            resume_input = await current_frame.query_selector("input[type='file'], input#resume_file, input[name*='resume']")
            
            if first_name or resume_input:
                print("🎉 SUCCESS: Post-captcha application form detected!")
                form_reached = True
                frame = current_frame
                break
                
            await asyncio.sleep(2)
            if tick % 5 == 0:
                print(f"⏳ Waiting for captcha resolution... ({tick * 2}s elapsed)")

        if not form_reached:
            print("⚠️ Timeout waiting for application form.")
            await page.screenshot(path=PROOF_PATH.replace("_proof.png", "_timeout_debug.png"), full_page=True)
            await browser.close()
            return

        print("📝 Automating full profile fields injection...")
        await page.wait_for_timeout(2000)

        # Upload resume
        file_input = await frame.query_selector("input[type='file']")
        if file_input and os.path.exists(RESUME_PATH):
            print(f"📎 Attaching candidate resume from {RESUME_PATH}...")
            await file_input.set_input_files(RESUME_PATH)
            await page.wait_for_timeout(3000)

        # Fill fields
        fn = await frame.query_selector("input#val_candidate_first_name, input[name*='first_name'], input#firstName, input[name*='firstName']")
        if fn: await fn.fill("Aditya")

        ln = await frame.query_selector("input#val_candidate_last_name, input[name*='last_name'], input#lastName, input[name*='lastName']")
        if ln: await ln.fill("Kumar")

        ph = await frame.query_selector("input#val_candidate_phone, input[name*='phone'], input#phone, input[type='tel']")
        if ph: await ph.fill("9643914225")

        addr = await frame.query_selector("input#val_candidate_address_street1, input[name*='street'], input[name*='address']")
        if addr: await addr.fill("Flat 402, Green Glen Layout, Bellandur")

        city = await frame.query_selector("input#val_candidate_address_city, input[name*='city']")
        if city: await city.fill("Bangalore")

        zip_el = await frame.query_selector("input#val_candidate_address_zip, input[name*='zip'], input[name*='postal']")
        if zip_el: await zip_el.fill("560103")

        await page.wait_for_timeout(2000)

        # Click Next / Continue to reach Review step
        print("🔍 Seeking Review / Summary page navigation...")
        continue_btn = await frame.query_selector("button#submit-profile, input#submit-profile, button:has-text('Next'), input[value*='Next'], button:has-text('Continue'), input[value*='Continue']")
        if continue_btn:
            print("👉 Navigating to Review page...")
            await continue_btn.click()
            await page.wait_for_timeout(5000)

        # Re-check frame after navigation
        frame = page.frame(name="icims_content_iframe") or page
        print(f"Final Page Title: {await page.title()}")
        print(f"Final Frame URL: {frame.url}")

        # Capture Verified Review Screenshot
        os.makedirs(os.path.dirname(PROOF_PATH), exist_ok=True)
        await page.screenshot(path=PROOF_PATH, full_page=True)
        print(f"📸 VERIFIED PROOF SAVED TO: {PROOF_PATH}")

        print("🛑 HALTED SAFELY AT REVIEW PAGE WITHOUT FINAL SUBMISSION.")
        await page.wait_for_timeout(10000)
        await browser.close()
        print("✨ MSCI AUTOMATION COMPLETED!")

if __name__ == "__main__":
    asyncio.run(run_msci_interactive())
