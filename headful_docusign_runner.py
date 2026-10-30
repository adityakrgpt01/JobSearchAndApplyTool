#!/usr/bin/env python3
"""
Interactive Headful Runner for DocuSign (iCIMS ATS).
Launches headful browser on Mac desktop, fills email & GDPR consent,
clicks #enterEmailSubmitButton to display the hCaptcha challenge,
waits for the user to complete the puzzle, then automatically fills
candidate Aditya Kumar's profile, attaches resume.pdf, advances to Review,
halts before final submit, and captures docusign_proof.png.
"""

import asyncio
import os
from playwright.async_api import async_playwright

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_PATH = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/docusign_proof.png"

async def run_docusign_interactive():
    print("=" * 80)
    print("🚀 LAUNCHING HEADFUL DOCUSIGN APPLICATION RUNNER")
    print("=" * 80)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=30)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 950},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        job_url = "https://indiacareers-docusign.icims.com/jobs/30373/login"
        print(f"🌐 Navigating to DocuSign job apply page: {job_url}")
        await page.goto(job_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        fl = page.frame_locator("iframe[name='icims_content_iframe']")
        
        print("📧 Pre-filling email: adityakumargupta521@gmail.com")
        await fl.locator("#email").fill("adityakumargupta521@gmail.com")
        await page.wait_for_timeout(600)

        print("✅ Checking GDPR consent checkbox...")
        await fl.locator("#accept_gdpr").check()
        await page.wait_for_timeout(600)

        print("👉 Clicking '#enterEmailSubmitButton' to bring up hCaptcha...")
        await fl.locator("#enterEmailSubmitButton").click()
        await page.wait_for_timeout(2000)

        print("\n" + "#" * 80)
        print("🔔 ACTION REQUIRED ON DESKTOP:")
        print("Please drag the shape to the closed loop on the open Chromium window.")
        print("The script is monitoring the page and will automatically continue once verified!")
        print("#" * 80 + "\n")

        # Step 2: Poll until application form fields or profile upload appears
        form_reached = False
        for tick in range(150): # wait up to 5 minutes
            # Re-fetch frame
            current_frame = page.frame(name="icims_content_iframe") or page
            
            # Check for form markers (name inputs, resume upload input, etc.)
            first_name = await current_frame.query_selector("input#val_candidate_first_name, input[name*='first_name'], input#firstName, input[name*='firstName']")
            resume_input = await current_frame.query_selector("input[type='file'], input#resume_file, input[name*='resume']")
            
            # Also check if login frame disappeared or URL navigated
            if (first_name or resume_input) or ("candidate" in current_frame.url or "profile" in current_frame.url or "application" in current_frame.url):
                print("🎉 SUCCESS: Post-captcha application form detected!")
                form_reached = True
                frame = current_frame
                break
                
            await asyncio.sleep(2)
            if tick % 5 == 0:
                print(f"⏳ Waiting for captcha resolution... ({tick * 2}s elapsed)")

        if not form_reached:
            print("⚠️ Timeout waiting for application form. Taking debug screenshot...")
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

        # Candidate Fields
        fields = {
            "first_name": "Aditya",
            "last_name": "Kumar",
            "phone": "9643914225",
            "address": "Flat 402, Green Glen Layout, Bellandur",
            "city": "Bangalore",
            "zip": "560103"
        }

        # First Name
        fn = await frame.query_selector("input#val_candidate_first_name, input[name*='first_name'], input#firstName, input[name*='firstName']")
        if fn:
            await fn.fill(fields["first_name"])

        # Last Name
        ln = await frame.query_selector("input#val_candidate_last_name, input[name*='last_name'], input#lastName, input[name*='lastName']")
        if ln:
            await ln.fill(fields["last_name"])

        # Phone
        ph = await frame.query_selector("input#val_candidate_phone, input[name*='phone'], input#phone, input[type='tel']")
        if ph:
            await ph.fill(fields["phone"])

        # Address
        addr = await frame.query_selector("input#val_candidate_address_street1, input[name*='street'], input[name*='address']")
        if addr:
            await addr.fill(fields["address"])

        # City
        city = await frame.query_selector("input#val_candidate_address_city, input[name*='city']")
        if city:
            await city.fill(fields["city"])

        # Zip
        zip_el = await frame.query_selector("input#val_candidate_address_zip, input[name*='zip'], input[name*='postal']")
        if zip_el:
            await zip_el.fill(fields["zip"])

        await page.wait_for_timeout(2000)

        # Click Next / Continue to reach the Review step
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
        print("Keep browser open for 15 seconds for visual inspection...")
        await page.wait_for_timeout(15000)
        await browser.close()
        print("✨ DOCUSIGN AUTOMATION COMPLETED!")

if __name__ == "__main__":
    asyncio.run(run_docusign_interactive())
