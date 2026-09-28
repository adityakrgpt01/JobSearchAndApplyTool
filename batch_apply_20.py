#!/usr/bin/env python3
import asyncio
import os
import sys
import sqlite3
from typing import List, Dict, Any
from stealth_applier import StealthApplier

def get_target_jobs(limit: int = 20) -> List[Dict[str, Any]]:
    conn = sqlite3.connect("jobs.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Pick distinct companies across greenhouse and ashby
    query = """
        SELECT job_id, company_name, title, ats_platform, apply_url, status
        FROM job_postings
        WHERE status NOT LIKE '%APPLIED%'
          AND apply_url IS NOT NULL
          AND ats_platform IN ('greenhouse', 'ashby')
          AND apply_url LIKE 'http%'
        GROUP BY company_name
        ORDER BY 
          CASE WHEN ats_platform = 'greenhouse' THEN 1 ELSE 2 END,
          job_id ASC
        LIMIT ?
    """
    rows = c.execute(query, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

async def run_batch():
    is_dry_run = os.environ.get("DRY_RUN", "false").lower() in ("true", "1", "yes")
    print(f"=== Starting Batch Apply (dry_run={is_dry_run}) ===", flush=True)
    
    targets = get_target_jobs(20)
    print(f"Selected {len(targets)} distinct company targets:", flush=True)
    for idx, t in enumerate(targets, 1):
        print(f"  {idx}. [{t['ats_platform'].upper()}] {t['company_name']} - {t['title']}", flush=True)

    applier = StealthApplier(dry_run=is_dry_run)
    results = []
    
    for idx, job in enumerate(targets, 1):
        print(f"\n--- [{idx}/{len(targets)}] Applying: {job['company_name']} ({job['ats_platform']}) ---", flush=True)
        print(f"URL: {job['apply_url']}", flush=True)
        try:
            res = await applier.apply_to_job(job)
            status_val = res.get("status") or "UNKNOWN"
            is_applied = "APPLIED" in status_val
            results.append({
                "job_id": job["job_id"],
                "company": job["company_name"],
                "platform": job["ats_platform"],
                "success": is_applied,
                "status": status_val,
                "message": res.get("message") or "",
                "screenshot": res.get("screenshot") or ""
            })
            print(f"Result [{job['company_name']}]: status={status_val}, message={res.get('message', '')[:70]}", flush=True)
        except Exception as e:
            print(f"Exception on {job['company_name']}: {e}", flush=True)
            results.append({
                "job_id": job["job_id"],
                "company": job["company_name"],
                "platform": job["ats_platform"],
                "success": False,
                "status": "ERROR",
                "message": str(e),
                "screenshot": None
            })
            
        # Midpoint validation & status check every 5 applications
        if idx % 5 == 0 or idx == len(targets):
            print(f"\n>>> MIDPOINT CHECK AT {idx}/{len(targets)} <<<", flush=True)
            applied_cnt = sum(1 for r in results if "APPLIED" in r["status"])
            review_cnt = sum(1 for r in results if "READY" in r["status"] or "REVIEW" in r["status"])
            failed_cnt = sum(1 for r in results if "FAIL" in r["status"] or "ERROR" in r["status"])
            print(f"Progress so far: {applied_cnt} applied, {review_cnt} ready/review, {failed_cnt} failed out of {len(results)} processed.", flush=True)
            for r in results[-5:]:
                msg_snippet = (r.get("message") or "")[:50]
                print(f"  * {r['company']} ({r['platform']}): {r['status']} [{msg_snippet}]", flush=True)

    print("\n================ FINAL BATCH SUMMARY ================", flush=True)
    for r in results:
        status_flag = "✅ APPLIED" if "APPLIED" in r["status"] else ("📋 READY/REVIEW" if "READY" in r["status"] else f"⚠️ {r['status']}")
        print(f"{status_flag} | {r['company']} ({r['platform']}) | Screenshot: {r.get('screenshot')}", flush=True)

if __name__ == "__main__":
    asyncio.run(run_batch())
