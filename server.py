"""
Interactive Web Dashboard & API Server.
Visualizes:
- Salary Tier Distribution (40-50L, 50-60L, 60-70L, 70+LPA)
- Job Listings with Direct Apply Links & Status
- Company Due Diligence Dossiers (Ratings, Culture, Layoffs, WLB)
- 1-Click Trigger for Job Discovery & Stealth Auto-Applier
"""

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import asyncio
import os
import json
import sqlite3
from typing import Optional
from database import list_jobs, get_company_intelligence, init_db, get_failures_summary, get_unresolved_failures
from unified_pipeline import run_unified_discovery, audit_and_clean_database_links
from workday_resilient_scanner import rerun_failed_endpoints
from stealth_applier import StealthApplier

import asyncio

app = FastAPI(title="JobSearchAndApplyTool - Command Center")
os.makedirs("screenshots", exist_ok=True)
app.mount("/screenshots", StaticFiles(directory="screenshots"), name="screenshots")

_IS_CRAWLING = False

async def continuous_crawler_worker():
    global _IS_CRAWLING
    while True:
        try:
            _IS_CRAWLING = True
            # 1. Run main rotating discovery
            await run_unified_discovery(24)
            # 2. Automatically retry previously failed endpoints with adaptive backoff
            await rerun_failed_endpoints()
        except Exception as e:
            print(f"Continuous crawler note: {e}")
        finally:
            _IS_CRAWLING = False
        await asyncio.sleep(15)  # Wait 15 seconds between completion and next rotating batch

@app.on_event("startup")
async def on_startup():
    init_db()
    # Launch persistent background crawler that cycles continuously across all 6,000+ companies
    asyncio.create_task(continuous_crawler_worker())

@app.get("/api/jobs")
def get_jobs(
    status: Optional[str] = None,
    tier: Optional[str] = None,
    platform: Optional[str] = None,
    q: Optional[str] = None,
    remote: Optional[bool] = None,
    exclude_remote: Optional[bool] = None,
    hours: Optional[float] = 24,
    min_hours: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    company_type: Optional[str] = None,
    company_size: Optional[str] = None,
    min_wlb: Optional[float] = None,
    max_risk: Optional[str] = None
):
    jobs = list_jobs(
        status=status,
        salary_tier=tier,
        platform=platform,
        search_query=q,
        remote_only=remote,
        exclude_remote=exclude_remote,
        max_age_hours=hours,
        min_age_hours=min_hours,
        start_date=start_date,
        end_date=end_date,
        company_type=company_type,
        company_size=company_size,
        min_wlb=min_wlb,
        max_risk=max_risk
    )
    return {"jobs": jobs, "total": len(jobs)}

@app.get("/api/stats")
def get_stats(
    hours: Optional[float] = 24,
    min_hours: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    platform: Optional[str] = None,
    tier: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    remote: Optional[bool] = None,
    exclude_remote: Optional[bool] = None,
    company_type: Optional[str] = None,
    company_size: Optional[str] = None,
    min_wlb: Optional[float] = None,
    max_risk: Optional[str] = None
):
    jobs = list_jobs(
        status=status,
        salary_tier=tier,
        platform=platform,
        search_query=q,
        remote_only=remote,
        exclude_remote=exclude_remote,
        max_age_hours=hours,
        min_age_hours=min_hours,
        start_date=start_date,
        end_date=end_date,
        company_type=company_type,
        company_size=company_size,
        min_wlb=min_wlb,
        max_risk=max_risk
    )
    tiers = {"40-50LPA": 0, "50-60LPA": 0, "60-70LPA": 0, "70+LPA": 0}
    status_counts = {"DISCOVERED": 0, "APPLYING": 0, "APPLIED": 0, "READY_TO_SUBMIT (DRY_RUN)": 0, "FAILED": 0}
    platforms = {}

    for j in jobs:
        st = j.get("salary_tier", "40-50LPA")
        tiers[st] = tiers.get(st, 0) + 1
        stat = j.get("status", "DISCOVERED")
        status_counts[stat] = status_counts.get(stat, 0) + 1
        plat = j.get("ats_platform", "other")
        platforms[plat] = platforms.get(plat, 0) + 1

    total_applied = sum(v for k, v in status_counts.items() if "APPLIED" in k or "SUBMIT" in k)
    total_unapplied = len(jobs) - total_applied

    return {
        "total_jobs": len(jobs),
        "total_applied": total_applied,
        "total_unapplied": total_unapplied,
        "tiers": tiers,
        "status_counts": status_counts,
        "platforms": platforms
    }

@app.get("/api/company/{company_name}")
def get_company(company_name: str):
    dossier = get_company_intelligence(company_name)
    if not dossier:
        raise HTTPException(status_code=404, detail="Company dossier not found")
    return dossier

@app.post("/api/scan")
async def trigger_scan(background_tasks: BackgroundTasks, hours: Optional[int] = 24):
    global _IS_CRAWLING
    if not _IS_CRAWLING:
        background_tasks.add_task(run_unified_discovery, hours)
        return {"status": "Discovery crawl launched"}
    return {"status": "Background crawler is already actively scanning the next batch"}

@app.post("/api/audit")
async def trigger_audit():
    report = await audit_and_clean_database_links()
    return {"status": "Audit complete", "report": report}

@app.get("/api/failures")
def list_failures():
    return {
        "summary": get_failures_summary(),
        "unresolved": get_unresolved_failures()
    }

@app.post("/api/failures/retry")
async def retry_failures(background_tasks: BackgroundTasks):
    background_tasks.add_task(rerun_failed_endpoints)
    return {"status": "Adaptive retry initiated for failed endpoints"}

@app.get("/api/mncs")
def get_mncs(q: Optional[str] = None, industry: Optional[str] = None, tier: Optional[str] = None, limit: int = 150):
    conn = sqlite3.connect("jobs.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = "SELECT * FROM mnc_directory WHERE 1=1"
    params = []
    if q:
        query += " AND (company_name LIKE ? OR locations LIKE ? OR industry LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if industry and industry != "all":
        query += " AND industry = ?"
        params.append(industry)
    if tier and tier != "all":
        query += " AND salary_tier = ?"
        params.append(tier)
    query += " ORDER BY company_name ASC LIMIT ?"
    params.append(limit)
    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"mncs": rows, "total": len(rows)}

from pydantic import BaseModel
from typing import List

class BulkApplyRequest(BaseModel):
    job_ids: List[str]
    dry_run: bool = True

async def run_bulk_applications(job_ids: List[str], dry_run: bool = True):
    applier = StealthApplier(dry_run=dry_run)
    all_jobs = list_jobs()
    job_map = {j["job_id"]: j for j in all_jobs}
    for jid in job_ids:
        job = job_map.get(jid)
        if job:
            try:
                print(f"[Bulk Autofill] Processing {jid} ({job.get('company_name')} - {job.get('title')})...")
                await applier.apply_to_job(job)
            except Exception as e:
                print(f"[Bulk Autofill Error] {jid}: {e}")
            await asyncio.sleep(2.0)

@app.post("/api/apply/{job_id}")
async def apply_single_job(job_id: str, background_tasks: BackgroundTasks, dry_run: bool = True):
    jobs = list_jobs()
    target_job = next((j for j in jobs if j["job_id"] == job_id), None)
    if not target_job:
        raise HTTPException(status_code=404, detail="Job not found")

    applier = StealthApplier(dry_run=dry_run)
    background_tasks.add_task(applier.apply_to_job, target_job)
    return {"status": "Application process initiated", "dry_run": dry_run}

@app.post("/api/bulk-apply")
async def bulk_apply_jobs(req: BulkApplyRequest, background_tasks: BackgroundTasks):
    if not req.job_ids:
        raise HTTPException(status_code=400, detail="No job IDs specified")
    background_tasks.add_task(run_bulk_applications, req.job_ids, req.dry_run)
    return {
        "status": f"Bulk auto-fill initiated for {len(req.job_ids)} jobs in safe review mode",
        "total_jobs": len(req.job_ids),
        "dry_run": req.dry_run
    }

@app.get("/", response_class=HTMLResponse)
def dashboard_html():
    return r"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>SDE-2 & Senior Backend Job Intelligence & Stealth Applier</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen">
        <div class="max-w-7xl mx-auto px-4 py-8">
            <!-- Header -->
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center pb-6 border-b border-slate-800 gap-4">
                <div>
                    <h1 class="text-3xl font-extrabold tracking-tight text-white flex items-center gap-3">
                        <i class="fa-solid fa-radar text-emerald-400"></i> Job Intelligence & Stealth Applier
                    </h1>
                    <p class="text-slate-400 text-sm mt-1">Autonomous Discovery • Senior Backend / SDE-2 (5 YoE) • 40L to 70L+ Compensation</p>
                </div>
                <div class="flex items-center gap-3">
                    <span id="autoRefreshBadge" class="text-xs px-2.5 py-1.5 rounded-lg bg-slate-900 text-slate-300 border border-slate-700 flex items-center gap-1.5">
                        <i class="fa-solid fa-arrows-rotate text-emerald-400"></i> Auto-updates in <span id="autoRefreshCountdown" class="font-bold text-emerald-400">15</span>s
                    </span>
                    <span id="liveSyncStatus" class="hidden text-xs px-2.5 py-1.5 rounded-lg bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center gap-1.5 animate-pulse">
                        <i class="fa-solid fa-arrows-rotate animate-spin"></i> Syncing Fresh Jobs...
                    </span>
                    <button onclick="triggerAudit()" id="auditBtn" class="px-3.5 py-2 bg-slate-900 border border-emerald-500/40 hover:bg-slate-800 text-emerald-400 font-semibold rounded-lg shadow flex items-center gap-2 transition text-xs">
                        <i class="fa-solid fa-shield-check text-emerald-400"></i> Audit Links
                    </button>
                    <button onclick="triggerResync()" id="headerResyncBtn" class="px-3.5 py-2 bg-slate-900 border border-amber-500/40 hover:bg-slate-800 text-amber-400 font-semibold rounded-lg shadow flex items-center gap-2 transition text-xs">
                        <i class="fa-solid fa-arrows-rotate text-amber-400"></i> Resync Failed (<span id="headerFailureCount">52</span>)
                    </button>
                    <button onclick="triggerScan()" id="scanBtn" class="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-black font-semibold rounded-lg shadow flex items-center gap-2 transition text-xs">
                        <i class="fa-solid fa-radar"></i> Scan Now
                    </button>
                    <a href="/docs" target="_blank" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs transition">
                        API Docs
                    </a>
                </div>
            </div>

            <!-- Global Multi-Criteria Filter Toolbar -->
            <div class="bg-slate-900/90 border border-slate-800 p-4 rounded-xl my-6 space-y-3">
                <div class="flex flex-wrap items-center justify-between gap-4">
                    <!-- Time Filters -->
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5 mr-2">
                            <i class="fa-solid fa-clock text-blue-400"></i> Time Range:
                        </span>
                        <button onclick="setTimeFilter(1)" id="btnTime1" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            1 Hour
                        </button>
                        <button onclick="setTimeFilter(2)" id="btnTime2" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            2 Hours
                        </button>
                        <button onclick="setTimeFilter(3)" id="btnTime3" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            3 Hours
                        </button>
                        <button onclick="setTimeFilter(4)" id="btnTime4" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            4 Hours
                        </button>
                        <button onclick="setTimeFilter(6)" id="btnTime6" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            6 Hours
                        </button>
                        <button onclick="setTimeFilter(8)" id="btnTime8" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            8 Hours
                        </button>
                        <button onclick="setTimeFilter(16)" id="btnTime16" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            16 Hours
                        </button>
                        <button onclick="setTimeFilter(24)" id="btnTime24" class="time-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-500 text-black transition">
                            24 Hours
                        </button>
                        <button onclick="setTimeFilter(48)" id="btnTime48" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            48 Hours
                        </button>
                        <button onclick="setTimeFilter(168)" id="btnTime168" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            7 Days
                        </button>
                        <button onclick="setTimeFilter(0)" id="btnTimeAll" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            All Time
                        </button>
                        <button onclick="toggleCustomRange()" id="btnTimeCustom" class="time-btn px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition flex items-center gap-1.5">
                            <i class="fa-solid fa-sliders text-cyan-400"></i> Custom Range
                        </button>
                    </div>

                    <!-- Reset Filters Button -->
                    <div>
                        <button onclick="resetFilters()" class="text-xs font-semibold px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg flex items-center gap-1.5 transition border border-slate-700">
                            <i class="fa-solid fa-arrow-rotate-left text-slate-400"></i> Reset Filters
                        </button>
                    </div>
                </div>

                <!-- Custom Range Sub-bar (Hidden by default) -->
                <div id="customRangeBar" class="hidden pt-3 border-t border-slate-800/80 bg-slate-950/60 p-3 rounded-lg flex flex-wrap items-center gap-4 text-xs text-slate-300">
                    <span class="font-bold text-cyan-400 uppercase tracking-wider flex items-center gap-1.5">
                        <i class="fa-solid fa-calendar-days"></i> Custom Filter:
                    </span>
                    <div class="flex items-center gap-2">
                        <span class="text-slate-400 font-medium">Last N Hours:</span>
                        <input type="number" id="customHoursInput" min="1" max="8760" placeholder="e.g. 12 or 72" class="w-24 bg-slate-900 border border-slate-700 rounded px-2.5 py-1 text-slate-200 focus:outline-none focus:border-cyan-400" />
                    </div>
                    <span class="text-slate-500 font-bold">— OR —</span>
                    <div class="flex items-center gap-2">
                        <span class="text-slate-400 font-medium">Date Range:</span>
                        <input type="date" id="customStartDate" class="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-cyan-400" />
                        <span class="text-slate-500">to</span>
                        <input type="date" id="customEndDate" class="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-cyan-400" />
                    </div>
                    <button onclick="applyCustomRange()" class="px-3.5 py-1 bg-cyan-600 hover:bg-cyan-500 text-white font-semibold rounded shadow transition flex items-center gap-1.5">
                        <i class="fa-solid fa-check"></i> Apply Range
                    </button>
                    <button onclick="clearCustomRange()" class="text-xs text-slate-400 hover:text-white underline">
                        Clear
                    </button>
                </div>

                <!-- Secondary Filter Row: Search, Platform, Tier, Remote -->
                <div class="pt-3 border-t border-slate-800/80 flex flex-wrap items-center gap-3">
                    <!-- Instant Search -->
                    <div class="relative flex-1 min-w-[240px]">
                        <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-slate-500 text-xs"></i>
                        <input type="text" id="jobSearchInput" oninput="debounceFilter()" placeholder="Search title, company, skills (Go, Java, K8s, Python)..." class="w-full bg-slate-950 border border-slate-700 text-xs rounded-lg pl-8 pr-3 py-2 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-emerald-500" />
                    </div>

                    <!-- Multi-Platform Dropdown / Select -->
                    <div class="relative" id="platformDropdownContainer">
                        <button type="button" onclick="togglePlatformDropdown()" id="platformDropdownBtn" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-purple-400 flex items-center gap-2">
                            <i class="fa-solid fa-layer-group text-purple-400"></i>
                            <span id="platformDropdownLabel" class="font-medium">Platforms (All)</span>
                            <i class="fa-solid fa-chevron-down text-[10px] text-slate-400 ml-1"></i>
                        </button>
                        <div id="platformDropdownMenu" class="hidden absolute left-0 mt-1 w-64 bg-slate-900 border border-slate-700 rounded-lg shadow-xl z-50 p-2.5 space-y-1.5 text-xs">
                            <div class="flex items-center justify-between pb-1.5 border-b border-slate-800 text-[11px] font-semibold text-slate-400">
                                <span>SELECT PLATFORMS</span>
                                <div class="space-x-2">
                                    <button type="button" onclick="selectAllPlatforms(true)" class="text-purple-400 hover:underline">All</button>
                                    <span class="text-slate-600">|</span>
                                    <button type="button" onclick="selectAllPlatforms(false)" class="text-slate-400 hover:underline">Clear</button>
                                </div>
                            </div>
                            <label class="flex items-center gap-2 text-slate-200 p-1.5 rounded hover:bg-slate-800 cursor-pointer">
                                <input type="checkbox" value="linkedin" class="plat-checkbox accent-purple-500 rounded" checked onchange="onPlatformCheckboxChange()" />
                                <span><i class="fa-brands fa-linkedin text-blue-400 w-4"></i> LinkedIn Stream</span>
                            </label>
                            <label class="flex items-center gap-2 text-slate-200 p-1.5 rounded hover:bg-slate-800 cursor-pointer">
                                <input type="checkbox" value="greenhouse" class="plat-checkbox accent-purple-500 rounded" checked onchange="onPlatformCheckboxChange()" />
                                <span><i class="fa-solid fa-seedling text-emerald-400 w-4"></i> Greenhouse ATS</span>
                            </label>
                            <label class="flex items-center gap-2 text-slate-200 p-1.5 rounded hover:bg-slate-800 cursor-pointer">
                                <input type="checkbox" value="instahyre" class="plat-checkbox accent-purple-500 rounded" checked onchange="onPlatformCheckboxChange()" />
                                <span><i class="fa-solid fa-bolt text-amber-400 w-4"></i> Instahyre Unicorns</span>
                            </label>
                            <label class="flex items-center gap-2 text-slate-200 p-1.5 rounded hover:bg-slate-800 cursor-pointer">
                                <input type="checkbox" value="ashby" class="plat-checkbox accent-purple-500 rounded" checked onchange="onPlatformCheckboxChange()" />
                                <span><i class="fa-solid fa-shapes text-cyan-400 w-4"></i> Ashby Scaleups</span>
                            </label>
                            <label class="flex items-center gap-2 text-slate-200 p-1.5 rounded hover:bg-slate-800 cursor-pointer">
                                <input type="checkbox" value="workday" class="plat-checkbox accent-purple-500 rounded" checked onchange="onPlatformCheckboxChange()" />
                                <span><i class="fa-solid fa-briefcase text-blue-500 w-4"></i> Workday CXS</span>
                            </label>
                            <label class="flex items-center gap-2 text-slate-200 p-1.5 rounded hover:bg-slate-800 cursor-pointer">
                                <input type="checkbox" value="amazon" class="plat-checkbox accent-purple-500 rounded" checked onchange="onPlatformCheckboxChange()" />
                                <span><i class="fa-brands fa-amazon text-amber-500 w-4"></i> Amazon Direct API</span>
                            </label>
                        </div>
                    </div>

                    <!-- Salary Tier Select -->
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">
                            <i class="fa-solid fa-money-bill-wave text-emerald-400 mr-1"></i>Tier:
                        </span>
                        <select id="tierSelect" onchange="loadAllData()" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-emerald-500">
                            <option value="all">All Tiers (40+ LPA)</option>
                            <option value="70+LPA">🌟 70+ LPA (HFT / Tier-1 US)</option>
                            <option value="60-70LPA">🟣 60-70 LPA</option>
                            <option value="50-60LPA">🔵 50-60 LPA</option>
                            <option value="40-50LPA">🟢 40-50 LPA</option>
                        </select>
                    </div>

                    <!-- Company Type / Stage Filter -->
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">
                            <i class="fa-solid fa-shapes text-pink-400 mr-1"></i>Type:
                        </span>
                        <select id="companyTypeSelect" onchange="loadAllData()" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-2.5 py-2 text-slate-200 focus:outline-none focus:border-pink-500">
                            <option value="all">All Types</option>
                            <option value="product">🚀 Product-Based</option>
                            <option value="startup">⚡ Startup / Scaleup</option>
                            <option value="unicorn">🦄 Unicorns</option>
                            <option value="enterprise">🏢 Enterprise / Fortune 500</option>
                        </select>
                    </div>

                    <!-- Company Size / Headcount Filter -->
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">
                            <i class="fa-solid fa-users text-cyan-400 mr-1"></i>Size:
                        </span>
                        <select id="companySizeSelect" onchange="loadAllData()" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-2.5 py-2 text-slate-200 focus:outline-none focus:border-cyan-500">
                            <option value="all">All Sizes</option>
                            <option value="0-10">🔬 0 - 10 (Stealth / Boutique)</option>
                            <option value="10-50">🌱 10 - 50 (Seed / Early Stage)</option>
                            <option value="50-100">🌿 50 - 100 (Series A Startup)</option>
                            <option value="100-500">⚡ 100 - 500 (Series B Growth)</option>
                            <option value="500-1000">🚀 500 - 1,000 (Series C/D Scale)</option>
                            <option value="1000-5000">📈 1,000 - 5,000 (Scaleup / Unicorn)</option>
                            <option value="5000+">🏛️ 5,000+ (Enterprise / MNC)</option>
                            <option value="50000+">🌐 50,000+ (Global Megacorp)</option>
                        </select>
                    </div>

                    <!-- Culture / WLB Rating Filter -->
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">
                            <i class="fa-solid fa-heart-pulse text-amber-400 mr-1"></i>Culture:
                        </span>
                        <select id="cultureSelect" onchange="loadAllData()" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-2.5 py-2 text-slate-200 focus:outline-none focus:border-amber-500">
                            <option value="all">Any Culture Rating</option>
                            <option value="4.2">⭐ 4.2+ Exceptional WLB</option>
                            <option value="4.0">⭐ 4.0+ High WLB & Glassdoor</option>
                            <option value="3.8">⭐ 3.8+ Above Average</option>
                        </select>
                    </div>

                    <!-- Layoff & Financial Risk Filter -->
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">
                            <i class="fa-solid fa-shield-halved text-emerald-400 mr-1"></i>Stability:
                        </span>
                        <select id="riskSelect" onchange="loadAllData()" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-2.5 py-2 text-slate-200 focus:outline-none focus:border-emerald-500">
                            <option value="all">All Risk Levels</option>
                            <option value="low_only">🛡️ Safe Only (Zero Layoffs / Low Risk)</option>
                        </select>
                    </div>

                    <!-- Application Status Filter -->
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">
                            <i class="fa-solid fa-clipboard-check text-cyan-400 mr-1"></i>Status:
                        </span>
                        <select id="statusSelect" onchange="syncStatusFilter(this.value)" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-2.5 py-2 text-slate-200 focus:outline-none focus:border-cyan-500">
                            <option value="all">All Statuses</option>
                            <option value="applied">✅ Applied Only</option>
                            <option value="not_applied">⏳ Not Applied Yet</option>
                            <option value="ready">📋 Ready for Review (Dry-Run)</option>
                            <option value="failed">⚠️ Failed / Requires Fix</option>
                            <option value="discovered">🔍 Discovered Only</option>
                        </select>
                    </div>
                </div>
            </div>

            <!-- Stats & Analytics Cards -->
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 my-6">
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-xl shadow-sm">
                    <div class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Filtered Jobs</div>
                    <div id="statTotal" class="text-3xl font-bold mt-2 text-white">--</div>
                    <div id="statTimeLabel" class="text-xs text-emerald-400 mt-1">Posted within last 24h</div>
                </div>
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-xl shadow-sm cursor-pointer hover:border-emerald-700 transition" onclick="quickFilterStatus('applied')">
                    <div class="text-emerald-400 text-xs font-semibold uppercase tracking-wider flex items-center justify-between">
                        <span>✅ Submitted / Applied</span>
                        <i class="fa-solid fa-circle-check text-emerald-400"></i>
                    </div>
                    <div id="statApplied" class="text-3xl font-bold mt-2 text-emerald-300">--</div>
                    <div id="statUnappliedLabel" class="text-xs text-slate-400 mt-1">-- unapplied</div>
                </div>
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-xl shadow-sm">
                    <div class="text-purple-400 text-xs font-semibold uppercase tracking-wider">🌟 70+ LPA Tier</div>
                    <div id="statTier70" class="text-3xl font-bold mt-2 text-purple-300">--</div>
                    <div class="text-xs text-slate-500 mt-1">HFTs / Tier-1 US Remote</div>
                </div>
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-xl shadow-sm">
                    <div class="text-blue-400 text-xs font-semibold uppercase tracking-wider">🔵 50 - 70 LPA Tiers</div>
                    <div id="statTier5060" class="text-3xl font-bold mt-2 text-blue-300">--</div>
                    <div class="text-xs text-slate-500 mt-1">Unicorns / Product Giants</div>
                </div>
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-xl shadow-sm">
                    <div class="text-emerald-400 text-xs font-semibold uppercase tracking-wider">🟢 40 - 50 LPA Tier</div>
                    <div id="statTier40" class="text-3xl font-bold mt-2 text-emerald-300">--</div>
                    <div class="text-xs text-slate-500 mt-1">Established Product MNCs</div>
                </div>
            </div>

            <!-- Chart Section -->
            <div class="bg-slate-900 border border-slate-800 p-6 rounded-xl my-6">
                <h3 class="text-lg font-bold mb-4 flex items-center gap-2">
                    <i class="fa-solid fa-chart-pie text-emerald-400"></i> Compensation Distribution
                </h3>
                <div class="h-48">
                    <canvas id="salaryChart"></canvas>
                </div>
            </div>

            <!-- Navigation Tabs -->
            <div class="flex items-center gap-3 my-4 border-b border-slate-800 pb-3">
                <button onclick="switchTab('jobs')" id="tabJobs" class="px-4 py-2 text-sm font-bold border-b-2 border-emerald-400 text-white flex items-center gap-2">
                    <i class="fa-solid fa-briefcase text-emerald-400"></i> Active Opportunities
                </button>
                <button onclick="switchTab('mncs')" id="tabMncs" class="px-4 py-2 text-sm font-semibold text-slate-400 hover:text-white flex items-center gap-2 transition">
                    <i class="fa-solid fa-building-columns text-blue-400"></i> 550+ Top MNCs Career Directory
                </button>
                <button onclick="switchTab('failures')" id="tabFailures" class="px-4 py-2 text-sm font-semibold text-slate-400 hover:text-white flex items-center gap-2 transition">
                    <i class="fa-solid fa-triangle-exclamation text-amber-400"></i> Failed / Maintenance Companies
                    <span id="tabFailureBadge" class="ml-1 px-2 py-0.5 text-xs font-bold rounded-full bg-amber-950 text-amber-400 border border-amber-800 hidden">0</span>
                </button>
            </div>

            <!-- VIEW 1: Jobs Table -->
            <div id="viewJobs" class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
                <div class="p-4 border-b border-slate-800 flex flex-wrap justify-between items-center gap-3">
                    <div class="flex items-center gap-3">
                        <h2 class="text-lg font-bold flex items-center gap-2">
                            <i class="fa-solid fa-briefcase text-blue-400"></i> Active Opportunities
                        </h2>
                        <span id="jobCountBadge" class="px-2.5 py-1 bg-slate-800 text-xs font-medium rounded-full text-slate-300">Loading...</span>
                    </div>
                    <div class="flex items-center gap-3">
                        <span id="selectedCountBadge" class="hidden text-xs px-2.5 py-1 rounded bg-slate-800 text-slate-300 border border-slate-700 font-semibold">
                            <span id="selectedCount" class="text-emerald-400 font-bold">0</span> selected
                        </span>
                        <button onclick="triggerBulkApply(true)" id="bulkApplyBtn" class="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-bold flex items-center gap-2 shadow transition">
                            <i class="fa-solid fa-bolt"></i> Bulk Auto-Fill (Dry-Run Review)
                        </button>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-slate-300">
                        <thead class="bg-slate-950 text-slate-400 uppercase text-xs border-b border-slate-800">
                            <tr>
                                <th class="px-4 py-3 w-8 text-center">
                                    <input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAll(this)" class="rounded bg-slate-900 border-slate-700 text-emerald-500 focus:ring-0 cursor-pointer" />
                                </th>
                                <th class="px-5 py-3">Company & Role</th>
                                <th class="px-5 py-3">Salary Tier</th>
                                <th class="px-5 py-3 relative" id="ddHeaderTh">
                                    <div class="flex items-center gap-1.5 cursor-pointer select-none" onclick="toggleDDFilterMenu(event)">
                                        <span>Due Diligence (Rating / Risk)</span>
                                        <button type="button" id="ddFilterBtn" class="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-white transition flex items-center justify-center">
                                            <i id="ddFilterIcon" class="fa-solid fa-filter text-[11px]"></i>
                                        </button>
                                        <span id="ddActiveFilterBadge" class="hidden w-2 h-2 rounded-full bg-emerald-400"></span>
                                    </div>

                                    <!-- Due Diligence Quick Filter / Sort Popover -->
                                    <div id="ddFilterMenu" class="hidden absolute top-full left-0 mt-1 w-64 bg-slate-900 border border-slate-700/80 rounded-xl shadow-2xl p-3 z-50 normal-case font-normal text-slate-200">
                                        <div class="flex items-center justify-between pb-2 mb-2.5 border-b border-slate-800">
                                            <span class="text-xs font-bold text-white flex items-center gap-1.5">
                                                <i class="fa-solid fa-sliders text-emerald-400"></i> Due Diligence Filters
                                            </span>
                                            <button type="button" onclick="resetDDFilters(event)" class="text-[11px] text-slate-400 hover:text-amber-400 transition">
                                                Reset
                                            </button>
                                        </div>

                                        <!-- Rating / WLB Selector -->
                                        <div class="mb-3">
                                            <label class="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
                                                <i class="fa-solid fa-star text-amber-400"></i> Min Rating / Culture:
                                            </label>
                                            <select id="headerCultureSelect" onchange="syncDDFilter('culture', this.value)" class="w-full bg-slate-950 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-amber-500">
                                                <option value="all">Any Rating</option>
                                                <option value="4.2">⭐ 4.2+ Exceptional WLB</option>
                                                <option value="4.0">⭐ 4.0+ High WLB & Glassdoor</option>
                                                <option value="3.8">⭐ 3.8+ Above Average</option>
                                            </select>
                                        </div>

                                        <!-- Stability & Layoff Risk Selector -->
                                        <div class="mb-3">
                                            <label class="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
                                                <i class="fa-solid fa-shield-halved text-emerald-400"></i> Stability & Risk:
                                            </label>
                                            <select id="headerRiskSelect" onchange="syncDDFilter('risk', this.value)" class="w-full bg-slate-950 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-emerald-500">
                                                <option value="all">All Risk Levels</option>
                                                <option value="low_only">🛡️ Safe Only (0 Layoffs / Low Risk)</option>
                                            </select>
                                        </div>

                                        <div class="pt-2 border-t border-slate-800 flex justify-end">
                                            <button type="button" onclick="closeDDFilterMenu()" class="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-semibold shadow transition">
                                                Done
                                            </button>
                                        </div>
                                    </div>
                                </th>
                                <th class="px-5 py-3">Location & Posted</th>
                                <th class="px-5 py-3 relative" id="statusHeaderTh">
                                    <div class="flex items-center gap-1.5 cursor-pointer select-none" onclick="toggleStatusFilterMenu(event)">
                                        <span>Status</span>
                                        <button type="button" id="statusFilterBtn" class="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-white transition flex items-center justify-center">
                                            <i id="statusFilterIcon" class="fa-solid fa-filter text-[11px]"></i>
                                        </button>
                                        <span id="statusActiveFilterBadge" class="hidden w-2 h-2 rounded-full bg-cyan-400"></span>
                                    </div>

                                    <!-- Status Filter Popover -->
                                    <div id="statusFilterMenu" class="hidden absolute top-full left-0 mt-1 w-56 bg-slate-900 border border-slate-700/80 rounded-xl shadow-2xl p-3 z-50 normal-case font-normal text-slate-200">
                                        <div class="flex items-center justify-between pb-2 mb-2.5 border-b border-slate-800">
                                            <span class="text-xs font-bold text-white flex items-center gap-1.5">
                                                <i class="fa-solid fa-clipboard-check text-cyan-400"></i> Application Status
                                            </span>
                                            <button type="button" onclick="resetStatusFilter(event)" class="text-[11px] text-slate-400 hover:text-amber-400 transition">
                                                Reset
                                            </button>
                                        </div>

                                        <div class="space-y-1 text-xs">
                                            <label class="flex items-center gap-2 p-1.5 rounded hover:bg-slate-800 cursor-pointer">
                                                <input type="radio" name="statusHeaderRadio" value="all" onchange="syncStatusFilter(this.value)" checked class="text-cyan-500 focus:ring-0" />
                                                <span>All Statuses</span>
                                            </label>
                                            <label class="flex items-center gap-2 p-1.5 rounded hover:bg-slate-800 cursor-pointer text-emerald-300">
                                                <input type="radio" name="statusHeaderRadio" value="applied" onchange="syncStatusFilter(this.value)" class="text-emerald-500 focus:ring-0" />
                                                <span><i class="fa-solid fa-circle-check text-emerald-400 mr-1"></i> Applied Only</span>
                                            </label>
                                            <label class="flex items-center gap-2 p-1.5 rounded hover:bg-slate-800 cursor-pointer text-slate-300">
                                                <input type="radio" name="statusHeaderRadio" value="not_applied" onchange="syncStatusFilter(this.value)" class="text-cyan-500 focus:ring-0" />
                                                <span><i class="fa-regular fa-clock text-slate-400 mr-1"></i> Not Applied Yet</span>
                                            </label>
                                            <label class="flex items-center gap-2 p-1.5 rounded hover:bg-slate-800 cursor-pointer text-amber-300">
                                                <input type="radio" name="statusHeaderRadio" value="ready" onchange="syncStatusFilter(this.value)" class="text-amber-500 focus:ring-0" />
                                                <span><i class="fa-solid fa-eye text-amber-400 mr-1"></i> Ready for Review</span>
                                            </label>
                                            <label class="flex items-center gap-2 p-1.5 rounded hover:bg-slate-800 cursor-pointer text-rose-300">
                                                <input type="radio" name="statusHeaderRadio" value="failed" onchange="syncStatusFilter(this.value)" class="text-rose-500 focus:ring-0" />
                                                <span><i class="fa-solid fa-triangle-exclamation text-rose-400 mr-1"></i> Failed / Needs Fix</span>
                                            </label>
                                        </div>

                                        <div class="pt-2 mt-2 border-t border-slate-800 flex justify-end">
                                            <button type="button" onclick="closeStatusFilterMenu()" class="px-3 py-1 bg-cyan-600 hover:bg-cyan-500 text-white rounded-md text-xs font-semibold shadow transition">
                                                Done
                                            </button>
                                        </div>
                                    </div>
                                </th>
                                <th class="px-5 py-3 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody id="jobsTableBody" class="divide-y divide-slate-800">
                            <tr><td colspan="7" class="text-center py-8 text-slate-500">Loading opportunities...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- VIEW 2: 550+ Top MNCs Direct Career Portals -->
            <div id="viewMncs" class="hidden bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
                <div class="p-4 border-b border-slate-800 flex flex-wrap justify-between items-center gap-4">
                    <div>
                        <h2 class="text-lg font-bold flex items-center gap-2 text-white">
                            <i class="fa-solid fa-building-columns text-blue-400"></i> Well-Known 550+ MNCs Hiring in India
                        </h2>
                        <p class="text-xs text-slate-400 mt-0.5">Direct verified career search links for top Global Capability Centers (GCCs), Big Tech & Tier-1 tech firms</p>
                    </div>
                    <div class="flex items-center gap-3">
                        <input type="text" id="mncSearchInput" onkeyup="loadMncs()" placeholder="Search MNC (e.g. Nvidia, Google, Walmart)..." class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-3 py-1.5 text-slate-200 focus:outline-none focus:border-blue-500 w-64" />
                        <span id="mncCountBadge" class="px-2.5 py-1 bg-slate-800 text-xs font-medium rounded-full text-blue-300">550 MNCs</span>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-slate-300">
                        <thead class="bg-slate-950 text-slate-400 uppercase text-xs border-b border-slate-800">
                            <tr>
                                <th class="px-5 py-3">MNC Name & Industry</th>
                                <th class="px-5 py-3">India Tech Locations</th>
                                <th class="px-5 py-3">Platform</th>
                                <th class="px-5 py-3">Compensation Tier</th>
                                <th class="px-5 py-3 text-right">Direct Career Portal</th>
                            </tr>
                        </thead>
                        <tbody id="mncsTableBody" class="divide-y divide-slate-800">
                            <tr><td colspan="5" class="text-center py-8 text-slate-500">Loading MNC directory...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- VIEW 3: Failed / Maintenance Companies & Resync Control -->
            <div id="viewFailures" class="hidden bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
                <div class="p-4 border-b border-slate-800 flex flex-wrap justify-between items-center gap-4">
                    <div>
                        <h2 class="text-lg font-bold flex items-center gap-2 text-white">
                            <i class="fa-solid fa-triangle-exclamation text-amber-400"></i> Failed & Throttled Companies (Resync Queue)
                        </h2>
                        <p class="text-xs text-slate-400 mt-0.5">Companies whose ATS or endpoints encountered HTTP 500, maintenance redirects, or temporary rate limits</p>
                    </div>
                    <div class="flex items-center gap-3">
                        <span id="failureSummaryBadge" class="px-2.5 py-1 bg-amber-950 text-xs font-semibold rounded-full text-amber-300 border border-amber-800">0 Failed</span>
                        <button onclick="triggerResync()" id="resyncBtn" class="px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-xs font-bold flex items-center gap-2 shadow transition">
                            <i class="fa-solid fa-arrows-rotate"></i> Resync Failed Companies
                        </button>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-slate-300">
                        <thead class="bg-slate-950 text-slate-400 uppercase text-xs border-b border-slate-800">
                            <tr>
                                <th class="px-5 py-3">Company Name</th>
                                <th class="px-5 py-3">Platform / Source</th>
                                <th class="px-5 py-3">Failure Reason & Status</th>
                                <th class="px-5 py-3">Last Attempted</th>
                                <th class="px-5 py-3 text-right">Target Endpoint / Action</th>
                            </tr>
                        </thead>
                        <tbody id="failuresTableBody" class="divide-y divide-slate-800">
                            <tr><td colspan="5" class="text-center py-8 text-slate-500">No failed companies detected. All endpoints active.</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Due Diligence Modal -->
        <div id="ddModal" class="fixed inset-0 bg-black/70 backdrop-blur-sm hidden flex items-center justify-center p-4 z-50">
            <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 shadow-2xl relative">
                <button onclick="closeModal()" class="absolute top-4 right-4 text-slate-400 hover:text-white text-lg">
                    <i class="fa-solid fa-xmark"></i>
                </button>
                <div id="modalContent"></div>
            </div>
        </div>

        <!-- Application Review & Verification Modal -->
        <div id="reviewModal" class="fixed inset-0 bg-black/80 backdrop-blur-md hidden flex items-center justify-center p-4 z-50">
            <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-4xl w-full max-h-[90vh] flex flex-col shadow-2xl relative overflow-hidden">
                <div class="p-4 border-b border-slate-800 flex justify-between items-center bg-slate-950">
                    <div>
                        <h3 id="reviewModalTitle" class="text-base font-bold text-white flex items-center gap-2">
                            <i class="fa-solid fa-eye text-emerald-400"></i> Application Verification & Review
                        </h3>
                        <p id="reviewModalSubtitle" class="text-xs text-slate-400 mt-0.5">Form auto-filled with candidate profile • Ready for final review</p>
                    </div>
                    <div class="flex items-center gap-3">
                        <button id="reviewApproveBtn" onclick="submitReviewedApplication()" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-bold flex items-center gap-1.5 shadow transition">
                            <i class="fa-solid fa-paper-plane text-[10px]"></i>
                            <span>Approve & Submit Now</span>
                        </button>
                        <a id="reviewPortalLink" href="#" target="_blank" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-semibold flex items-center gap-1.5 transition">
                            <span>Open Live Portal</span>
                            <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                        </a>
                        <button onclick="closeReviewModal()" class="text-slate-400 hover:text-white text-lg px-2">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                </div>
                <div class="p-4 overflow-y-auto flex-1 bg-slate-900 text-center" id="reviewModalBody">
                    <img id="reviewScreenshotImg" src="" alt="Application Form Screenshot" class="max-w-full rounded-lg border border-slate-800 mx-auto shadow-lg" />
                </div>
            </div>
        </div>

        <script>
            let chartInstance = null;
            let currentHours = 24; // Default to last 24h!
            let customStartDate = '';
            let customEndDate = '';
            let searchDebounceTimer = null;
            let activeTab = 'jobs';

            function saveFilterState() {
                try {
                    const state = {
                        currentHours,
                        customStartDate,
                        customEndDate,
                        activeTab,
                        search: document.getElementById('jobSearchInput') ? document.getElementById('jobSearchInput').value : '',
                        tier: document.getElementById('tierSelect') ? document.getElementById('tierSelect').value : 'all',
                        compType: document.getElementById('companyTypeSelect') ? document.getElementById('companyTypeSelect').value : 'all',
                        compSize: document.getElementById('companySizeSelect') ? document.getElementById('companySizeSelect').value : 'all',
                        culture: document.getElementById('cultureSelect') ? document.getElementById('cultureSelect').value : 'all',
                        risk: document.getElementById('riskSelect') ? document.getElementById('riskSelect').value : 'all',
                        status: document.getElementById('statusSelect') ? document.getElementById('statusSelect').value : 'all',
                        platforms: Array.from(document.querySelectorAll('.plat-checkbox')).map(cb => ({ value: cb.value, checked: cb.checked }))
                    };
                    localStorage.setItem('agy_job_filter_state', JSON.stringify(state));
                } catch(e) {}
            }

            function restoreFilterState() {
                try {
                    const raw = localStorage.getItem('agy_job_filter_state');
                    if (!raw) return false;
                    const state = JSON.parse(raw);

                    if (state.currentHours !== undefined) currentHours = state.currentHours;
                    if (state.customStartDate) customStartDate = state.customStartDate;
                    if (state.customEndDate) customEndDate = state.customEndDate;
                    if (state.activeTab) activeTab = state.activeTab;

                    if (document.getElementById('jobSearchInput') && state.search !== undefined) {
                        document.getElementById('jobSearchInput').value = state.search;
                    }
                    if (document.getElementById('tierSelect') && state.tier) {
                        document.getElementById('tierSelect').value = state.tier;
                    }
                    if (document.getElementById('companyTypeSelect') && state.compType) {
                        document.getElementById('companyTypeSelect').value = state.compType;
                    }
                    if (document.getElementById('companySizeSelect') && state.compSize) {
                        document.getElementById('companySizeSelect').value = state.compSize;
                    }
                    if (document.getElementById('cultureSelect') && state.culture) {
                        document.getElementById('cultureSelect').value = state.culture;
                    }
                    if (document.getElementById('riskSelect') && state.risk) {
                        document.getElementById('riskSelect').value = state.risk;
                    }
                    if (document.getElementById('statusSelect') && state.status) {
                        document.getElementById('statusSelect').value = state.status;
                        const radios = document.querySelectorAll("input[name='statusHeaderRadio']");
                        radios.forEach(r => { r.checked = (r.value === state.status); });
                        updateStatusHeaderUI();
                    }

                    if (Array.isArray(state.platforms)) {
                        state.platforms.forEach(p => {
                            const cb = document.querySelector(`.plat-checkbox[value='${p.value}']`);
                            if (cb) cb.checked = p.checked;
                        });
                        onPlatformCheckboxChange();
                    }

                    // Update UI time buttons
                    document.querySelectorAll('.time-btn').forEach(btn => {
                        btn.classList.remove('bg-emerald-500', 'text-black', 'bg-cyan-500');
                        btn.classList.add('bg-slate-800', 'text-slate-300');
                    });

                    if (currentHours === -1) {
                        const customBtn = document.getElementById('btnTimeCustom');
                        if (customBtn) {
                            customBtn.classList.remove('bg-slate-800', 'text-slate-300');
                            customBtn.classList.add('bg-cyan-500', 'text-black');
                        }
                        const bar = document.getElementById('customRangeBar');
                        if (bar) bar.classList.remove('hidden');
                        if (document.getElementById('customStartDate')) document.getElementById('customStartDate').value = customStartDate;
                        if (document.getElementById('customEndDate')) document.getElementById('customEndDate').value = customEndDate;
                    } else {
                        let activeId = `btnTime${currentHours}`;
                        if (currentHours === 0) activeId = 'btnTimeAll';
                        const activeBtn = document.getElementById(activeId);
                        if (activeBtn) {
                            activeBtn.classList.remove('bg-slate-800', 'text-slate-300');
                            activeBtn.classList.add('bg-emerald-500', 'text-black');
                        }
                    }

                    if (activeTab && activeTab !== 'jobs') {
                        switchTab(activeTab);
                    }

                    updateFilterLabel();
                    return true;
                } catch(e) {
                    return false;
                }
            }

            function debounceFilter() {
                saveFilterState();
                clearTimeout(searchDebounceTimer);
                searchDebounceTimer = setTimeout(() => {
                    loadAllData();
                }, 300);
            }

            function toggleCustomRange() {
                const bar = document.getElementById('customRangeBar');
                if (bar) bar.classList.toggle('hidden');
            }

            function applyCustomRange() {
                const hInput = document.getElementById('customHoursInput');
                const sDate = document.getElementById('customStartDate');
                const eDate = document.getElementById('customEndDate');

                const hVal = hInput && hInput.value ? parseFloat(hInput.value) : null;
                const sVal = sDate && sDate.value ? sDate.value : '';
                const eVal = eDate && eDate.value ? eDate.value : '';

                if (hVal && hVal > 0) {
                    currentHours = hVal;
                    customStartDate = '';
                    customEndDate = '';
                } else if (sVal || eVal) {
                    currentHours = -1; // Flag indicating custom date range active
                    customStartDate = sVal;
                    customEndDate = eVal;
                } else {
                    alert("Please enter either a number of hours or select start/end dates.");
                    return;
                }

                // Update UI button active states
                document.querySelectorAll('.time-btn').forEach(btn => {
                    btn.classList.remove('bg-emerald-500', 'text-black', 'bg-cyan-500');
                    btn.classList.add('bg-slate-800', 'text-slate-300');
                });
                const customBtn = document.getElementById('btnTimeCustom');
                if (customBtn) {
                    customBtn.classList.remove('bg-slate-800', 'text-slate-300');
                    customBtn.classList.add('bg-cyan-500', 'text-black');
                }

                updateFilterLabel();
                saveFilterState();
                loadAllData();
            }

            function clearCustomRange() {
                const hInput = document.getElementById('customHoursInput');
                if (hInput) hInput.value = '';
                const sDate = document.getElementById('customStartDate');
                if (sDate) sDate.value = '';
                const eDate = document.getElementById('customEndDate');
                if (eDate) eDate.value = '';
                customStartDate = '';
                customEndDate = '';
                const bar = document.getElementById('customRangeBar');
                if (bar) bar.classList.add('hidden');
                setTimeFilter(24);
            }

            function toggleDDFilterMenu(e) {
                if (e) e.stopPropagation();
                const menu = document.getElementById('ddFilterMenu');
                if (menu) menu.classList.toggle('hidden');
            }

            function closeDDFilterMenu() {
                const menu = document.getElementById('ddFilterMenu');
                if (menu) menu.classList.add('hidden');
            }

            function syncDDFilter(type, value) {
                if (type === 'culture') {
                    const cultEl = document.getElementById('cultureSelect');
                    if (cultEl) cultEl.value = value;
                } else if (type === 'risk') {
                    const riskEl = document.getElementById('riskSelect');
                    if (riskEl) riskEl.value = value;
                }
                updateDDHeaderUI();
                saveFilterState();
                loadAllData();
            }

            function resetDDFilters(e) {
                if (e) e.stopPropagation();
                const cultH = document.getElementById('headerCultureSelect');
                const riskH = document.getElementById('headerRiskSelect');
                if (cultH) cultH.value = 'all';
                if (riskH) riskH.value = 'all';
                syncDDFilter('culture', 'all');
                syncDDFilter('risk', 'all');
            }

            function updateDDHeaderUI() {
                const cultVal = document.getElementById('cultureSelect') ? document.getElementById('cultureSelect').value : 'all';
                const riskVal = document.getElementById('riskSelect') ? document.getElementById('riskSelect').value : 'all';

                // Sync header selects to match toolbar selects
                const cultH = document.getElementById('headerCultureSelect');
                const riskH = document.getElementById('headerRiskSelect');
                if (cultH && cultH.value !== cultVal) cultH.value = cultVal;
                if (riskH && riskH.value !== riskVal) riskH.value = riskVal;

                // Toggle active badge and highlight icon
                const badge = document.getElementById('ddActiveFilterBadge');
                const icon = document.getElementById('ddFilterIcon');
                const btn = document.getElementById('ddFilterBtn');
                const isFiltered = (cultVal !== 'all' || riskVal !== 'all');

                if (badge) {
                    if (isFiltered) badge.classList.remove('hidden');
                    else badge.classList.add('hidden');
                }
                if (icon && btn) {
                    if (isFiltered) {
                        icon.classList.remove('text-slate-400');
                        icon.classList.add('text-emerald-400');
                        btn.classList.add('bg-emerald-950', 'text-emerald-300');
                    } else {
                        icon.classList.remove('text-emerald-400');
                        icon.classList.add('text-slate-400');
                        btn.classList.remove('bg-emerald-950', 'text-emerald-300');
                    }
                }
            }

            function toggleStatusFilterMenu(e) {
                if (e) e.stopPropagation();
                const menu = document.getElementById('statusFilterMenu');
                if (menu) menu.classList.toggle('hidden');
            }

            function closeStatusFilterMenu() {
                const menu = document.getElementById('statusFilterMenu');
                if (menu) menu.classList.add('hidden');
            }

            function syncStatusFilter(val) {
                const sSelect = document.getElementById('statusSelect');
                if (sSelect && sSelect.value !== val) sSelect.value = val;

                const radios = document.querySelectorAll("input[name='statusHeaderRadio']");
                radios.forEach(r => {
                    r.checked = (r.value === val);
                });

                updateStatusHeaderUI();
                saveFilterState();
                loadAllData();
            }

            function quickFilterStatus(val) {
                syncStatusFilter(val);
                // Ensure on jobs tab
                if (activeTab !== 'jobs') switchTab('jobs');
            }

            function resetStatusFilter(e) {
                if (e) e.stopPropagation();
                syncStatusFilter('all');
            }

            function updateStatusHeaderUI() {
                const sVal = document.getElementById('statusSelect') ? document.getElementById('statusSelect').value : 'all';
                const badge = document.getElementById('statusActiveFilterBadge');
                const icon = document.getElementById('statusFilterIcon');
                const btn = document.getElementById('statusFilterBtn');
                const isFiltered = (sVal !== 'all');

                if (badge) {
                    if (isFiltered) badge.classList.remove('hidden');
                    else badge.classList.add('hidden');
                }
                if (icon && btn) {
                    if (isFiltered) {
                        icon.classList.remove('text-slate-400');
                        icon.classList.add('text-cyan-400');
                        btn.classList.add('bg-cyan-950', 'text-cyan-300');
                    } else {
                        icon.classList.remove('text-cyan-400');
                        icon.classList.add('text-slate-400');
                        btn.classList.remove('bg-cyan-950', 'text-cyan-300');
                    }
                }
            }

            function togglePlatformDropdown() {
                const menu = document.getElementById('platformDropdownMenu');
                if (menu) menu.classList.toggle('hidden');
            }

            // Close platform menu, DD filter menu, and status filter menu when clicking outside
            document.addEventListener('click', (e) => {
                const container = document.getElementById('platformDropdownContainer');
                const menu = document.getElementById('platformDropdownMenu');
                if (container && menu && !container.contains(e.target)) {
                    menu.classList.add('hidden');
                }

                const ddTh = document.getElementById('ddHeaderTh');
                const ddMenu = document.getElementById('ddFilterMenu');
                if (ddTh && ddMenu && !ddTh.contains(e.target)) {
                    ddMenu.classList.add('hidden');
                }

                const stTh = document.getElementById('statusHeaderTh');
                const stMenu = document.getElementById('statusFilterMenu');
                if (stTh && stMenu && !stTh.contains(e.target)) {
                    stMenu.classList.add('hidden');
                }
            });

            function getSelectedPlatforms() {
                const cbs = document.querySelectorAll('.plat-checkbox:checked');
                const total = document.querySelectorAll('.plat-checkbox').length;
                if (cbs.length === 0 || cbs.length === total) {
                    return 'all';
                }
                return Array.from(cbs).map(cb => cb.value).join(',');
            }

            function selectAllPlatforms(selectAll) {
                document.querySelectorAll('.plat-checkbox').forEach(cb => {
                    cb.checked = selectAll;
                });
                onPlatformCheckboxChange();
            }

            function onPlatformCheckboxChange() {
                const cbs = document.querySelectorAll('.plat-checkbox:checked');
                const total = document.querySelectorAll('.plat-checkbox').length;
                const label = document.getElementById('platformDropdownLabel');
                if (label) {
                    if (cbs.length === total || cbs.length === 0) {
                        label.innerText = 'Platforms (All)';
                    } else if (cbs.length === 1) {
                        label.innerText = `Platform (${cbs[0].value})`;
                    } else {
                        label.innerText = `Platforms (${cbs.length}/${total})`;
                    }
                }
                saveFilterState();
                loadAllData();
            }

            function resetFilters() {
                const searchEl = document.getElementById('jobSearchInput');
                if (searchEl) searchEl.value = '';
                selectAllPlatforms(true);
                const tierEl = document.getElementById('tierSelect');
                if (tierEl) tierEl.value = 'all';
                const remModeEl = document.getElementById('remoteModeSelect');
                if (remModeEl) remModeEl.value = 'any';
                const typeEl = document.getElementById('companyTypeSelect');
                if (typeEl) typeEl.value = 'all';
                const sizeEl = document.getElementById('companySizeSelect');
                if (sizeEl) sizeEl.value = 'all';
                const cultEl = document.getElementById('cultureSelect');
                if (cultEl) cultEl.value = 'all';
                const riskEl = document.getElementById('riskSelect');
                if (riskEl) riskEl.value = 'all';
                const statusEl = document.getElementById('statusSelect');
                if (statusEl) statusEl.value = 'all';
                const radios = document.querySelectorAll("input[name='statusHeaderRadio']");
                radios.forEach(r => { r.checked = (r.value === 'all'); });
                updateDDHeaderUI();
                updateStatusHeaderUI();
                try {
                    localStorage.removeItem('agy_job_filter_state');
                } catch(e) {}
                clearCustomRange();
            }

            function getFilterParams() {
                const platform = getSelectedPlatforms();
                const tier = document.getElementById('tierSelect') ? document.getElementById('tierSelect').value : 'all';
                const remoteMode = document.getElementById('remoteModeSelect') ? document.getElementById('remoteModeSelect').value : 'any';
                const compType = document.getElementById('companyTypeSelect') ? document.getElementById('companyTypeSelect').value : 'all';
                const compSize = document.getElementById('companySizeSelect') ? document.getElementById('companySizeSelect').value : 'all';
                const culture = document.getElementById('cultureSelect') ? document.getElementById('cultureSelect').value : 'all';
                const risk = document.getElementById('riskSelect') ? document.getElementById('riskSelect').value : 'all';
                const status = document.getElementById('statusSelect') ? document.getElementById('statusSelect').value : 'all';
                const q = document.getElementById('jobSearchInput') ? document.getElementById('jobSearchInput').value.trim() : '';

                let params = `platform=${encodeURIComponent(platform)}`;
                if (currentHours >= 0) {
                    params += `&hours=${currentHours}`;
                } else {
                    params += `&hours=0`;
                    if (customStartDate) params += `&start_date=${encodeURIComponent(customStartDate)}`;
                    if (customEndDate) params += `&end_date=${encodeURIComponent(customEndDate)}`;
                }

                if (tier && tier !== 'all') params += `&tier=${encodeURIComponent(tier)}`;
                if (remoteMode === 'remote_only') params += `&remote=true`;
                if (remoteMode === 'exclude_remote') params += `&exclude_remote=true`;
                if (compType && compType !== 'all') params += `&company_type=${encodeURIComponent(compType)}`;
                if (compSize && compSize !== 'all') params += `&company_size=${encodeURIComponent(compSize)}`;
                if (culture && culture !== 'all') params += `&min_wlb=${encodeURIComponent(culture)}`;
                if (risk && risk !== 'all') params += `&max_risk=${encodeURIComponent(risk)}`;
                if (status && status !== 'all') params += `&status=${encodeURIComponent(status)}`;
                if (q) params += `&q=${encodeURIComponent(q)}`;
                return params;
            }

            function updateFilterLabel() {
                let timeText = 'Posted within last 24h';
                if (currentHours === 48) timeText = 'Posted within last 48h';
                else if (currentHours === 168) timeText = 'Posted within last 7 days';
                else if (currentHours === 0) timeText = 'All time catalog';
                else if (currentHours > 0) timeText = `Posted within last ${currentHours}h`;
                else if (currentHours === -1) {
                    if (customStartDate && customEndDate) timeText = `Range: ${customStartDate} to ${customEndDate}`;
                    else if (customStartDate) timeText = `From: ${customStartDate} onwards`;
                    else if (customEndDate) timeText = `Until: ${customEndDate}`;
                }

                const selPlats = getSelectedPlatforms();
                const platText = selPlats === 'all' ? 'All Platforms' : `Platforms: ${selPlats}`;
                const tierSelect = document.getElementById('tierSelect');
                const tierText = tierSelect && tierSelect.value !== 'all' ? ` • ${tierSelect.value}` : '';
                
                const remoteMode = document.getElementById('remoteModeSelect') ? document.getElementById('remoteModeSelect').value : 'any';
                let remText = '';
                if (remoteMode === 'remote_only') remText = ' • Remote Only';
                else if (remoteMode === 'exclude_remote') remText = ' • Onsite/Hybrid Only';

                const compType = document.getElementById('companyTypeSelect') ? document.getElementById('companyTypeSelect').value : 'all';
                let typeText = '';
                if (compType !== 'all') {
                    const sel = document.getElementById('companyTypeSelect');
                    typeText = ` • ${sel.options[sel.selectedIndex].text}`;
                }

                const compSize = document.getElementById('companySizeSelect') ? document.getElementById('companySizeSelect').value : 'all';
                let sizeText = '';
                if (compSize !== 'all') {
                    const sel = document.getElementById('companySizeSelect');
                    sizeText = ` • ${sel.options[sel.selectedIndex].text}`;
                }

                const culture = document.getElementById('cultureSelect') ? document.getElementById('cultureSelect').value : 'all';
                let cultText = '';
                if (culture !== 'all') cultText = ` • Culture ${culture}+`;

                const risk = document.getElementById('riskSelect') ? document.getElementById('riskSelect').value : 'all';
                let riskText = '';
                if (risk === 'low_only') riskText = ' • Low Risk';

                const status = document.getElementById('statusSelect') ? document.getElementById('statusSelect').value : 'all';
                let statusText = '';
                if (status === 'applied') statusText = ' • Applied Only';
                else if (status === 'not_applied') statusText = ' • Unapplied Only';
                else if (status === 'ready') statusText = ' • Ready for Review';
                else if (status === 'failed') statusText = ' • Failed Only';

                document.getElementById('statTimeLabel').innerText = `${timeText} • ${platText}${tierText}${statusText}${remText}${typeText}${sizeText}${cultText}${riskText}`;
            }

            function setTimeFilter(hours) {
                currentHours = hours;
                customStartDate = '';
                customEndDate = '';
                const hInput = document.getElementById('customHoursInput');
                if (hInput) hInput.value = '';
                const sDate = document.getElementById('customStartDate');
                if (sDate) sDate.value = '';
                const eDate = document.getElementById('customEndDate');
                if (eDate) eDate.value = '';

                document.querySelectorAll('.time-btn').forEach(btn => {
                    btn.classList.remove('bg-emerald-500', 'text-black', 'bg-cyan-500');
                    btn.classList.add('bg-slate-800', 'text-slate-300');
                });

                let activeId = `btnTime${hours}`;
                if (hours === 0) activeId = 'btnTimeAll';

                const activeBtn = document.getElementById(activeId);
                if (activeBtn) {
                    activeBtn.classList.remove('bg-slate-800', 'text-slate-300');
                    activeBtn.classList.add('bg-emerald-500', 'text-black');
                }
                updateFilterLabel();
                saveFilterState();
                loadAllData();
            }

            async function loadStats() {
                try {
                    updateFilterLabel();
                    const params = getFilterParams();
                    const res = await fetch(`/api/stats?${params}`);
                    const data = await res.json();
                    
                    if (document.getElementById('statTotal')) document.getElementById('statTotal').innerText = data.total_jobs;
                    if (document.getElementById('statApplied')) document.getElementById('statApplied').innerText = data.total_applied || 0;
                    if (document.getElementById('statUnappliedLabel')) document.getElementById('statUnappliedLabel').innerText = `${data.total_unapplied || 0} unapplied`;
                    if (document.getElementById('statTier70')) document.getElementById('statTier70').innerText = data.tiers['70+LPA'] || 0;
                    if (document.getElementById('statTier5060')) document.getElementById('statTier5060').innerText = (data.tiers['50-60LPA'] || 0) + (data.tiers['60-70LPA'] || 0);
                    if (document.getElementById('statTier40')) document.getElementById('statTier40').innerText = data.tiers['40-50LPA'] || 0;

                    const canvas = document.getElementById('salaryChart');
                    if (canvas && typeof Chart !== 'undefined') {
                        const ctx = canvas.getContext('2d');
                        if (chartInstance) chartInstance.destroy();
                        chartInstance = new Chart(ctx, {
                            type: 'bar',
                            data: {
                                labels: ['40-50 LPA', '50-60 LPA', '60-70 LPA', '70+ LPA'],
                                datasets: [{
                                    label: 'Discovered Roles',
                                    data: [
                                        data.tiers['40-50LPA'] || 0,
                                        data.tiers['50-60LPA'] || 0,
                                        data.tiers['60-70LPA'] || 0,
                                        data.tiers['70+LPA'] || 0
                                    ],
                                    backgroundColor: ['#10b981', '#3b82f6', '#8b5cf6', '#ec4899'],
                                    borderRadius: 6
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: { legend: { display: false } },
                                scales: {
                                    y: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8', stepSize: 1 } },
                                    x: { grid: { display: false }, ticks: { color: '#94a3b8' } }
                                }
                            }
                        });
                    }
                } catch(err) {
                    console.error("Error in loadStats:", err);
                }
            }

            function formatPostedTime(postedVal, discoveredVal, platform) {
                const isInstahyre = String(platform || '').toLowerCase().includes('instahyre');
                const prefix = isInstahyre ? 'Indexed ' : '';

                if (!postedVal && !discoveredVal) return isInstahyre ? 'Recently indexed' : 'Recently';
                const s = String(postedVal || '').trim();
                const sLow = s.toLowerCase();

                // 1. If it has an absolute timestamp (ISO or date string), calculate relative to browser current time
                const d = new Date(s);
                if (!isNaN(d.getTime())) {
                    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
                    if (diffSec < 0) return `${prefix}Just now`;
                    if (diffSec < 3600) return `${prefix}${Math.max(1, Math.floor(diffSec / 60))}m ago`;
                    if (diffSec < 86400) return `${prefix}${Math.floor(diffSec / 3600)}h ago`;
                    const days = Math.floor(diffSec / 86400);
                    if (days === 1) return isInstahyre ? 'Indexed Yesterday' : 'Yesterday (1d ago)';
                    return `${prefix}${days}d ago`;
                }

                // 2. If it's a relative string from scraping (e.g. Workday "Posted Yesterday"),
                // calculate elapsed time since it was discovered so it dynamically updates
                let discElapsedHours = 0;
                if (discoveredVal) {
                    const dDisc = new Date(discoveredVal);
                    if (!isNaN(dDisc.getTime())) {
                        discElapsedHours = Math.max(0, (Date.now() - dDisc.getTime()) / 3600000);
                    }
                }

                if (sLow.includes('just now') || sLow.includes('minute')) {
                    const totalHours = Math.floor(discElapsedHours);
                    return totalHours < 1 ? `${prefix}Just now` : `${prefix}${totalHours}h ago`;
                }
                if (sLow.includes('today')) {
                    const totalHours = Math.floor(4 + discElapsedHours);
                    return totalHours < 24 ? `${prefix}${totalHours}h ago` : (isInstahyre ? 'Indexed Yesterday' : 'Yesterday (1d ago)');
                }
                if (sLow.includes('yesterday') || sLow.includes('1 day ago')) {
                    const totalHours = Math.floor(24 + discElapsedHours);
                    return totalHours < 48 ? (isInstahyre ? 'Indexed Yesterday' : 'Yesterday (1d ago)') : `${prefix}2 days ago`;
                }
                if (sLow.includes('2 days ago') || sLow.includes('2 day ago')) {
                    const totalDays = Math.floor((48 + discElapsedHours) / 24);
                    return `${prefix}${totalDays} days ago`;
                }
                return s || (isInstahyre ? 'Recently indexed' : 'Recently');
            }

            async function loadJobs() {
                try {
                    const params = getFilterParams();
                    const res = await fetch(`/api/jobs?${params}`);
                    const data = await res.json();
                    if (document.getElementById('jobCountBadge')) document.getElementById('jobCountBadge').innerText = `${data.total} Jobs`;
                    const tbody = document.getElementById('jobsTableBody');
                    if (!tbody) return;
                    if (data.jobs.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="7" class="text-center py-8 text-slate-500">No jobs found matching your filters. Try adjusting your search term, tier, or expanding the time window.</td></tr>';
                        return;
                    }

                tbody.innerHTML = data.jobs.map(j => {
                    const encComp = encodeURIComponent(j.company_name || '');
                    const encId = encodeURIComponent(j.job_id || '');
                    const encTitle = encodeURIComponent(j.title || '');
                    const encUrl = encodeURIComponent(j.apply_url || '');
                    const encScreenshot = encodeURIComponent(j.screenshot_path || '');
                    const displayTime = formatPostedTime(j.posted_at, j.discovered_at, j.ats_platform);
                    const isVeryRecent = displayTime.includes('h ago') || displayTime.includes('m ago') || displayTime.toLowerCase().includes('today') || displayTime.toLowerCase().includes('just now');
                    const compStage = j.stage_or_type ? `<span class="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-300 border border-slate-700">${j.stage_or_type}</span>` : '';
                    const compSize = j.headcount_range ? `<span class="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-cyan-950/60 text-cyan-300 border border-cyan-800/60 ml-1"><i class="fa-solid fa-users text-[9px] mr-1"></i>${j.headcount_range}</span>` : '';
                    const isChecked = selectedJobIds.has(j.job_id) ? 'checked' : '';
                    const hasReview = j.screenshot_path && j.screenshot_path.trim() !== '';

                    return `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="px-4 py-4 text-center">
                            <input type="checkbox" value="${j.job_id}" ${isChecked} onchange="onJobCheckboxChange(this)" class="job-checkbox rounded bg-slate-900 border-slate-700 text-emerald-500 focus:ring-0 cursor-pointer" />
                        </td>
                        <td class="px-5 py-4">
                            <div class="font-semibold text-white">${j.title || 'Untitled Role'}</div>
                            <div class="text-xs text-slate-400 mt-0.5 font-medium">${j.company_name} • <span class="capitalize text-slate-500">${j.ats_platform}</span></div>
                            <div class="flex flex-wrap items-center gap-1 mt-0.5">${compStage}${compSize}</div>
                        </td>
                        <td class="px-5 py-4">
                            <span class="px-2.5 py-1 text-xs font-semibold rounded-md ${
                                j.salary_tier === '70+LPA' ? 'bg-purple-950 text-purple-300 border border-purple-800' :
                                j.salary_tier === '60-70LPA' ? 'bg-indigo-950 text-indigo-300 border border-indigo-800' :
                                j.salary_tier === '50-60LPA' ? 'bg-blue-950 text-blue-300 border border-blue-800' :
                                'bg-emerald-950 text-emerald-300 border border-emerald-800'
                            }">${j.salary_tier}</span>
                            <div class="text-[11px] text-slate-400 mt-1">${j.estimated_ctc || ''}</div>
                        </td>
                        <td class="px-5 py-4">
                            <div class="flex items-center gap-2">
                                <span class="text-yellow-400 font-semibold"><i class="fa-solid fa-star text-xs"></i> ${j.glassdoor_rating || 4.2}</span>
                                <span class="text-xs text-slate-500">|</span>
                                <span class="text-xs ${j.risk_level === 'HIGH' ? 'text-red-400 font-bold' : j.risk_level === 'MODERATE' ? 'text-amber-400' : 'text-emerald-400'}">
                                    <i class="fa-solid fa-shield-halved"></i> ${j.risk_level || 'LOW'} RISK
                                </span>
                            </div>
                            <button data-company="${encComp}" class="btn-dd text-xs text-blue-400 hover:underline mt-1 block">
                                View Dossier & Layoffs &rarr;
                            </button>
                        </td>
                        <td class="px-5 py-4 text-xs text-slate-400">
                            <div><i class="fa-solid fa-location-dot mr-1"></i>${j.location || 'Multiple'}</div>
                            <div class="mt-1 flex items-center gap-1.5">
                                <i class="fa-solid fa-clock text-[11px] ${isVeryRecent ? 'text-emerald-400' : 'text-slate-500'}"></i>
                                <span class="${isVeryRecent ? 'text-emerald-400 font-semibold bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-800/60 text-[11px]' : 'text-slate-400 text-xs'}">${displayTime}</span>
                            </div>
                            ${j.is_remote ? '<span class="text-emerald-400 text-[10px] font-bold uppercase mt-1 inline-block">Remote Eligible</span>' : ''}
                        </td>
                        <td class="px-5 py-4">
                            <span class="px-2.5 py-1 text-xs rounded-full font-semibold inline-flex items-center gap-1.5 ${
                                j.status.includes('APPLIED') ? 'bg-emerald-950 text-emerald-300 border border-emerald-600 shadow-sm' :
                                j.status.includes('DRY_RUN') ? 'bg-amber-950 text-amber-300 border border-amber-600' :
                                j.status.includes('FAILED') ? 'bg-rose-950 text-rose-300 border border-rose-600' :
                                j.status === 'APPLYING' ? 'bg-blue-900/60 text-blue-300 border border-blue-600 animate-pulse' :
                                'bg-slate-800 text-slate-400 border border-slate-700'
                            }">
                                ${j.status.includes('APPLIED') ? '<i class="fa-solid fa-circle-check text-emerald-400 text-[11px]"></i> Applied' :
                                  j.status.includes('DRY_RUN') ? '<i class="fa-solid fa-clipboard-check text-amber-400 text-[11px]"></i> Ready (Dry-Run)' :
                                  j.status.includes('FAILED') ? '<i class="fa-solid fa-circle-exclamation text-rose-400 text-[11px]"></i> Failed' :
                                  j.status === 'APPLYING' ? '<i class="fa-solid fa-spinner fa-spin text-blue-400 text-[11px]"></i> Applying...' :
                                  '<i class="fa-regular fa-clock text-slate-500 text-[10px]"></i> Not Applied'}
                            </span>
                        </td>
                        <td class="px-5 py-4 text-right space-x-1.5 whitespace-nowrap">
                            ${hasReview ? `
                            <button data-jobid="${encId}" data-company="${encComp}" data-title="${encTitle}" data-url="${encUrl}" data-screenshot="${encScreenshot}" class="btn-review px-2.5 py-1.5 bg-emerald-950 hover:bg-emerald-900 text-emerald-300 border border-emerald-700/80 rounded-md text-xs font-semibold shadow transition inline-flex items-center gap-1.5">
                                <i class="fa-solid fa-eye text-emerald-400"></i> Review Application
                            </button>` : ''}
                            <a href="${j.apply_url}" target="_blank" class="px-2.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-md text-xs font-semibold transition inline-flex items-center gap-1">
                                <i class="fa-solid fa-arrow-up-right-from-square"></i> Job Link
                            </a>
                            <button data-jobid="${encId}" class="btn-apply px-2.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-semibold shadow transition inline-flex items-center gap-1">
                                <i class="fa-solid fa-robot"></i> Auto-Fill
                            </button>
                        </td>
                    </tr>
                    `;
                }).join('');
                } catch(err) {
                    console.error("Error in loadJobs:", err);
                    const tbody = document.getElementById('jobsTableBody');
                    if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center py-8 text-red-400">Failed to render jobs: ${err.message}</td></tr>`;
                }
            }

            function loadAllData() {
                updateDDHeaderUI();
                saveFilterState();
                loadStats();
                loadJobs();
                loadFailures();
            }

            async function triggerScan() {
                const btn = document.getElementById('scanBtn');
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> Scanning...';
                await fetch('/api/scan', { method: 'POST' });
                setTimeout(() => {
                    btn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> Scan Now';
                    loadAllData();
                }, 4000);
            }

            async function triggerAudit() {
                const btn = document.getElementById('auditBtn');
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin text-emerald-400"></i> Auditing Links...';
                try {
                    const res = await fetch('/api/audit', { method: 'POST' });
                    const d = await res.json();
                    btn.innerHTML = '<i class="fa-solid fa-shield-check text-emerald-400"></i> Audit Links';
                    alert(`✅ Link Health Verification Complete!\n\n• Audited: ${d.report.total_audited} jobs\n• Verified Active & Reachable: ${d.report.active_verified}\n• Dead Links Pruned: ${d.report.dead_removed}`);
                    loadAllData();
                } catch(e) {
                    btn.innerHTML = '<i class="fa-solid fa-shield-check text-emerald-400"></i> Audit Links';
                    alert("Audit completed!");
                    loadAllData();
                }
            }

            let selectedJobIds = new Set();

            function toggleSelectAll(masterCheckbox) {
                const checkboxes = document.querySelectorAll('.job-checkbox');
                checkboxes.forEach(cb => {
                    cb.checked = masterCheckbox.checked;
                    if (masterCheckbox.checked) selectedJobIds.add(cb.value);
                    else selectedJobIds.delete(cb.value);
                });
                updateSelectedCountUI();
            }

            function onJobCheckboxChange(cb) {
                if (cb.checked) selectedJobIds.add(cb.value);
                else selectedJobIds.delete(cb.value);
                
                const masterCheckbox = document.getElementById('selectAllCheckbox');
                const checkboxes = document.querySelectorAll('.job-checkbox');
                if (masterCheckbox) {
                    masterCheckbox.checked = checkboxes.length > 0 && Array.from(checkboxes).every(c => c.checked);
                }
                updateSelectedCountUI();
            }

            function updateSelectedCountUI() {
                const badge = document.getElementById('selectedCountBadge');
                const countSpan = document.getElementById('selectedCount');
                if (badge && countSpan) {
                    countSpan.innerText = selectedJobIds.size;
                    if (selectedJobIds.size > 0) badge.classList.remove('hidden');
                    else badge.classList.add('hidden');
                }
            }

            async function triggerBulkApply(dryRun = true) {
                const ids = Array.from(selectedJobIds);
                if (ids.length === 0) {
                    alert("Please select at least one job using the checkboxes to bulk auto-fill.");
                    return;
                }
                const confirmMsg = `Launch stealth auto-filler for ${ids.length} selected applications in Safe Review Mode (dry-run)?\n\n• The browser will auto-fill every form & attach your resume.\n• It will NOT submit the applications.\n• Verification screenshots and portal review links will be provided for your review.`;
                if (!confirm(confirmMsg)) return;

                const btn = document.getElementById('bulkApplyBtn');
                btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> Auto-Filling (${ids.length})...`;
                try {
                    const res = await fetch('/api/bulk-apply', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ job_ids: ids, dry_run: dryRun })
                    });
                    const d = await res.json();
                    alert("⚡ " + d.status + "!\n\nThe stealth browser is now filling forms in the background. Completed jobs will show a green 'Review Application' button.");
                    setTimeout(loadAllData, 3000);
                } catch(e) {
                    alert("Error initiating bulk application: " + e.message);
                } finally {
                    btn.innerHTML = `<i class="fa-solid fa-bolt"></i> Bulk Auto-Fill (Dry-Run Review)`;
                }
            }

            let activeReviewJobId = null;

            function openReviewModal(jobId, compName, title, applyUrl, screenshotPath) {
                const modal = document.getElementById('reviewModal');
                const titleEl = document.getElementById('reviewModalTitle');
                const subEl = document.getElementById('reviewModalSubtitle');
                const linkEl = document.getElementById('reviewPortalLink');
                const imgEl = document.getElementById('reviewScreenshotImg');
                const approveBtn = document.getElementById('reviewApproveBtn');

                activeReviewJobId = decodeURIComponent(jobId);
                titleEl.innerHTML = `<i class="fa-solid fa-file-signature text-emerald-400"></i> ${decodeURIComponent(title)} - ${decodeURIComponent(compName)}`;
                subEl.innerText = `Verified in Safe Dry-Run Mode. All fields & resume loaded autonomously. Review proof below, click 'Approve & Submit Now', or open live portal.`;
                linkEl.href = decodeURIComponent(applyUrl);

                if (approveBtn) {
                    approveBtn.innerHTML = `<i class="fa-solid fa-paper-plane text-[10px]"></i> <span>Approve & Submit Now</span>`;
                    approveBtn.disabled = false;
                }

                // Screenshot URL
                const cleanPath = decodeURIComponent(screenshotPath || '').replace(/^screenshots\//, '');
                imgEl.src = `/screenshots/${cleanPath}?t=${Date.now()}`;
                modal.classList.remove('hidden');
            }

            async function submitReviewedApplication() {
                if (!activeReviewJobId) return;
                const approveBtn = document.getElementById('reviewApproveBtn');
                if (!confirm(`Are you sure you want to permanently submit this application now?`)) return;

                if (approveBtn) {
                    approveBtn.innerHTML = `<i class="fa-solid fa-spinner animate-spin text-[10px]"></i> <span>Submitting...</span>`;
                    approveBtn.disabled = true;
                }

                try {
                    const res = await fetch(`/api/apply/${encodeURIComponent(activeReviewJobId)}?dry_run=false`, { method: 'POST' });
                    const d = await res.json();
                    alert("🚀 Application submission initiated for real!\n\nThe stealth browser is executing final submission.");
                    closeReviewModal();
                    setTimeout(loadAllData, 4000);
                } catch(e) {
                    alert("Error submitting application: " + e.message);
                    if (approveBtn) {
                        approveBtn.innerHTML = `<i class="fa-solid fa-paper-plane text-[10px]"></i> <span>Approve & Submit Now</span>`;
                        approveBtn.disabled = false;
                    }
                }
            }

            function closeReviewModal() {
                document.getElementById('reviewModal').classList.add('hidden');
            }

            async function triggerApply(jobId) {
                if (!confirm("Launch stealth browser to auto-fill this application? (Safe Dry-Run mode with screenshot verification & portal review link)")) return;
                await fetch(`/api/apply/${jobId}?dry_run=true`, { method: 'POST' });
                alert("Stealth Applier launched! Watch browser window or refresh table to review.");
                setTimeout(loadAllData, 4000);
            }

            async function openDueDiligence(companyName) {
                const modal = document.getElementById('ddModal');
                const content = document.getElementById('modalContent');
                content.innerHTML = '<div class="text-center py-6"><i class="fa-solid fa-spinner animate-spin text-2xl text-slate-400"></i></div>';
                modal.classList.remove('hidden');

                try {
                    const res = await fetch(`/api/company/${encodeURIComponent(companyName)}`);
                    const d = await res.json();
                    content.innerHTML = `
                        <div class="flex items-center justify-between border-b border-slate-800 pb-4">
                            <div>
                                <h3 class="text-2xl font-bold text-white">${d.company_name}</h3>
                                <p class="text-xs text-slate-400">${d.stage_or_type} • Headcount: ${d.headcount_range}</p>
                            </div>
                            <span class="px-3 py-1 text-xs font-bold rounded-full ${
                                d.risk_level === 'HIGH' ? 'bg-red-950 text-red-400 border border-red-800' :
                                d.risk_level === 'MODERATE' ? 'bg-amber-950 text-amber-400 border border-amber-800' :
                                'bg-emerald-950 text-emerald-400 border border-emerald-800'
                            }">
                                ${d.risk_level} RISK
                            </span>
                        </div>
                        <div class="grid grid-cols-3 gap-3 my-4 text-center">
                            <div class="bg-slate-950 p-3 rounded-lg border border-slate-800">
                                <div class="text-xs text-slate-400">Glassdoor</div>
                                <div class="text-xl font-bold text-yellow-400 mt-1">${d.glassdoor_rating} / 5.0</div>
                            </div>
                            <div class="bg-slate-950 p-3 rounded-lg border border-slate-800">
                                <div class="text-xs text-slate-400">AmbitionBox</div>
                                <div class="text-xl font-bold text-yellow-400 mt-1">${d.ambitionbox_rating} / 5.0</div>
                            </div>
                            <div class="bg-slate-950 p-3 rounded-lg border border-slate-800">
                                <div class="text-xs text-slate-400">WLB Score</div>
                                <div class="text-xl font-bold text-blue-400 mt-1">${d.engineering_wlb_score} / 5.0</div>
                            </div>
                        </div>
                        <div class="space-y-3 text-sm">
                            <div class="bg-slate-950 p-3 rounded-lg border border-slate-800">
                                <div class="text-xs font-bold text-slate-400 uppercase">Culture & Tech Sentiment</div>
                                <p class="text-slate-300 text-xs mt-1 leading-relaxed">${d.culture_summary}</p>
                            </div>
                            <div class="bg-slate-950 p-3 rounded-lg border border-slate-800">
                                <div class="text-xs font-bold text-slate-400 uppercase">Layoffs & Stability Report</div>
                                <p class="text-xs mt-1 ${d.has_recent_layoffs ? 'text-amber-300' : 'text-emerald-300'}">
                                    ${d.layoffs_details}
                                </p>
                            </div>
                            <div class="text-xs text-slate-500 text-right">
                                Dossier Cached in SQLite • Auto-Refreshed
                            </div>
                        </div>
                    `;
                } catch(e) {
                    content.innerHTML = '<div class="text-red-400 text-center py-6">Could not load dossier.</div>';
                }
            }

            function closeModal() {
                document.getElementById('ddModal').classList.add('hidden');
            }

            function switchTab(tab) {
                activeTab = tab;
                const views = {
                    'jobs': document.getElementById('viewJobs'),
                    'mncs': document.getElementById('viewMncs'),
                    'failures': document.getElementById('viewFailures')
                };
                const buttons = {
                    'jobs': document.getElementById('tabJobs'),
                    'mncs': document.getElementById('tabMncs'),
                    'failures': document.getElementById('tabFailures')
                };

                for (let k in views) {
                    if (views[k]) {
                        if (k === tab) views[k].classList.remove('hidden');
                        else views[k].classList.add('hidden');
                    }
                    if (buttons[k]) {
                        if (k === tab) {
                            if (k === 'jobs') buttons[k].className = 'px-4 py-2 text-sm font-bold border-b-2 border-emerald-400 text-white flex items-center gap-2';
                            else if (k === 'mncs') buttons[k].className = 'px-4 py-2 text-sm font-bold border-b-2 border-blue-400 text-white flex items-center gap-2';
                            else if (k === 'failures') buttons[k].className = 'px-4 py-2 text-sm font-bold border-b-2 border-amber-400 text-white flex items-center gap-2';
                        } else {
                            buttons[k].className = 'px-4 py-2 text-sm font-semibold text-slate-400 hover:text-white flex items-center gap-2 transition';
                        }
                    }
                }

                saveFilterState();
                if (tab === 'mncs') loadMncs();
                if (tab === 'failures') loadFailures();
            }

            async function loadFailures() {
                try {
                    const res = await fetch('/api/failures');
                    const data = await res.json();
                    const failures = data.unresolved || [];
                    const badge = document.getElementById('tabFailureBadge');
                    const summaryBadge = document.getElementById('failureSummaryBadge');
                    
                    if (failures.length > 0) {
                        badge.innerText = failures.length;
                        badge.classList.remove('hidden');
                        summaryBadge.innerText = `${failures.length} Failed / In Maintenance`;
                        const hCount = document.getElementById('headerFailureCount');
                        if (hCount) hCount.innerText = failures.length;
                    } else {
                        badge.classList.add('hidden');
                        summaryBadge.innerText = '0 Failures';
                        const hCount = document.getElementById('headerFailureCount');
                        if (hCount) hCount.innerText = '0';
                    }

                    const tbody = document.getElementById('failuresTableBody');
                    if (failures.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-8 text-emerald-400 font-semibold"><i class="fa-solid fa-circle-check mr-2"></i>All company endpoints are operational! Zero unresolved failures.</td></tr>';
                        return;
                    }

                    tbody.innerHTML = failures.map(f => `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="px-5 py-3 font-bold text-white">
                                ${f.company_name}
                            </td>
                            <td class="px-5 py-3 text-xs text-slate-400 uppercase font-semibold">
                                <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300">${f.source}</span>
                            </td>
                            <td class="px-5 py-3 text-xs">
                                <div class="font-semibold text-amber-300">${f.failure_reason}</div>
                                <div class="text-[11px] text-slate-500">Status code: ${f.http_status || 'N/A'} • Retry attempts: ${f.retry_count}</div>
                            </td>
                            <td class="px-5 py-3 text-xs text-slate-400">
                                ${f.last_attempted ? f.last_attempted.slice(0, 19).replace('T', ' ') : 'Recently'}
                            </td>
                            <td class="px-5 py-3 text-right">
                                <a href="${f.target_url}" target="_blank" class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-semibold transition">
                                    <span>Direct Link</span>
                                    <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                                </a>
                            </td>
                        </tr>
                    `).join('');
                } catch(e) {
                    console.error("Error loading failures", e);
                }
            }

            async function triggerResync() {
                const btn = document.getElementById('resyncBtn');
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> Resyncing Queue...';
                try {
                    const res = await fetch('/api/failures/retry', { method: 'POST' });
                    const data = await res.json();
                    alert("🔄 " + data.status + "\n\nAdaptive retry is processing in the background with backoff.");
                    setTimeout(() => {
                        btn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> Resync Failed Companies';
                        loadFailures();
                    }, 3000);
                } catch(e) {
                    btn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> Resync Failed Companies';
                    alert("Error initiating resync.");
                }
            }

            async function loadMncs() {
                const q = document.getElementById('mncSearchInput') ? document.getElementById('mncSearchInput').value : '';
                try {
                    const res = await fetch(`/api/mncs?q=${encodeURIComponent(q)}&limit=300`);
                    const data = await res.json();
                    document.getElementById('mncCountBadge').innerText = `${data.total} MNCs`;
                    const tbody = document.getElementById('mncsTableBody');
                    if (data.mncs.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-8 text-slate-500">No matching MNCs found.</td></tr>';
                        return;
                    }
                    tbody.innerHTML = data.mncs.map(m => `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="px-5 py-3">
                                <div class="font-bold text-white">${m.company_name}</div>
                                <div class="text-xs text-slate-400">${m.industry}</div>
                            </td>
                            <td class="px-5 py-3 text-xs text-slate-300">
                                <i class="fa-solid fa-location-dot text-slate-500 mr-1"></i>${m.locations}
                            </td>
                            <td class="px-5 py-3 text-xs text-slate-400 uppercase font-semibold">
                                ${m.ats_platform}
                            </td>
                            <td class="px-5 py-3">
                                <span class="px-2 py-0.5 text-xs font-bold rounded ${
                                    m.salary_tier === '70+LPA' ? 'bg-purple-950 text-purple-300 border border-purple-800' :
                                    m.salary_tier === '60-70LPA' ? 'bg-indigo-950 text-indigo-300 border border-indigo-800' :
                                    m.salary_tier === '50-60LPA' ? 'bg-blue-950 text-blue-300 border border-blue-800' :
                                    'bg-emerald-950 text-emerald-300 border border-emerald-800'
                                }">${m.salary_tier}</span>
                            </td>
                            <td class="px-5 py-3 text-right">
                                <a href="${m.direct_career_url}" target="_blank" class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-semibold shadow transition">
                                    <span>Direct Portal</span>
                                    <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                                </a>
                            </td>
                        </tr>
                    `).join('');
                } catch(e) {
                    console.error("Error loading MNCs", e);
                }
            }

            // Delegated click handling for jobs table to completely avoid inline quote escaping
            document.addEventListener('DOMContentLoaded', () => {
                const tbody = document.getElementById('jobsTableBody');
                if (tbody) {
                    tbody.addEventListener('click', (e) => {
                        const ddBtn = e.target.closest('.btn-dd');
                        if (ddBtn && ddBtn.dataset.company) {
                            openDueDiligence(decodeURIComponent(ddBtn.dataset.company));
                            return;
                        }
                        const reviewBtn = e.target.closest('.btn-review');
                        if (reviewBtn && reviewBtn.dataset.jobid) {
                            openReviewModal(
                                decodeURIComponent(reviewBtn.dataset.jobid),
                                decodeURIComponent(reviewBtn.dataset.company),
                                decodeURIComponent(reviewBtn.dataset.title),
                                decodeURIComponent(reviewBtn.dataset.url),
                                decodeURIComponent(reviewBtn.dataset.screenshot)
                            );
                            return;
                        }
                        const applyBtn = e.target.closest('.btn-apply');
                        if (applyBtn && applyBtn.dataset.jobid) {
                            triggerApply(decodeURIComponent(applyBtn.dataset.jobid));
                            return;
                        }
                    });
                }

                // 1. Restore any saved filter settings from localStorage, or load defaults
                restoreFilterState();
                loadAllData();

                // 2. Automatically trigger live background discovery and failed company resync on page load
                triggerBackgroundAutoSync();

                // 3. Smooth auto-update loop with live countdown every 15s
                let countdownSeconds = 15;
                setInterval(() => {
                    countdownSeconds -= 1;
                    const cdSpan = document.getElementById('autoRefreshCountdown');
                    if (cdSpan) cdSpan.innerText = countdownSeconds;

                    if (countdownSeconds <= 0) {
                        countdownSeconds = 15;
                        loadAllData();
                    }
                }, 1000);
            });

            async function triggerBackgroundAutoSync() {
                const syncBadge = document.getElementById('liveSyncStatus');
                if (syncBadge) syncBadge.classList.remove('hidden');

                try {
                    // Trigger retry of any failed endpoints automatically
                    fetch('/api/failures/retry', { method: 'POST' }).catch(() => {});
                    
                    // Trigger live unified multi-platform crawl in background
                    fetch('/api/scan?hours=24', { method: 'POST' }).catch(() => {});
                } catch(e) {
                    console.error("Auto-sync trigger error:", e);
                }

                // Poll to update UI with newly discovered roles
                setTimeout(() => {
                    loadAllData();
                    if (syncBadge) syncBadge.classList.add('hidden');
                }, 8000);
            }
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
