from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    api_calls = []
    page.on("response", lambda res: api_calls.append(res.url) if "job" in res.url.lower() or "api" in res.url.lower() or "graphql" in res.url.lower() else None)
    
    print("Navigating to Wayfair jobs...")
    page.goto("https://www.wayfair.com/careers/jobs?teamIds=1", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    print("Page title:", page.title())
    print("API calls captured:", len(api_calls))
    for call in api_calls[:15]:
        print(" Call:", call[:100])
        
    # Check for job listings in DOM
    job_cards = page.query_selector_all("a[href*='/careers/jobs/'], [data-test*='job'], .job-title, [class*='jobCard']")
    print(f"Job elements found: {len(job_cards)}")
    for jc in job_cards[:10]:
        print(" Job:", jc.inner_text().strip().replace('\n', ' -- '), "| href:", jc.get_attribute("href"))
        
    page.screenshot(path="scratch/wayfair_rendered.png")
    print("Saved scratch/wayfair_rendered.png")
    browser.close()
