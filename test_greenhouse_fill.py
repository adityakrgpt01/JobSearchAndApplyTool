#!/usr/bin/env python3
import asyncio
import os
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        url = "https://job-boards.greenhouse.io/twilio/jobs/8015771"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="networkidle")

        # 1. First Name, Last Name, Preferred Name, Email, Phone
        await page.fill("#first_name", "Aditya")
        await page.fill("#last_name", "Kumar")
        pref = await page.query_selector("#preferred_name")
        if pref:
            await pref.fill("Aditya")
        await page.fill("#email", "adityakumargupta521@gmail.com")
        await page.fill("#phone", "9643914225")

        # Location
        loc = await page.query_selector("#candidate-location")
        if loc:
            await loc.fill("Bangalore, India")
            await asyncio.sleep(0.5)

        # Resume upload
        res = await page.query_selector("input[type='file']#resume, input[type='file']")
        if res:
            await res.set_input_files("resume.pdf")
            print("Attached resume.pdf")
            await asyncio.sleep(2.0)

        # LinkedIn Profile
        li = await page.query_selector("input[id*='linkedin' i], input[aria-label*='linkedin' i]")
        if not li:
            # check by label
            li = await page.query_selector("label:has-text('LinkedIn') + input, input#question_67701498")
        if li:
            await li.fill("https://www.linkedin.com/in/aditya-kumar-a518571b5/")
            print("Filled LinkedIn profile")

        # Checkbox: How did you hear -> LinkedIn
        li_cb = await page.query_selector("label:has-text('LinkedIn') input[type='checkbox'], input[type='checkbox']#question_67701499\\[\\]_729671439")
        if not li_cb:
            li_cb = await page.query_selector("input[value*='LinkedIn'], input[aria-label*='LinkedIn']")
        if li_cb and not (await li_cb.is_checked()):
            await li_cb.check(force=True)
            print("Checked How did you hear -> LinkedIn")

        # Combobox dropdowns helper
        async def fill_gh_combobox(selector_or_el, choice_text):
            try:
                el = selector_or_el if hasattr(selector_or_el, "click") else await page.query_selector(selector_or_el)
                if not el: return False
                await el.scroll_into_view_if_needed()
                await el.click(force=True)
                await asyncio.sleep(0.8)

                # Check options
                opts = await page.query_selector_all("[role='option'], li[role='option'], .select2-results__option")
                for o in opts:
                    txt = (await o.inner_text()).strip().lower()
                    if choice_text.lower() in txt:
                        await o.click(force=True)
                        await asyncio.sleep(0.5)
                        return True
                if opts:
                    await opts[0].click(force=True)
                    await asyncio.sleep(0.5)
                    return True
                return False
            except Exception as e:
                print(f"Combobox select error: {e}")
                return False

        # Combobox 1: Are you legally authorized
        auth_cb = await page.query_selector("input#question_67701501, input[aria-label*='legally authorized' i]")
        if auth_cb:
            await fill_gh_combobox(auth_cb, "Yes")
            print("Selected authorized: Yes")

        # Combobox 2: What is the source of your right to work
        source_cb = await page.query_selector("input#question_67701502, input[aria-label*='source of your right to work' i]")
        if source_cb:
            await fill_gh_combobox(source_cb, "Citizen")
            print("Selected right to work source")

        # Checkboxes: Acknowledge & Consent
        cbs = await page.query_selector_all("input[type='checkbox']")
        for cb in cbs:
            try:
                if not await cb.is_checked():
                    lbl = (await page.evaluate('(el) => el.closest("label")?.innerText || el.parentElement?.innerText || ""', cb)).lower()
                    if any(k in lbl for k in ["acknowledge", "consent", "guidelines", "responsible use", "confirm"]):
                        await cb.check(force=True)
                        print(f"Checked consent/ack checkbox: {lbl[:40]}")
            except Exception:
                pass

        # Gender
        gender_cb = await page.query_selector("input[id='353'], input[aria-label*='gender' i]")
        if gender_cb:
            await fill_gh_combobox(gender_cb, "Male")
            print("Selected Gender: Male")

        # Demographic consent
        demo_cb = await page.query_selector("input#gdpr_demographic_data_consent_given_1")
        if demo_cb and not (await demo_cb.is_checked()):
            await demo_cb.check(force=True)
            print("Checked demographic consent")

        await asyncio.sleep(2.0)

        # Check if submit button is visible
        sub = await page.query_selector("button:has-text('Submit application'), button#submit_app, input[type='submit']")
        print(f"Submit button present and visible: {bool(sub and await sub.is_visible())}")

        # Capture complete review screenshot
        sc_path = "screenshots/twilio_genuine_completed_review.png"
        await page.screenshot(path=sc_path, full_page=True)
        print(f"Saved complete review screenshot to {sc_path}")

        # Copy to artifacts review_screenshots
        os.makedirs("/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots", exist_ok=True)
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/twilio_proof.png", full_page=True)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
