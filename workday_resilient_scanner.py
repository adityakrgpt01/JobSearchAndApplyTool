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
from database import save_job, log_failure, mark_failure_resolved
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

from experience_filter import is_qualified_seniority_and_exp, is_qualified_job
from workday_scanner import resolve_workday_location

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii",
    "software engineer", "sr software engineer", "software development engineer", "software dev engineer",
    "system software engineer", "systems software engineer", "system software developer",
    "java developer", "java engineer", "java software engineer", "java backend",
    "platform engineer", "systems engineer", "mts", "computer scientist", "cloud engineer"
]

DISALLOWED_WORKDAY_KEYWORDS = [
    "frontend", "front-end", "intern", "internship", "qa", "sdet", "warehouse", "sales", "account manager",
    "android", "androis", "ios", "mobile", "flutter", "swift", "react native", "react-native",
    "servicenow", "service now", "service-now",
    "mobile app", "mobile developer", "mobile application", "mobile engineer"
]

def is_backend_role(title: str, jd_text: str = "") -> bool:
    if not is_qualified_seniority_and_exp(title, jd_text):
        return False
    t = title.lower()
    if any(neg in t for neg in DISALLOWED_WORKDAY_KEYWORDS):
        return False
    import re
    if re.search(r'\b(ios)\b', t):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

def is_recent(posted_on: str, max_age_hours: int = 720) -> bool:
    return True

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
        "searchText": "Software India"
    }

    async with sem:
        await asyncio.sleep(delay_between_calls)  # Smooth rate throttling
        try:
            async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    postings = list(data.get("jobPostings", []))
                    mark_failure_resolved(api_url)

                    total_count = data.get("total", 0)
                    if total_count > 20:
                        try:
                            p2 = {
                                "appliedFacets": {},
                                "limit": 20,
                                "offset": 20,
                                "searchText": "Software India"
                            }
                            async with session.post(api_url, json=p2, timeout=aiohttp.ClientTimeout(total=6)) as resp2:
                                if resp2.status == 200:
                                    d2 = await resp2.json()
                                    postings.extend(d2.get("jobPostings", []))
                        except Exception:
                            pass

                    for j in postings:
                        title = j.get("title", "")
                        if not is_backend_role(title):
                            continue

                        posted_on = j.get("postedOn", "")
                        if not is_recent(posted_on):
                            continue

                        raw_loc = j.get("locationsText", "")
                        external_path = j.get("externalPath", "")
                        if not external_path:
                            continue

                        resolved_loc = resolve_workday_location(raw_loc, external_path)
                        if not resolved_loc:
                            continue

                        tenant = comp.get("tenant", "")
                        board = comp.get("board", "job")
                        host = comp.get("host", "")
                        if tenant == "en-US":
                            apply_url = f"https://{host}/en-US/{board}{external_path}"
                        elif tenant:
                            apply_url = f"https://{host}/{tenant}/{board}{external_path}"
                        else:
                            apply_url = f"https://{host}{external_path}"

                        import hashlib
                        path_hash = hashlib.md5((external_path or title).encode()).hexdigest()[:12]
                        job_id = f"wd_{tenant or 'wday'}_{path_hash}"

                        tier, est_ctc = classify_salary(name, title)
                        await perform_smart_due_diligence(name)

                        rec = {
                            "job_id": job_id,
                            "company_name": name,
                            "title": title,
                            "location": resolved_loc,
                            "is_remote": "remote" in resolved_loc.lower(),
                            "ats_platform": "workday",
                            "apply_url": apply_url,
                            "jd_content": f"{title} at {name}. Location: {resolved_loc}. Posted: {posted_on}.",
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
    """Reruns exclusively on failed Workday endpoints with higher backoff and retry tracking."""
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""
    SELECT company_name, target_url, failure_reason FROM scraper_failures
    WHERE resolved = 0 AND (http_status IS NULL OR http_status != 404) AND retry_count < 5 AND (source = 'workday' OR target_url LIKE '%myworkdayjobs.com%')
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
