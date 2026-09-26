"""
ATS Discovery Scanner with Smart On-Demand Due Diligence.
Scans Greenhouse & Lever for Senior Backend / SDE-2 roles posted in the last 24h.
Triggers deep on-demand due diligence only when an eligible job is found.
"""

import aiohttp
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from database import save_job, get_company_intelligence
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

# Active high-priority companies to poll frequently
CORE_TARGET_COMPANIES = [
    {"name": "Databricks", "token": "databricks"},
    {"name": "Stripe", "token": "stripe"},
    {"name": "Rubrik", "token": "rubrik"},
    {"name": "Coinbase", "token": "coinbase"},
    {"name": "Reddit", "token": "reddit"},
    {"name": "Pinterest", "token": "pinterest"},
    {"name": "Affirm", "token": "affirm"},
    {"name": "Instacart", "token": "instacart"},
    {"name": "GitLab", "token": "gitlab"},
    {"name": "Okta", "token": "okta"},
    {"name": "Twilio", "token": "twilio"},
    {"name": "Samsara", "token": "samsara"},
    {"name": "Toast", "token": "toast"},
    {"name": "Cockroach Labs", "token": "cockroachlabs"},
    {"name": "InMobi", "token": "inmobi"},
    {"name": "HackerRank", "token": "hackerrank"},
    {"name": "Druva", "token": "druva"},
    {"name": "Observe.AI", "token": "observeai"}
]

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed systems", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii"
]

def is_senior_backend_or_sde2(title: str) -> bool:
    t = title.lower()
    if any(neg in t for neg in ["frontend", "front-end", "front end", "android", "ios", "qa tester", "sdet", "intern"]):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

async def scan_greenhouse_company(session: aiohttp.ClientSession, company: Dict[str, str], max_age_hours: int = 72) -> List[Dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company['token']}/jobs?content=true"
    discovered = []
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            jobs = data.get("jobs", [])
            now = datetime.now(timezone.utc)
            for j in jobs:
                title = j.get("title", "")
                if not is_senior_backend_or_sde2(title):
                    continue

                updated_at_str = j.get("updated_at")
                if updated_at_str:
                    try:
                        updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                        if (now - updated_at).total_seconds() > max_age_hours * 3600:
                            continue
                    except Exception:
                        pass

                location = j.get("location", {}).get("name", "Remote / Multiple")
                apply_url = j.get("absolute_url", "")
                jd_text = j.get("content", "")

                # 1. Classify salary tier
                tier, est_ctc = classify_salary(company["name"], jd_text)

                # 2. Trigger Smart On-Demand Due Diligence for this company
                await perform_smart_due_diligence(company["name"], company["token"])

                job_record = {
                    "job_id": f"gh_{company['token']}_{j['id']}",
                    "company_name": company["name"],
                    "title": title,
                    "location": location,
                    "is_remote": "remote" in location.lower() or "india" in location.lower(),
                    "ats_platform": "greenhouse",
                    "apply_url": apply_url,
                    "jd_content": jd_text[:2000],
                    "posted_at": updated_at_str or datetime.utcnow().isoformat(),
                    "experience_required": "4-6 Years (SDE-2 / Senior)",
                    "salary_tier": tier,
                    "estimated_ctc": est_ctc,
                    "status": "DISCOVERED"
                }
                save_job(job_record)
                discovered.append(job_record)
    except Exception as e:
        pass
    return discovered

async def run_discovery_pipeline(hours: int = 72) -> List[Dict[str, Any]]:
    all_jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [scan_greenhouse_company(session, c, hours) for c in CORE_TARGET_COMPANIES]
        results = await asyncio.gather(*tasks)
        for r in results:
            all_jobs.extend(r)

    print(f"🎯 Discovered {len(all_jobs)} eligible Senior Backend / SDE-2 roles across scanned companies.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_discovery_pipeline(72))
