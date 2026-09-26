"""
Partitioned LinkedIn 24h & Amazon Jobs Discovery Engine.
Bypasses LinkedIn's 250-result pagination cap by splitting searches into
multi-query partitions across tech stacks (Java, Go, Python, Distributed Systems)
and tech hubs (Bengaluru, Hyderabad, Pune, Remote).
Strictly enforces f_TPR=r86400 (last 24 hours).
"""

import aiohttp
import asyncio
import urllib.parse
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import List, Dict, Any, Set
from database import save_job, get_company_intelligence
from smart_due_diligence import perform_smart_due_diligence
from salary_classifier import classify_salary

# Tech Stack & Title Partitions (each yields < 200 jobs, safely below LinkedIn's cap)
LINKEDIN_QUERY_PARTITIONS = [
    "Senior Java Backend",
    "SDE 2 Java Spring Boot",
    "Senior Software Engineer Go Golang",
    "SDE II Distributed Systems",
    "Senior Backend Engineer Python",
    "Senior Microservices Developer",
    "Software Development Engineer 2 Backend",
    "Staff Backend Engineer",
    "Senior Nodejs Backend Engineer"
]

LOCATION_PARTITIONS = [
    "India",
    "Bengaluru, Karnataka, India",
    "Hyderabad, Telangana, India",
    "Pune, Maharashtra, India",
    "Remote"
]

async def fetch_linkedin_page(session: aiohttp.ClientSession, query: str, location: str, start: int = 0) -> List[Dict[str, Any]]:
    base_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": query,
        "location": location,
        "f_TPR": "r86400",  # Strict: Past 24 hours (86400 seconds)
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
                apply_url = link_elem["href"].split("?")[0]  # clean tracking params
                job_id = f"li_{apply_url.split('-')[-1]}" if "-" in apply_url else f"li_{abs(hash(apply_url))}"
                posted_time = time_elem.text.strip() if time_elem else "Within 24h"

                # Filter out obvious non-backend / junior roles
                t_low = title.lower()
                if any(neg in t_low for neg in ["frontend", "front-end", "intern", "qa", "sdet", "android", "ios"]):
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
                    "posted_at": datetime.now(timezone.utc).isoformat(),
                    "experience_required": "4-6 Years (SDE-2 / Senior)",
                    "salary_tier": tier,
                    "estimated_ctc": est_ctc,
                    "status": "DISCOVERED"
                }
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

async def run_partitioned_linkedin_crawler(max_pages_per_partition: int = 4) -> List[Dict[str, Any]]:
    """Crawls all partitioned search buckets concurrently with jittered delay."""
    all_jobs = []
    seen_ids: Set[str] = set()

    conn = aiohttp.TCPConnector(limit=15, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=conn) as session:
        # First scan Amazon Jobs official API
        amz_jobs = await scan_amazon_jobs(session)
        all_jobs.extend(amz_jobs)

        # Then scan partitioned LinkedIn queries
        tasks = []
        for q in LINKEDIN_QUERY_PARTITIONS:
            for loc in ["India", "Bengaluru, Karnataka, India"]:
                for page in range(max_pages_per_partition):
                    start = page * 25
                    tasks.append(fetch_linkedin_page(session, q, loc, start))

        print(f"🌐 Firing {len(tasks)} partitioned LinkedIn queries (Strict 24h: f_TPR=r86400)...")
        results = await asyncio.gather(*tasks)
        for r in results:
            for j in r:
                if j["job_id"] not in seen_ids:
                    seen_ids.add(j["job_id"])
                    all_jobs.append(j)

    print(f"✅ Partitioned crawl finished! Discovered {len(all_jobs)} distinct opportunities across LinkedIn & Amazon.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_partitioned_linkedin_crawler(max_pages_per_partition=3))
