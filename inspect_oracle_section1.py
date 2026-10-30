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
        await page.wait_for_timeout(3000)
        
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
        await page.wait_for_timeout(600)
        
        next_btn = await page.wait_for_selector("button:has-text('NEXT')", timeout=8000)
        await next_btn.click()
        
        print("Waiting for Section 1 to load...")
        await page.wait_for_url("**/apply/section/**", timeout=25000)
        await page.wait_for_timeout(4000)
        
        print("Section 1 loaded! Current URL:", page.url)
        
        # Look for file inputs
        file_inputs = await page.locator("input[type='file']").all()
        print(f"Total file inputs found on Section 1: {len(file_inputs)}")
        for i, fi in enumerate(file_inputs):
            cls = await fi.get_attribute("class") or ""
            iid = await fi.get_attribute("id") or ""
            print(f"File input [{i}]: id='{iid}', class='{cls}'")
            if "oda" not in cls:
                print(f"Uploading resume to file input [{i}]...")
                await fi.set_input_files("/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf")
                await page.wait_for_timeout(8000)
                break
                
        # Fill Personal Info fields
        print("Filling candidate personal info...")
        fn = await page.wait_for_selector("input[id*='firstName'], input[name*='firstName']", timeout=8000)
        await fn.fill("Aditya")
        await page.wait_for_timeout(300)
        
        ln = await page.wait_for_selector("input[id*='lastName'], input[name*='lastName']", timeout=8000)
        await ln.fill("Kumar")
        await page.wait_for_timeout(300)
        
        phone = await page.query_selector("input[type='tel']")
        if phone:
            await phone.fill("9643914225")
            await page.wait_for_timeout(300)
            
        # Address fields
        addr = await page.query_selector("input[id*='address'], input[name*='address']")
        if addr:
            await addr.fill("Flat 402, Green Glen Layout, Bellandur")
            
        city = await page.query_selector("input[id*='city'], input[name*='city']")
        if city:
            await city.fill("Bangalore")
            
        postal = await page.query_selector("input[id*='postal'], input[id*='zip']")
        if postal:
            await postal.fill("560103")
            
        # Country
        country = await page.query_selector("input[id*='country'], select[id*='country'], [aria-label*='Country' i]")
        if country:
            try:
                await country.click()
                await page.keyboard.type("India")
                await page.wait_for_timeout(400)
                await page.keyboard.press("Enter")
            except: pass
            
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_section1_done.png", full_page=True)
        print("Saved oracle_section1_done.png")
        
        # Click Next to advance
        print("Clicking NEXT to Section 2...")
        next_sec = await page.query_selector("button:has-text('NEXT')")
        if next_sec:
            await next_sec.click()
            await page.wait_for_timeout(8000)
            
        print("URL after Section 1 Next:", page.url)
        print("Title:", await page.title())
        await page.screenshot(path="/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_section2_view.png", full_page=True)
        print("Saved oracle_section2_view.png")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
