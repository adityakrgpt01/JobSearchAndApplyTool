import asyncio
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

PROOF_DIR = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=50)
        context = await browser.new_context(viewport={"width": 1400, "height": 950})
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        url = "https://careers.teradata.com/jobs/220870/senior-ai-engineer/apply"
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        try:
            ack = await page.query_selector("button:has-text('Acknowledge')")
            if ack: await ack.click()
        except: pass
        await page.wait_for_timeout(1000)
        
        email_el = await page.wait_for_selector("#textfield-email")
        await email_el.click()
        for ch in "adityakumargupta521@gmail.com":
            await page.keyboard.type(ch, delay=25)
        await page.wait_for_timeout(500)
        
        await page.check("input[name='acceptPrivacyPolicy']")
        await page.wait_for_timeout(500)
        
        await page.click("button.submitButton-12, button:has-text('Continue')")
        await page.wait_for_timeout(4000)
        
        print("URL after Step 1:", page.url)
        print("Page Title:", await page.title())
        await page.screenshot(path=f"{PROOF_DIR}/teradata_after_step1_check.png", full_page=True)
        print("Saved teradata_after_step1_check.png")
        
        inputs = await page.locator("input, select, button, textarea").all()
        for i, el in enumerate(inputs):
            try:
                tag = await el.evaluate("e => e.tagName")
                eid = await el.get_attribute("id")
                ename = await el.get_attribute("name")
                etype = await el.get_attribute("type")
                text = (await el.inner_text()).strip()
                val = await el.input_value() if tag in ['INPUT', 'TEXTAREA'] else ""
                print(f"[{i}] {tag} id={eid} name={ename} type={etype} val='{val}' text='{text}'")
            except: pass
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
