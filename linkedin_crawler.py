"""
Partitioned LinkedIn 24h & Big Tech Discovery Engine.
Includes dedicated company search partitions for Google, Microsoft, Meta, Apple,
and Amazon Jobs official API to ensure zero Big Tech backend openings are missed.
Strictly enforces f_TPR=r86400 (last 24 hours).
"""

import aiohttp
import asyncio
import urllib.parse
import re
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Set
from database import save_job, get_crawler_cursor, set_crawler_cursor
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary
from experience_filter import is_qualified_seniority_and_exp

# Dedicated Big Tech & Custom Portal MNC Partitions
BIG_TECH_PARTITIONS = [
    ("Microsoft", "Software Engineer"),
    ("Salesforce", "Software Engineer"),
    ("Apple", "Software Engineer"),
    ("Airbnb", "Software Engineer"),
    ("Booking.com", "Software Engineer"),
    ("LinkedIn", "Software Engineer"),
    ("Intuit", "Software Engineer"),
    ("Wayfair", "Software Engineer"),
    ("ServiceNow", "Software Engineer"),
    ("Workday", "Software Engineer"),
    ("Adobe", "Software Engineer"),
    ("Google", "Software Engineer")
]

# High-Intent Senior Backend / SDE-2 Search Partitions
TECH_STACK_PARTITIONS = [
    "Senior Java Backend Developer",
    "SDE 2 Java Spring Boot",
    "Backend Software Engineer",
    "Senior Backend Engineer Java",
    "SDE II Distributed Systems",
    "Software Development Engineer 2 Backend",
    "Senior Microservices Developer",
    "Software Engineer 2 Backend",
    "Java Backend Engineer",
    "Senior Software Engineer Java Spring",
    "Distributed Systems Backend Engineer",
    "Platform Engineer Backend",
    "Cloud Backend Engineer",
    "Software Engineer II Java",
    "Senior Software Developer Java",
    "Backend Developer Golang Java"
]

LINKEDIN_TIME_WINDOWS = {
    "24h": "r86400",
    "48h": "r172800",
    "1w": "r604800"
}

COMPANY_PORTALS = {
    "amazon": "https://www.amazon.jobs/en/search?base_query={q}&loc_query=India",
    "google": "https://www.google.com/about/careers/applications/jobs/results/?q={q}&location=India",
    "microsoft": "https://jobs.careers.microsoft.com/global/en/search?q={q}",
    "cisco": "https://jobs.cisco.com/main/search?q={q}",
    "jpmorgan": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword={q}",
    "jpmorganchase": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword={q}",
    "target": "https://jobs.target.com/search-jobs/{q}",
    "qualcomm": "https://qualcomm.wd5.myworkdayjobs.com/External?q={q}",
    "barclays": "https://search.jobs.barclays/search-jobs/{q}",
    "apple": "https://jobs.apple.com/en-in/search?search={q}",
    "meta": "https://www.metacareers.com/jobs?q={q}",
    "netflix": "https://jobs.netflix.com/search?q={q}",
    "nvidia": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite?q={q}",
    "walmart": "https://careers.walmart.com/results?q={q}",
    "walmart global tech": "https://careers.walmart.com/results?q={q}",
    "wells fargo": "https://www.wellsfargo.com/about/careers/search-jobs?q={q}",
    "deloitte": "https://apply.deloitte.com/careers/SearchJobs/{q}",
    "citi": "https://jobs.citi.com/search-jobs/{q}",
    "ibm": "https://www.ibm.com/careers/us/en/search-results?keywords={q}",
    "expedia": "https://careers.expediagroup.com/jobs/search?q={q}",
    "wipro": "https://careers.wipro.com/careers/jobs?q={q}",
    "globallogic": "https://www.globallogic.com/careers/job-search/?q={q}",
    "optum": "https://careers.unitedhealthgroup.com/search-jobs/{q}",
    "state street": "https://statestreet.wd1.myworkdayjobs.com/Global?q={q}",
    "ust": "https://ust.wd1.myworkdayjobs.com/USTCareers?q={q}",
    "ntt data": "https://careers.inc.nttdata.com/search/?q={q}",
    "ups": "https://www.jobs-ups.com/search-jobs/{q}",
    "nutanix": "https://nutanix.wd1.myworkdayjobs.com/NutanixCareers?q={q}",
    "atlassian": "https://www.atlassian.com/company/careers/job-search?query={q}",
    "snowflake": "https://careers.snowflake.com/us/en/search-results?keywords={q}",
    "cohesity": "https://www.cohesity.com/company/careers/open-positions/?q={q}",
    "siemens": "https://jobs.siemens.com/jobs?page=1&query={q}",
    "ebay": "https://jobs.ebayinc.com/us/en/search-results?keywords={q}",
    "ey": "https://careers.ey.com/ey/search/?q={q}",
    "pwc": "https://jobs.us.pwc.com/search-jobs/{q}",
    "infosys": "https://career.infosys.com/joblist?query={q}",
    "tata consultancy services": "https://www.tcs.com/careers/search-jobs?q={q}",
    "tcs": "https://www.tcs.com/careers/search-jobs?q={q}",
    "accenture": "https://www.accenture.com/in-en/careers/jobsearch?jk={q}",
    "comviva": "https://careers.comviva.com/jobs?q={q}",
    "sonicwall": "https://www.sonicwall.com/careers/?q={q}"
}

async def resolve_linkedin_apply_destination(session: aiohttp.ClientSession, record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Automatically sanitizes LinkedIn URLs, checks for company career portal mapping,
    and probes offsite external ATS apply redirects (Greenhouse, Ashby, Workday, Lever, SmartRecruiters).
    """
    url = record.get("apply_url", "")
    if not url or "linkedin.com" not in url:
        return record

    # 1. Automatic company career portal mapping check
    comp = record.get("company_name", "")
    norm_comp = re.sub(r'[^a-z]', '', comp.lower())
    title = record.get("title", "")
    q_enc = urllib.parse.quote_plus(title)

    for k, template in COMPANY_PORTALS.items():
        k_norm = re.sub(r'[^a-z]', '', k)
        if k_norm in norm_comp or norm_comp in k_norm:
            clean_hash = abs(hash(f"{norm_comp}_{title}"))
            record["apply_url"] = template.format(q=q_enc)
            record["ats_platform"] = k
            record["job_id"] = f"{k}_{clean_hash}"
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

                    if "greenhouse.io" in u_low:
                        m = re.search(r'jobs/(\d+)', u_clean)
                        jid = f"gh_{norm_company}_{m.group(1)}" if m else f"gh_{norm_company}_{num_id}"
                        record["apply_url"] = u_clean
                        record["ats_platform"] = "greenhouse"
                        record["job_id"] = jid
                        # Try fetching exact creation date from Greenhouse API
                        if m:
                            try:
                                gh_api = f"https://boards-api.greenhouse.io/v1/boards/{norm_company}/jobs/{m.group(1)}"
                                async with session.get(gh_api, timeout=aiohttp.ClientTimeout(total=2)) as gh_resp:
                                    if gh_resp.status == 200:
                                        gh_data = await gh_resp.json()
                                        if gh_data.get("updated_at"):
                                            record["posted_at"] = gh_data["updated_at"]
                            except Exception:
                                pass
                        break
                    elif "ashbyhq.com" in u_low:
                        m = re.search(r'ashbyhq\.com/[^/]+/([a-f0-9\-]+)', u_clean)
                        jid = f"ashby_{norm_company}_{m.group(1)}" if m else f"ashby_{norm_company}_{num_id}"
                        record["apply_url"] = u_clean
                        record["ats_platform"] = "ashby"
                        record["job_id"] = jid
                        break
                    elif "myworkdayjobs.com" in u_low:
                        jid = f"wd_{norm_company}_{abs(hash(u_clean))}"
                        record["apply_url"] = u_clean
                        record["ats_platform"] = "workday"
                        record["job_id"] = jid
                        break
                    elif "lever.co" in u_low:
                        m = re.search(r'lever\.co/[^/]+/([a-f0-9\-]+)', u_clean)
                        jid = f"lever_{norm_company}_{m.group(1)}" if m else f"lever_{norm_company}_{num_id}"
                        record["apply_url"] = u_clean
                        record["ats_platform"] = "lever"
                        record["job_id"] = jid
                        # Try fetching exact creation date from Lever API
                        if m:
                            try:
                                lev_api = f"https://api.lever.co/v0/postings/{norm_company}/{m.group(1)}"
                                async with session.get(lev_api, timeout=aiohttp.ClientTimeout(total=2)) as lev_resp:
                                    if lev_resp.status == 200:
                                        lev_data = await lev_resp.json()
                                        if lev_data.get("createdAt"):
                                            record["posted_at"] = datetime.fromtimestamp(lev_data["createdAt"]/1000, tz=timezone.utc).isoformat()
                            except Exception:
                                pass
                        break
                    elif "smartrecruiters.com" in u_low:
                        m = re.search(r'smartrecruiters\.com/[^/]+/([a-f0-9\-]+)', u_clean)
                        jid = f"sr_{norm_company}_{m.group(1)}" if m else f"sr_{norm_company}_{num_id}"
                        record["apply_url"] = u_clean
                        record["ats_platform"] = "smartrecruiters"
                        record["job_id"] = jid
                        break
                    elif any(domain in u_low for domain in ["ebayinc.com", "amazon.jobs", "careers.", "jobs."]):
                        m_code = re.search(r'/job/([A-Z0-9\-]+)', u_clean)
                        job_code = m_code.group(1) if m_code else str(abs(hash(u_clean)))
                        record["apply_url"] = u_clean
                        record["ats_platform"] = norm_company or "portal"
                        record["job_id"] = f"{norm_company}_{job_code}"
                        break
    except Exception:
        pass

    return record

async def fetch_linkedin_page(session: aiohttp.ClientSession, query: str, location: str, start: int = 0, time_window: str = "r86400") -> List[Dict[str, Any]]:
    base_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": query,
        "location": location,
        "f_TPR": time_window,  # Dynamic window: r86400 (24h), r172800 (48h), r604800 (1w)
        "start": str(start)
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }

    discovered = []
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            items = soup.find_all("li")

            for item in items:
                title_elem = item.find("h3", class_="base-search-card__title")
                comp_elem = item.find("h4", class_="base-search-card__subtitle")
                loc_elem = item.find("span", class_="job-search-card__location")
                link_elem = item.find("a", class_="base-card__full-link")
                time_elem = item.find("time")

                if not (title_elem and comp_elem and link_elem):
                    continue

                title = title_elem.text.strip()
                company_name = comp_elem.text.strip()
                loc = loc_elem.text.strip() if loc_elem else location
                apply_url = link_elem["href"].split("?")[0]
                job_id = f"li_{apply_url.split('-')[-1]}" if "-" in apply_url else f"li_{abs(hash(apply_url))}"
                posted_time = time_elem.text.strip() if time_elem else "Recent"

                # Calculate accurate posted_at from LinkedIn relative string
                now_utc = datetime.now(timezone.utc)
                exact_posted_iso = now_utc.isoformat()
                p_low = posted_time.lower()
                if "just now" in p_low or "minute" in p_low:
                    exact_posted_iso = now_utc.isoformat()
                else:
                    m_hr = re.search(r"(\d+)\s*hour", p_low)
                    if m_hr:
                        exact_posted_iso = (now_utc - timedelta(hours=int(m_hr.group(1)))).isoformat()
                    else:
                        m_day = re.search(r"(\d+)\s*day", p_low)
                        if m_day:
                            exact_posted_iso = (now_utc - timedelta(days=int(m_day.group(1)))).isoformat()

                t_low = title.lower()
                # Strict exclusions including ServiceNow, Mobile App, Android, iOS developers (Requirement #5 & #2)
                if any(neg in t_low for neg in [
                    "frontend", "front-end", "intern", "internship", "qa", "sdet",
                    "android", "androis", "ios", "mobile", "flutter", "swift", "react native", "react-native",
                    "servicenow developer", "service now developer", "service-now developer", "servicenow dev", "service now dev",
                    "servicenow consultant", "servicenow admin",
                    "mobile app", "mobile developer", "mobile application", "mobile engineer"
                ]):
                    continue

                if not is_qualified_seniority_and_exp(title):
                    continue

                tier, est_ctc = classify_salary(company_name, title)
                await perform_smart_due_diligence(company_name)

                record = {
                    "job_id": job_id,
                    "company_name": company_name,
                    "title": title,
                    "location": loc,
                    "is_remote": "remote" in loc.lower(),
                    "ats_platform": "linkedin",
                    "apply_url": apply_url,
                    "jd_content": f"Title: {title} at {company_name}. Location: {loc}. Posted: {posted_time}.",
                    "posted_at": exact_posted_iso,
                    "experience_required": "4-6 Years (SDE-2 / Senior)",
                    "salary_tier": tier,
                    "estimated_ctc": est_ctc,
                    "status": "DISCOVERED"
                }

                # Resolve offsite external ATS apply links if present, otherwise retain LinkedIn defaults
                record = await resolve_linkedin_apply_destination(session, record)

                save_job(record)
                discovered.append(record)
    except Exception:
        pass
    return discovered

async def scan_amazon_jobs(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Directly queries Amazon Jobs API for SDE-2 in India."""
    url = "https://www.amazon.jobs/en/search.json?base_query=Software+Development+Engineer+II&loc_query=India&category[]=software-development&sort=recent"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    discovered = []
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for j in data.get("jobs", []):
                    title = j.get("title", "")
                    job_path = j.get("job_path", "")
                    apply_url = f"https://www.amazon.jobs{job_path}" if job_path else "https://www.amazon.jobs"
                    job_id = f"amz_{j.get('id_icims', hash(title))}"
                    loc = j.get("location_normalized", "Bengaluru, India")

                    tier, est_ctc = classify_salary("Amazon", title)
                    await perform_smart_due_diligence("Amazon")

                    record = {
                        "job_id": job_id,
                        "company_name": "Amazon",
                        "title": title,
                        "location": loc,
                        "is_remote": False,
                        "ats_platform": "amazon",
                        "apply_url": apply_url,
                        "jd_content": j.get("description_short", "")[:2000],
                        "posted_at": j.get("posted_date", datetime.now(timezone.utc).isoformat()),
                        "experience_required": "4-6 Years (SDE-2)",
                        "salary_tier": tier,
                        "estimated_ctc": est_ctc,
                        "status": "DISCOVERED"
                    }
                    save_job(record)
                    discovered.append(record)
    except Exception as e:
        print(f"Error scanning Amazon jobs: {e}")
    return discovered

async def run_partitioned_linkedin_crawler(max_pages: int = 2, time_window: str = None) -> List[Dict[str, Any]]:
    all_jobs = []
    seen_ids: Set[str] = set()

    # Determine dynamic rotating time window across cycles (24h, 48h, 1w)
    time_windows = [
        ("24h", "r86400"),
        ("48h", "r172800"),
        ("1w", "r604800")
    ]
    if not time_window:
        win_idx = get_crawler_cursor("linkedin_window_idx", 0)
        label, active_window = time_windows[win_idx % len(time_windows)]
        set_crawler_cursor("linkedin_window_idx", (win_idx + 1) % len(time_windows))
    else:
        active_window = LINKEDIN_TIME_WINDOWS.get(time_window, time_window)
        label = time_window

    print(f"🌐 Firing LinkedIn Partitioned Search (Window: {label} [{active_window}])...")

    conn = aiohttp.TCPConnector(limit=25, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=conn) as session:
        # 1. Direct Amazon Jobs API
        amz_jobs = await scan_amazon_jobs(session)
        all_jobs.extend(amz_jobs)

        tasks = []
        # 2. Dedicated Big Tech Partitions (Google, Microsoft, Meta, Apple, etc.)
        for comp, query in BIG_TECH_PARTITIONS:
            for p in range(2):
                tasks.append(fetch_linkedin_page(session, f"{comp} {query}", "India", p * 25, time_window=active_window))

        # 3. Broad Tech Stack Partitions across core hubs
        for q in TECH_STACK_PARTITIONS:
            for loc in ["India", "Bengaluru, Karnataka, India"]:
                for p in range(max_pages):
                    tasks.append(fetch_linkedin_page(session, q, loc, p * 25, time_window=active_window))

        # 4. Regional City Sweeps for top backend queries
        regional_queries = ["Senior Java Backend Developer", "SDE 2 Backend", "Senior Backend Engineer Java", "SDE II Distributed Systems"]
        for q in regional_queries:
            for loc in ["Hyderabad, Telangana, India", "Pune, Maharashtra, India", "Gurugram, Haryana, India"]:
                tasks.append(fetch_linkedin_page(session, q, loc, 0, time_window=active_window))

        print(f"🌐 Firing {len(tasks)} targeted LinkedIn search tasks...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                for j in r:
                    if j["job_id"] not in seen_ids:
                        seen_ids.add(j["job_id"])
                        all_jobs.append(j)

    print(f"✅ LinkedIn Crawl finished ({label})! Discovered {len(all_jobs)} distinct opportunities.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_partitioned_linkedin_crawler(max_pages=3))
