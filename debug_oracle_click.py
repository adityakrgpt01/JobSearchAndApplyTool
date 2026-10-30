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
        
        url = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/347786"
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        
        apply_btn = await page.wait_for_selector("button:has-text('APPLY NOW')", timeout=15000)
        await apply_btn.click()
        await page.wait_for_timeout(4000)
        
        email_inp = await page.wait_for_selector("input[type='email'], input[type='text']", timeout=15000)
        await email_inp.click()
        for ch in "adityakumargupta521@gmail.com":
            await email_inp.type(ch, delay=25)
        await page.wait_for_timeout(500)
        
        try:
            await page.click("text='I agree with the terms and conditions'", timeout=3000)
        except:
            await page.evaluate("""() => {
                const chk = document.querySelector('input[type=\"checkbox\"], [role=\"checkbox\"]');
                if (chk) chk.click();
            }""")
        await page.wait_for_timeout(800)
        
        # Take screenshot before clicking next
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_debug_before_next.png", full_page=True)
        
        next_btn = await page.wait_for_selector("button:has-text('NEXT'), button[title='Next']", timeout=8000)
        print("Clicking next...")
        await next_btn.click()
        await page.wait_for_timeout(4000)
        
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_debug_after_next.png", full_page=True)
        print("URL after next:", page.url)
        print("Title after next:", await page.title())
        
        text = await page.evaluate("() => document.body.innerText")
        print("Visible text snippet:")
        print(text[:400])
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
