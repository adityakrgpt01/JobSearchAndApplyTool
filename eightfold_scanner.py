"""
Eightfold.ai Native Enterprise Scanner.
Queries public Eightfold API endpoints for Tier-1 companies (Netflix, Micron, PayPal, Vodafone, Nutanix, etc.)
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

# Verified Eightfold.ai Enterprise Tenancies
EIGHTFOLD_TARGETS = [
    {"name": "Netflix", "tenant": "netflix", "domain": "netflix.com", "career_base": "https://jobs.netflix.com"},
    {"name": "Micron Technology", "tenant": "micron", "domain": "micron.com", "career_base": "https://micron.eightfold.ai"},
    {"name": "PayPal", "tenant": "paypal", "domain": "paypal.com", "career_base": "https://paypal.eightfold.ai"},
    {"name": "Vodafone", "tenant": "vodafone", "domain": "vodafone.com", "career_base": "https://vodafone.eightfold.ai"},
    {"name": "Nutanix", "tenant": "nutanix", "domain": "nutanix.com", "career_base": "https://nutanix.eightfold.ai"},
    {"name": "Capital One", "tenant": "capitalone", "domain": "capitalone.com", "career_base": "https://capitalone.eightfold.ai"},
    {"name": "Bayer", "tenant": "bayer", "domain": "bayer.com", "career_base": "https://bayer.eightfold.ai"},
    {"name": "Chevron", "tenant": "chevron", "domain": "chevron.com", "career_base": "https://chevron.eightfold.ai"}
]

def is_backend_role(title: str, jd_text: str = "") -> bool:
    if not is_qualified_seniority_and_exp(title, jd_text):
        return False
    t = title.lower()
    if any(neg in t for neg in DISALLOWED_KEYWORDS):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

async def scan_single_eightfold_tenant(session: aiohttp.ClientSession, comp: Dict[str, str], sem: asyncio.Semaphore) -> List[Dict[str, Any]]:
    name = comp["name"]
    tenant = comp["tenant"]
    domain = comp["domain"]
    career_base = comp.get("career_base", f"https://{tenant}.eightfold.ai")
    
    url = f"https://{tenant}.eightfold.ai/api/apply/v2/jobs"
    params = {
        "domain": domain,
        "start": "0",
        "num": "30",
        "location": "India",
        "query": "Software"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": career_base,
        "Origin": career_base,
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin"
    }

    discovered = []
    async with sem:
        await asyncio.sleep(random.uniform(0.5, 1.5))
        try:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    if resp.status in (403, 429):
                        log_failure("eightfold", name, url, f"Eightfold WAF/Rate-limit (HTTP {resp.status}) - routed to LinkedIn Bridge", resp.status)
                    else:
                        log_failure("eightfold", name, url, f"HTTP {resp.status}", resp.status)
                    return []
                
                resp_headers = getattr(resp, "headers", {})
                ct = resp_headers.get("Content-Type", "").lower() if hasattr(resp_headers, "get") else ""
                if "html" in ct:
                    log_failure("eightfold", name, url, "Unexpected HTML response from Eightfold", 502)
                    return []

                try:
                    data = await resp.json()
                except Exception as e:
                    log_failure("eightfold", name, url, f"Malformed JSON: {e}", 502)
                    return []
                positions = data.get("positions", [])
                
                for pos in positions:
                    title = pos.get("name", "")
                    if not is_backend_role(title):
                        continue
                    
                    locs = pos.get("locations", ["India"])
                    loc_str = locs[0] if isinstance(locs, list) and locs else str(locs)
                    if not is_qualified_location(loc_str):
                        continue
                    
                    pos_id = str(pos.get("id", ""))
                    job_url = pos.get("canonicalPositionUrl") or f"{career_base}/jobs/{pos_id}"
                    job_id = f"eightfold_{tenant}_{pos_id or abs(hash(title))}"
                    
                    tier, est_ctc = classify_salary(name, title)
                    await perform_smart_due_diligence(name)
                    
                    record = {
                        "job_id": job_id,
                        "company_name": name,
                        "title": title,
                        "location": loc_str,
                        "is_remote": "remote" in loc_str.lower(),
                        "ats_platform": "eightfold",
                        "apply_url": job_url,
                        "jd_content": f"{title} at {name}. Location: {loc_str}. Department: {pos.get('department', 'Engineering')}",
                        "posted_at": datetime.now(timezone.utc).isoformat(),
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier,
                        "estimated_ctc": est_ctc,
                        "status": "DISCOVERED"
                    }
                    save_job(record)
                    discovered.append(record)
        except Exception as e:
            log_failure("eightfold", name, url, str(e), 500)
            
    return discovered

async def scan_eightfold_pipeline() -> List[Dict[str, Any]]:
    print("⚡ Firing Eightfold.ai Enterprise Scanner (Netflix, Micron, PayPal, etc.)...")
    sem = asyncio.Semaphore(3)
    conn = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
    all_jobs = []
    
    async with aiohttp.ClientSession(connector=conn) as session:
        tasks = [scan_single_eightfold_tenant(session, comp, sem) for comp in EIGHTFOLD_TARGETS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_jobs.extend(r)
                
    print(f"✅ Eightfold.ai scan complete! Discovered {len(all_jobs)} opportunities.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(scan_eightfold_pipeline())
