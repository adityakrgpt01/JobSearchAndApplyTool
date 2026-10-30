#!/usr/bin/env python3
"""
Sync Booking Holdings Job Postings from official jobs.booking.com API to SQLite jobs.db.
Target: India Center of Excellence roles.
Preserves existing applied/processed statuses.
"""
import os
import sys
import sqlite3
import logging
from datetime import datetime
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_booking")

DB_PATH = os.path.join(os.path.dirname(__file__), "jobs.db")
BASE_API = "https://jobs.booking.com/api/jobs?location=India"

SWE_KEYWORDS = [
    "software", "engineer", "developer", "sde", "backend", "frontend", "full stack",
    "fullstack", "platform", "systems", "cloud", "infrastructure", "devops", "qa",
    "test", "automation", "security", "data", "machine learning", "ai", "site reliability",
    "architect", "tech", "technology", "support technician", "analyst"
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

def fetch_booking_jobs():
    logger.info("Fetching Booking Holdings jobs from jobs.booking.com API...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    all_jobs = []
    page = 1
    while True:
        url = f"{BASE_API}&page={page}"
        logger.info(f"Querying page {page}...")
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            break
        data = resp.json()
        jobs = data.get("jobs", [])
        if not jobs:
            break
        all_jobs.extend(jobs)
        total_count = data.get("totalCount", len(all_jobs))
        if len(all_jobs) >= total_count or len(jobs) == 0:
            break
        page += 1
        
    logger.info(f"Retrieved {len(all_jobs)} total postings from Booking Holdings.")
    return all_jobs

def is_qualified(job):
    data = job.get("data", {})
    title = data.get("title", "").lower()
    categories = [str(c).lower() for c in data.get("category", [])]
    
    # Exclude non-tech pure accountant/HR unless requested
    return any(kw in title for kw in SWE_KEYWORDS) or any(any(kw in cat for kw in SWE_KEYWORDS) for cat in categories)

def sync_jobs():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    c = conn.cursor()
    
    postings = fetch_booking_jobs()
    discovered_count = 0
    updated_count = 0
    skipped_count = 0
    
    for p in postings:
        data = p.get("data", {})
        raw_id = str(data.get("req_id") or data.get("slug"))
        job_id = f"bk_{raw_id}"
        title = data.get("title", "Engineer")
        location = data.get("full_location") or data.get("short_location") or "Bangalore, India"
        is_remote = 1 if "remote" in location.lower() else 0
        apply_url = data.get("apply_url") or f"https://careers-holdings-workingatbooking.icims.com/jobs/{raw_id}/login"
        posted_at = data.get("posted_date") or data.get("create_date")
        desc = (data.get("description") or "")[:500]
        
        c.execute("SELECT status FROM job_postings WHERE job_id = ?", (job_id,))
        row = c.fetchone()
        
        if row is None:
            c.execute("""
                INSERT INTO job_postings (
                    job_id, company_name, title, location, is_remote, ats_platform,
                    apply_url, jd_content, posted_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED')
            """, (
                job_id, "Booking Holdings", title, location, is_remote, "iCIMS",
                apply_url, f"Title: {title} | Location: {location} | Desc: {desc}", posted_at
            ))
            discovered_count += 1
        else:
            updated_count += 1
            
    conn.commit()
    conn.close()
    logger.info(f"Booking Sync complete. New discovered: {discovered_count}, already tracked: {updated_count}, total processed: {len(postings)}")

if __name__ == "__main__":
    sync_jobs()
