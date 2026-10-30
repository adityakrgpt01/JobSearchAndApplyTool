import asyncio
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_DIR = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1400, "height": 950}
        )
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        url = "https://careers.teradata.com/jobs/220870/senior-ai-engineer/apply"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        # Acknowledge banner
        try:
            ack = await page.query_selector("button:has-text('Acknowledge')")
            if ack: await ack.click()
        except: pass
        await page.wait_for_timeout(1000)
        
        # Fill email
        print("Filling email...")
        email_el = await page.wait_for_selector("#textfield-email")
        await email_el.click()
        for ch in "adityakumargupta521@gmail.com":
            await page.keyboard.type(ch, delay=25)
        await page.wait_for_timeout(500)
        
        # Check agreement
        await page.check("input[name='acceptPrivacyPolicy']")
        await page.wait_for_timeout(500)
        
        # Mouse movement
        await page.mouse.move(200, 300)
        await page.wait_for_timeout(200)
        await page.mouse.move(350, 420)
        await page.wait_for_timeout(200)
        
        # Click Continue on Step 1
        print("Submitting Step 1...")
        await page.click("button.submitButton-12, button:has-text('Continue')")
        await page.wait_for_timeout(4000)
        
        # Step 2: Profile Creation
        print("Waiting for Profile Creation...")
        fn = await page.wait_for_selector("#textfield-firstName", timeout=15000)
        print("Found First Name field!")

        # Remove TrustArc overlay without triggering page reload
        await page.evaluate("""() => {
            const el = document.getElementById('truste-consent-track') || document.querySelector('[class*="truste"]');
            if (el) el.remove();
        }""")
        await page.wait_for_timeout(500)

        print("Typing First Name & Last Name...")
        await fn.click()
        await fn.fill("Aditya")
        await page.wait_for_timeout(400)
        
        ln = await page.wait_for_selector("#textfield-lastName")
        await ln.click()
        await ln.fill("Kumar")
        await page.wait_for_timeout(400)
        
        # Select source
        print("Selecting Source...")
        src_input = await page.query_selector("input[name='sourceSelection']")
        if src_input:
            await src_input.click()
            await page.wait_for_timeout(800)
            options = await page.query_selector_all("li[role='option'], div[role='option']")
            print(f"Found {len(options)} source options:")
            for o in options[:5]:
                print(" - Option:", await o.inner_text())
            if options:
                await options[0].click()
            else:
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(600)
            
        await page.screenshot(path=f"{PROOF_DIR}/teradata_step2_filled.png", full_page=True)
        print("Saved teradata_step2_filled.png")
        
        # Click Continue on Step 2
        print("Clicking Continue on Step 2...")
        cont_btn = await page.query_selector("button:has-text('Continue')")
        if cont_btn:
            await cont_btn.click()
            await page.wait_for_timeout(6000)
            
        print("URL after Step 2:", page.url)
        print("Page Title:", await page.title())
        await page.screenshot(path=f"{PROOF_DIR}/teradata_step3_view.png", full_page=True)
        print("Saved teradata_step3_view.png")
        
        # Inspect inputs on Step 3
        elements = await page.locator("input, select, button, textarea, [role='button']").all()
        print(f"Total elements on Step 3: {len(elements)}")
        for i, el in enumerate(elements[:30]):
            try:
                tag = await el.evaluate("e => e.tagName")
                eid = await el.get_attribute("id")
                ename = await el.get_attribute("name")
                etype = await el.get_attribute("type")
                text = (await el.inner_text()).strip()
                print(f"[{i}] {tag} id={eid} name={ename} type={etype} text='{text}'")
            except: pass

        await page.wait_for_timeout(4000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
