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

app = FastAPI(title="JobSearchAndApplyTool - Command Center")
os.makedirs("screenshots", exist_ok=True)
app.mount("/screenshots", StaticFiles(directory="screenshots"), name="screenshots")

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/api/jobs")
def get_jobs(
    status: Optional[str] = None,
    tier: Optional[str] = None,
    platform: Optional[str] = None,
    q: Optional[str] = None,
    remote: Optional[bool] = None,
    hours: Optional[float] = 24,
    min_hours: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    jobs = list_jobs(
        status=status,
        salary_tier=tier,
        platform=platform,
        search_query=q,
        remote_only=remote,
        max_age_hours=hours,
        min_age_hours=min_hours,
        start_date=start_date,
        end_date=end_date
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
    remote: Optional[bool] = None
):
    jobs = list_jobs(
        status=status,
        salary_tier=tier,
        platform=platform,
        search_query=q,
        remote_only=remote,
        max_age_hours=hours,
        min_age_hours=min_hours,
        start_date=start_date,
        end_date=end_date
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

    return {
        "total_jobs": len(jobs),
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
    background_tasks.add_task(run_unified_discovery, hours)
    return {"status": "Unified Multi-Platform Discovery & Link Verification started in background"}

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

@app.post("/api/apply/{job_id}")
async def apply_single_job(job_id: str, background_tasks: BackgroundTasks, dry_run: bool = True):
    jobs = list_jobs()
    target_job = next((j for j in jobs if j["job_id"] == job_id), None)
    if not target_job:
        raise HTTPException(status_code=404, detail="Job not found")

    applier = StealthApplier(dry_run=dry_run)
    background_tasks.add_task(applier.apply_to_job, target_job)
    return {"status": "Application process initiated", "dry_run": dry_run}

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
                        <button onclick="setTimeFilter(24)" id="btnTime24" class="time-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-500 text-black transition">
                            Last 24 Hours
                        </button>
                        <button onclick="setTimeFilter(48)" id="btnTime48" class="time-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            Last 48 Hours
                        </button>
                        <button onclick="setTimeFilter(168)" id="btnTime168" class="time-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            Last 7 Days
                        </button>
                        <button onclick="setTimeFilter(0)" id="btnTimeAll" class="time-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition">
                            All Time (Full DB)
                        </button>
                        <button onclick="toggleCustomRange()" id="btnTimeCustom" class="time-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition flex items-center gap-1.5">
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

                    <!-- Platform Select -->
                    <div class="flex items-center gap-1.5">
                        <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">
                            <i class="fa-solid fa-layer-group text-purple-400 mr-1"></i>Platform:
                        </span>
                        <select id="platformSelect" onchange="loadAllData()" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-3 py-2 text-slate-200 focus:outline-none focus:border-emerald-500">
                            <option value="all">All Sources</option>
                            <option value="linkedin">LinkedIn Stream</option>
                            <option value="greenhouse">Greenhouse ATS</option>
                            <option value="instahyre">Instahyre Unicorns</option>
                            <option value="ashby">Ashby Scaleups</option>
                            <option value="workday">Workday CXS</option>
                            <option value="amazon">Amazon Direct API</option>
                        </select>
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

                    <!-- Remote Only Checkbox -->
                    <label class="flex items-center gap-2 text-xs font-semibold text-slate-300 bg-slate-950 px-3 py-2 rounded-lg border border-slate-700 cursor-pointer hover:border-slate-600 select-none">
                        <input type="checkbox" id="remoteFilter" onchange="loadAllData()" class="accent-emerald-500 rounded cursor-pointer" />
                        <span><i class="fa-solid fa-house-laptop text-emerald-400 mr-1"></i>Remote Only</span>
                    </label>
                </div>
            </div>

            <!-- Stats & Analytics Cards -->
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4 my-6">
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-xl shadow-sm">
                    <div class="text-slate-400 text-xs font-semibold uppercase tracking-wider">Filtered Jobs</div>
                    <div id="statTotal" class="text-3xl font-bold mt-2 text-white">--</div>
                    <div id="statTimeLabel" class="text-xs text-emerald-400 mt-1">Posted within last 24h</div>
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
                <div class="p-4 border-b border-slate-800 flex justify-between items-center">
                    <h2 class="text-lg font-bold flex items-center gap-2">
                        <i class="fa-solid fa-briefcase text-blue-400"></i> Active Opportunities
                    </h2>
                    <span id="jobCountBadge" class="px-2.5 py-1 bg-slate-800 text-xs font-medium rounded-full text-slate-300">Loading...</span>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-slate-300">
                        <thead class="bg-slate-950 text-slate-400 uppercase text-xs border-b border-slate-800">
                            <tr>
                                <th class="px-5 py-3">Company & Role</th>
                                <th class="px-5 py-3">Salary Tier</th>
                                <th class="px-5 py-3">Due Diligence (Rating / Risk)</th>
                                <th class="px-5 py-3">Location & Posted</th>
                                <th class="px-5 py-3">Status</th>
                                <th class="px-5 py-3 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody id="jobsTableBody" class="divide-y divide-slate-800">
                            <tr><td colspan="6" class="text-center py-8 text-slate-500">Loading opportunities...</td></tr>
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

        <script>
            let chartInstance = null;
            let currentHours = 24; // Default to last 24h!
            let customStartDate = '';
            let customEndDate = '';
            let searchDebounceTimer = null;

            function debounceFilter() {
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

            function resetFilters() {
                const searchEl = document.getElementById('jobSearchInput');
                if (searchEl) searchEl.value = '';
                const platEl = document.getElementById('platformSelect');
                if (platEl) platEl.value = 'all';
                const tierEl = document.getElementById('tierSelect');
                if (tierEl) tierEl.value = 'all';
                const remEl = document.getElementById('remoteFilter');
                if (remEl) remEl.checked = false;
                clearCustomRange();
            }

            function getFilterParams() {
                const platform = document.getElementById('platformSelect') ? document.getElementById('platformSelect').value : 'all';
                const tier = document.getElementById('tierSelect') ? document.getElementById('tierSelect').value : 'all';
                const remote = document.getElementById('remoteFilter') && document.getElementById('remoteFilter').checked ? 'true' : '';
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
                if (remote) params += `&remote=true`;
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

                const pSelect = document.getElementById('platformSelect');
                const platText = pSelect ? pSelect.options[pSelect.selectedIndex].text : 'All Sources';
                const tierSelect = document.getElementById('tierSelect');
                const tierText = tierSelect && tierSelect.value !== 'all' ? ` • ${tierSelect.value}` : '';
                const isRem = document.getElementById('remoteFilter') && document.getElementById('remoteFilter').checked ? ' • Remote Only' : '';
                document.getElementById('statTimeLabel').innerText = `${timeText} • ${platText}${tierText}${isRem}`;
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

                let activeId = 'btnTime24';
                if (hours === 48) activeId = 'btnTime48';
                else if (hours === 168) activeId = 'btnTime168';
                else if (hours === 0) activeId = 'btnTimeAll';

                const activeBtn = document.getElementById(activeId);
                if (activeBtn) {
                    activeBtn.classList.remove('bg-slate-800', 'text-slate-300');
                    activeBtn.classList.add('bg-emerald-500', 'text-black');
                }
                updateFilterLabel();
                loadAllData();
            }

            async function loadStats() {
                try {
                    updateFilterLabel();
                    const params = getFilterParams();
                    const res = await fetch(`/api/stats?${params}`);
                    const data = await res.json();
                    
                    if (document.getElementById('statTotal')) document.getElementById('statTotal').innerText = data.total_jobs;
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
                        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-slate-500">No jobs found matching your filters. Try adjusting your search term, tier, or expanding the time window.</td></tr>';
                        return;
                    }

                tbody.innerHTML = data.jobs.map(j => {
                    const encComp = encodeURIComponent(j.company_name || '');
                    const encId = encodeURIComponent(j.job_id || '');
                    const displayTime = formatPostedTime(j.posted_at, j.discovered_at, j.ats_platform);
                    const isVeryRecent = displayTime.includes('h ago') || displayTime.includes('m ago') || displayTime.toLowerCase().includes('today') || displayTime.toLowerCase().includes('just now');
                    return `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="px-5 py-4">
                            <div class="font-semibold text-white">${j.title || 'Untitled Role'}</div>
                            <div class="text-xs text-slate-400 mt-0.5 font-medium">${j.company_name} • <span class="capitalize text-slate-500">${j.ats_platform}</span></div>
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
                            <span class="px-2.5 py-1 text-xs rounded-full font-medium ${
                                j.status.includes('APPLIED') ? 'bg-emerald-900/60 text-emerald-300 border border-emerald-700' :
                                j.status.includes('DRY_RUN') ? 'bg-amber-900/60 text-amber-300 border border-amber-700' :
                                j.status === 'APPLYING' ? 'bg-blue-900/60 text-blue-300 animate-pulse' :
                                'bg-slate-800 text-slate-400'
                            }">${j.status}</span>
                        </td>
                        <td class="px-5 py-4 text-right space-x-2">
                            <a href="${j.apply_url}" target="_blank" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-md text-xs font-semibold transition">
                                <i class="fa-solid fa-arrow-up-right-from-square"></i> Job Link
                            </a>
                            <button data-jobid="${encId}" class="btn-apply px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-semibold shadow transition">
                                <i class="fa-solid fa-robot"></i> Stealth Apply
                            </button>
                        </td>
                    </tr>
                `;
                }).join('');
                } catch(err) {
                    console.error("Error in loadJobs:", err);
                    const tbody = document.getElementById('jobsTableBody');
                    if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-red-400">Failed to render jobs: ${err.message}</td></tr>`;
                }
            }

            function loadAllData() {
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

            async function triggerApply(jobId) {
                if (!confirm("Launch stealth browser to auto-fill this application? (Safe Dry-Run mode with screenshot verification)")) return;
                await fetch(`/api/apply/${jobId}?dry_run=true`, { method: 'POST' });
                alert("Stealth Applier launched! Watch browser window or refresh table to see screenshot.");
                setTimeout(loadAllData, 5000);
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
                        const applyBtn = e.target.closest('.btn-apply');
                        if (applyBtn && applyBtn.dataset.jobid) {
                            triggerApply(decodeURIComponent(applyBtn.dataset.jobid));
                            return;
                        }
                    });
                }

                // 1. Instantly render cached database listings & stats
                loadAllData();

                // 2. Automatically trigger live background discovery and failed company resync
                triggerBackgroundAutoSync();

                // 3. Periodic UI polling every 15s to display freshly scraped roles seamlessly
                setInterval(loadAllData, 15000);
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
