import sqlite3
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from experience_filter import is_qualified_seniority_and_exp, is_qualified_job

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
        headcount_min INTEGER DEFAULT 0,
        headcount_max INTEGER DEFAULT 0,
        stage_or_type TEXT,
        has_recent_layoffs BOOLEAN DEFAULT 0,
        layoffs_details TEXT,
        risk_level TEXT, -- LOW, MODERATE, HIGH
        salary_benchmark_tier TEXT, -- 40-50LPA, 50-60LPA, 60-70LPA, 70+LPA
        salary_max_lpa REAL DEFAULT 0.0,
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
        applied_at TIMESTAMP,
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

    # 6. Persistent Crawler State & Rotating Batch Cursors
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS crawler_state (
        key TEXT PRIMARY KEY,
        value INTEGER DEFAULT 0,
        iteration INTEGER DEFAULT 1,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()

def get_crawler_cursor(key: str, default: int = 0, db_path: str = DB_PATH) -> int:
    """Retrieves persistent batch cursor for a scanner platform."""
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT value FROM crawler_state WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return int(row[0]) if row and row[0] is not None else default
    except Exception:
        return default

def set_crawler_cursor(key: str, value: int, iteration: Optional[int] = None, db_path: str = DB_PATH):
    """Saves persistent batch cursor and iteration count for a scanner platform."""
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        if iteration is not None:
            c.execute("""
            INSERT INTO crawler_state (key, value, iteration, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, iteration=excluded.iteration, updated_at=CURRENT_TIMESTAMP
            """, (key, value, iteration))
        else:
            c.execute("""
            INSERT INTO crawler_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """, (key, value))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_crawler_iteration(key: str, default: int = 1, db_path: str = DB_PATH) -> int:
    """Retrieves current full-iteration sweep count for a scanner platform."""
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT iteration FROM crawler_state WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        return int(row[0]) if (row and row[0] is not None) else default
    except Exception:
        return default

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
    disc_dt = parse_posted_dt(discovered_at)
    elapsed_since_scrape = max(0.0, (now - disc_dt).total_seconds() / 3600.0) if disc_dt else 0.0
    
    if posted_at:
        # 1. Absolute ISO / Date string
        dt = parse_posted_dt(posted_at)
        if dt:
            posted_age = max(0.0, (now - dt).total_seconds() / 3600.0)
            if disc_dt and dt > disc_dt + timedelta(minutes=5):
                return elapsed_since_scrape
            return posted_age
            
        # 2. Relative string: calculate offset + real time elapsed since discovery
        p_lower = str(posted_at).lower().strip()
        
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
        m_days = re.search(r"(\d+)\s*\+?\s*days?\s*ago", p_lower)
        if m_days:
            return (float(m_days.group(1)) * 24.0) + elapsed_since_scrape
        if "30+ days" in p_lower:
            return (30.0 * 24.0) + elapsed_since_scrape
        m_week = re.search(r"(\d+)\s*\+?\s*weeks?\s*ago", p_lower)
        if m_week:
            return (float(m_week.group(1)) * 7.0 * 24.0) + elapsed_since_scrape
        if "week" in p_lower:
            return (7.0 * 24.0) + elapsed_since_scrape
        m_month = re.search(r"(\d+)\s*\+?\s*months?\s*ago", p_lower)
        if m_month:
            return (float(m_month.group(1)) * 30.0 * 24.0) + elapsed_since_scrape
        if "month" in p_lower:
            return (30.0 * 24.0) + elapsed_since_scrape
            
    if disc_dt:
        return elapsed_since_scrape
            
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

def _parse_multi_param(val: Optional[Any]) -> List[str]:
    if val is None or val == "all":
        return []
    if isinstance(val, str):
        items = [x.strip() for x in val.split(",") if x.strip() and x.strip().lower() != "all"]
    elif isinstance(val, (list, tuple, set)):
        items = [str(x).strip() for x in val if str(x).strip() and str(x).strip().lower() != "all"]
    else:
        items = [str(val).strip()]
    return [x for x in items if x]

def list_jobs(
    status: Optional[Any] = None,
    salary_tier: Optional[Any] = None,
    platform: Optional[Any] = None,
    search_query: Optional[str] = None,
    location_filter: Optional[str] = None,
    elite_only: Optional[bool] = None,
    remote_only: Optional[bool] = None,
    exclude_remote: Optional[bool] = None,
    max_age_hours: Optional[float] = None,
    min_age_hours: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    company_type: Optional[Any] = None,
    company_size: Optional[Any] = None,
    min_wlb: Optional[Any] = None,
    max_risk: Optional[Any] = None,
    exclude_companies: Optional[str] = None,
    exclude_locations: Optional[str] = None,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
    SELECT j.*, 
           c.glassdoor_rating, c.ambitionbox_rating, c.engineering_wlb_score,
           c.culture_summary, c.headcount_range, c.stage_or_type, c.has_recent_layoffs,
           c.layoffs_details, c.risk_level,
           IFNULL(ca.total_company_applied, 0) as total_company_applied
    FROM job_postings j
    LEFT JOIN companies_intelligence c ON LOWER(TRIM(j.company_name)) = c.normalized_name
    LEFT JOIN (
        SELECT LOWER(TRIM(company_name)) as company_norm, COUNT(id) as total_company_applied 
        FROM job_postings 
        WHERE status = 'APPLIED' 
        GROUP BY LOWER(TRIM(company_name))
    ) ca ON LOWER(TRIM(j.company_name)) = ca.company_norm
    WHERE 1=1
    """
    params = []

    # Multi-Status Support
    statuses = _parse_multi_param(status)
    if statuses:
        if any(s.lower().strip() == 'none' for s in statuses):
            conn.close()
            return []
        status_conds = []
        has_applied = False
        for s in statuses:
            s_norm = s.lower().strip()
            if s_norm == "applied":
                has_applied = True
                status_conds.append("j.status = 'APPLIED'")
            elif s_norm in ("not_applied", "unapplied"):
                status_conds.append("(j.status = 'DISCOVERED' OR (j.status NOT LIKE '%APPLIED%' AND j.status != 'INVALID'))")
            elif s_norm in ("ready", "dry_run", "ready_to_submit"):
                status_conds.append("(j.status LIKE '%READY_TO_SUBMIT%' OR j.status LIKE '%DRY_RUN%')")
            elif s_norm in ("review_required", "review"):
                status_conds.append("j.status LIKE '%REVIEW_REQUIRED%'")
            elif s_norm == "failed":
                status_conds.append("j.status LIKE '%FAILED%'")
            elif s_norm == "applying":
                status_conds.append("j.status = 'APPLYING'")
            elif s_norm == "discovered":
                status_conds.append("j.status = 'DISCOVERED'")
            elif s_norm in ("invalid", "not_valid", "expired"):
                status_conds.append("j.status = 'INVALID'")
            else:
                status_conds.append("(j.status = ? OR LOWER(j.status) = ?)")
                params.extend([s, s_norm])
        if status_conds:
            query += f" AND ({' OR '.join(status_conds)})"
        if not has_applied:
            query += " AND j.status != 'APPLIED' AND j.status NOT LIKE '%APPLIED%'"

    # Multi Salary Tier Support
    tiers = _parse_multi_param(salary_tier)
    if tiers:
        placeholders = ",".join("?" for _ in tiers)
        query += f" AND j.salary_tier IN ({placeholders})"
        params.extend(tiers)

    # Multi-platform support
    plats = _parse_multi_param(platform)
    if plats and not elite_only:
        plats_lower = [p.lower() for p in plats]
        placeholders = ",".join("?" for _ in plats_lower)
        query += f" AND LOWER(j.ats_platform) IN ({placeholders})"
        params.extend(plats_lower)

    if remote_only:
        query += " AND (j.is_remote = 1 OR LOWER(j.location) LIKE '%remote%')"
    elif exclude_remote:
        query += " AND (j.is_remote = 0 AND LOWER(j.location) NOT LIKE '%remote%')"

    # Multi Company Stage / Type filter (Product, Startup, Enterprise, Unicorn)
    ctypes = _parse_multi_param(company_type)
    if ctypes:
        ctype_conds = []
        for ct in ctypes:
            ct_norm = ct.lower()
            if ct_norm == "product":
                ctype_conds.append("(LOWER(c.stage_or_type) LIKE '%product%' OR LOWER(c.stage_or_type) LIKE '%saas%')")
            elif ct_norm == "startup":
                ctype_conds.append("(LOWER(c.stage_or_type) LIKE '%startup%' OR LOWER(c.stage_or_type) LIKE '%scaleup%' OR j.ats_platform IN ('ashby', 'instahyre'))")
            elif ct_norm == "unicorn":
                ctype_conds.append("(LOWER(c.stage_or_type) LIKE '%unicorn%' OR LOWER(c.stage_or_type) LIKE '%pre-ipo%')")
            elif ct_norm == "enterprise":
                ctype_conds.append("(LOWER(c.stage_or_type) LIKE '%enterprise%' OR LOWER(c.stage_or_type) LIKE '%fortune%' OR LOWER(c.stage_or_type) LIKE '%public%' OR j.ats_platform = 'workday' OR j.ats_platform = 'amazon')")
        if ctype_conds:
            query += f" AND ({' OR '.join(ctype_conds)})"

    # Multi Company Size / Headcount filter
    csizes = _parse_multi_param(company_size)
    if csizes:
        csize_conds = []
        for cs_raw in csizes:
            cs = cs_raw.lower().replace(" ", "").replace("_", "-")
            if cs in ("0-10", "seed-nano"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max <= 10) OR (c.headcount_max = 0 AND c.headcount_range = '0 - 10'))")
            elif cs in ("10-50", "stealth-seed"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max > 10 AND c.headcount_max <= 50) OR (c.headcount_max = 0 AND c.headcount_range = '10 - 50'))")
            elif cs in ("50-100", "series-a"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max > 50 AND c.headcount_max <= 100) OR (c.headcount_max = 0 AND c.headcount_range = '50 - 100'))")
            elif cs in ("100-500", "series-b"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max > 100 AND c.headcount_max <= 500) OR (c.headcount_max = 0 AND c.headcount_range = '100 - 500'))")
            elif cs in ("500-1000", "series-c-d", "startup-small", "startupsmall"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max > 500 AND c.headcount_max <= 1000) OR (c.headcount_max = 0 AND (c.headcount_range = '500 - 1,000' OR c.headcount_range LIKE '100%' OR c.headcount_range LIKE '500%' OR j.ats_platform = 'ashby')))")
            elif cs in ("1000-5000", "mid-scaleup", "midscaleup"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max > 1000 AND c.headcount_max <= 5000) OR (c.headcount_max = 0 AND (c.headcount_range = '1,000 - 5,000' OR c.headcount_range LIKE '2,%' OR c.headcount_range LIKE '3,%' OR c.headcount_range LIKE '4,%')))")
            elif cs in ("5000+", "large-enterprise", "largeenterprise"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max > 5000) OR (c.headcount_max = 0 AND (c.headcount_range = '50,000+' OR c.headcount_range = '5,000 - 50,000' OR c.headcount_range LIKE '5,%' OR c.headcount_range LIKE '6,%' OR c.headcount_range LIKE '7,%' OR c.headcount_range LIKE '8,%' OR c.headcount_range LIKE '10,%' OR c.headcount_range LIKE '30,%' OR c.headcount_range LIKE '200,%' OR c.headcount_range LIKE '1,000,000%' OR j.ats_platform = 'workday')))")
            elif cs in ("50000+", "mega-enterprise"):
                csize_conds.append("((c.headcount_max > 0 AND c.headcount_max >= 50000) OR (c.headcount_max = 0 AND (c.headcount_range = '50,000+' OR c.headcount_range LIKE '200,%' OR c.headcount_range LIKE '1,000,000%')))")
        if csize_conds:
            query += f" AND ({' OR '.join(csize_conds)})"

    # Multi / Minimum Culture Rating filter
    if min_wlb is not None and min_wlb != "all":
        wlb_vals = []
        if isinstance(min_wlb, (int, float)):
            wlb_vals = [float(min_wlb)]
        elif isinstance(min_wlb, str):
            for x in min_wlb.split(","):
                try:
                    wlb_vals.append(float(x.strip()))
                except ValueError:
                    pass
        elif isinstance(min_wlb, (list, tuple)):
            for x in min_wlb:
                try:
                    wlb_vals.append(float(str(x).strip()))
                except ValueError:
                    pass
        if wlb_vals:
            effective_min = min(wlb_vals)
            query += " AND (c.engineering_wlb_score >= ? OR c.glassdoor_rating >= ?)"
            params.extend([effective_min, effective_min])

    # Layoff / Financial Risk filter
    risks = _parse_multi_param(max_risk)
    if risks:
        if any(r.lower() == "low_only" for r in risks):
            query += " AND (c.risk_level = 'LOW' AND (c.has_recent_layoffs = 0 OR c.has_recent_layoffs IS NULL))"

    if search_query and search_query.strip():
        term = f"%{search_query.strip()}%"
        query += " AND (j.company_name LIKE ? OR j.title LIKE ? OR j.location LIKE ? OR j.jd_content LIKE ? OR c.stage_or_type LIKE ? OR c.culture_summary LIKE ?)"
        params.extend([term, term, term, term, term, term])

    if location_filter and location_filter.strip():
        term = f"%{location_filter.strip()}%"
        query += " AND j.location LIKE ?"
        params.extend([term])

    if elite_only:
        elite_companies = ['microsoft', 'salesforce', 'apple', 'airbnb', 'booking', 'booking.com', 'linkedin', 'intuit', 'wayfair', 'servicenow', 'workday', 'adobe', 'google']
        placeholders = ",".join("?" for _ in elite_companies)
        query += f" AND LOWER(j.company_name) IN ({placeholders})"
        params.extend(elite_companies)

    if exclude_companies and exclude_companies.strip():
        ex_comps = [c.strip().lower() for c in exclude_companies.split(',') if c.strip()]
        for ec in ex_comps:
            if len(ec) >= 4:
                query += " AND LOWER(TRIM(j.company_name)) NOT LIKE ?"
                params.append(f"%{ec}%")
            else:
                query += " AND (LOWER(TRIM(j.company_name)) != ? AND LOWER(TRIM(j.company_name)) NOT LIKE ? AND LOWER(TRIM(j.company_name)) NOT LIKE ?)"
                params.extend([ec, f"{ec} %", f"{ec} (%"])

    if exclude_locations and exclude_locations.strip():
        ex_locs = [l.strip().lower() for l in exclude_locations.split(',') if l.strip()]
        for el in ex_locs:
            query += " AND (j.location IS NULL OR LOWER(j.location) NOT LIKE ?)"
            params.append(f"%{el}%")

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Filter strictly: exclude Lead/Staff/Architect, foreign non-India roles, and >6 YoE positions
    rows = [
        r for r in rows
        if is_qualified_job(r.get("title", ""), r.get("location", ""), r.get("jd_content", ""))
    ]

    if (max_age_hours and max_age_hours > 0) or (min_age_hours and min_age_hours > 0) or start_date or end_date:
        _always_keep_statuses = ("APPLIED", "READY_TO_SUBMIT (DRY_RUN)", "READY_TO_SUBMIT (REVIEW_REQUIRED)", "APPLYING", "FAILED")
        rows = [
            r for r in rows
            if r.get("status") in _always_keep_statuses or is_within_age(
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
    conn = sqlite3.connect(db_path, timeout=10.0)
    cursor = conn.cursor()
    
    if status == 'APPLIED':
        cursor.execute("""
        UPDATE job_postings 
        SET status = ?, apply_log = ?, screenshot_path = ?, apply_attempts = apply_attempts + 1, applied_at = CURRENT_TIMESTAMP
        WHERE job_id = ?;
        """, (status, apply_log, screenshot_path, job_id))
    else:
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
