from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-setuid-sandbox']
    )
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        viewport={'width': 1280, 'height': 900},
        extra_http_headers={
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"macOS"'
        }
    )
    context.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined});')
    page = context.new_page()
    page.goto('https://jobs.smartrecruiters.com/ServiceNow/744000155663370', wait_until='domcontentloaded')
    time.sleep(2)
    btn = page.query_selector("button:has-text(\"I'm interested\"), a:has-text(\"I'm interested\")")
    if btn:
        btn.click()
        time.sleep(3.5)
        file_input = page.query_selector("input[type='file']")
        file_input.set_input_files('resume.pdf')
        time.sleep(6)
        
        # Click all Cancel buttons via JS click on spl-button or its span
        c_count = page.evaluate('''() => {
            const btns = Array.from(document.querySelectorAll("spl-button[aria-label*='Cancel' i], spl-button:has(span)"));
            const cancelBtns = btns.filter(b => b.innerText && b.innerText.trim().toLowerCase() === 'cancel');
            for (const b of cancelBtns) {
                b.click();
            }
            return cancelBtns.length;
        }''')
        print(f"Clicked {c_count} cancel buttons! Waiting 2s...")
        time.sleep(2)
        
        page.screenshot(path="screenshots/servicenow_after_cancel.png", full_page=True)
        print("Saved screenshots/servicenow_after_cancel.png")
    browser.close()
