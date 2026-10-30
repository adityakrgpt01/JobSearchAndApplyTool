import sys
import time
import re
from playwright.sync_api import sync_playwright

url = "https://jobs.smartrecruiters.com/ServiceNow/744000155663370"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900}
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = context.new_page()
    
    print(f"Navigating to {url}...")
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(2)
    
    # Click "I'm interested"
    interest_btn = page.query_selector("button:has-text(\"I'm interested\"), a:has-text(\"I'm interested\"), [data-test='apply-button']")
    if interest_btn:
        print("Clicking I'm interested...")
        interest_btn.click()
        time.sleep(3.5)
    print("Current URL:", page.url)
    
    # Upload resume
    file_input = page.query_selector("input[type='file'][accept*='pdf'], input#resume-upload, input[data-test*='resume'], input[type='file']")
    if file_input:
        print("Uploading resume.pdf...")
        file_input.set_input_files("resume.pdf")
        print("Waiting 6s for parsing...")
        time.sleep(6)
    
    # Inspect open experience cards
    cards_info = page.evaluate('''() => {
        const cancelBtns = Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.trim() === 'Cancel');
        const saveBtns = Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.trim() === 'Save');
        const titleInputs = Array.from(document.querySelectorAll('input')).filter(i => (i.placeholder && i.placeholder.toLowerCase().includes('title')) || (i.id && i.id.toLowerCase().includes('title')));
        return {
            cancelCount: cancelBtns.length,
            saveCount: saveBtns.length,
            titleInputs: titleInputs.map(i => ({ id: i.id, placeholder: i.placeholder, val: i.value }))
        };
    }''')
    print("Cards info after resume upload:", cards_info)
    
    # Take screenshot
    page.screenshot(path="screenshots/servicenow_initial_upload.png", full_page=True)
    print("Saved screenshots/servicenow_initial_upload.png")
    browser.close()
