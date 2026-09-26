"""
ATS Discovery Scanner.
Polls Greenhouse, Lever, and Ashby public endpoints for Senior Backend / SDE-2 roles
posted in the last 24 hours (or recent active requisitions).
"""

import aiohttp
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from database import save_job, get_company_intelligence
from due_diligence import get_or_create_due_diligence
from salary_classifier import classify_salary

# Target companies with high-paying tech bars and active ATS boards
TARGET_BOARDS = {
    "greenhouse": [
        {"name": "Rippling", "token": "rippling"},
        {"name": "Rubrik", "token": "rubrik"},
        {"name": "Stripe", "token": "stripe"},
        {"name": "Databricks", "token": "databricks"},
        {"name": "Snowflake", "token": "snowflake"},
        {"name": "Figma", "token": "figma"},
        {"name": "Coinbase", "token": "coinbase"},
        {"name": "DoorDash", "token": "doordash"},
        {"name": "Reddit", "token": "reddit"},
        {"name": "Affirm", "token": "affirm"}
    ],
    "lever": [
        {"name": "Atlassian", "token": "atlassian"},
        {"name": "Palantir", "token": "palantir"},
        {"name": "Postman", "token": "postman"},
        {"name": "BrowserStack", "token": "browserstack"}
    ],
    "ashby": [
        {"name": "Linear", "token": "linear"},
        {"name": "Ramp", "token": "ramp"},
        {"name": "Retool", "token": "retool"},
        {"name": "Vercel", "token": "vercel"}
    ]
}

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed systems", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii"
]

def is_senior_backend_or_sde2(title: str) -> bool:
    t = title.lower()
    # Reject mobile, frontend, qa, devops, data science unless backend explicitly stated
    if any(neg in t for neg in ["frontend", "front-end", "front end", "android", "ios", "qa tester", "sdet", "intern"]):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

async def scan_greenhouse(session: aiohttp.ClientSession, company: Dict[str, str], max_age_hours: int = 48) -> List[Dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company['token']}/jobs?content=true"
    discovered = []
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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

                tier, est_ctc = classify_salary(company["name"], jd_text)
                get_or_create_due_diligence(company["name"]) # ensures company dossier in DB

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
        print(f"Error scanning Greenhouse for {company['name']}: {e}")
    return discovered

async def scan_lever(session: aiohttp.ClientSession, company: Dict[str, str], max_age_hours: int = 48) -> List[Dict[str, Any]]:
    url = f"https://api.lever.co/v0/postings/{company['token']}?mode=json"
    discovered = []
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            jobs = await resp.json()
            now = datetime.now(timezone.utc)
            for j in jobs:
                title = j.get("text", "")
                if not is_senior_backend_or_sde2(title):
                    continue

                created_at_ms = j.get("createdAt")
                if created_at_ms:
                    created_at = datetime.fromtimestamp(created_at_ms / 1000.0, timezone.utc)
                    if (now - created_at).total_seconds() > max_age_hours * 3600:
                        continue

                location = j.get("categories", {}).get("location", "Remote / Multiple")
                apply_url = j.get("applyUrl", "")
                jd_text = j.get("descriptionPlain", "")

                tier, est_ctc = classify_salary(company["name"], jd_text)
                get_or_create_due_diligence(company["name"])

                job_record = {
                    "job_id": f"lever_{company['token']}_{j['id']}",
                    "company_name": company["name"],
                    "title": title,
                    "location": location,
                    "is_remote": "remote" in location.lower(),
                    "ats_platform": "lever",
                    "apply_url": apply_url,
                    "jd_content": jd_text[:2000],
                    "posted_at": datetime.fromtimestamp(created_at_ms / 1000.0).isoformat() if created_at_ms else datetime.utcnow().isoformat(),
                    "experience_required": "4-6 Years (SDE-2 / Senior)",
                    "salary_tier": tier,
                    "estimated_ctc": est_ctc,
                    "status": "DISCOVERED"
                }
                save_job(job_record)
                discovered.append(job_record)
    except Exception as e:
        print(f"Error scanning Lever for {company['name']}: {e}")
    return discovered

async def run_discovery_pipeline(hours: int = 48) -> List[Dict[str, Any]]:
    """Runs concurrent scanning across all configured boards."""
    all_jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = []
        for c in TARGET_BOARDS["greenhouse"]:
            tasks.append(scan_greenhouse(session, c, hours))
        for c in TARGET_BOARDS["lever"]:
            tasks.append(scan_lever(session, c, hours))

        results = await asyncio.gather(*tasks)
        for r in results:
            all_jobs.extend(r)

    print(f"Discovered {len(all_jobs)} eligible Senior Backend / SDE-2 roles.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_discovery_pipeline(72))
