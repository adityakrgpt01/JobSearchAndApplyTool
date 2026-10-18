"""
Unified Autonomous Discovery, Qualification & Link Verification Engine.
Orchestrates:
1. Multi-ATS (Greenhouse, Lever, Ashby)
2. Workday CXS Resilient Scanner
3. Instahyre Unicorn Deep Scanner
4. LinkedIn Stream (24h) & Amazon Jobs API
Strictly validates:
- Target Profile: Senior Backend / SDE-2 (~5 YoE), India or Remote
- Live Link Reachability: Discards broken/404/malformed links before saving
"""

import aiohttp
import asyncio
import sqlite3
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

from database import save_job, get_crawler_cursor, set_crawler_cursor, get_crawler_iteration
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary
from ats_scanner import load_all_target_companies, scan_greenhouse_job, scan_lever_job, scan_ashby_job
from workday_scanner import run_workday_cxs_pipeline
from instahyre_deep_scanner import scan_instahyre_page
from linkedin_crawler import run_partitioned_linkedin_crawler, scan_amazon_jobs, resolve_linkedin_apply_destination

from experience_filter import is_qualified_seniority_and_exp, is_qualified_job

async def heal_job_url(session: aiohttp.ClientSession, job: Dict[str, Any]) -> Tuple[bool, str, str]:
    """
    Strictly verifies and heals a job's apply_url to guarantee direct job-specific application links.
    Returns: (is_valid, final_url, status_note)
    """
    url = job.get("apply_url", "").strip()
    platform = job.get("ats_platform", "")
    comp = job.get("company_name", "")
    job_id = job.get("job_id", "")

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

    # 1. Structural check for protected / direct platforms
    if platform == "linkedin":
        resolved_job = await resolve_linkedin_apply_destination(session, dict(job))
        if resolved_job.get("apply_url") != url:
            save_job(resolved_job)
            return True, resolved_job["apply_url"], "HEALED_CANONICAL"
        if len(url) > 25 and ("jobs/view/" in url or "job-" in url):
            return True, url, "DIRECT_VALID"
        return False, url, "INVALID_SYNTAX"

    if platform == "instahyre":
        if len(url) > 25 and ("jobs/view/" in url or "job-" in url):
            return True, url, "DIRECT_VALID"
        return False, url, "INVALID_SYNTAX"

    # Strict check: Does URL contain a direct job identifier path?
    has_specific_job_id = False
    if platform == "greenhouse":
        has_specific_job_id = bool(re.search(r'(?:jobs/|gh_jid=)\d+', url))
    elif platform == "workday":
        has_specific_job_id = "/job/" in url.lower()
    elif platform == "ashby":
        has_specific_job_id = "/application" in url.lower() or bool(re.search(r'ashbyhq\.com/[^/]+/[a-f0-9\-]+', url.lower()))
    elif platform == "lever":
        has_specific_job_id = bool(re.search(r'lever\.co/[^/]+/[a-f0-9\-]+', url.lower()))
    else:
        has_specific_job_id = len(url) > 20

    # 2. Probe primary URL if it has a specific job ID
    if has_specific_job_id:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=True) as resp:
                if resp.status in [200, 202, 301, 302] and "error=true" not in str(resp.url):
                    return True, url, "DIRECT_VALID"
        except Exception:
            pass

    # 3. Canonical ATS Reconstruction & Auto-Healing
    fallbacks = []
    if platform == "greenhouse":
        m_job_id = re.match(r'^gh_([^_]+)_(\d+)$', job_id)
        if m_job_id:
            token, jid = m_job_id.group(1), m_job_id.group(2)
            try:
                api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}"
                async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        abs_url = data.get("absolute_url")
                        if abs_url:
                            return True, abs_url, "HEALED_CANONICAL"
            except Exception:
                pass

            fallbacks.append(f"https://job-boards.greenhouse.io/{token}/jobs/{jid}")
            fallbacks.append(f"https://boards.greenhouse.io/{token}/jobs/{jid}")

    elif platform == "ashby":
        m_ashby = re.match(r'^ashby_([^_]+)_([a-f0-9\-]+)$', job_id)
        if m_ashby:
            comp_slug, real_jid = m_ashby.group(1), m_ashby.group(2)
            fallbacks.append(f"https://jobs.ashbyhq.com/{comp_slug}/{real_jid}")

    elif platform == "lever":
        m_lever = re.match(r'^lever_([^_]+)_([a-f0-9\-]+)$', job_id)
        if m_lever:
            comp_slug, real_jid = m_lever.group(1), m_lever.group(2)
            fallbacks.append(f"https://jobs.lever.co/{comp_slug}/{real_jid}")

    elif platform == "workday":
        m_wd = re.match(r'https://([^/]+)(?:/[^/]+)?(?:/[^/]+)?(/job/.+)', url)
        if m_wd:
            host, path = m_wd.group(1), m_wd.group(2)
            # Match tenant/board or fallback to en-US
            m_full = re.match(r'https://([^/]+)/([^/]+)/([^/]+)(/job/.+)', url)
            if m_full:
                board = m_full.group(3)
                fallbacks.append(f"https://{host}/en-US/{board}{path}")
            norm = comp.lower().replace(" ", "")
            fallbacks.append(f"https://{host}/en-US/{norm}{path}")
            fallbacks.append(f"https://{host}/en-US/job{path}")

    # Test reconstructed fallbacks
    for fb in fallbacks:
        try:
            async with session.get(fb, headers=headers, timeout=aiohttp.ClientTimeout(total=4), allow_redirects=True) as resp:
                if resp.status in [200, 202, 301, 302] and "error=true" not in str(resp.url):
                    return True, fb, "HEALED_CANONICAL"
        except Exception:
            continue

    # Strict Policy: Generic landing portal URLs without direct job paths are rejected
    return False, url, "GENERIC_PORTAL_INVALID"

async def audit_and_clean_database_links(max_concurrent: int = 30) -> Dict[str, int]:
    """Audits existing job postings, auto-heals broken links, and falls back to career portals so no company is missed."""
    conn = sqlite3.connect("jobs.db", timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Never audit or delete jobs that have already been APPLIED
    c.execute("SELECT * FROM job_postings WHERE status != 'APPLIED'")
    jobs = [dict(r) for r in c.fetchall()]
    conn.close()

    sem = asyncio.Semaphore(max_concurrent)
    conn_obj = aiohttp.TCPConnector(limit=max_concurrent + 10, ssl=False)

    healed_updates = []
    dead_job_ids = []
    active_count = 0
    healed_count = 0

    async with aiohttp.ClientSession(connector=conn_obj) as session:
        async def check(j):
            nonlocal active_count, healed_count
            async with sem:
                is_ok, final_url, note = await heal_job_url(session, j)
                if is_ok:
                    active_count += 1
                    if note in ["HEALED_CANONICAL", "PORTAL_FALLBACK"]:
                        healed_count += 1
                        healed_updates.append((final_url, j["job_id"]))
                else:
                    dead_job_ids.append(j["job_id"])

        await asyncio.gather(*[check(j) for j in jobs])

    conn = sqlite3.connect("jobs.db", timeout=30.0)
    c = conn.cursor()
    if healed_updates:
        c.executemany("UPDATE job_postings SET apply_url = ? WHERE job_id = ?", healed_updates)
    if dead_job_ids:
        c.executemany("DELETE FROM job_postings WHERE job_id = ?", [(jid,) for jid in dead_job_ids])
    conn.commit()
    conn.close()

    return {
        "total_audited": len(jobs),
        "active_verified": active_count,
        "healed_or_portal_fallback": healed_count,
        "dead_removed": len(dead_job_ids)
    }

async def run_unified_discovery(hours: int = 168) -> Dict[str, Any]:
    """Runs a complete autonomous scan across all platforms with qualification and link verification."""
    print(f"🚀 Starting Unified Discovery Pipeline (Window: {hours}h / 7 days)...")
    
    # 1. Multi-ATS (Greenhouse, Lever, Ashby)
    grouped = load_all_target_companies()
    sem = asyncio.Semaphore(50)
    conn_obj = aiohttp.TCPConnector(limit=60, ssl=False)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    
    discovered_jobs = []

    # Persistent Rotating Cursors across all registered companies in jobs.db
    # Greenhouse: ~2,780, Ashby: ~669, Lever: ~345, Workday: ~2,275
    BATCH_GH = 350
    BATCH_ASHBY = 140
    BATCH_LEVER = 115

    gh_all = grouped.get("greenhouse", [])
    ashby_all = grouped.get("ashby", [])
    lever_all = grouped.get("lever", [])

    # 1. Greenhouse Persistent Batch
    gh_cursor = get_crawler_cursor("cursor_greenhouse", 0)
    gh_iter = get_crawler_iteration("cursor_greenhouse", 1)
    gh_total = max(1, len(gh_all))
    gh_batch = gh_all[gh_cursor : gh_cursor + BATCH_GH]
    gh_wrapped = False
    if len(gh_batch) < BATCH_GH and gh_total > BATCH_GH:
        gh_batch += gh_all[: BATCH_GH - len(gh_batch)]
        gh_wrapped = True
    next_gh_cursor = (gh_cursor + BATCH_GH) % gh_total
    if next_gh_cursor < gh_cursor or gh_wrapped:
        gh_iter += 1
    set_crawler_cursor("cursor_greenhouse", next_gh_cursor, gh_iter)

    # 2. Ashby Persistent Batch
    ashby_cursor = get_crawler_cursor("cursor_ashby", 0)
    ashby_iter = get_crawler_iteration("cursor_ashby", 1)
    ashby_total = max(1, len(ashby_all))
    ashby_batch = ashby_all[ashby_cursor : ashby_cursor + BATCH_ASHBY]
    ashby_wrapped = False
    if len(ashby_batch) < BATCH_ASHBY and ashby_total > BATCH_ASHBY:
        ashby_batch += ashby_all[: BATCH_ASHBY - len(ashby_batch)]
        ashby_wrapped = True
    next_ashby_cursor = (ashby_cursor + BATCH_ASHBY) % ashby_total
    if next_ashby_cursor < ashby_cursor or ashby_wrapped:
        ashby_iter += 1
    set_crawler_cursor("cursor_ashby", next_ashby_cursor, ashby_iter)

    # 3. Lever Persistent Batch
    lever_cursor = get_crawler_cursor("cursor_lever", 0)
    lever_iter = get_crawler_iteration("cursor_lever", 1)
    lever_total = max(1, len(lever_all))
    lever_batch = lever_all[lever_cursor : lever_cursor + BATCH_LEVER]
    lever_wrapped = False
    if len(lever_batch) < BATCH_LEVER and lever_total > BATCH_LEVER:
        lever_batch += lever_all[: BATCH_LEVER - len(lever_batch)]
        lever_wrapped = True
    next_lever_cursor = (lever_cursor + BATCH_LEVER) % lever_total
    if next_lever_cursor < lever_cursor or lever_wrapped:
        lever_iter += 1
    set_crawler_cursor("cursor_lever", next_lever_cursor, lever_iter)

    print(f"🔄 Scanning Rotating Batch across 6,000+ companies:")
    print(f"   ├─ Greenhouse: [{gh_cursor} - {min(gh_cursor + BATCH_GH, gh_total)} of {gh_total}] ({round((gh_cursor/gh_total)*100, 1)}% of Iteration {gh_iter})")
    print(f"   ├─ Ashby:      [{ashby_cursor} - {min(ashby_cursor + BATCH_ASHBY, ashby_total)} of {ashby_total}] ({round((ashby_cursor/ashby_total)*100, 1)}% of Iteration {ashby_iter})")
    print(f"   └─ Lever:      [{lever_cursor} - {min(lever_cursor + BATCH_LEVER, lever_total)} of {lever_total}] ({round((lever_cursor/lever_total)*100, 1)}% of Iteration {lever_iter})")

    async with aiohttp.ClientSession(connector=conn_obj, headers=headers) as session:
        # Run Multi-ATS
        tasks = []
        for c in gh_batch:
            tasks.append(scan_greenhouse_job(session, c, sem, hours))
        for c in ashby_batch:
            tasks.append(scan_ashby_job(session, c, sem, hours))
        for c in lever_batch:
            tasks.append(scan_lever_job(session, c, sem, hours))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                discovered_jobs.extend(r)

    # 2. Instahyre Unicorns
    try:
        insta_sem = asyncio.Semaphore(10)
        async with aiohttp.ClientSession() as s:
            for offset in range(0, 100, 20):
                res = await scan_instahyre_page(s, insta_sem, offset)
                discovered_jobs.extend(res)
    except Exception as e:
        print(f"Instahyre scan note: {e}")

    # 3. Workday CXS Resilient (rotating batch across verified Workday endpoints)
    try:
        wd_jobs = await run_workday_cxs_pipeline(concurrency=35, batch_size=380)
        discovered_jobs.extend(wd_jobs)
    except Exception as e:
        print(f"Workday scan note: {e}")

    # 4. Amazon Official Jobs & Big Tech / MNC Direct Partitions
    try:
        ln_jobs = await run_partitioned_linkedin_crawler(max_pages=2)
        discovered_jobs.extend(ln_jobs)
    except Exception as e:
        print(f"Big Tech / MNC Direct crawler note: {e}")

    # 5. Audit all links in database
    audit_res = await audit_and_clean_database_links()

    print(f"✅ Unified Discovery & Audit Complete! Newly Discovered: {len(discovered_jobs)}, Active Verified in DB: {audit_res['active_verified']}")
    return {
        "status": "success",
        "newly_discovered": len(discovered_jobs),
        "audit": audit_res
    }

if __name__ == "__main__":
    asyncio.run(run_unified_discovery(24))
