import asyncio
import os
from playwright.async_api import async_playwright

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_DIR = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=60)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 950},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        url = "https://careers.teradata.com/jobs/220870/senior-ai-engineer/apply"
        print(f"Opening {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        # 1. Top banner Acknowledge only
        try:
            ack = await page.query_selector("button:has-text(\"Acknowledge\")")
            if ack:
                print("Clicked Acknowledge banner.")
                await ack.click()
        except Exception as e:
            print("Banner error:", e)
        await page.wait_for_timeout(1000)
        
        # 2. Type email
        print("Typing email...")
        await page.click("#textfield-email")
        for ch in "adityakumargupta521@gmail.com":
            await page.keyboard.type(ch, delay=25)
        await page.wait_for_timeout(600)
        
        # 3. Privacy checkbox
        print("Checking privacy policy...")
        await page.check("input[name=\"acceptPrivacyPolicy\"]")
        await page.wait_for_timeout(600)
        
        # 4. Mouse movements
        await page.mouse.move(250, 320)
        await page.wait_for_timeout(250)
        await page.mouse.move(450, 480)
        await page.wait_for_timeout(250)
        
        # 5. Click submit
        print("Submitting Step 1...")
        submit_btn = await page.query_selector("button.submitButton-12, button:has-text(\"Continue\")")
        if submit_btn:
            await submit_btn.click()
        await page.wait_for_timeout(4000)
        
        # Check if robot message
        robot_msg = await page.query_selector("text='Please verify that you are not a robot'")
        if robot_msg and await robot_msg.is_visible():
            print("⚠️ Robot message detected. Taking debug screenshot...")
            await page.screenshot(path=f"{PROOF_DIR}/teradata_robot_debug.png", full_page=True)
            # If robot message appears in headful mode, wait 30 seconds for manual solve if user wants
            print("Giving 20s window in case captcha challenge is visible...")
            await page.wait_for_timeout(20000)
        else:
            print("🎉 Passed Step 1 successfully!")

        # Check for Profile Creation
        fn = await page.query_selector("#textfield-firstName, input[name=\"firstName\"]")
        if fn:
            print("Step 2: Profile Creation reached!")
            # Dismiss TrustArc modal now
            try:
                truste = await page.query_selector("#truste-consent-button")
                if truste and await truste.is_visible():
                    print("Dismissing TrustArc modal on Step 2...")
                    await truste.click()
                    await page.wait_for_timeout(1000)
            except Exception as e:
                print("TrustArc dismiss error:", e)
                
            print("Filling First & Last name...")
            await fn.fill("Aditya")
            await page.wait_for_timeout(300)
            
            ln = await page.query_selector("#textfield-lastName, input[name=\"lastName\"]")
            if ln:
                await ln.fill("Kumar")
                await page.wait_for_timeout(300)
                
            # Click Source dropdown
            src = await page.query_selector("input[name=\"sourceSelection\"], [aria-labelledby*=\"source\"], div[role=\"combobox\"]")
            if src:
                print("Opening Source dropdown...")
                await src.click()
                await page.wait_for_timeout(800)
                opts = await page.query_selector_all("li[role=\"option\"], [role=\"option\"]")
                if opts:
                    print(f"Selecting source: {await opts[0].inner_text()}")
                    await opts[0].click()
                else:
                    await page.keyboard.press("ArrowDown")
                    await page.keyboard.press("Enter")
                await page.wait_for_timeout(500)
                
            # Click Continue on Step 2
            print("Clicking Continue on Step 2...")
            cont2 = await page.query_selector("button:has-text(\"Continue\")")
            if cont2:
                await cont2.click()
                await page.wait_for_timeout(5000)
                
        print("Page URL after Step 2:", page.url)
        await page.screenshot(path=f"{PROOF_DIR}/teradata_step2_progress.png", full_page=True)
        print("Saved teradata_step2_progress.png")

        # Let's see what is on the page now
        inputs = await page.locator("input, select, button, textarea").all()
        print(f"Found {len(inputs)} elements on next step:")
        for el in inputs[:25]:
            try:
                itype = await el.get_attribute("type")
                iid = await el.get_attribute("id")
                iname = await el.get_attribute("name")
                itext = (await el.inner_text()).strip()
                print(f"Element: tag={el}, id={iid}, name={iname}, type={itype}, text={itext}")
            except: pass

        await page.wait_for_timeout(5000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
