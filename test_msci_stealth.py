import asyncio
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(viewport={"width": 1400, "height": 950})
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        url = "https://globalcareers-msci.icims.com/jobs/5973/job?mode=apply&apply=yes_iframe=1"
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        fl = page.frame_locator("iframe[name='icims_content_iframe']")
        await fl.locator("#email").fill("adityakumargupta521@gmail.com")
        await page.wait_for_timeout(500)
        
        chk = fl.locator("input[type='checkbox']")
        if await chk.count() > 0:
            await chk.check()
            await page.wait_for_timeout(500)
            
        await fl.locator("#enterEmailSubmitButton, input#enter-profile, button#enter-profile").click()
        await page.wait_for_timeout(4000)
        
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/msci_stealth_test.png", full_page=True)
        print("Screenshot saved to msci_stealth_test.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
