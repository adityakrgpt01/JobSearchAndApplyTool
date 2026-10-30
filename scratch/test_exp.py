from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"'
        }
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = context.new_page()
    page.goto("https://jobs.smartrecruiters.com/ServiceNow/744000157740014", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    page.click("button:has-text(\"I'm interested\"), a:has-text(\"I'm interested\")")
    page.wait_for_timeout(3000)
    page.set_input_files("input[type='file']", "resume.pdf")
    page.wait_for_timeout(3500)
    
    # Check Cancel buttons
    cancels = page.locator("button:has-text('Cancel')").all()
    print("Found cancels:", len(cancels))
    for idx, c in enumerate(cancels):
        print(f"Clicking cancel {idx}...")
        c.scroll_into_view_if_needed()
        c.click(force=True)
        page.wait_for_timeout(500)
        
    page.screenshot(path="scratch/servicenow_cancels_forced.png", full_page=True)
    print("Saved scratch/servicenow_cancels_forced.png")
    browser.close()
