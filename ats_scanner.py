"""
Unified Multi-ATS High-Throughput Scanner.
Scans across 2,000+ companies hosted on:
- Greenhouse (boards-api.greenhouse.io)
- Lever (api.lever.co)
- Ashby (api.ashbyhq.com)
Filters strictly for Senior Backend / SDE-2 (4-6 YoE), India/Remote eligibility,
and evaluates jobs posted within the last 72 hours.
"""

import aiohttp
import asyncio
import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Any
from database import save_job, get_company_intelligence, log_failure
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

from experience_filter import is_qualified_seniority_and_exp

BACKEND_KEYWORDS = [
    "backend", "back end", "back-end", "distributed systems", "sde 2", "sde-2",
    "sde ii", "software engineer ii", "senior software engineer", "software development engineer ii"
]

def is_senior_backend_or_sde2(title: str, jd_text: str = "") -> bool:
    if not is_qualified_seniority_and_exp(title, jd_text):
        return False
    t = title.lower()
    if any(neg in t for neg in ["frontend", "front-end", "front end", "android", "ios", "qa tester", "sdet", "intern", "director", "manager"]):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

def load_all_target_companies() -> Dict[str, List[Dict[str, str]]]:
    """Loads all companies grouped by ATS: greenhouse, lever, ashby, including top 550 MNCs."""
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("SELECT company_name, normalized_name, raw_metadata FROM companies_intelligence")
    rows = c.fetchall()

    grouped = {"greenhouse": [], "lever": [], "ashby": []}
    seen = {"greenhouse": set(), "lever": set(), "ashby": set()}

    for name, norm, meta in rows:
        if norm.startswith("lever_"):
            tok = norm.replace("lever_", "")
            grouped["lever"].append({"name": name.replace(" (Lever)", ""), "token": tok})
            seen["lever"].add(tok)
        elif norm.startswith("ashby_"):
            tok = norm.replace("ashby_", "")
            grouped["ashby"].append({"name": name.replace(" (Ashby)", ""), "token": tok})
            seen["ashby"].add(tok)
        else:
            grouped["greenhouse"].append({"name": name, "token": norm})
            seen["greenhouse"].add(norm)

    # Seamlessly inject all 550 MNC directory companies with ATS endpoints
    c.execute("SELECT company_name, direct_career_url, ats_platform FROM mnc_directory")
    import re
    for cname, url, plat in c.fetchall():
        if plat == "greenhouse":
            m = re.search(r'greenhouse\.io/([^/]+)', url)
            if m:
                tok = m.group(1).strip()
                if tok not in seen["greenhouse"]:
                    grouped["greenhouse"].insert(0, {"name": cname, "token": tok})
                    seen["greenhouse"].add(tok)
        elif plat == "lever":
            m = re.search(r'lever\.co/([^/]+)', url)
            if m:
                tok = m.group(1).strip()
                if tok not in seen["lever"]:
                    grouped["lever"].insert(0, {"name": cname, "token": tok})
                    seen["lever"].add(tok)
        elif plat == "ashby":
            m = re.search(r'ashbyhq\.com/([^/]+)', url)
            if m:
                tok = m.group(1).strip()
                if tok not in seen["ashby"]:
                    grouped["ashby"].insert(0, {"name": cname, "token": tok})
                    seen["ashby"].add(tok)

    conn.close()
    return grouped

# --- 1. Greenhouse Scanner ---
async def scan_greenhouse_job(session: aiohttp.ClientSession, comp: Dict[str, str], sem: asyncio.Semaphore, max_age_hours: int = 72) -> List[Dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{comp['token']}/jobs?content=true"
    discovered = []
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    log_failure("greenhouse", comp["name"], url, f"HTTP {resp.status}", resp.status)
                    return []
                data = await resp.json()
                jobs = data.get("jobs", [])
                now = datetime.now(timezone.utc)
                for j in jobs:
                    title = j.get("title", "")
                    if not is_senior_backend_or_sde2(title):
                        continue
                    loc = j.get("location", {}).get("name", "Remote / Multiple")
                    loc_low = loc.lower()
                    if not ("india" in loc_low or "bengaluru" in loc_low or "bangalore" in loc_low or "remote" in loc_low):
                        continue
                    updated_at_str = j.get("updated_at")
                    if updated_at_str:
                        try:
                            updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                            if (now - updated_at).total_seconds() > max_age_hours * 3600:
                                continue
                        except Exception:
                            pass

                    tier, est_ctc = classify_salary(comp["name"], j.get("content", ""))
                    await perform_smart_due_diligence(comp["name"], comp["token"])
                    rec = {
                        "job_id": f"gh_{comp['token']}_{j['id']}",
                        "company_name": comp["name"],
                        "title": title, "location": loc, "is_remote": "remote" in loc_low,
                        "ats_platform": "greenhouse", "apply_url": j.get("absolute_url", ""),
                        "jd_content": j.get("content", "")[:2000],
                        "posted_at": updated_at_str or datetime.utcnow().isoformat(),
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier, "estimated_ctc": est_ctc, "status": "DISCOVERED"
                    }
                    save_job(rec)
                    discovered.append(rec)
        except Exception:
            pass
    return discovered

# --- 2. Lever Scanner ---
async def scan_lever_job(session: aiohttp.ClientSession, comp: Dict[str, str], sem: asyncio.Semaphore, max_age_hours: int = 72) -> List[Dict[str, Any]]:
    url = f"https://api.lever.co/v0/postings/{comp['token']}?mode=json"
    discovered = []
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    log_failure("lever", comp["name"], url, f"HTTP {resp.status}", resp.status)
                    return []
                jobs = await resp.json()
                now = datetime.now(timezone.utc)
                for j in jobs:
                    title = j.get("text", "")
                    if not is_senior_backend_or_sde2(title):
                        continue
                    loc = j.get("categories", {}).get("location", "Remote")
                    loc_low = loc.lower()
                    if not ("india" in loc_low or "bengaluru" in loc_low or "bangalore" in loc_low or "remote" in loc_low):
                        continue
                    created_at_ms = j.get("createdAt")
                    if created_at_ms:
                        created_at = datetime.fromtimestamp(created_at_ms / 1000.0, timezone.utc)
                        if (now - created_at).total_seconds() > max_age_hours * 3600:
                            continue

                    tier, est_ctc = classify_salary(comp["name"], j.get("descriptionPlain", ""))
                    await perform_smart_due_diligence(comp["name"], f"lever_{comp['token']}")
                    rec = {
                        "job_id": f"lever_{comp['token']}_{j['id']}",
                        "company_name": comp["name"],
                        "title": title, "location": loc, "is_remote": "remote" in loc_low,
                        "ats_platform": "lever", "apply_url": j.get("applyUrl", ""),
                        "jd_content": j.get("descriptionPlain", "")[:2000],
                        "posted_at": datetime.fromtimestamp(created_at_ms / 1000.0).isoformat() if created_at_ms else datetime.utcnow().isoformat(),
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier, "estimated_ctc": est_ctc, "status": "DISCOVERED"
                    }
                    save_job(rec)
                    discovered.append(rec)
        except Exception:
            pass
    return discovered

# --- 3. Ashby Scanner ---
async def scan_ashby_job(session: aiohttp.ClientSession, comp: Dict[str, str], sem: asyncio.Semaphore, max_age_hours: int = 72) -> List[Dict[str, Any]]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{comp['token']}"
    discovered = []
    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    log_failure("ashby", comp["name"], url, f"HTTP {resp.status}", resp.status)
                    return []
                data = await resp.json()
                jobs = data.get("jobs", [])
                now = datetime.now(timezone.utc)
                for j in jobs:
                    title = j.get("title", "")
                    if not is_senior_backend_or_sde2(title):
                        continue
                    loc = j.get("location", "Remote")
                    loc_low = loc.lower()
                    if not ("india" in loc_low or "bengaluru" in loc_low or "bangalore" in loc_low or "remote" in loc_low):
                        continue
                    published_str = j.get("publishedDate")
                    if published_str:
                        try:
                            published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                            if (now - published).total_seconds() > max_age_hours * 3600:
                                continue
                        except Exception:
                            pass

                    # Direct salary detection from Ashby JSON!
                    comp_summary = j.get("compensation", {}).get("compensationTierSummary", "")
                    tier, est_ctc = classify_salary(comp["name"], comp_summary or title)
                    if comp_summary:
                        est_ctc = f"{comp_summary} (Disclosed)"

                    await perform_smart_due_diligence(comp["name"], f"ashby_{comp['token']}")
                    rec = {
                        "job_id": f"ashby_{comp['token']}_{j['id']}",
                        "company_name": comp["name"],
                        "title": title, "location": loc, "is_remote": "remote" in loc_low,
                        "ats_platform": "ashby", "apply_url": j.get("jobUrl", ""),
                        "jd_content": comp_summary or title,
                        "posted_at": published_str or datetime.utcnow().isoformat(),
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier, "estimated_ctc": est_ctc, "status": "DISCOVERED"
                    }
                    save_job(rec)
                    discovered.append(rec)
        except Exception:
            pass
    return discovered

async def run_discovery_pipeline(hours: int = 72, concurrency: int = 60) -> List[Dict[str, Any]]:
    grouped = load_all_target_companies()
    total_comps = len(grouped["greenhouse"]) + len(grouped["lever"]) + len(grouped["ashby"])
    print(f"🚀 Scanning {total_comps} companies across Greenhouse ({len(grouped['greenhouse'])}), Lever ({len(grouped['lever'])}), & Ashby ({len(grouped['ashby'])}) (Concurrency: {concurrency})...")

    sem = asyncio.Semaphore(concurrency)
    conn = aiohttp.TCPConnector(limit=concurrency + 10, ttl_dns_cache=300)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

    all_jobs = []
    async with aiohttp.ClientSession(connector=conn, headers=headers) as session:
        tasks = []
        for c in grouped["greenhouse"]:
            tasks.append(scan_greenhouse_job(session, c, sem, hours))
        for c in grouped["lever"]:
            tasks.append(scan_lever_job(session, c, sem, hours))
        for c in grouped["ashby"]:
            tasks.append(scan_ashby_job(session, c, sem, hours))

        results = await asyncio.gather(*tasks)
        for r in results:
            all_jobs.extend(r)

    print(f"✅ Multi-ATS scan complete! Discovered {len(all_jobs)} eligible Senior Backend / SDE-2 opportunities across all 3 platforms.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_discovery_pipeline(72, concurrency=60))
