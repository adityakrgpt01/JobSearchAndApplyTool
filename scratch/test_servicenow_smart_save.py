import time
import re
from playwright.sync_api import sync_playwright

url = "https://jobs.smartrecruiters.com/ServiceNow/744000155663370"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900}
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = context.new_page()
    
    print("Navigating to ServiceNow job...")
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(2)
    
    btn = page.query_selector("button:has-text(\"I'm interested\"), a:has-text(\"I'm interested\")")
    if btn:
        btn.click()
        time.sleep(3.5)
        
    print("Uploading resume.pdf...")
    file_input = page.query_selector("input[type='file']")
    if file_input:
        file_input.set_input_files("resume.pdf")
        print("Uploaded resume.pdf. Waiting 6s for parser...")
        time.sleep(6)

    # Personal info
    fn_input = page.query_selector("input#first-name-input")
    if fn_input and not fn_input.input_value():
        fn_input.fill("Aditya")
        
    ln_input = page.query_selector("input#last-name-input")
    if ln_input and not ln_input.input_value():
        ln_input.fill("Kumar")
        
    email_in = page.query_selector("input#email-input")
    if email_in:
        email_in.fill("adityakumargupta521@gmail.com")
        
    confirm_email_in = page.query_selector("input#confirm-email-input")
    if confirm_email_in:
        confirm_email_in.fill("adityakumargupta521@gmail.com")
        
    # City
    city_loc = page.locator("input[id^='spl-form-element'][type='text']").first
    if city_loc.count() > 0:
        current_val = city_loc.input_value()
        if not current_val:
            city_loc.click()
            city_loc.fill("Bengaluru")
            time.sleep(1.2)
            suggestions = page.locator("li, [role='option'], .c-spl-autocomplete__list-item").all()
            if suggestions:
                suggestions[0].click()
            else:
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")

    # Phone
    try:
        flag_btn = page.query_selector(".c-spl-phone-input__country-code, button[aria-label*='country' i], .c-spl-select__button")
        if flag_btn:
            flag_btn.click()
            time.sleep(0.6)
            in_opt = page.query_selector("li:has-text('India'), [role='option']:has-text('India'), li[data-country-code='in']")
            if in_opt:
                in_opt.click()
                time.sleep(0.4)
            else:
                page.keyboard.press("Escape")
    except Exception:
        pass

    phone_in = page.query_selector("input[type='tel']")
    if phone_in:
        phone_in.fill("9643914225")

    # LinkedIn
    li_in = page.query_selector("input#linkedin-input")
    if li_in and not li_in.input_value():
        li_in.fill("https://www.linkedin.com/in/aditya-kumar-a518571b5/")

    print("Fixing experience draft cards...")
    # Execute JS smart save
    fixed_count = page.evaluate('''() => {
        const saveBtns = Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.trim() === 'Save');
        let count = 0;
        for (const saveBtn of saveBtns) {
            let card = saveBtn.parentElement;
            while (card && card.tagName !== 'BODY') {
                if (card.querySelectorAll('input').length >= 2) break;
                card = card.parentElement;
            }
            if (!card) continue;
            
            const inputs = Array.from(card.querySelectorAll('input'));
            const descArea = card.querySelector('textarea');
            const descText = descArea ? descArea.value.toLowerCase() : '';
            
            // First text input in card is typically Title
            const titleInput = inputs.find(i => (i.placeholder && i.placeholder.toLowerCase().includes('title')) || (i.id && i.id.toLowerCase().includes('title'))) || inputs[0];
            const companyInput = inputs.find(i => (i.placeholder && i.placeholder.toLowerCase().includes('company')) || (i.id && i.id.toLowerCase().includes('company'))) || inputs[1];

            let targetTitle = 'SDE II';
            let targetCompany = '';
            if (descText.includes('73 strings') || descText.includes('approval workflow') || descText.includes('uam')) {
                targetTitle = 'SDE II';
                targetCompany = '73 Strings';
            } else if (descText.includes('sumo logic') || descText.includes('metrics ingestion') || descText.includes('kafka')) {
                targetTitle = 'SDE II';
                targetCompany = 'Sumo Logic';
            } else if (descText.includes('open financial') || descText.includes('banking partners')) {
                targetTitle = 'SDE';
                targetCompany = 'Open Financial Technologies';
            } else if (descText.includes('exotel') || descText.includes('pilot number') || descText.includes('asr')) {
                targetTitle = 'SDE';
                targetCompany = 'Exotel';
            } else {
                targetTitle = 'Software Engineer';
            }

            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;

            if (titleInput && (!titleInput.value || titleInput.value.trim().length === 0)) {
                setter.call(titleInput, targetTitle);
                titleInput.dispatchEvent(new Event('input', { bubbles: true }));
                titleInput.dispatchEvent(new Event('change', { bubbles: true }));
            }

            if (companyInput && targetCompany) {
                setter.call(companyInput, targetCompany);
                companyInput.dispatchEvent(new Event('input', { bubbles: true }));
                companyInput.dispatchEvent(new Event('change', { bubbles: true }));
            }

            saveBtn.click();
            count++;
        }
        return count;
    }''')
    print(f"Fixed and saved {fixed_count} cards. Waiting 2s...")
    time.sleep(2)

    # Any remaining open cards with cancel button? Cancel them cleanly
    cancelled_count = page.evaluate('''() => {
        const cancelBtns = Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.trim() === 'Cancel');
        let c = 0;
        for (const b of cancelBtns) {
            b.click();
            c++;
        }
        return c;
    }''')
    print(f"Cancelled {cancelled_count} remaining draft cards.")
    time.sleep(2)

    # Save screenshot
    screenshot_path = "screenshots/servicenow_smart_save_proof.png"
    page.screenshot(path=screenshot_path, full_page=True)
    print(f"Saved {screenshot_path}!")
    browser.close()
