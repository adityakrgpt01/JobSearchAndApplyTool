from playwright.sync_api import sync_playwright
import time
import pprint

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
        
        btns = page.evaluate('''() => {
            const all = Array.from(document.querySelectorAll('*'));
            const cancelLike = all.filter(el => el.innerText && el.innerText.trim().toLowerCase() === 'cancel' && el.children.length === 0);
            return cancelLike.map(el => ({
                tag: el.tagName,
                className: el.className,
                outer: el.outerHTML.substring(0, 200),
                parentTag: el.parentElement ? el.parentElement.tagName : '',
                parentClass: el.parentElement ? el.parentElement.className : '',
                parentOuter: el.parentElement ? el.parentElement.outerHTML.substring(0, 200) : ''
            }));
        }''')
        print('Cancel-like elements:')
        pprint.pprint(btns)
    browser.close()
