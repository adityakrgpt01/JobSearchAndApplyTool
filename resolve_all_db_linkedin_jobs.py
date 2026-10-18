"""
Batch resolution script for existing LinkedIn URLs in jobs.db.
Probes all 1,985+ LinkedIn jobs in parallel batches to extract offsite external ATS links
(Greenhouse, Workday, Ashby, Lever, SmartRecruiters).
Updates job_id, apply_url, ats_platform, and posted_at in SQLite.
"""

import sqlite3
import asyncio
import aiohttp
import re
from datetime import datetime, timezone

DB_PATH = "jobs.db"

async def resolve_record(session: aiohttp.ClientSession, record: dict) -> dict:
    url = record.get("apply_url", "")
    if not url or "linkedin.com" not in url:
        return record

    num_id = url.split("-")[-1].split("/")[0] if "-" in url else ""
    if not num_id.isdigit():
        return record

    detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{num_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    }

    try:
        async with session.get(detail_url, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status == 200:
                html = await resp.text()
                urls = re.findall(r'https?://[^\s"\'<>]+', html)
                norm_company = re.sub(r'[^a-z0-9]', '', record.get("company_name", "").lower())

                for u in urls:
                    u_clean = u.split("?")[0].rstrip("/")
                    u_low = u_clean.lower()
                    if "linkedin.com" in u_low or "licdn.com" in u_low:
                        continue

                    # Greenhouse
                    if "greenhouse.io" in u_low:
                        m = re.search(r'jobs/(\d+)', u_clean)
                        jid = f"gh_{norm_company}_{m.group(1)}" if m else f"gh_{norm_company}_{num_id}"
                        record["new_apply_url"] = u_clean
                        record["new_platform"] = "greenhouse"
                        record["new_job_id"] = jid
                        if m:
                            try:
                                gh_api = f"https://boards-api.greenhouse.io/v1/boards/{norm_company}/jobs/{m.group(1)}"
                                async with session.get(gh_api, timeout=aiohttp.ClientTimeout(total=2)) as gh_resp:
                                    if gh_resp.status == 200:
                                        gh_data = await gh_resp.json()
                                        if gh_data.get("updated_at"):
                                            record["new_posted_at"] = gh_data["updated_at"]
                            except Exception:
                                pass
                        break

                    # Ashby
                    elif "ashbyhq.com" in u_low:
                        m = re.search(r'ashbyhq\.com/[^/]+/([a-f0-9\-]+)', u_clean)
                        jid = f"ashby_{norm_company}_{m.group(1)}" if m else f"ashby_{norm_company}_{num_id}"
                        record["new_apply_url"] = u_clean
                        record["new_platform"] = "ashby"
                        record["new_job_id"] = jid
                        break

                    # Workday
                    elif "myworkdayjobs.com" in u_low:
                        jid = f"wd_{norm_company}_{abs(hash(u_clean))}"
                        record["new_apply_url"] = u_clean
                        record["new_platform"] = "workday"
                        record["new_job_id"] = jid
                        break

                    # Lever
                    elif "lever.co" in u_low:
                        m = re.search(r'lever\.co/[^/]+/([a-f0-9\-]+)', u_clean)
                        jid = f"lever_{norm_company}_{m.group(1)}" if m else f"lever_{norm_company}_{num_id}"
                        record["new_apply_url"] = u_clean
                        record["new_platform"] = "lever"
                        record["new_job_id"] = jid
                        if m:
                            try:
                                lev_api = f"https://api.lever.co/v0/postings/{norm_company}/{m.group(1)}"
                                async with session.get(lev_api, timeout=aiohttp.ClientTimeout(total=2)) as lev_resp:
                                    if lev_resp.status == 200:
                                        lev_data = await lev_resp.json()
                                        if lev_data.get("createdAt"):
                                            record["new_posted_at"] = datetime.fromtimestamp(lev_data["createdAt"]/1000, tz=timezone.utc).isoformat()
                            except Exception:
                                pass
                        break

                    # SmartRecruiters
                    elif "smartrecruiters.com" in u_low:
                        m = re.search(r'smartrecruiters\.com/[^/]+/([a-f0-9\-]+)', u_clean)
                        jid = f"sr_{norm_company}_{m.group(1)}" if m else f"sr_{norm_company}_{num_id}"
                        record["new_apply_url"] = u_clean
                        record["new_platform"] = "smartrecruiters"
                        record["new_job_id"] = jid
                        break

                    # Company Career Portals (e.g. ebayinc.com, amazon.jobs, etc.)
                    elif any(domain in u_low for domain in ["ebayinc.com", "amazon.jobs", "careers.", "jobs."]):
                        m_code = re.search(r'/job/([A-Z0-9\-]+)', u_clean)
                        job_code = m_code.group(1) if m_code else str(abs(hash(u_clean)))
                        record["new_apply_url"] = u_clean
                        record["new_platform"] = norm_company or "portal"
                        record["new_job_id"] = f"{norm_company}_{job_code}"
                        break
    except Exception:
        pass

    return record

async def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT job_id, company_name, title, apply_url, posted_at FROM job_postings WHERE ats_platform = 'linkedin'")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    total = len(rows)
    print(f"🚀 Found {total} LinkedIn job records in database to resolve...")

    resolved_count = 0
    retained_count = 0
    platform_stats = {}

    batch_size = 25
    conn_aio = aiohttp.TCPConnector(limit=25, ttl_dns_cache=300)

    async with aiohttp.ClientSession(connector=conn_aio) as session:
        for i in range(0, total, batch_size):
            chunk = rows[i:i + batch_size]
            tasks = [resolve_record(session, r) for r in chunk]
            results = await asyncio.gather(*tasks)

            db_updates = []
            for r in results:
                if "new_apply_url" in r:
                    resolved_count += 1
                    plat = r["new_platform"]
                    platform_stats[plat] = platform_stats.get(plat, 0) + 1
                    db_updates.append((
                        r["new_job_id"],
                        r["new_apply_url"],
                        r["new_platform"],
                        r.get("new_posted_at", r["posted_at"]),
                        r["job_id"]
                    ))
                else:
                    retained_count += 1

            if db_updates:
                conn_up = sqlite3.connect(DB_PATH)
                c_up = conn_up.cursor()
                for new_jid, new_url, new_plat, new_posted, old_jid in db_updates:
                    c_up.execute("""
                        UPDATE job_postings 
                        SET job_id = ?, apply_url = ?, ats_platform = ?, posted_at = ?
                        WHERE job_id = ?
                    """, (new_jid, new_url, new_plat, new_posted, old_jid))
                conn_up.commit()
                conn_up.close()

            print(f"  Processed {min(i + batch_size, total)}/{total} | Resolved to ATS: {resolved_count} | Retained LinkedIn: {retained_count}")

    print("\n✅ Resolution Complete!")
    print(f"• Total Processed: {total}")
    print(f"• Resolved to External ATS: {resolved_count}")
    print(f"• Retained Native LinkedIn/Easy Apply: {retained_count}")
    for p, cnt in platform_stats.items():
        print(f"   - {p.upper()}: {cnt}")

if __name__ == "__main__":
    asyncio.run(main())
