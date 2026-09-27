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
from typing import Optional
from database import list_jobs, get_company_intelligence, init_db
from ats_scanner import run_discovery_pipeline
from stealth_applier import StealthApplier

app = FastAPI(title="JobSearchAndApplyTool - Command Center")
os.makedirs("screenshots", exist_ok=True)
app.mount("/screenshots", StaticFiles(directory="screenshots"), name="screenshots")

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/api/jobs")
def get_jobs(status: Optional[str] = None, tier: Optional[str] = None, platform: Optional[str] = None, hours: Optional[int] = 24):
    jobs = list_jobs(status=status, salary_tier=tier, platform=platform, max_age_hours=hours)
    return {"jobs": jobs, "total": len(jobs)}

@app.get("/api/stats")
def get_stats(hours: Optional[int] = 24, platform: Optional[str] = None):
    jobs = list_jobs(platform=platform, max_age_hours=hours)
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
async def trigger_scan(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_discovery_pipeline, 72)
    return {"status": "Discovery pipeline started in background"}

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
    return """
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
                    <button onclick="triggerScan()" id="scanBtn" class="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-black font-semibold rounded-lg shadow flex items-center gap-2 transition">
                        <i class="fa-solid fa-arrows-rotate"></i> Scan Now
                    </button>
                    <a href="/docs" target="_blank" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm transition">
                        API Docs
                    </a>
                </div>
            </div>

            <!-- Global Time Filter Toolbar -->
            <div class="bg-slate-900/90 border border-slate-800 p-4 rounded-xl my-6 flex flex-wrap items-center justify-between gap-4">
                <div class="flex items-center gap-2">
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
                </div>

                <!-- Platform Filter dropdown -->
                <div class="flex items-center gap-2">
                    <span class="text-xs font-bold text-slate-400 uppercase tracking-wider mr-1">
                        <i class="fa-solid fa-layer-group text-purple-400"></i> Platform:
                    </span>
                    <select id="platformSelect" onchange="loadAllData()" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-3 py-1.5 text-slate-200 focus:outline-none focus:border-emerald-500">
                        <option value="all">All Sources</option>
                        <option value="linkedin">LinkedIn Stream</option>
                        <option value="greenhouse">Greenhouse ATS</option>
                        <option value="instahyre">Instahyre Unicorns</option>
                        <option value="ashby">Ashby Scaleups</option>
                        <option value="workday">Workday CXS</option>
                        <option value="amazon">Amazon Direct API</option>
                    </select>
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

            <!-- Jobs Table -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
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

            function updateFilterLabel() {
                let timeText = 'Posted within last 24h';
                if (currentHours === 48) timeText = 'Posted within last 48h';
                else if (currentHours === 168) timeText = 'Posted within last 7 days';
                else if (currentHours === 0) timeText = 'All time catalog';

                const pSelect = document.getElementById('platformSelect');
                const platText = pSelect.options[pSelect.selectedIndex].text;
                document.getElementById('statTimeLabel').innerText = `${timeText} • ${platText}`;
            }

            function setTimeFilter(hours) {
                currentHours = hours;
                document.querySelectorAll('.time-btn').forEach(btn => {
                    btn.classList.remove('bg-emerald-500', 'text-black');
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
                const platform = document.getElementById('platformSelect').value;
                updateFilterLabel();
                const res = await fetch(`/api/stats?hours=${currentHours}&platform=${platform}`);
                const data = await res.json();
                document.getElementById('statTotal').innerText = data.total_jobs;
                document.getElementById('statTier70').innerText = data.tiers['70+LPA'] || 0;
                document.getElementById('statTier5060').innerText = (data.tiers['50-60LPA'] || 0) + (data.tiers['60-70LPA'] || 0);
                document.getElementById('statTier40').innerText = data.tiers['40-50LPA'] || 0;

                const ctx = document.getElementById('salaryChart').getContext('2d');
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

            async function loadJobs() {
                const platform = document.getElementById('platformSelect').value;
                const res = await fetch(`/api/jobs?hours=${currentHours}&platform=${platform}`);
                const data = await res.json();
                document.getElementById('jobCountBadge').innerText = `${data.total} Jobs`;
                const tbody = document.getElementById('jobsTableBody');
                if (data.jobs.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-slate-500">No jobs found for selected time range & source. Try expanding the time filter.</td></tr>';
                    return;
                }

                tbody.innerHTML = data.jobs.map(j => `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="px-5 py-4">
                            <div class="font-semibold text-white">${j.title}</div>
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
                            <button onclick="openDueDiligence('${j.company_name}')" class="text-xs text-blue-400 hover:underline mt-1 block">
                                View Dossier & Layoffs &rarr;
                            </button>
                        </td>
                        <td class="px-5 py-4 text-xs text-slate-400">
                            <div><i class="fa-solid fa-location-dot mr-1"></i>${j.location || 'Multiple'}</div>
                            <div class="text-slate-500 mt-0.5"><i class="fa-solid fa-clock mr-1"></i>${j.posted_at || 'Recent'}</div>
                            ${j.is_remote ? '<span class="text-emerald-400 text-[10px] font-bold uppercase mt-0.5 inline-block">Remote Eligible</span>' : ''}
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
                            <button onclick="triggerApply('${j.job_id}')" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-semibold shadow transition">
                                <i class="fa-solid fa-robot"></i> Stealth Apply
                            </button>
                        </td>
                    </tr>
                `).join('');
            }

            function loadAllData() {
                loadStats();
                loadJobs();
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

            loadAllData();
            setInterval(loadAllData, 15000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
