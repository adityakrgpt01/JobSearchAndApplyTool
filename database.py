import sqlite3
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

DB_PATH = "jobs.db"

def init_db(db_path: str = DB_PATH):
    """Initializes the SQLite database with decoupled tables."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Company Due Diligence & Intelligence Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS companies_intelligence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT UNIQUE NOT NULL,
        normalized_name TEXT UNIQUE NOT NULL,
        domain TEXT,
        glassdoor_rating REAL,
        ambitionbox_rating REAL,
        engineering_wlb_score REAL,
        culture_summary TEXT,
        headcount_range TEXT,
        stage_or_type TEXT,
        has_recent_layoffs BOOLEAN DEFAULT 0,
        layoffs_details TEXT,
        risk_level TEXT, -- LOW, MODERATE, HIGH
        salary_benchmark_tier TEXT, -- 40-50LPA, 50-60LPA, 60-70LPA, 70+LPA
        raw_metadata TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Discovered Job Postings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS job_postings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT UNIQUE NOT NULL, -- e.g. greenhouse:12345 or hash
        company_name TEXT NOT NULL,
        title TEXT NOT NULL,
        location TEXT,
        is_remote BOOLEAN DEFAULT 0,
        ats_platform TEXT, -- greenhouse, lever, ashby, workday, linkedin, instahyre, amazon
        apply_url TEXT NOT NULL,
        jd_content TEXT,
        posted_at TIMESTAMP,
        discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        experience_required TEXT,
        salary_tier TEXT, -- 40-50LPA, 50-60LPA, 60-70LPA, 70+LPA
        estimated_ctc TEXT,
        status TEXT DEFAULT 'DISCOVERED', -- DISCOVERED, QUEUED, APPLYING, APPLIED, FAILED, SKIPPED
        apply_attempts INTEGER DEFAULT 0,
        apply_log TEXT,
        screenshot_path TEXT,
        FOREIGN KEY (company_name) REFERENCES companies_intelligence(normalized_name)
    );
    """)

    # 3. Application Logs & History Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS application_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        action TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 4. Scraper Failures Tracking Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scraper_failures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        company_name TEXT NOT NULL,
        target_url TEXT NOT NULL,
        failure_reason TEXT NOT NULL,
        http_status INTEGER,
        retry_count INTEGER DEFAULT 0,
        resolved BOOLEAN DEFAULT 0,
        last_attempted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()

def save_company_intelligence(data: Dict[str, Any], db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    norm = data["company_name"].strip().lower()
    cursor.execute("""
    INSERT INTO companies_intelligence (
        company_name, normalized_name, domain, glassdoor_rating, ambitionbox_rating,
        engineering_wlb_score, culture_summary, headcount_range, stage_or_type,
        has_recent_layoffs, layoffs_details, risk_level, salary_benchmark_tier, raw_metadata, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(normalized_name) DO UPDATE SET
        glassdoor_rating=excluded.glassdoor_rating,
        ambitionbox_rating=excluded.ambitionbox_rating,
        engineering_wlb_score=excluded.engineering_wlb_score,
        culture_summary=excluded.culture_summary,
        headcount_range=excluded.headcount_range,
        stage_or_type=excluded.stage_or_type,
        has_recent_layoffs=excluded.has_recent_layoffs,
        layoffs_details=excluded.layoffs_details,
        risk_level=excluded.risk_level,
        salary_benchmark_tier=excluded.salary_benchmark_tier,
        raw_metadata=excluded.raw_metadata,
        updated_at=CURRENT_TIMESTAMP;
    """, (
        data["company_name"],
        norm,
        data.get("domain", ""),
        data.get("glassdoor_rating"),
        data.get("ambitionbox_rating"),
        data.get("engineering_wlb_score"),
        data.get("culture_summary", ""),
        data.get("headcount_range", ""),
        data.get("stage_or_type", ""),
        1 if data.get("has_recent_layoffs") else 0,
        data.get("layoffs_details", ""),
        data.get("risk_level", "LOW"),
        data.get("salary_benchmark_tier", "50-60LPA"),
        json.dumps(data.get("raw_metadata", {}))
    ))
    conn.commit()
    conn.close()

def get_company_intelligence(company_name: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    norm = company_name.strip().lower()
    cursor.execute("SELECT * FROM companies_intelligence WHERE normalized_name = ?", (norm,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def save_job(job: Dict[str, Any], db_path: str = DB_PATH) -> bool:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO job_postings (
            job_id, company_name, title, location, is_remote, ats_platform,
            apply_url, jd_content, posted_at, experience_required, salary_tier, estimated_ctc, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO NOTHING;
        """, (
            job["job_id"],
            job["company_name"],
            job["title"],
            job.get("location", "India"),
            1 if job.get("is_remote") else 0,
            job.get("ats_platform", "unknown"),
            job["apply_url"],
            job.get("jd_content", ""),
            job.get("posted_at"),
            job.get("experience_required", "~5 YoE"),
            job.get("salary_tier", "50-60LPA"),
            job.get("estimated_ctc", ""),
            job.get("status", "DISCOVERED")
        ))
        conn.commit()
        inserted = cursor.rowcount > 0
        conn.close()
        return inserted
    except Exception as e:
        conn.close()
        print(f"Error saving job: {e}")
        return False

def parse_posted_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    val = str(val).strip()
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    try:
        return datetime.strptime(val, "%B %d, %Y").replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None

def is_within_age(posted_at: Optional[str], discovered_at: Optional[str], max_age_hours: Optional[int]) -> bool:
    if not max_age_hours or max_age_hours <= 0:
        return True
    
    # 1. Check relative strings
    if posted_at:
        p_lower = str(posted_at).lower().strip()
        if any(w in p_lower for w in ["today", "hour", "minute", "just now"]):
            return True
        if any(w in p_lower for w in ["yesterday", "1 day ago"]):
            return max_age_hours >= 24
        m = re.search(r"(\d+)\s+days?\s+ago", p_lower)
        if m:
            days = int(m.group(1))
            return max_age_hours >= (days * 24)
            
        dt = parse_posted_dt(posted_at)
        if dt:
            now = datetime.now(timezone.utc)
            diff_hours = (now - dt).total_seconds() / 3600.0
            return diff_hours <= max_age_hours

    # 2. Fallback to discovered_at only if posted_at was absent
    if not posted_at and discovered_at:
        dt = parse_posted_dt(discovered_at)
        if dt:
            now = datetime.now(timezone.utc)
            diff_hours = (now - dt).total_seconds() / 3600.0
            return diff_hours <= max_age_hours

    return False

def list_jobs(
    status: Optional[str] = None,
    salary_tier: Optional[str] = None,
    platform: Optional[str] = None,
    max_age_hours: Optional[int] = None,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
    SELECT j.*, 
           c.glassdoor_rating, c.ambitionbox_rating, c.engineering_wlb_score,
           c.culture_summary, c.headcount_range, c.has_recent_layoffs,
           c.layoffs_details, c.risk_level
    FROM job_postings j
    LEFT JOIN companies_intelligence c ON LOWER(TRIM(j.company_name)) = c.normalized_name
    WHERE 1=1
    """
    params = []
    if status:
        query += " AND j.status = ?"
        params.append(status)
    if salary_tier:
        query += " AND j.salary_tier = ?"
        params.append(salary_tier)
    if platform and platform != "all":
        query += " AND j.ats_platform = ?"
        params.append(platform)

    query += " ORDER BY j.posted_at DESC, j.discovered_at DESC"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if max_age_hours and max_age_hours > 0:
        rows = [r for r in rows if is_within_age(r.get("posted_at"), r.get("discovered_at"), max_age_hours)]

    return rows

def update_job_status(job_id: str, status: str, apply_log: str = "", screenshot_path: str = "", db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE job_postings 
    SET status = ?, apply_log = ?, screenshot_path = ?, apply_attempts = apply_attempts + 1
    WHERE job_id = ?;
    """, (status, apply_log, screenshot_path, job_id))
    cursor.execute("""
    INSERT INTO application_logs (job_id, action, status, message)
    VALUES (?, 'UPDATE_STATUS', ?, ?)
    """, (job_id, status, apply_log))
    conn.commit()
    conn.close()

def log_failure(source: str, company_name: str, target_url: str, failure_reason: str, http_status: int = 0, db_path: str = DB_PATH):
    """Logs an endpoint or URL error into the scraper_failures dead-letter queue."""
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("""
        INSERT INTO scraper_failures (source, company_name, target_url, failure_reason, http_status, retry_count, last_attempted)
        VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """, (source, company_name, target_url, failure_reason, http_status))
        conn.commit()
        conn.close()
    except Exception:
        pass

def mark_failure_resolved(target_url: str, db_path: str = DB_PATH):
    """Marks an endpoint failure as resolved after a successful retry."""
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("UPDATE scraper_failures SET resolved = 1 WHERE target_url = ?", (target_url,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_unresolved_failures(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Fetches all active failures that need retry."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
    SELECT source, company_name, target_url, failure_reason, http_status, retry_count, last_attempted
    FROM scraper_failures
    WHERE resolved = 0 AND retry_count < 5
    ORDER BY last_attempted DESC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_failures_summary(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Summary of scraper errors for health monitoring."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scraper_failures WHERE resolved = 0")
    unresolved = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM scraper_failures WHERE resolved = 1")
    resolved = c.fetchone()[0]
    c.execute("SELECT source, COUNT(*) FROM scraper_failures WHERE resolved = 0 GROUP BY source")
    by_source = dict(c.fetchall())
    conn.close()
    return {"unresolved_count": unresolved, "resolved_count": resolved, "by_source": by_source}

if __name__ == "__main__":
    init_db()
