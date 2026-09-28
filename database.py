import sqlite3
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from experience_filter import is_qualified_seniority_and_exp

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
        target_url TEXT UNIQUE NOT NULL,
        failure_reason TEXT NOT NULL,
        http_status INTEGER,
        retry_count INTEGER DEFAULT 0,
        resolved BOOLEAN DEFAULT 0,
        last_attempted TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_scraper_failures_url ON scraper_failures(target_url);")

    # 5. Portal Credentials & Accounts Table (Workday, Taleo, Oracle, etc.)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS portal_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        portal_domain TEXT UNIQUE NOT NULL, -- e.g. sglottery.wd5.myworkdayjobs.com
        company_name TEXT NOT NULL,
        email TEXT NOT NULL,
        password TEXT NOT NULL,
        status TEXT DEFAULT 'ACTIVE', -- ACTIVE, LOCKED, VERIFIED
        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_portal_accounts_domain ON portal_accounts(portal_domain);")

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
        ON CONFLICT(job_id) DO UPDATE SET
            posted_at = excluded.posted_at,
            apply_url = excluded.apply_url,
            location = excluded.location;
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
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
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

def compute_job_exact_age_hours(posted_at: Optional[str], discovered_at: Optional[str]) -> float:
    now = datetime.now(timezone.utc)
    
    if posted_at:
        # 1. Absolute ISO / Date string
        dt = parse_posted_dt(posted_at)
        if dt:
            return max(0.0, (now - dt).total_seconds() / 3600.0)
            
        # 2. Relative string: calculate offset + real time elapsed since discovery
        p_lower = str(posted_at).lower().strip()
        disc_dt = parse_posted_dt(discovered_at)
        elapsed_since_scrape = max(0.0, (now - disc_dt).total_seconds() / 3600.0) if disc_dt else 0.0
        
        if any(w in p_lower for w in ["just now", "minute"]):
            return elapsed_since_scrape
        m_hr = re.search(r"(\d+)\s+hour", p_lower)
        if m_hr:
            return float(m_hr.group(1)) + elapsed_since_scrape
        if "today" in p_lower:
            return 4.0 + elapsed_since_scrape
        if "yesterday" in p_lower or "1 day ago" in p_lower:
            return 24.0 + elapsed_since_scrape
        if "2 days ago" in p_lower:
            return 48.0 + elapsed_since_scrape
        m_days = re.search(r"(\d+)\s+days?\s+ago", p_lower)
        if m_days:
            return (float(m_days.group(1)) * 24.0) + elapsed_since_scrape
            
    if discovered_at:
        disc_dt = parse_posted_dt(discovered_at)
        if disc_dt:
            return max(0.0, (now - disc_dt).total_seconds() / 3600.0)
            
    return 999999.0

def is_within_age(
    posted_at: Optional[str],
    discovered_at: Optional[str],
    max_age_hours: Optional[float] = None,
    min_age_hours: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> bool:
    age_hours = compute_job_exact_age_hours(posted_at, discovered_at)
    
    if max_age_hours is not None and max_age_hours > 0:
        if age_hours > float(max_age_hours):
            return False
            
    if min_age_hours is not None and min_age_hours > 0:
        if age_hours < float(min_age_hours):
            return False

    if start_date or end_date:
        now = datetime.now(timezone.utc)
        job_sort_dt = now - timedelta(hours=age_hours)
        job_date_str = job_sort_dt.strftime("%Y-%m-%d")
        if start_date and job_date_str < start_date:
            return False
        if end_date and job_date_str > end_date:
            return False

    return True

def get_job_sort_timestamp(row: Dict[str, Any]) -> float:
    now = datetime.now(timezone.utc)
    age_hours = compute_job_exact_age_hours(row.get('posted_at'), row.get('discovered_at'))
    return now.timestamp() - (age_hours * 3600.0)

def list_jobs(
    status: Optional[str] = None,
    salary_tier: Optional[str] = None,
    platform: Optional[Any] = None,
    search_query: Optional[str] = None,
    remote_only: Optional[bool] = None,
    exclude_remote: Optional[bool] = None,
    max_age_hours: Optional[float] = None,
    min_age_hours: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    company_type: Optional[str] = None,
    company_size: Optional[str] = None,
    min_wlb: Optional[float] = None,
    max_risk: Optional[str] = None,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
    SELECT j.*, 
           c.glassdoor_rating, c.ambitionbox_rating, c.engineering_wlb_score,
           c.culture_summary, c.headcount_range, c.stage_or_type, c.has_recent_layoffs,
           c.layoffs_details, c.risk_level
    FROM job_postings j
    LEFT JOIN companies_intelligence c ON LOWER(TRIM(j.company_name)) = c.normalized_name
    WHERE 1=1
    """
    params = []
    if status and status != "all":
        s_norm = status.lower().strip()
        if s_norm == "applied":
            query += " AND (j.status LIKE '%APPLIED%' OR j.status LIKE '%SUBMIT%')"
        elif s_norm in ("not_applied", "unapplied"):
            query += " AND (j.status NOT LIKE '%APPLIED%' AND j.status NOT LIKE '%SUBMIT%')"
        elif s_norm in ("ready", "dry_run", "ready_to_submit"):
            query += " AND j.status LIKE '%READY_TO_SUBMIT%'"
        elif s_norm == "failed":
            query += " AND j.status LIKE '%FAILED%'"
        elif s_norm == "discovered":
            query += " AND j.status = 'DISCOVERED'"
        else:
            query += " AND j.status = ?"
            params.append(status)
    if salary_tier and salary_tier != "all":
        query += " AND j.salary_tier = ?"
        params.append(salary_tier)

    # Multi-platform support (handles list, tuple, or comma-separated string)
    if platform and platform != "all":
        if isinstance(platform, str):
            plats = [p.strip().lower() for p in platform.split(",") if p.strip() and p.strip().lower() != "all"]
        elif isinstance(platform, (list, tuple, set)):
            plats = [str(p).strip().lower() for p in platform if str(p).strip() and str(p).strip().lower() != "all"]
        else:
            plats = []

        if plats:
            placeholders = ",".join("?" for _ in plats)
            query += f" AND LOWER(j.ats_platform) IN ({placeholders})"
            params.extend(plats)

    if remote_only:
        query += " AND (j.is_remote = 1 OR LOWER(j.location) LIKE '%remote%')"
    elif exclude_remote:
        query += " AND (j.is_remote = 0 AND LOWER(j.location) NOT LIKE '%remote%')"

    # Company Stage / Type filter (Product, Startup, Enterprise, Unicorn)
    if company_type and company_type != "all":
        ct = company_type.lower()
        if ct == "product":
            query += " AND (LOWER(c.stage_or_type) LIKE '%product%' OR LOWER(c.stage_or_type) LIKE '%saas%')"
        elif ct == "startup":
            query += " AND (LOWER(c.stage_or_type) LIKE '%startup%' OR LOWER(c.stage_or_type) LIKE '%scaleup%' OR j.ats_platform IN ('ashby', 'instahyre'))"
        elif ct == "unicorn":
            query += " AND (LOWER(c.stage_or_type) LIKE '%unicorn%' OR LOWER(c.stage_or_type) LIKE '%pre-ipo%')"
        elif ct == "enterprise":
            query += " AND (LOWER(c.stage_or_type) LIKE '%enterprise%' OR LOWER(c.stage_or_type) LIKE '%fortune%' OR LOWER(c.stage_or_type) LIKE '%public%' OR j.ats_platform = 'workday' OR j.ats_platform = 'amazon')"

    # Company Size / Headcount filter (Supports granular 0-10, 10-50, 50-100, 100-500, 500-1000, 1000-5000, 5000+, 50000+)
    if company_size and company_size != "all":
        cs = company_size.lower().replace(" ", "").replace("_", "-")
        if cs in ("0-10", "seed-nano"):
            query += " AND (c.headcount_range = '0 - 10')"
        elif cs in ("10-50", "stealth-seed"):
            query += " AND (c.headcount_range = '10 - 50')"
        elif cs in ("50-100", "series-a"):
            query += " AND (c.headcount_range = '50 - 100')"
        elif cs in ("100-500", "series-b"):
            query += " AND (c.headcount_range = '100 - 500')"
        elif cs in ("500-1000", "series-c-d", "startup-small", "startupsmall"):
            query += " AND (c.headcount_range = '500 - 1,000' OR c.headcount_range LIKE '100%' OR c.headcount_range LIKE '500%' OR j.ats_platform = 'ashby')"
        elif cs in ("1000-5000", "mid-scaleup", "midscaleup"):
            query += " AND (c.headcount_range = '1,000 - 5,000' OR c.headcount_range LIKE '2,%' OR c.headcount_range LIKE '3,%' OR c.headcount_range LIKE '4,%')"
        elif cs in ("5000+", "large-enterprise", "largeenterprise"):
            query += " AND (c.headcount_range = '50,000+' OR c.headcount_range = '5,000 - 50,000' OR c.headcount_range LIKE '5,%' OR c.headcount_range LIKE '6,%' OR c.headcount_range LIKE '7,%' OR c.headcount_range LIKE '8,%' OR c.headcount_range LIKE '10,%' OR c.headcount_range LIKE '30,%' OR c.headcount_range LIKE '200,%' OR c.headcount_range LIKE '1,000,000%' OR j.ats_platform = 'workday')"
        elif cs in ("50000+", "mega-enterprise"):
            query += " AND (c.headcount_range = '50,000+' OR c.headcount_range LIKE '200,%' OR c.headcount_range LIKE '1,000,000%')"

    # Work-Life Balance / Culture Rating filter (e.g. 4.0+)
    if min_wlb and min_wlb > 0:
        query += " AND (c.engineering_wlb_score >= ? OR c.glassdoor_rating >= ?)"
        params.extend([min_wlb, min_wlb])

    # Layoff / Financial Risk filter
    if max_risk and max_risk != "all":
        if max_risk.lower() == "low_only":
            query += " AND (c.risk_level = 'LOW' AND (c.has_recent_layoffs = 0 OR c.has_recent_layoffs IS NULL))"

    if search_query and search_query.strip():
        term = f"%{search_query.strip()}%"
        query += " AND (j.company_name LIKE ? OR j.title LIKE ? OR j.location LIKE ? OR j.jd_content LIKE ? OR c.stage_or_type LIKE ? OR c.culture_summary LIKE ?)"
        params.extend([term, term, term, term, term, term])

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Filter strictly: exclude Lead, Staff, Principal, Architect, and >6 YoE positions
    rows = [
        r for r in rows
        if is_qualified_seniority_and_exp(r.get("title", ""), r.get("jd_content", ""))
    ]

    if (max_age_hours and max_age_hours > 0) or (min_age_hours and min_age_hours > 0) or start_date or end_date:
        rows = [
            r for r in rows
            if is_within_age(
                r.get("posted_at"),
                r.get("discovered_at"),
                max_age_hours=max_age_hours,
                min_age_hours=min_age_hours,
                start_date=start_date,
                end_date=end_date
            )
        ]

    rows.sort(key=get_job_sort_timestamp, reverse=True)
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
    """Logs an endpoint or URL error into the scraper_failures dead-letter queue with deduplication and auto-retirement of 404s."""
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        # Auto-resolve permanent 404s so they don't bloat the retry queue
        is_permanent = 1 if http_status == 404 else 0
        c.execute("""
        INSERT INTO scraper_failures (source, company_name, target_url, failure_reason, http_status, retry_count, resolved, last_attempted)
        VALUES (?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(target_url) DO UPDATE SET
            company_name = excluded.company_name,
            source = excluded.source,
            failure_reason = excluded.failure_reason,
            http_status = excluded.http_status,
            retry_count = scraper_failures.retry_count + 1,
            resolved = CASE WHEN excluded.http_status = 404 THEN 1 ELSE scraper_failures.resolved END,
            last_attempted = CURRENT_TIMESTAMP;
        """, (source, company_name, target_url, failure_reason, http_status, is_permanent))
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
    """Fetches all active distinct failures that need retry (excludes retired 404s and max-retried items)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
    SELECT source, company_name, target_url, failure_reason, http_status, retry_count, last_attempted
    FROM scraper_failures
    WHERE resolved = 0 AND (http_status IS NULL OR http_status != 404) AND retry_count < 5
    ORDER BY last_attempted DESC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_failures_summary(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Summary of scraper errors for health monitoring, counting unique actionable endpoints."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scraper_failures WHERE resolved = 0 AND (http_status IS NULL OR http_status != 404) AND retry_count < 5")
    unresolved = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM scraper_failures WHERE resolved = 1 OR http_status = 404")
    resolved = c.fetchone()[0]
    c.execute("""
    SELECT source, COUNT(*) 
    FROM scraper_failures 
    WHERE resolved = 0 AND (http_status IS NULL OR http_status != 404) AND retry_count < 5
    GROUP BY source
    """)
    by_source = dict(c.fetchall())
    conn.close()
    return {"unresolved_count": unresolved, "resolved_count": resolved, "by_source": by_source}

def save_portal_account(domain: str, company: str, email: str, password: str, status: str = "ACTIVE", db_path: str = DB_PATH):
    """Saves or updates portal credentials for a domain."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
    INSERT INTO portal_accounts (portal_domain, company_name, email, password, status, last_used)
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(portal_domain) DO UPDATE SET
        password = excluded.password,
        status = excluded.status,
        last_used = CURRENT_TIMESTAMP;
    """, (domain, company, email, password, status))
    conn.commit()
    conn.close()

def get_portal_account(domain: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves credentials for a given portal domain."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM portal_accounts WHERE portal_domain = ?", (domain,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

if __name__ == "__main__":
    init_db()
