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

        url = "https://jobs.ashbyhq.com/anyscale/8b29c5e5-d56f-4c52-8887-a6a1827cf042/application"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="networkidle")

        # 1. Resume upload
        res_file = "resume.pdf"
        file_input = await page.wait_for_selector("input[type='file']", timeout=10000)
        await file_input.set_input_files(res_file)
        print("Attached resume.pdf. Waiting for Ashby to process autofill...")

        try:
            await page.wait_for_selector("text=Autofill completed", timeout=15000)
            print("Ashby autofill completed banner detected!")
        except Exception:
            await asyncio.sleep(4.0)

        await asyncio.sleep(2.0)

        # 2. Fill fields via get_by_label
        async def fill_field(label, val):
            try:
                loc = page.get_by_label(label, exact=False)
                if await loc.count() > 0:
                    await loc.first.fill(val)
                    print(f"Filled '{label}': {val}")
                    return True
                # Fallback to placeholder
                loc_ph = page.get_by_placeholder(label, exact=False)
                if await loc_ph.count() > 0:
                    await loc_ph.first.fill(val)
                    print(f"Filled placeholder '{label}': {val}")
                    return True
            except Exception as e:
                print(f"Error filling {label}: {e}")
            return False

        await fill_field("Name", "Aditya Kumar")
        await fill_field("Email", "adityakumargupta521@gmail.com")
        await fill_field("Phone", "+91 9643914225")
        await fill_field("Current location", "Bangalore, India")
        await fill_field("Current company", "73 Strings")
        await fill_field("LinkedIn", "https://www.linkedin.com/in/aditya-kumar-a518571b5/")
        await fill_field("GitHub", "https://github.com/adityakrgpt01")

        # 3. Radio button for visa sponsorship: click "No"
        try:
            await page.locator("label:has-text('No')").click(force=True)
            print("Selected sponsorship: No")
        except Exception as e:
            print(f"Radio note: {e}")

        await asyncio.sleep(1.5)

        # Check Submit button
        sub = await page.query_selector("button:has-text('Submit Application'), button[type='submit']")
        print(f"Submit button present and visible: {bool(sub and await sub.is_visible())}")

        sc_path = "screenshots/anyscale_genuine_completed_review.png"
        await page.screenshot(path=sc_path, full_page=True)
        print(f"Saved complete review screenshot to {sc_path}")

        # Copy to artifacts review_screenshots
        os.makedirs("/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots", exist_ok=True)
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/anyscale_proof.png", full_page=True)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
