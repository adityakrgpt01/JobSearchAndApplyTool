from playwright.sync_api import sync_playwright
import time
import pprint

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
    print("Navigating to ServiceNow job...")
    page = context.new_page()
    page.goto("https://jobs.smartrecruiters.com/ServiceNow/744000155663370", wait_until="domcontentloaded")
    time.sleep(2)
    btn = page.query_selector("button:has-text(\"I'm interested\"), a:has-text(\"I'm interested\")")
    print("Found btn:", btn)
    if btn:
        btn.click()
        time.sleep(3)
        print("URL after click:", page.url)
        file_input = page.wait_for_selector("input[type='file']", timeout=10000)
        print("Found file_input:", file_input)
        if file_input:
            file_input.set_input_files("resume.pdf")
            print("Uploaded resume.pdf, waiting 8s for parser...")
            time.sleep(8)
        
        info = page.evaluate('''() => {
            // Find all containers containing a 'Save' button and a 'Cancel' button
            const saveButtons = Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.trim() === 'Save');
            return saveButtons.map((sb, i) => {
                // Find enclosing card
                let container = sb.parentElement;
                while (container && container.tagName !== 'BODY') {
                    if (container.querySelectorAll('input').length >= 2) break;
                    container = container.parentElement;
                }
                const inputs = Array.from(container.querySelectorAll('input, textarea')).map(inp => ({
                    id: inp.id,
                    name: inp.name,
                    placeholder: inp.placeholder,
                    value: inp.value,
                    outerHTML: inp.outerHTML.substring(0, 150)
                }));
                const buttons = Array.from(container.querySelectorAll('button')).map(b => b.innerText.trim());
                return { cardIndex: i, inputs, buttons };
            });
        }''')
        pprint.pprint(info)
    browser.close()
