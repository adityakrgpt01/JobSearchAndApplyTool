"""
Workday Enterprise CXS Scanner.
Connects directly to Workday's official POST endpoint:
https://{host}/wday/cxs/{tenant}/{board}/jobs
Filters for:
- Senior Backend / SDE-2 roles
- Posted Today / Posted Yesterday (<24-48h)
- India locations (Bengaluru, Hyderabad, Pune, Gurgaon, Remote)
"""

import aiohttp
import asyncio
import json
from typing import List, Dict, Any
from database import save_job
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii"
]

def is_backend_role(title: str) -> bool:
    t = title.lower()
    if any(neg in t for neg in ["frontend", "front-end", "intern", "qa", "sdet", "warehouse", "sales", "account manager"]):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

def is_recent_workday(posted_on: str) -> bool:
    p = posted_on.lower()
    return "today" in p or "yesterday" in p or "1 day ago" in p or "2 days ago" in p

async def scan_workday_company(session: aiohttp.ClientSession, comp: Dict[str, Any], sem: asyncio.Semaphore) -> List[Dict[str, Any]]:
    api_url = comp["api_url"]
    name = comp["company_name"]
    discovered = []

    payload = {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": ""
    }

    async with sem:
        try:
            async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                postings = data.get("jobPostings", [])

                for j in postings:
                    title = j.get("title", "")
                    if not is_backend_role(title):
                        continue

                    posted_on = j.get("postedOn", "")
                    if not is_recent_workday(posted_on):
                        continue

                    locations = j.get("locationsText", "")
                    loc_low = locations.lower()
                    if not ("india" in loc_low or "bengaluru" in loc_low or "bangalore" in loc_low or "hyderabad" in loc_low or "pune" in loc_low or "remote" in loc_low):
                        continue

                    external_path = j.get("externalPath", "")
                    apply_url = f"https://{comp['host']}{external_path}" if external_path else f"https://{comp['host']}"
                    job_id = f"wd_{comp['tenant']}_{abs(hash(external_path or title))}"

                    tier, est_ctc = classify_salary(name, title)
                    await perform_smart_due_diligence(name)

                    rec = {
                        "job_id": job_id,
                        "company_name": name,
                        "title": title,
                        "location": locations,
                        "is_remote": "remote" in loc_low,
                        "ats_platform": "workday",
                        "apply_url": apply_url,
                        "jd_content": f"{title} at {name}. Location: {locations}. Posted: {posted_on}.",
                        "posted_at": posted_on,
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier,
                        "estimated_ctc": est_ctc,
                        "status": "DISCOVERED"
                    }
                    save_job(rec)
                    discovered.append(rec)
        except Exception:
            pass

    return discovered

async def run_workday_scanner(max_companies: int = 150, concurrency: int = 30) -> List[Dict[str, Any]]:
    try:
        with open("workday_2000.json", "r") as f:
            all_comps = json.load(f)
    except Exception:
        return []

    target_comps = all_comps[:max_companies]
    print(f"🚀 Scanning {len(target_comps)} Workday multinational companies (Concurrency: {concurrency})...")

    sem = asyncio.Semaphore(concurrency)
    conn = aiohttp.TCPConnector(limit=concurrency + 10, ttl_dns_cache=300)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    all_jobs = []
    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        tasks = [scan_workday_company(session, c, sem) for c in target_comps]
        results = await asyncio.gather(*tasks)
        for r in results:
            all_jobs.extend(r)

    print(f"✅ Workday scan finished! Discovered {len(all_jobs)} eligible Senior Backend / SDE-2 opportunities.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_workday_scanner(max_companies=100))
