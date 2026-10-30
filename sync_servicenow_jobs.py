#!/usr/bin/env python3
"""
Sync ServiceNow Job Postings from SmartRecruiters API to SQLite jobs.db.
Target: India Engineering/Tech roles.
Strictly preserves existing applied/processed statuses.
"""
import os
import sys
import sqlite3
import logging
from datetime import datetime
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_servicenow")

DB_PATH = os.path.join(os.path.dirname(__file__), "jobs.db")
API_URL = "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings?country=in&limit=100"

SWE_KEYWORDS = [
    "software", "engineer", "developer", "sde", "backend", "frontend", "full stack",
    "fullstack", "platform", "systems", "cloud", "infrastructure", "devops", "qa",
    "test", "automation", "security", "data", "machine learning", "ai", "site reliability"
]

def init_db(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS job_postings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT UNIQUE NOT NULL,
            company_name TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT,
            is_remote BOOLEAN DEFAULT 0,
            ats_platform TEXT,
            apply_url TEXT NOT NULL,
            jd_content TEXT,
            posted_at TIMESTAMP,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            experience_required TEXT,
            salary_tier TEXT,
            estimated_ctc TEXT,
            status TEXT DEFAULT 'DISCOVERED',
            apply_attempts INTEGER DEFAULT 0,
            apply_log TEXT,
            screenshot_path TEXT,
            applied_at TIMESTAMP
        )
    """)
    conn.commit()

def fetch_servicenow_jobs():
    logger.info("Fetching ServiceNow jobs from SmartRecruiters API...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    resp = requests.get(API_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("content", [])
    logger.info(f"Retrieved {len(jobs)} postings from ServiceNow API.")
    return jobs

def is_qualified_swe(job):
    title = job.get("name", "").lower()
    dept = (job.get("department") or {}).get("label", "").lower()
    func = (job.get("function") or {}).get("label", "").lower()
    
    # Exclude non-tech / sales / legal / intern if any
    exclude = ["sales representative", "account executive", "recruiter", "talent acquisition", "hr generalist"]
    if any(ex in title for ex in exclude):
        return False
        
    return any(kw in title or kw in dept or kw in func for kw in SWE_KEYWORDS)

def sync_jobs():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    c = conn.cursor()
    
    postings = fetch_servicenow_jobs()
    discovered_count = 0
    updated_count = 0
    skipped_count = 0
    
    for p in postings:
        if not is_qualified_swe(p):
            skipped_count += 1
            continue
            
        raw_id = str(p.get("id"))
        job_id = f"sn_{raw_id}"
        title = p.get("name", "Software Engineer")
        loc_data = p.get("location", {})
        location = loc_data.get("fullLocation") or f"{loc_data.get('city', 'Hyderabad')}, India"
        is_remote = 1 if loc_data.get("remote") else 0
        apply_url = f"https://jobs.smartrecruiters.com/ServiceNow/{raw_id}"
        released_date = p.get("releasedDate")
        
        # Check if exists
        c.execute("SELECT status FROM job_postings WHERE job_id = ?", (job_id,))
        row = c.fetchone()
        
        if row is None:
            c.execute("""
                INSERT INTO job_postings (
                    job_id, company_name, title, location, is_remote, ats_platform,
                    apply_url, jd_content, posted_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED')
            """, (
                job_id, "ServiceNow", title, location, is_remote, "SmartRecruiters",
                apply_url, f"Title: {title} | Location: {location}", released_date
            ))
            discovered_count += 1
        else:
            updated_count += 1
            
    conn.commit()
    conn.close()
    
    logger.info(f"Sync complete. New discovered: {discovered_count}, already tracked: {updated_count}, skipped unqualified: {skipped_count}")

if __name__ == "__main__":
    sync_jobs()
