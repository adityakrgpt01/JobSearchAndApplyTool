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
    
    city_inp = page.locator("input[id^='spl-form-element'][type='text']").first
    print("Found city input:", city_inp.get_attribute("id"))
    city_inp.click()
    city_inp.fill("Bengaluru")
    page.wait_for_timeout(1500)
    
    # Check what items appear
    items = page.locator("li, [role='option'], .c-spl-autocomplete__list-item").all()
    print(f"Items found: {len(items)}")
    for it in items[:5]:
        print("Item:", it.text_content().strip())
    if items:
        items[0].click()
        print("Clicked first suggestion!")
    page.wait_for_timeout(1000)
    print("City value now:", city_inp.input_value())
    
    # Click any Cancel buttons on experience draft cards
    for b in page.locator("button:has-text('Cancel')").all():
        b.click()
        page.wait_for_timeout(300)
        
    page.screenshot(path="scratch/servicenow_city_fixed.png", full_page=True)
    print("Saved scratch/servicenow_city_fixed.png")
    browser.close()
