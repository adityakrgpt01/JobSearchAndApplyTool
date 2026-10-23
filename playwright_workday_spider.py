"""
Stealth Headless Playwright Workday Spider.
Bypasses Akamai Bot Manager (HTTP 408), Cloudflare (HTTP 403), and Workday Session Gates (HTTP 500)
for Tier-1 global enterprises (Adobe, AMD, ARM, ASML, AT&T, Intel, Nvidia, Qualcomm, Citi, Amex, etc.)
Extracts Senior Backend / SDE-2 (4-6 YoE) roles in India directly from hydrated portal DOM.
"""

import asyncio
import hashlib
import json
import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from database import save_job, mark_failure_resolved, get_crawler_cursor, set_crawler_cursor
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

STEALTH_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
]

def is_backend_role(title: str, jd_text: str = "") -> bool:
    if not is_qualified_seniority_and_exp(title, jd_text):
        return False
    t = title.lower()
    if any(neg in t for neg in DISALLOWED_KEYWORDS):
        return False
    return any(k in t for k in BACKEND_KEYWORDS)

def load_stealth_workday_targets(batch_size: int = 12) -> List[Dict[str, Any]]:
    """Loads a rotating batch of companies currently blocked by WAF (408) or Session Gates (500)."""
    conn = sqlite3.connect("jobs.db")
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT company_name, target_url, http_status
        FROM scraper_failures
        WHERE source = 'workday' 
          AND http_status IN (408, 500, 403)
          AND company_name != ''
          AND company_name NOT LIKE 'workday_%'
        ORDER BY http_status, company_name
    """)
    rows = c.fetchall()
    conn.close()

    targets = []
    seen = set()
    for name, url, status in rows:
        m = re.search(r'https://([a-zA-Z0-9_\-\.]+myworkdayjobs\.com)/(?:wday/cxs/[^/]+/|en-US/)?([a-zA-Z0-9_\-]+)', url)
        if m:
            host = m.group(1).lower()
            board = m.group(2)
            tenant = host.split('.')[0]
            clean_name = re.sub(r'\s*\(.*?\)\s*', '', re.sub(r'\s*\[.*?\]\s*', '', name)).strip()
            key = f"{host}_{board}".lower()
            if key not in seen and clean_name:
                seen.add(key)
                targets.append({
                    "company_name": clean_name,
                    "host": host,
                    "tenant": tenant,
                    "board": board,
                    "target_url": url,
                    "http_status": status,
                    "portal_url": f"https://{host}/en-US/{board}"
                })

    if not targets:
        return []

    cursor_idx = get_crawler_cursor("playwright_workday_cursor", 0)
    start = cursor_idx % len(targets)
    batch = []
    for i in range(min(batch_size, len(targets))):
        batch.append(targets[(start + i) % len(targets)])
    set_crawler_cursor("playwright_workday_cursor", (cursor_idx + batch_size) % len(targets))
    return batch

async def scrape_single_workday_stealth(context, target: Dict[str, Any], stealth: Stealth) -> List[Dict[str, Any]]:
    name = target["company_name"]
    host = target["host"]
    board = target["board"]
    portal_url = target["portal_url"]
    search_url = f"{portal_url}?q=Software%20India"
    discovered = []

    page = await context.new_page()
    await stealth.apply_stealth_async(page)

    try:
        # Navigate to search page
        await page.goto(search_url, wait_until="domcontentloaded", timeout=22000)
        await asyncio.sleep(random.uniform(2.0, 3.5))

        # Wait for either job links or empty state
        try:
            await page.wait_for_selector('a[href*="/job/"]', timeout=8000)
        except Exception:
            pass

        # Evaluate live job cards from DOM
        job_cards = await page.evaluate("""() => {
            const results = [];
            const links = Array.from(document.querySelectorAll('a[href*="/job/"]'));
            for (const a of links) {
                const title = a.innerText.trim();
                const href = a.href;
                if (!title || title.length < 3) continue;

                // Traverse parent container to find location
                let loc = "India";
                const parent = a.closest('li') || a.closest('[role="listitem"]') || a.parentElement?.parentElement;
                if (parent) {
                    const text = parent.innerText;
                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                    for (const l of lines) {
                        const low = l.toLowerCase();
                        if (low.includes('india') || low.includes('bengaluru') || low.includes('bangalore') ||
                            low.includes('hyderabad') || low.includes('pune') || low.includes('gurgaon') ||
                            low.includes('mumbai') || low.includes('chennai') || low.includes('remote')) {
                            loc = l;
                            break;
                        }
                    }
                }
                results.push({ title: title, url: href, location: loc });
            }
            return results;
        }""")

        for card in job_cards:
            title = card["title"]
            if not is_backend_role(title):
                continue

            loc = card["location"]
            if not is_qualified_location(loc):
                continue

            apply_url = card["url"]
            path_hash = hashlib.md5((apply_url or title).encode()).hexdigest()[:12]
            job_id = f"wd_stealth_{target['tenant']}_{path_hash}"

            tier, est_ctc = classify_salary(name, title)
            await perform_smart_due_diligence(name)

            rec = {
                "job_id": job_id,
                "company_name": name,
                "title": title,
                "location": loc,
                "is_remote": "remote" in loc.lower(),
                "ats_platform": "workday",
                "apply_url": apply_url,
                "jd_content": f"{title} at {name}. Location: {loc}. Extracted via Stealth Playwright.",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "experience_required": "4-6 Years (SDE-2 / Senior)",
                "salary_tier": tier,
                "estimated_ctc": est_ctc,
                "status": "DISCOVERED"
            }
            save_job(rec)
            discovered.append(rec)

        if discovered or len(job_cards) > 0:
            mark_failure_resolved(target["target_url"])

    except Exception:
        pass
    finally:
        await page.close()

    return discovered

async def run_playwright_workday_batch(batch_size: int = 10) -> List[Dict[str, Any]]:
    """Runs a controlled stealth headless Playwright session across rotating WAF-locked Workday targets."""
    targets = load_stealth_workday_targets(batch_size=batch_size)
    if not targets:
        return []

    print(f"🕵️ Firing Stealth Headless Playwright Spider across {len(targets)} WAF/Auth-locked Workday portals...")
    stealth = Stealth()
    all_jobs = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars"
            ]
        )

        context = await browser.new_context(
            user_agent=random.choice(STEALTH_USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="Asia/Kolkata"
        )

        for target in targets:
            jobs = await scrape_single_workday_stealth(context, target, stealth)
            all_jobs.extend(jobs)
            await asyncio.sleep(random.uniform(1.5, 3.0))

        await context.close()
        await browser.close()

    print(f"✅ Stealth Playwright Spider complete! Discovered {len(all_jobs)} opportunities.")
    return all_jobs

if __name__ == "__main__":
    from database import init_db
    init_db()
    asyncio.run(run_playwright_workday_batch(batch_size=5))
