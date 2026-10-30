import asyncio
import os
from playwright.async_api import async_playwright

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_DIR = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=60)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 950},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        job_url = "https://careers.teradata.com/jobs/220870/senior-ai-engineer/apply"
        print(f"Navigating to {job_url}...")
        await page.goto(job_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        # 1. Handle cookie banners safely
        for selector in ["button:has-text('Acknowledge')", "#truste-consent-button", "button:has-text('Accept')"]:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    print(f"Clicking cookie banner: {selector}")
                    await btn.click()
                    await page.wait_for_timeout(2000)
            except Exception as e:
                print(f"Banner notice ({selector}): {e}")

        # Wait for page stability
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)

        # 2. Check if email entry step
        try:
            email_input = await page.wait_for_selector("input#textfield-email, input[type='email'], input[name='email']", timeout=5000)
            if email_input:
                print("Found email input. Typing email...")
                await email_input.click()
                for ch in "adityakumargupta521@gmail.com":
                    await email_input.type(ch, delay=30)
                await page.wait_for_timeout(800)

                # Check privacy checkbox
                chk = await page.query_selector("input[name='acceptPrivacyPolicy'], input[type='checkbox']")
                if chk and not await chk.is_checked():
                    print("Checking privacy policy...")
                    await chk.check()
                    await page.wait_for_timeout(800)

                # Human mouse moves
                await page.mouse.move(150, 250)
                await page.wait_for_timeout(200)
                await page.mouse.move(400, 480)
                await page.wait_for_timeout(300)

                cont_btn = await page.query_selector("button:has-text('Continue')")
                if cont_btn:
                    print("Clicking Continue on Step 1...")
                    await cont_btn.click()
                    await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"Step 1 notice: {e}")

        await page.screenshot(path=f"{PROOF_DIR}/teradata_step1_done.png", full_page=True)
        print("Saved teradata_step1_done.png")

        # 3. Step 2: Profile Creation
        try:
            fn = await page.wait_for_selector("input#textfield-firstName, input[name='firstName']", timeout=8000)
            if fn:
                print("Profile Creation step detected! Filling names...")
                await fn.click()
                await fn.fill("Aditya")
                await page.wait_for_timeout(500)

                ln = await page.query_selector("input#textfield-lastName, input[name='lastName']")
                if ln:
                    await ln.click()
                    await ln.fill("Kumar")
                    await page.wait_for_timeout(500)

                # Dismiss any lingering TrustArc if it reappeared
                try:
                    truste = await page.query_selector("#truste-consent-button")
                    if truste and await truste.is_visible():
                        await truste.click()
                        await page.wait_for_timeout(1000)
                except: pass

                # Source dropdown
                src = await page.query_selector("input[name='sourceSelection'], div[role='combobox'], [aria-labelledby*='source']")
                if src:
                    print("Interacting with Source dropdown...")
                    await src.click()
                    await page.wait_for_timeout(1000)
                    options = await page.query_selector_all("li[role='option'], [role='option']")
                    print(f"Found {len(options)} options for Source.")
                    if options:
                        print(f"Selecting option: {await options[0].inner_text()}")
                        await options[0].click()
                    else:
                        # Press ArrowDown and Enter
                        await page.keyboard.press("ArrowDown")
                        await page.wait_for_timeout(300)
                        await page.keyboard.press("Enter")
                    await page.wait_for_timeout(800)

                await page.screenshot(path=f"{PROOF_DIR}/teradata_step2_filled.png", full_page=True)
                print("Saved teradata_step2_filled.png")

                cont2 = await page.query_selector("button:has-text('Continue')")
                if cont2:
                    print("Clicking Continue on Step 2...")
                    await cont2.click()
                    await page.wait_for_timeout(6000)
        except Exception as e:
            print(f"Step 2 notice: {e}")

        await page.screenshot(path=f"{PROOF_DIR}/teradata_step3_current.png", full_page=True)
        print("Saved teradata_step3_current.png")
        print(f"Current URL: {page.url}")

        # Keep browser open for inspection or next actions
        await page.wait_for_timeout(5000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
