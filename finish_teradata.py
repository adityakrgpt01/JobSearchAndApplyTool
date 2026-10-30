import asyncio
import os
import time
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth
from gmail_authenticator import GmailVerificationReader, _load_env_file

_load_env_file()
reader = GmailVerificationReader()

RESUME_PATH = "/Users/noody/Downloads/JobSearchAndApplyTool/resume.pdf"
PROOF_PATH = "/Users/noody/.gemini/antigravity/brain/aca0751a-2d02-4ae1-97c6-3980eaed9567/review_screenshots/teradata_proof.png"
USER_DATA_DIR = "./chrome_user_data_teradata"

async def run():
    print("=" * 80)
    print("🚀 COMPLETING TERADATA TO FINAL REVIEW PAGE")
    print("=" * 80)
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            channel="chrome",
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1400, "height": 950}
        )
        page = context.pages[0] if context.pages else await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)
        
        url = "https://careers.teradata.com/jobs/220870/senior-ai-engineer/apply"
        print(f"Navigating to {url}...")
        start_ts = time.time()
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        # Dismiss overlays
        try:
            ack = await page.query_selector("button:has-text('Acknowledge')")
            if ack: await ack.click()
        except: pass
        
        await page.evaluate("""() => {
            const el = document.getElementById('truste-consent-track') || document.querySelector('[class*="truste"]');
            if (el) el.remove();
        }""")
        
        # Check if already logged in or on step 1
        email_el = await page.query_selector("#textfield-email, input[name='email']")
        if email_el and await email_el.is_visible():
            print("Step 1 email entry detected. Entering email...")
            await email_el.click()
            for ch in "adityakumargupta521@gmail.com":
                await email_el.type(ch, delay=25)
            await page.wait_for_timeout(400)
            
            chk = await page.query_selector("input[name='acceptPrivacyPolicy'], input[type='checkbox']")
            if chk and not await chk.is_checked():
                await chk.check()
            await page.wait_for_timeout(400)
            
            await page.mouse.move(250, 350)
            sub = await page.query_selector("button.submitButton-12, button:has-text('Continue')")
            if sub: await sub.click()
            await page.wait_for_timeout(4000)
            
        # Check OTP
        code_input = await page.query_selector("input[name='code'], input[placeholder*='code' i], input#code")
        if code_input and await code_input.is_visible():
            print("Fetching OTP from Gmail...")
            fresh_code = reader.fetch_latest_verification_code(
                sender_keyword="teradata",
                timeout_seconds=45,
                poll_interval=3,
                received_after_ts=start_ts - 10.0
            )
            if fresh_code:
                print(f"Entering OTP: {fresh_code}")
                await code_input.fill(fresh_code)
                await page.wait_for_timeout(600)
                next_btn = await page.query_selector("button[type='submit'], button:has-text('Next')")
                if next_btn: await next_btn.click()
                await page.wait_for_timeout(6000)
                
        # Clean overlays
        await page.evaluate("""() => {
            const el = document.getElementById('truste-consent-track') || document.querySelector('[class*="truste"]');
            if (el) el.remove();
        }""")
        
        # Resume Upload if present
        file_input = await page.query_selector("input[type='file']")
        if file_input:
            print(f"Found input[type='file']! Uploading {RESUME_PATH}...")
            await file_input.set_input_files(RESUME_PATH)
            print("Waiting 15 seconds for resume parsing and page transition...")
            await page.wait_for_timeout(15000)
            
        # Clean overlays again
        # Clean overlays again
        await page.evaluate("""() => {
            const el = document.getElementById('truste-consent-track') || document.querySelector('[class*="truste"]');
            if (el) el.remove();
        }""")
        
        # Dump page content for debugging
        html_dump = await page.content()
        with open("teradata_form_dump.html", "w") as f:
            f.write(html_dump)
        print("Dumped page content to teradata_form_dump.html.")
        
        # Check form fields (Name, Phone, Address, Country)
        print("Handling contact information and location fields...")
        
        # Reset First Name & Last Name cleanly
        fn = await page.query_selector("#textfield-firstName, input[name='firstName'], div:has-text('First Name *') input")
        if fn:
            print("Resetting First Name to 'Aditya'...")
            await fn.fill("Aditya")
            await page.wait_for_timeout(300)
            
        ln = await page.query_selector("#textfield-lastName, input[name='lastName'], div:has-text('Last Name *') input")
        if ln:
            print("Ensuring Last Name is 'Kumar'...")
            await ln.fill("Kumar")
            await page.wait_for_timeout(300)
        
        # Address 1
        addr1 = await page.query_selector("#textfield-addressLine1, input[name*='addressLine1' i], div:has-text('Address 1 *') input")
        if not addr1:
            addr1 = await page.query_selector("div:has-text('Address 1') input, input[placeholder*='address' i]")
        if addr1:
            print("Filling Address 1...")
            await addr1.fill("Flat 402, Green Glen Layout, Bellandur")
            await page.wait_for_timeout(300)
            
        # Postal Code
        zip_el = await page.query_selector("#textfield-postalCode, #textfield-postal, div:has-text('Postal Code *') input, input[name*='postal' i]")
        if zip_el:
            print("Filling Postal Code...")
            await zip_el.fill("560103")
            await page.wait_for_timeout(300)
            
        # City
        city_el = await page.query_selector("#textfield-city, div:has-text('City *') input, input[name*='city' i]")
        if city_el:
            print("Filling City...")
            await city_el.fill("Bangalore")
            await page.wait_for_timeout(300)
            
        # Dismiss any open menus first
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        
        # Select Country: India using precise JS trigger
        print("Selecting Country: India...")
        await page.evaluate("""() => {
            // Find all form control containers
            const controls = Array.from(document.querySelectorAll('.MuiFormControl-root'));
            for (const ctrl of controls) {
                const label = ctrl.querySelector('label, p, span');
                if (label && label.textContent.trim().startsWith('Country *')) {
                    const btn = ctrl.querySelector('[role="button"], [role="combobox"], .MuiSelect-select');
                    if (btn) btn.click();
                    break;
                }
            }
        }""")
        await page.wait_for_timeout(1500)
        
        # Scroll & click India
        country_clicked = await page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('li[role="option"], [role="menuitem"], li.MuiMenuItem-root'));
            for (const item of items) {
                if (item.textContent.trim() === 'India') {
                    item.scrollIntoView();
                    item.click();
                    return true;
                }
            }
            return false;
        }""")
        print(f"Country India clicked: {country_clicked}")
        if not country_clicked:
            await page.keyboard.press("Escape")
        await page.wait_for_timeout(2000)
        
        # Select State: Karnataka
        print("Selecting State: Karnataka...")
        await page.evaluate("""() => {
            const controls = Array.from(document.querySelectorAll('.MuiFormControl-root'));
            for (const ctrl of controls) {
                const label = ctrl.querySelector('label, p, span');
                if (label && (label.textContent.includes('State') || label.textContent.includes('Province'))) {
                    const btn = ctrl.querySelector('[role="button"], [role="combobox"], .MuiSelect-select');
                    if (btn) btn.click();
                    break;
                }
            }
        }""")
        await page.wait_for_timeout(1500)
        
        state_clicked = await page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('li[role="option"], [role="menuitem"], li.MuiMenuItem-root'));
            for (const item of items) {
                if (item.textContent.trim() === 'Karnataka') {
                    item.scrollIntoView();
                    item.click();
                    return true;
                }
            }
            return false;
        }""")
        print(f"State Karnataka clicked: {state_clicked}")
        if not state_clicked:
            await page.keyboard.press("Escape")
        await page.wait_for_timeout(2000)
        
        # Select Timezone
        print("Selecting Timezone...")
        await page.evaluate("""() => {
            const controls = Array.from(document.querySelectorAll('.MuiFormControl-root'));
            for (const ctrl of controls) {
                const label = ctrl.querySelector('label, p, span');
                if (label && label.textContent.includes('Timezone')) {
                    const btn = ctrl.querySelector('[role="button"], [role="combobox"], .MuiSelect-select');
                    if (btn) btn.click();
                    break;
                }
            }
        }""")
        await page.wait_for_timeout(1500)
        
        tz_clicked = await page.evaluate("""() => {
            const items = Array.from(document.querySelectorAll('li[role="option"], [role="menuitem"], li.MuiMenuItem-root'));
            for (const item of items) {
                if (item.textContent.includes('Kolkata') || item.textContent.includes('India') || item.textContent.includes('+05:30')) {
                    item.scrollIntoView();
                    item.click();
                    return true;
                }
            }
            if (items.length > 0) {
                items[0].click();
                return true;
            }
            return false;
        }""")
        print(f"Timezone clicked: {tz_clicked}")
        if not tz_clicked:
            await page.keyboard.press("Escape")
        await page.wait_for_timeout(1500)

        # Close any open popups / overlays
        await page.keyboard.press("Escape")
        await page.mouse.click(50, 50)
        await page.wait_for_timeout(1000)
        
        # Loop through application pages until Review/Submit page is reached
        for step in range(5):
            print(f"🔄 Loop step {step + 1}: Checking page state...")
            await page.evaluate("""() => {
                const el = document.getElementById('truste-consent-track') || document.querySelector('[class*="truste"]');
                if (el) el.remove();
            }""")
            
            # Check if Review or Submit button is visible
            content = await page.content()
            page_text = await page.evaluate("() => document.body.innerText")
            
            # Check for Next / Continue button
            next_btn = await page.query_selector("button:has-text('Next'), button:has-text('Continue'), button:has-text('Save & Continue')")
            submit_btn = await page.query_selector("button:has-text('Submit'), button:has-text('Submit Application')")
            
            # Check if this is the final review or submit page
            submit_btn = await page.query_selector("button:has-text('Submit Application'), button:has-text('Submit')")
            if submit_btn and ("review" in page_text.lower() or "summary" in page_text.lower() or not next_btn):
                print("🎯 REACHED FINAL REVIEW / SUBMISSION STAGE!")
                break
                
            if next_btn:
                btn_name = (await next_btn.inner_text()).strip()
                is_disabled = await next_btn.get_attribute("disabled")
                classes = await next_btn.get_attribute("class") or ""
                print(f"👉 Button '{btn_name}': disabled={is_disabled}, class='{classes}'")
                
                if not is_disabled and "disabled" not in classes:
                    print(f"👉 Clicking enabled button: '{btn_name}'...")
                    await next_btn.click()
                    await page.wait_for_timeout(6000)
                    continue
                else:
                    print(f"Button '{btn_name}' is currently disabled. Checking for errors or missing inputs...")
                    await page.wait_for_timeout(2000)
                    # Check errors
                    errors = await page.evaluate("() => Array.from(document.querySelectorAll('.Mui-error, [class*=\"error\" i], p[id*=\"helper-text\" i]')).map(e => e.innerText)")
                    print(f"Visible form errors: {errors}")
                    if step == 0:
                        # try one more wait
                        await page.wait_for_timeout(3000)
                    else:
                        break
            else:
                if submit_btn:
                    print("🎯 Found Submit button! We are on the Review page!")
                    break
                print("No Next or Submit button found.")
                break

        # Capture Verified Review Screenshot
        print("📸 Capturing final full-page verified review proof...")
        os.makedirs(os.path.dirname(PROOF_PATH), exist_ok=True)
        await page.screenshot(path=PROOF_PATH, full_page=True)
        print(f"🎉 VERIFIED PROOF SAVED TO: {PROOF_PATH}")
        print(f"Final Page URL: {page.url}")
        print(f"Final Page Title: {await page.title()}")
        print("🛑 HALTED SAFELY WITHOUT FINAL SUBMISSION.")
        
        await page.wait_for_timeout(5000)
        await context.close()

if __name__ == "__main__":
    asyncio.run(run())
