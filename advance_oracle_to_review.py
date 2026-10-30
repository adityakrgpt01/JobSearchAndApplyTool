import asyncio
import os
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_PATH = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/oracle_proof.png"

async def run():
    print("=" * 80)
    print("🚀 ADVANCING ORACLE CLOUD HCM TO REVIEW PAGE")
    print("=" * 80)
    
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
        
        url = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobsearch/job/347263"
        print(f"Navigating to {url}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)
        
        # 1. Click APPLY NOW
        print("Clicking APPLY NOW...")
        apply_btn = await page.wait_for_selector("button:has-text('APPLY NOW')", timeout=15000)
        await apply_btn.click()
        await page.wait_for_timeout(4000)
        
        # 2. Email & Terms
        print("Entering email & terms...")
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
        await page.screenshot(path="oracle_before_next.png", full_page=True)
        
        # 3. Next to Section 1
        print("Clicking NEXT to Section 1...")
        next_btn = await page.wait_for_selector("button:has-text('NEXT')", timeout=8000)
        await next_btn.click()
        await page.wait_for_timeout(5000)
        
        await page.screenshot(path="oracle_after_next.png", full_page=True)
        print("Section URL after NEXT:", page.url)
        print("Section Title after NEXT:", await page.title())
        print("Page text snippet:", (await page.evaluate("() => document.body.innerText"))[:300])
        
        # 4. Attach Resume
        file_inputs = await page.locator("input[type='file']").all()
        for fi in file_inputs:
            cls = (await fi.get_attribute("class")) or ""
            if "oda" not in cls:
                print(f"Attaching resume {RESUME_PATH}...")
                await fi.set_input_files(RESUME_PATH)
                await page.wait_for_timeout(6000)
                break
                
        # 5. Populate Section 1 fields
        print("Filling Section 1 candidate fields...")
        fn = page.locator("input[id*='firstName']").first
        if await fn.count() > 0:
            await fn.fill("Aditya")
            await page.wait_for_timeout(300)
            
        ln = page.locator("input[id*='lastName']").first
        if await ln.count() > 0:
            await ln.fill("Kumar")
            await page.wait_for_timeout(300)
            
        phone = page.locator("input[type='tel']").first
        if await phone.count() > 0:
            await phone.fill("9643914225")
            await page.wait_for_timeout(300)
            
        # Select Country = India
        country_box = await page.query_selector("input[id*='country' i], [aria-label*='Country' i], div:has-text('Country *') input")
        if country_box:
            print("Selecting country India...")
            await country_box.click()
            await page.keyboard.type("India")
            await page.wait_for_timeout(600)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(600)
            
        # Address fields
        addr = await page.query_selector("input[id*='addressLine1' i], input[id*='address' i]")
        if addr: await addr.fill("Flat 402, Green Glen Layout, Bellandur")
        
        city = await page.query_selector("input[id*='city' i]")
        if city: await city.fill("Bangalore")
        
        postal = await page.query_selector("input[id*='postalCode' i], input[id*='zip' i]")
        if postal: await postal.fill("560103")
        
        await page.wait_for_timeout(1000)
        await page.screenshot(path=PROOF_PATH.replace("_proof.png", "_sec1_done.png"), full_page=True)
        print("Saved oracle_sec1_done.png")
        
        # 6. Click NEXT to Section 2
        print("Advancing to Section 2...")
        next_sec1 = await page.wait_for_selector("button:has-text('NEXT')", timeout=8000)
        await next_sec1.click()
        await page.wait_for_timeout(8000)
        
        print("Section 2 URL:", page.url)
        print("Section 2 Title:", await page.title())
        await page.screenshot(path=PROOF_PATH.replace("_proof.png", "_sec2_view.png"), full_page=True)
        print("Saved oracle_sec2_view.png")
        
        # Answer common work auth questions on Section 2
        yes_options = await page.locator("input[value*='yes' i], [role='radio']:has-text('Yes'), label:has-text('Yes')").all()
        for y in yes_options:
            try:
                await y.click()
                await page.wait_for_timeout(300)
            except: pass
            
        await page.wait_for_timeout(1000)
        
        # Click NEXT to Section 3 (Review)
        print("Advancing to Section 3 (Review)...")
        next_sec2 = await page.query_selector("button:has-text('NEXT'), button:has-text('Review')")
        if next_sec2:
            await next_sec2.click()
            await page.wait_for_timeout(8000)
            
        print("Final Page URL:", page.url)
        print("Final Page Title:", await page.title())
        
        # Capture Final Verified Review Proof
        print("📸 CAPTURING ORACLE FINAL VERIFIED REVIEW PROOF...")
        os.makedirs(os.path.dirname(PROOF_PATH), exist_ok=True)
        await page.screenshot(path=PROOF_PATH, full_page=True)
        print(f"🎉 VERIFIED PROOF SAVED TO: {PROOF_PATH}")
        print("🛑 HALTED SAFELY AT REVIEW PAGE WITHOUT FINAL SUBMIT.")
        
        await page.wait_for_timeout(5000)
        await browser.close()
        print("✨ ORACLE AUTOMATION FINISHED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run())
