import time
import random
from playwright.sync_api import sync_playwright

url = "https://careers-holdings-workingatbooking.icims.com/jobs/30010/login"
email = "adityakumargupta521@gmail.com"

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-position=0,0",
            "--ignore-certifcate-errors",
            "--ignore-certifcate-errors-spki-list"
        ]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
        device_scale_factor=2,
        has_touch=False,
        is_mobile=False,
        locale="en-US",
        timezone_id="Asia/Kolkata",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"'
        }
    )
    
    # Advanced stealth init scripts
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.navigator.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    """)
    
    page = context.new_page()
    print(f"Navigating to {url}...")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(3)
    
    frame = page.frame_locator("#icims_content_iframe")
    email_in = frame.locator("#email")
    email_in.wait_for(state="visible", timeout=15000)
    
    # Human-like typing
    email_in.click()
    time.sleep(0.5)
    for ch in email:
        email_in.type(ch, delay=random.randint(40, 90))
    time.sleep(0.8)
    
    consent_sel = frame.locator("#gdpr_consent_type")
    if consent_sel.count() > 0:
        consent_sel.select_option("37002057001")
        time.sleep(0.6)
        
    gdpr_chk = frame.locator("#accept_gdpr")
    if gdpr_chk.count() > 0 and not gdpr_chk.is_checked():
        gdpr_chk.click()
        time.sleep(0.8)
        
    sub_btn = frame.locator("#enterEmailSubmitButton")
    # Move mouse and click like a human
    box = sub_btn.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=10)
        time.sleep(0.3)
        page.mouse.down()
        time.sleep(0.1)
        page.mouse.up()
    else:
        sub_btn.click()
        
    print("Clicked submit button! Waiting 8s...")
    time.sleep(8)
    
    page.screenshot(path="screenshots/booking_stealth_result.png", full_page=True)
    print("Saved screenshots/booking_stealth_result.png!")
    
    for f in page.frames:
        if "hcaptcha" in f.url:
            print(f"hCaptcha frame active: {f.url[:80]}...")
        elif "icims" in f.url:
            print(f"iCIMS frame URL: {f.url}")
            
    browser.close()
