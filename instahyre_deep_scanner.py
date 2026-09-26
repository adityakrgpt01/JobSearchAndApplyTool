"""
Deep Instahyre Scanner (20+ Pages).
Paginates through Instahyre's entire active job inventory.
Strictly captures Senior Backend & SDE-2 roles across Indian product unicorns
(Swiggy, Zepto, CRED, PhonePe, Razorpay, Tekion, Coupang, etc.).
"""

import aiohttp
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any
from database import save_job
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii"
]

def is_backend_match(title: str) -> bool:
    t = title.lower()
    if any(neg in t for neg in ["frontend", "front-end", "qa", "sdet", "intern", "android", "ios", "devops", "marketing", "sales"]):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

async def scan_instahyre_page(session: aiohttp.ClientSession, sem: asyncio.Semaphore, offset: int = 0) -> List[Dict[str, Any]]:
    url = f"https://www.instahyre.com/api/v1/job_search?offset={offset}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    discovered = []
    async with sem:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                jobs = data.get("objects", [])

                for j in jobs:
                    title = j.get("title", "")
                    if not is_backend_match(title):
                        continue

                    employer = j.get("employer", {})
                    company_name = employer.get("company_name", "Tech Startup")
                    locations = j.get("locations", "India")
                    public_url = j.get("public_url", "")
                    job_id = f"instahyre_{j.get('id')}"

                    tier, est_ctc = classify_salary(company_name, title)
                    await perform_smart_due_diligence(company_name)

                    record = {
                        "job_id": job_id,
                        "company_name": company_name,
                        "title": title,
                        "location": locations,
                        "is_remote": "remote" in str(locations).lower(),
                        "ats_platform": "instahyre",
                        "apply_url": public_url,
                        "jd_content": f"{title} at {company_name}. Location: {locations}. {employer.get('company_tagline', '')}",
                        "posted_at": datetime.now(timezone.utc).isoformat(),
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier,
                        "estimated_ctc": est_ctc,
                        "status": "DISCOVERED"
                    }
                    save_job(record)
                    discovered.append(record)
        except Exception:
            pass
    return discovered

async def run_deep_instahyre_scanner(total_pages: int = 25, concurrency: int = 8) -> List[Dict[str, Any]]:
    print(f"🚀 Running Deep Instahyre Scanner across {total_pages} pages ({total_pages * 35} raw jobs)...")
    sem = asyncio.Semaphore(concurrency)
    conn = aiohttp.TCPConnector(limit=concurrency + 5, ttl_dns_cache=300)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

    all_jobs = []
    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        tasks = [scan_instahyre_page(session, sem, offset=p * 35) for p in range(total_pages)]
        results = await asyncio.gather(*tasks)
        for r in results:
            all_jobs.extend(r)

    print(f"✅ Deep Instahyre Scan Complete! Found {len(all_jobs)} eligible Senior Backend / SDE-2 opportunities.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_deep_instahyre_scanner(total_pages=25))
