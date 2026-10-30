import asyncio
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(viewport={"width": 1400, "height": 950})
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        url = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/347786/apply/email"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        
        # Click email input
        print("Finding email input...")
        email_inp = await page.wait_for_selector("input[type='email'], input[type='text'], input#email", timeout=15000)
        if email_inp:
            await email_inp.click()
            for ch in "adityakumargupta521@gmail.com":
                await email_inp.type(ch, delay=25)
            await page.wait_for_timeout(600)
            
        # Check checkbox by clicking text or force click
        print("Checking agree checkbox...")
        try:
            await page.click("text='I agree with the terms and conditions'", timeout=3000)
        except:
            await page.evaluate("""() => {
                const chk = document.querySelector('input[type=\"checkbox\"], [role=\"checkbox\"]');
                if (chk) chk.click();
            }""")
        await page.wait_for_timeout(1000)
        
        # Click Next
        print("Clicking Next button...")
        next_btn = await page.wait_for_selector("button:has-text('Next'), button[title='Next'], button.apply-flow-pagination__button", timeout=5000)
        if next_btn:
            print("Next button found. Clicking...")
            await next_btn.click()
            await page.wait_for_timeout(8000)
            
        print("URL after next:", page.url)
        print("Page Title:", await page.title())
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_headful_test.png", full_page=True)
        print("Saved oracle_headful_test.png")
        
        # Check elements
        inputs = await page.locator("input, button, select, textarea").all()
        print(f"Found {len(inputs)} elements on next step:")
        for i, el in enumerate(inputs[:25]):
            try:
                tag = await el.evaluate("e => e.tagName")
                eid = await el.get_attribute("id")
                etype = await el.get_attribute("type")
                text = (await el.inner_text()).strip()
                print(f"[{i}] {tag} id={eid} type={etype} text='{text}'")
            except: pass
            
        await page.wait_for_timeout(3000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
