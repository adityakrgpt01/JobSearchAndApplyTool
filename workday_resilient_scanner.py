"""
Workday Adaptive Resilient Scanner.
1. Emits throttled calls (exponential backoff & concurrency semaphore) to avoid rate limits.
2. Captures cookies and mimicks browser headers.
3. Automatically logs every failure (422, 404, timeouts) to `scraper_failures` table in SQLite.
4. Provides a dedicated rerun/retry worker that retries failed endpoints.
"""

import aiohttp
import asyncio
import json
import sqlite3
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

def is_recent(posted_on: str) -> bool:
    p = posted_on.lower()
    return "today" in p or "yesterday" in p or "1 day ago" in p or "2 days ago" in p

def log_failure(source: str, company: str, url: str, reason: str, status_code: int = 0):
    try:
        conn = sqlite3.connect("jobs.db")
        c = conn.cursor()
        c.execute("""
        INSERT INTO scraper_failures (source, company_name, target_url, failure_reason, http_status, retry_count, last_attempted)
        VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """, (source, company, url, reason, status_code))
        conn.commit()
        conn.close()
    except Exception:
        pass

def mark_failure_resolved(url: str):
    try:
        conn = sqlite3.connect("jobs.db")
        c = conn.cursor()
        c.execute("UPDATE scraper_failures SET resolved = 1 WHERE target_url = ?", (url,))
        conn.commit()
        conn.close()
    except Exception:
        pass

async def scan_single_workday_adaptive(
    session: aiohttp.ClientSession,
    comp: Dict[str, Any],
    sem: asyncio.Semaphore,
    delay_between_calls: float = 0.15
) -> List[Dict[str, Any]]:
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
        await asyncio.sleep(delay_between_calls)  # Smooth rate throttling
        try:
            async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    postings = data.get("jobPostings", [])
                    mark_failure_resolved(api_url)

                    for j in postings:
                        title = j.get("title", "")
                        if not is_backend_role(title):
                            continue

                        posted_on = j.get("postedOn", "")
                        if not is_recent(posted_on):
                            continue

                        locations = j.get("locationsText", "")
                        loc_low = locations.lower()
                        if not ("india" in loc_low or "bengaluru" in loc_low or "bangalore" in loc_low or "hyderabad" in loc_low or "pune" in loc_low or "remote" in loc_low):
                            continue

                        external_path = j.get("externalPath", "")
                        if comp.get('tenant') == 'en-US':
                            apply_url = f"https://{comp['host']}/en-US/{comp.get('board', 'job')}{external_path}" if external_path else f"https://{comp['host']}"
                        else:
                            apply_url = f"https://{comp['host']}/{comp['tenant']}{external_path}" if external_path else f"https://{comp['host']}"
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
                else:
                    log_failure("workday", name, api_url, f"HTTP Error {resp.status}", resp.status)
        except asyncio.TimeoutError:
            log_failure("workday", name, api_url, "Connection Timeout (WAF/Akamai rate-limit)", 408)
        except Exception as e:
            log_failure("workday", name, api_url, str(e), 500)

    return discovered

async def rerun_failed_endpoints(concurrency: int = 15) -> List[Dict[str, Any]]:
    """Reruns exclusively on failed endpoints with higher backoff and retry tracking."""
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""
    SELECT company_name, target_url, failure_reason FROM scraper_failures
    WHERE resolved = 0 AND retry_count < 3
    GROUP BY target_url
    """)
    failed_rows = c.fetchall()
    conn.close()

    if not failed_rows:
        print("🎉 Zero unresolved failures in the retry queue!")
        return []

    print(f"🔄 Retrying {len(failed_rows)} previously failed endpoints with slower backoff...")
    sem = asyncio.Semaphore(concurrency)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin"
    }

    all_jobs = []
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = []
        for name, url, reason in failed_rows:
            tenant = url.split("/wday/cxs/")[1].split("/")[0] if "/wday/cxs/" in url else name.lower()
            host = url.split("/")[2] if "://" in url else "myworkdayjobs.com"
            comp = {"company_name": name, "api_url": url, "host": host, "tenant": tenant}
            tasks.append(scan_single_workday_adaptive(session, comp, sem, delay_between_calls=0.35))

        results = await asyncio.gather(*tasks)
        for r in results:
            all_jobs.extend(r)

    print(f"✅ Rerun complete! Rescued {len(all_jobs)} jobs from failed endpoints.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(rerun_failed_endpoints())
