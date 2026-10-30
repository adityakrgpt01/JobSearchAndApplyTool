import time
from playwright.sync_api import sync_playwright

url = "https://careers-holdings-workingatbooking.icims.com/jobs/30010/login"
email = "adityakumargupta521@gmail.com"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox"]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"'
        }
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = context.new_page()
    
    print(f"Navigating to {url}...")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(3)
    
    frame = page.frame_locator("#icims_content_iframe")
    email_in = frame.locator("#email")
    email_in.wait_for(state="visible", timeout=15000)
    print("Found email input. Filling email...")
    email_in.fill(email)
    
    consent_sel = frame.locator("#gdpr_consent_type")
    if consent_sel.count() > 0:
        print("Selecting GDPR consent option...")
        consent_sel.select_option("37002057001")
        
    gdpr_chk = frame.locator("#accept_gdpr")
    if gdpr_chk.count() > 0 and not gdpr_chk.is_checked():
        print("Checking GDPR checkbox...")
        gdpr_chk.check()
        
    time.sleep(1)
    sub_btn = frame.locator("#enterEmailSubmitButton")
    print("Clicking #enterEmailSubmitButton...")
    sub_btn.click()
    
    print("Waiting 6s for next page...")
    time.sleep(6)
    
    page.screenshot(path="screenshots/booking_after_email_submit.png", full_page=True)
    print("Saved screenshots/booking_after_email_submit.png!")
    
    # Check iframe contents
    for f in page.frames:
        print(f"Frame URL: {f.url}")
        inputs = f.evaluate('''() => {
            return Array.from(document.querySelectorAll('input, select, textarea, button')).map(el => ({
                tag: el.tagName,
                id: el.id,
                name: el.name,
                type: el.type,
                value: el.value,
                text: el.innerText ? el.innerText.trim() : ''
            })).filter(x => x.id || x.name || x.text);
        }''')
        if inputs:
            print(f"Frame elements ({len(inputs)}):")
            for inp in inputs[:25]:
                print("  ", inp)
                
    browser.close()
