import asyncio
from playwright.async_api import async_playwright
import json

async def scrape_apple(page):
    print("Scraping Apple...")
    await page.goto("https://jobs.apple.com/en-in/search?search=software&sort=relevance", wait_until="networkidle")
    await page.wait_for_timeout(3000)
    
    jobs = []
    links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a')).map(a => {
            return {
                title: a.innerText,
                url: a.href
            }
        }).filter(l => l.url.includes('/details/'));
    }""")
    
    for l in set([json.dumps(x) for x in links]):
        item = json.loads(l)
        if item['title'].strip():
            jobs.append({"company": "Apple", "title": item['title'], "url": item['url']})
    return jobs

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        jobs = await scrape_apple(page)
        print(f"Found {len(jobs)} Apple jobs.")
        for j in jobs[:3]: print(j)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
