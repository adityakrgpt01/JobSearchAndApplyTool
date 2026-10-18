import asyncio
from playwright.async_api import async_playwright
import json
import re
import datetime
from database import save_job
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

# Only implement the primary FAANGs here. The others (Salesforce, Adobe, Intuit, Airbnb)
# are handled natively by the unified pipeline's Workday/Greenhouse hooks.

async def scrape_microsoft(page):
    print("Scraping Microsoft...")
    await page.goto("https://jobs.careers.microsoft.com/global/en/search?q=Software%20Engineer&l=en_us&pg=1&pgSz=20&o=Recent&flt=true", wait_until="networkidle")
    await page.wait_for_timeout(3000)
    
    jobs = []
    links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a')).map(a => {
            return {
                title: a.innerText,
                url: a.href
            }
        }).filter(l => l.url.includes('/job/') && l.title.length > 5);
    }""")
    
    for l in set([json.dumps(x) for x in links]):
        item = json.loads(l)
        parts = item['title'].split('\n')
        if len(parts) >= 1:
            title = parts[0].strip()
            loc = parts[1].strip() if len(parts) > 1 else "India"
            jobs.append({"company": "Microsoft", "title": title, "url": item['url'], "location": loc})
    return jobs

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
        }).filter(l => l.url.includes('/details/') && !l.title.includes('See full role description') && !l.title.includes('Submit Resume'));
    }""")
    
    for l in set([json.dumps(x) for x in links]):
        item = json.loads(l)
        if item['title'].strip():
            jobs.append({"company": "Apple", "title": item['title'].strip(), "url": item['url'], "location": "India"})
    return jobs

async def process_and_save_elite_jobs(jobs):
    print(f"Discovered {len(jobs)} elite jobs directly from portals. Processing...")
    saved_count = 0
    for j in jobs:
        if "engineer" not in j['title'].lower() and "developer" not in j['title'].lower():
            continue
            
        jd_content = f"Official posting directly from {j['company']} career site."
        tier, ctc = classify_salary(j['company'], jd_content)
        dd_data = await perform_smart_due_diligence(j['company'])
        
        if dd_data.get('risk_level') != 'HIGH':
            save_job({
                "job_id": j['url'],
                "company_name": j['company'],
                "title": j['title'],
                "location": j['location'],
                "is_remote": bool("remote" in j['location'].lower()),
                "apply_url": j['url'],
                "ats_platform": "CustomPortal",
                "jd_content": jd_content,
                "salary_tier": tier,
                "estimated_ctc": ctc,
                "posted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "experience_required": "Unknown",
                "status": "discovered"
            })
            saved_count += 1
    print(f"Successfully evaluated and saved {saved_count} elite jobs.")
    return saved_count

async def run_elite_spider():
    print("🚀 Starting Elite FAANG Spider via Headless Chrome...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        all_jobs = []
        try:
            ms_jobs = await scrape_microsoft(page)
            all_jobs.extend(ms_jobs)
        except Exception as e:
            print(f"Microsoft scrape failed: {e}")
            
        try:
            apple_jobs = await scrape_apple(page)
            all_jobs.extend(apple_jobs)
        except Exception as e:
            print(f"Apple scrape failed: {e}")
            
        await browser.close()
        
        return await process_and_save_elite_jobs(all_jobs)

if __name__ == "__main__":
    asyncio.run(run_elite_spider())
