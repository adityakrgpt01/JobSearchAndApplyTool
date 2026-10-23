"""
Phenom People Native Enterprise Scanner.
Queries public Phenom People career API endpoints for Tier-1 companies (Walmart Global Tech, Thermo Fisher, Honeywell, etc.)
Filters for Senior Backend / SDE-2 (4-6 YoE) roles in Indian tech hubs.
"""

import aiohttp
import asyncio
import json
import random
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from database import save_job, log_failure
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary
from experience_filter import is_qualified_seniority_and_exp, is_qualified_location

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii",
    "software engineer", "sr software engineer", "software development engineer", "software dev engineer",
    "java developer", "java engineer", "java software engineer", "platform engineer", "systems engineer", "mts"
]

DISALLOWED_KEYWORDS = [
    "frontend", "front-end", "front end", "android", "ios", "mobile", "flutter", "react native",
    "qa tester", "qa engineer", "sdet", "intern", "internship", "director", "manager",
    "servicenow", "salesforce developer"
]

PHENOM_TARGETS = [
    {
        "name": "Walmart Global Tech",
        "domain": "careers.walmart.com",
        "search_url": "https://careers.walmart.com/api/v1/jobs",
        "params": {"q": "Software Engineer", "location": "India", "page": "1", "sort": "date"}
    },
    {
        "name": "Thermo Fisher Scientific",
        "domain": "jobs.thermofisher.com",
        "search_url": "https://jobs.thermofisher.com/widgets/jobsearch/jobs",
        "params": {"q": "Software", "location": "India"}
    },
    {
        "name": "Honeywell",
        "domain": "careers.honeywell.com",
        "search_url": "https://careers.honeywell.com/widgets/jobsearch/jobs",
        "params": {"q": "Software", "location": "India"}
    },
    {
        "name": "Humana",
        "domain": "careers.humana.com",
        "search_url": "https://careers.humana.com/widgets/jobsearch/jobs",
        "params": {"q": "Software", "location": "India"}
    },
    {
        "name": "Brother International",
        "domain": "careers.brother.com",
        "search_url": "https://careers.brother.com/widgets/jobsearch/jobs",
        "params": {"q": "Software", "location": "India"}
    }
]

def is_backend_role(title: str, jd_text: str = "") -> bool:
    if not is_qualified_seniority_and_exp(title, jd_text):
        return False
    t = title.lower()
    if any(neg in t for neg in DISALLOWED_KEYWORDS):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

async def scan_single_phenom_tenant(session: aiohttp.ClientSession, comp: Dict[str, Any], sem: asyncio.Semaphore) -> List[Dict[str, Any]]:
    name = comp["name"]
    domain = comp["domain"]
    url = comp["search_url"]
    params = comp.get("params", {"q": "Software", "location": "India"})

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9"
    }

    discovered = []
    async with sem:
        await asyncio.sleep(random.uniform(0.5, 1.5))
        try:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log_failure("phenom", name, url, f"HTTP {resp.status}", resp.status)
                    return []

                resp_headers = getattr(resp, "headers", {})
                ct = resp_headers.get("Content-Type", "").lower() if hasattr(resp_headers, "get") else ""
                if "html" in ct:
                    log_failure("phenom", name, url, "Phenom portal returned HTML redirect - routed to LinkedIn Bridge", 200)
                    return []

                try:
                    data = await resp.json()
                except Exception as e:
                    log_failure("phenom", name, url, f"Malformed JSON: {e}", 502)
                    return []

                jobs = data.get("jobs", []) or data.get("data", {}).get("jobs", []) or data.get("results", [])

                for j in jobs:
                    title = j.get("title", "") or j.get("name", "")
                    if not is_backend_role(title):
                        continue

                    city = j.get("city", "")
                    country = j.get("country", "")
                    loc = f"{city}, {country}".strip(", ") or j.get("location", "India")
                    if not is_qualified_location(loc):
                        continue

                    jid = str(j.get("jobId") or j.get("refNumber") or j.get("id") or abs(hash(title)))
                    job_url = j.get("applyUrl") or j.get("canonicalUrl") or f"https://{domain}/job/{jid}"

                    tier, est_ctc = classify_salary(name, title)
                    await perform_smart_due_diligence(name)

                    record = {
                        "job_id": f"phenom_{abs(hash(name))}_{jid}",
                        "company_name": name,
                        "title": title,
                        "location": loc,
                        "is_remote": "remote" in loc.lower(),
                        "ats_platform": "phenom",
                        "apply_url": job_url,
                        "jd_content": f"{title} at {name}. Location: {loc}.",
                        "posted_at": j.get("postedDate") or datetime.now(timezone.utc).isoformat(),
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier,
                        "estimated_ctc": est_ctc,
                        "status": "DISCOVERED"
                    }
                    save_job(record)
                    discovered.append(record)
        except Exception as e:
            log_failure("phenom", name, url, str(e), 500)

    return discovered

async def scan_phenom_pipeline() -> List[Dict[str, Any]]:
    print("⚡ Firing Phenom People Enterprise Scanner (Walmart Global Tech, Thermo Fisher, Honeywell, etc.)...")
    sem = asyncio.Semaphore(3)
    conn = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
    all_jobs = []

    async with aiohttp.ClientSession(connector=conn) as session:
        tasks = [scan_single_phenom_tenant(session, comp, sem) for comp in PHENOM_TARGETS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_jobs.extend(r)

    print(f"✅ Phenom People scan complete! Discovered {len(all_jobs)} opportunities.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(scan_phenom_pipeline())
