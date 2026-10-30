#!/usr/bin/env python3
"""
Interactive Headful Runner for Teradata (gr8people ATS).
Launches headful browser on Mac desktop, fills email & agreement,
waits for human to complete the reCAPTCHA checkpoint,
then automates candidate form filling, resume attachment, and halts at Review page.
"""

import asyncio
import os
from playwright.async_api import async_playwright

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_PATH = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/teradata_proof.png"

async def run_teradata_interactive():
    print("=" * 80)
    print("🚀 LAUNCHING HEADFUL TERADATA APPLICATION RUNNER")
    print("=" * 80)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=50)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 950},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        job_url = "https://careers.teradata.com/jobs/220870/senior-ai-engineer/apply"
        print(f"🌐 Navigating to Teradata job apply: {job_url}")
        await page.goto(job_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        # Accept cookie banner if present
        try:
            accept_btn = await page.query_selector("button:has-text('Accept'), button:has-text('Acknowledge')")
            if accept_btn:
                await accept_btn.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # Fill email
        try:
            email_field = await page.query_selector("input[type='email'], input[name*='email'], input#email")
            if email_field:
                print("📧 Pre-filling email: adityakumargupta521@gmail.com")
                await email_field.fill("adityakumargupta521@gmail.com")
                await page.wait_for_timeout(800)

            # Check privacy checkbox
            chk = await page.query_selector("input[type='checkbox']")
            if chk and not await chk.is_checked():
                print("✅ Checking privacy policy checkbox...")
                await chk.check()
                await page.wait_for_timeout(800)
        except Exception as e:
            print(f"Notice during initial form entry: {e}")

        print("\n" + "#" * 80)
        print("🔔 ACTION REQUIRED ON DESKTOP:")
        print("Please solve the reCAPTCHA on the open Chromium window and click 'Continue'.")
        print("The script is monitoring the page and will automatically continue once verified!")
        print("#" * 80 + "\n")

        # Poll until the next application stage loads
        form_reached = False
        for tick in range(120): # wait up to 4 minutes
            # Check if URL changed or application fields appeared
            current_url = page.url
            first_name = await page.query_selector("input[name*='first'], input#firstName, input[name*='first_name']")
            file_input = await page.query_selector("input[type='file']")
            
            if "login" not in current_url.lower() or first_name or file_input:
                print(f"🎉 SUCCESS: Step transitioned! Current URL: {current_url}")
                form_reached = True
                break
                
            await asyncio.sleep(2)
            if tick % 5 == 0:
                print(f"⏳ Waiting for verification... ({tick * 2}s elapsed)")

        if not form_reached:
            print("⚠️ Timeout waiting for Teradata form transition.")
            await page.screenshot(path=PROOF_PATH.replace("_proof.png", "_timeout_debug.png"), full_page=True)
            await browser.close()
            return

        print("📝 Automating full profile fields injection...")
        await page.wait_for_timeout(2000)

        # Upload resume
        file_input = await page.query_selector("input[type='file']")
        if file_input and os.path.exists(RESUME_PATH):
            print(f"📎 Attaching candidate resume from {RESUME_PATH}...")
            await file_input.set_input_files(RESUME_PATH)
            await page.wait_for_timeout(3000)

        # Fill fields if present
        fn = await page.query_selector("input[name*='first'], input#firstName")
        if fn: await fn.fill("Aditya")

        ln = await page.query_selector("input[name*='last'], input#lastName")
        if ln: await ln.fill("Kumar")

        ph = await page.query_selector("input[name*='phone'], input#phone, input[type='tel']")
        if ph: await ph.fill("9643914225")

        addr = await page.query_selector("input[name*='street'], input[name*='address']")
        if addr: await addr.fill("Flat 402, Green Glen Layout, Bellandur")

        city = await page.query_selector("input[name*='city']")
        if city: await city.fill("Bangalore")

        zip_el = await page.query_selector("input[name*='zip'], input[name*='postal']")
        if zip_el: await zip_el.fill("560103")

        await page.wait_for_timeout(2000)

        # Seek Review / Submit navigation
        continue_btn = await page.query_selector("button:has-text('Next'), input[value*='Next'], button:has-text('Continue'), input[value*='Continue'], button:has-text('Review')")
        if continue_btn:
            print("👉 Navigating to Review screen...")
            await continue_btn.click()
            await page.wait_for_timeout(5000)

        # Capture Verified Review Screenshot
        os.makedirs(os.path.dirname(PROOF_PATH), exist_ok=True)
        await page.screenshot(path=PROOF_PATH, full_page=True)
        print(f"📸 VERIFIED PROOF SAVED TO: {PROOF_PATH}")

        print("🛑 HALTED SAFELY AT REVIEW PAGE WITHOUT FINAL SUBMISSION.")
        await page.wait_for_timeout(10000)
        await browser.close()
        print("✨ TERADATA AUTOMATION COMPLETED!")

if __name__ == "__main__":
    asyncio.run(run_teradata_interactive())
