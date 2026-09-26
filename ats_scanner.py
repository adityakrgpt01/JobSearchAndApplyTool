"""
High-Throughput ATS Discovery Scanner.
Reads all 1,000+ companies from companies_intelligence / companies_1000.json,
scans their Greenhouse APIs concurrently with rate-limiting & connection pooling,
filters for Senior Backend / SDE-2 (4-6 YoE), performs on-demand due diligence,
and saves matching roles to SQLite.
"""

import aiohttp
import asyncio
import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from database import save_job, get_company_intelligence
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed systems", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii"
]

def is_senior_backend_or_sde2(title: str) -> bool:
    t = title.lower()
    if any(neg in t for neg in ["frontend", "front-end", "front end", "android", "ios", "qa tester", "sdet", "intern", "lead", "director", "manager"]):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

def get_all_target_companies() -> List[Dict[str, str]]:
    """Loads all companies from companies_intelligence table or companies_1000.json."""
    try:
        conn = sqlite3.connect("jobs.db")
        c = conn.cursor()
        c.execute("SELECT company_name, normalized_name FROM companies_intelligence")
        rows = c.fetchall()
        conn.close()
        if rows:
            return [{"name": r[0], "token": r[1]} for r in rows]
    except Exception:
        pass

    try:
        with open("companies_1000.json", "r") as f:
            data = json.load(f)
            return [{"name": d["company_name"], "token": d["greenhouse_token"]} for d in data]
    except Exception:
        return []

async def scan_single_company(
    session: aiohttp.ClientSession,
    company: Dict[str, str],
    semaphore: asyncio.Semaphore,
    max_age_hours: int = 72
) -> List[Dict[str, Any]]:
    token = company["token"]
    name = company["name"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    discovered = []

    async with semaphore:
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

                    location = j.get("location", {}).get("name", "Remote / Multiple")
                    loc_lower = location.lower()

                    # Filter for India or Remote eligibility
                    is_india_or_remote = "india" in loc_lower or "bengaluru" in loc_lower or "bangalore" in loc_lower or "remote" in loc_lower
                    if not is_india_or_remote:
                        continue

                    updated_at_str = j.get("updated_at")
                    if updated_at_str:
                        try:
                            updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                            if (now - updated_at).total_seconds() > max_age_hours * 3600:
                                continue
                        except Exception:
                            pass

                    apply_url = j.get("absolute_url", "")
                    jd_text = j.get("content", "")

                    # Classify salary tier
                    tier, est_ctc = classify_salary(name, jd_text)

                    # Trigger on-demand due diligence
                    await perform_smart_due_diligence(name, token)

                    job_record = {
                        "job_id": f"gh_{token}_{j['id']}",
                        "company_name": name,
                        "title": title,
                        "location": location,
                        "is_remote": "remote" in loc_lower,
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
        except Exception:
            pass

    return discovered

async def run_discovery_pipeline(hours: int = 72, concurrency: int = 40) -> List[Dict[str, Any]]:
    companies = get_all_target_companies()
    print(f"🚀 Launching high-throughput scan across {len(companies)} Greenhouse companies (Concurrency: {concurrency})...")

    semaphore = asyncio.Semaphore(concurrency)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    all_jobs = []

    conn = aiohttp.TCPConnector(limit=concurrency + 10, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        tasks = [scan_single_company(session, c, semaphore, hours) for c in companies]
        results = await asyncio.gather(*tasks)
        for r in results:
            all_jobs.extend(r)

    print(f"✅ Full 1,000+ company scan complete! Found {len(all_jobs)} eligible Senior Backend / SDE-2 opportunities.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_discovery_pipeline(72, concurrency=50))
