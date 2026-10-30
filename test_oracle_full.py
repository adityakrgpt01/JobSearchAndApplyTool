import asyncio
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            slow_mo=60,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(viewport={"width": 1400, "height": 950})
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        url = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/347786"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        
        # Click APPLY NOW
        print("Clicking APPLY NOW...")
        apply_btn = await page.wait_for_selector("button:has-text('APPLY NOW')", timeout=15000)
        await apply_btn.click()
        await page.wait_for_timeout(5000)
        
        print("URL after Apply Now:", page.url)
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_after_apply_click.png", full_page=True)
        
        # Find email input
        print("Looking for email input...")
        email_inp = await page.wait_for_selector("input[type='email'], input[type='text']", timeout=15000)
        if email_inp:
            print("Typing email...")
            await email_inp.click()
            for ch in "adityakumargupta521@gmail.com":
                await email_inp.type(ch, delay=25)
            await page.wait_for_timeout(600)
            
        # Check agree checkbox
        print("Clicking terms checkbox...")
        try:
            await page.click("text='I agree with the terms and conditions'", timeout=4000)
        except:
            await page.evaluate("""() => {
                const chk = document.querySelector('input[type=\"checkbox\"], [role=\"checkbox\"]');
                if (chk) chk.click();
            }""")
        await page.wait_for_timeout(800)
        
        # Click Next
        print("Clicking Next...")
        next_btn = await page.wait_for_selector("button:has-text('NEXT'), button[title='Next']", timeout=8000)
        await next_btn.click()
        await page.wait_for_timeout(8000)
        
        print("URL after Next:", page.url)
        print("Page Title:", await page.title())
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_step2_flow.png", full_page=True)
        print("Saved oracle_step2_flow.png")
        
        # List elements
        inputs = await page.locator("input, button, select, textarea").all()
        print(f"Total elements on step: {len(inputs)}")
        for i, el in enumerate(inputs[:25]):
            try:
                tag = await el.evaluate("e => e.tagName")
                eid = await el.get_attribute("id")
                etype = await el.get_attribute("type")
                text = (await el.inner_text()).strip()
                print(f"[{i}] {tag} id={eid} type={etype} text='{text}'")
            except: pass
            
        await page.wait_for_timeout(4000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
