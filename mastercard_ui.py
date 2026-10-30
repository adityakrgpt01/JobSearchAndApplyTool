"""
Dedicated Mastercard Careers Hub & Stealth Auto-Applier UI.
Renders a specialized dark-themed dashboard for Mastercard Workday applications.
"""

def get_mastercard_dashboard_html() -> str:
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mastercard Careers Hub & Stealth Auto-Applier</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        @keyframes pulse-glow {
            0%, 100% { box-shadow: 0 0 15px rgba(235, 0, 27, 0.4), 0 0 30px rgba(247, 158, 27, 0.2); }
            50% { box-shadow: 0 0 25px rgba(235, 0, 27, 0.7), 0 0 45px rgba(247, 158, 27, 0.4); }
        }
        .mc-glow { animation: pulse-glow 3s infinite; }
        .mc-gradient { background: linear-gradient(135deg, #EB001B 0%, #FF5F00 50%, #F79E1B 100%); }
        .mc-gradient-text {
            background: linear-gradient(135deg, #FF5F00 0%, #F79E1B 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .mc-card { background: rgba(18, 24, 38, 0.85); backdrop-filter: blur(12px); }
        /* Custom scrollbar */
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: #0B0F17; }
        ::-webkit-scrollbar-thumb { background: #1E293B; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #334155; }
    </style>
</head>
<body class="bg-[#0B0F17] text-slate-100 min-h-screen font-sans antialiased selection:bg-red-500 selection:text-white">

    <!-- Top Navigation Bar -->
    <header class="border-b border-slate-800/80 bg-[#0E1422]/90 backdrop-blur sticky top-0 z-40">
        <div class="max-w-7xl mx-auto px-4 py-3.5 flex flex-wrap items-center justify-between gap-4">
            <div class="flex items-center gap-3">
                <a href="/" class="px-3 py-1.5 bg-slate-800/80 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold flex items-center gap-2 transition border border-slate-700/60">
                    <i class="fa-solid fa-arrow-left"></i> All Companies
                </a>
                <a href="/visa" class="px-3 py-1.5 bg-slate-800/80 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold flex items-center gap-2 transition border border-slate-700/60">
                    <span class="w-2 h-2 rounded-full bg-blue-500"></span> Visa
                </a>
                <a href="/lseg" class="px-3 py-1.5 bg-slate-800/80 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold flex items-center gap-2 transition border border-slate-700/60">
                    <span class="w-2 h-2 rounded-full bg-cyan-400"></span> LSEG
                </a>
                <div class="h-5 w-px bg-slate-700/60 hidden sm:block"></div>
                <div class="flex items-center gap-2.5">
                    <!-- Mastercard Interlocking Circles Logo -->
                    <div class="relative w-9 h-6 flex items-center">
                        <div class="w-5 h-5 rounded-full bg-[#EB001B] opacity-90 absolute left-0 shadow-lg"></div>
                        <div class="w-5 h-5 rounded-full bg-[#F79E1B] opacity-90 absolute right-0 shadow-lg"></div>
                    </div>
                    <div>
                        <h1 class="text-lg font-extrabold tracking-tight text-white flex items-center gap-2">
                            Mastercard <span class="mc-gradient-text">Careers Hub</span>
                        </h1>
                        <p class="text-[11px] text-slate-400 font-medium">Workday Autonomous Auto-Applier • Pune & India Tech Hubs</p>
                    </div>
                </div>
            </div>

            <div class="flex items-center gap-2.5">
                <span class="text-xs bg-slate-800/80 border border-slate-700/60 px-3 py-1.5 rounded-lg text-slate-300 flex items-center gap-2">
                    <i class="fa-solid fa-user-circle text-amber-400"></i>
                    <span>Aditya Kumar (5 YoE)</span>
                </span>
                <button onclick="loadMastercardJobs()" class="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700/60 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition">
                    <i id="refreshIcon" class="fa-solid fa-rotate-right"></i> Refresh
                </button>
                <a href="/" class="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition shadow">
                    <i class="fa-solid fa-radar"></i> Main Radar
                </a>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-4 py-6 space-y-6">

        <!-- Banner Alert & Status -->
        <div id="statusBanner" class="hidden p-3.5 rounded-xl border flex items-center justify-between gap-3 text-xs transition">
            <div class="flex items-center gap-2" id="statusBannerText">
                <i class="fa-solid fa-circle-info"></i>
                <span>Status update message</span>
            </div>
            <button onclick="document.getElementById('statusBanner').classList.add('hidden')" class="text-slate-400 hover:text-white">
                <i class="fa-solid fa-xmark"></i>
            </button>
        </div>

        <!-- KPI Summary Cards -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <!-- Total Discovered -->
            <div class="mc-card border border-slate-800 p-4 rounded-xl shadow-lg relative overflow-hidden group hover:border-slate-700 transition">
                <div class="absolute top-0 right-0 w-24 h-24 bg-red-600/5 rounded-full blur-2xl group-hover:bg-red-600/10 transition"></div>
                <div class="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2">
                    <span>Total Roles</span>
                    <i class="fa-solid fa-briefcase text-slate-500"></i>
                </div>
                <div class="text-3xl font-extrabold text-white" id="statTotal">--</div>
                <div class="text-[11px] text-slate-400 mt-1">Qualified Software / Backend</div>
            </div>

            <!-- Direct Workday Portals -->
            <div class="mc-card border border-amber-500/30 p-4 rounded-xl shadow-lg relative overflow-hidden group hover:border-amber-500/50 transition">
                <div class="absolute top-0 right-0 w-24 h-24 bg-amber-500/10 rounded-full blur-2xl group-hover:bg-amber-500/20 transition"></div>
                <div class="flex items-center justify-between text-amber-400 text-xs font-semibold uppercase tracking-wider mb-2">
                    <span>Direct Workday</span>
                    <span class="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 text-[10px] font-bold">1-Click</span>
                </div>
                <div class="text-3xl font-extrabold text-amber-400" id="statWorkday">--</div>
                <div class="text-[11px] text-slate-400 mt-1">Direct Official Portal URLs</div>
            </div>

            <!-- Pending Apply -->
            <div class="mc-card border border-blue-500/30 p-4 rounded-xl shadow-lg relative overflow-hidden group hover:border-blue-500/50 transition">
                <div class="absolute top-0 right-0 w-24 h-24 bg-blue-500/10 rounded-full blur-2xl group-hover:bg-blue-500/20 transition"></div>
                <div class="flex items-center justify-between text-blue-400 text-xs font-semibold uppercase tracking-wider mb-2">
                    <span>Pending Apply</span>
                    <i class="fa-solid fa-clock text-blue-500"></i>
                </div>
                <div class="text-3xl font-extrabold text-blue-400" id="statPending">--</div>
                <div class="text-[11px] text-slate-400 mt-1">Ready for Auto-Fill Engine</div>
            </div>

            <!-- Applied Count -->
            <div class="mc-card border border-emerald-500/30 p-4 rounded-xl shadow-lg relative overflow-hidden group hover:border-emerald-500/50 transition">
                <div class="absolute top-0 right-0 w-24 h-24 bg-emerald-500/10 rounded-full blur-2xl group-hover:bg-emerald-500/20 transition"></div>
                <div class="flex items-center justify-between text-emerald-400 text-xs font-semibold uppercase tracking-wider mb-2">
                    <span>Applied / Done</span>
                    <i class="fa-solid fa-check-double text-emerald-500"></i>
                </div>
                <div class="text-3xl font-extrabold text-emerald-400" id="statApplied">--</div>
                <div class="text-[11px] text-slate-400 mt-1">Verified Submissions</div>
            </div>
        </div>

        <!-- Automation Control Center -->
        <div class="mc-card border border-slate-800 p-5 rounded-2xl shadow-xl relative overflow-hidden">
            <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-5">
                <div class="space-y-1">
                    <h2 class="text-base font-bold text-white flex items-center gap-2">
                        <i class="fa-solid fa-bolt-lightning text-amber-400"></i> Batch Auto-Apply Control Center
                    </h2>
                    <p class="text-xs text-slate-400">
                        Launches Playwright Stealth, solves Workday auth, uploads resume, answers India questionnaire, and captures proof.
                    </p>
                </div>

                <div class="flex flex-wrap items-center gap-3">
                    <!-- Batch Size -->
                    <div class="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-2 rounded-xl text-xs">
                        <span class="text-slate-400 font-medium">Batch Size:</span>
                        <select id="applyLimit" class="bg-transparent text-white font-bold outline-none cursor-pointer">
                            <option value="1" class="bg-slate-900">1 Job</option>
                            <option value="3" class="bg-slate-900" selected>3 Jobs</option>
                            <option value="5" class="bg-slate-900">5 Jobs</option>
                            <option value="10" class="bg-slate-900">10 Jobs</option>
                        </select>
                    </div>

                    <!-- Mode -->
                    <div class="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-2 rounded-xl text-xs">
                        <span class="text-slate-400 font-medium">Mode:</span>
                        <select id="applyMode" class="bg-transparent text-amber-400 font-bold outline-none cursor-pointer">
                            <option value="dry_run" class="bg-slate-900 text-amber-400" selected>👁️ Review First (Park on Review Screen)</option>
                            <option value="live" class="bg-slate-900 text-emerald-400">🚀 Live Submit (Auto-Submit Directly)</option>
                        </select>
                    </div>

                    <!-- Tabs Option -->
                    <label class="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-2 rounded-xl text-xs cursor-pointer hover:border-slate-700 transition">
                        <input type="checkbox" id="batchTabs" checked class="rounded border-slate-700 text-amber-500 focus:ring-0">
                        <span class="text-slate-300">Open in Parallel Tabs</span>
                    </label>

                    <!-- Trigger Button -->
                    <button onclick="triggerBatchApply()" id="btnBatchApply" class="mc-gradient hover:opacity-95 text-white font-bold text-xs px-5 py-2.5 rounded-xl shadow-lg flex items-center gap-2 transition transform active:scale-95">
                        <i class="fa-solid fa-play"></i>
                        <span>Start Auto-Apply</span>
                    </button>
                </div>
            </div>
        </div>

        <!-- Filter & Search Toolbar -->
        <div class="mc-card border border-slate-800 p-4 rounded-xl space-y-3">
            <div class="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3">
                <!-- Search Box -->
                <div class="relative flex-1">
                    <i class="fa-solid fa-magnifying-glass absolute left-3.5 top-3 text-slate-500 text-xs"></i>
                    <input type="text" id="searchInput" oninput="applyFilters()" placeholder="Filter by title (Java, Senior, AI, React, Distributed, DevSecOps)..." class="w-full bg-slate-900 border border-slate-800 rounded-xl pl-9 pr-8 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-amber-500/60 transition">
                    <button onclick="document.getElementById('searchInput').value=''; applyFilters();" class="absolute right-3 top-2.5 text-slate-500 hover:text-white text-xs">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>

                <!-- Dropdown Filters -->
                <div class="flex flex-wrap items-center gap-2">
                    <!-- Platform -->
                    <select id="platformFilter" onchange="applyFilters()" class="bg-slate-900 border border-slate-800 text-slate-300 rounded-xl px-3 py-2 text-xs font-semibold focus:outline-none focus:border-amber-500/60 cursor-pointer">
                        <option value="all">All Platforms</option>
                        <option value="workday">Workday Direct Only</option>
                        <option value="linkedin">LinkedIn Links</option>
                    </select>

                    <!-- Status -->
                    <select id="statusFilter" onchange="applyFilters()" class="bg-slate-900 border border-slate-800 text-slate-300 rounded-xl px-3 py-2 text-xs font-semibold focus:outline-none focus:border-amber-500/60 cursor-pointer">
                        <option value="all">All Statuses</option>
                        <option value="DISCOVERED" selected>Pending / Discovered</option>
                        <option value="APPLIED">Already Applied</option>
                        <option value="READY_TO_SUBMIT">Ready to Submit</option>
                        <option value="FAILED">Failed</option>
                    </select>

                    <!-- Location -->
                    <select id="locationFilter" onchange="applyFilters()" class="bg-slate-900 border border-slate-800 text-slate-300 rounded-xl px-3 py-2 text-xs font-semibold focus:outline-none focus:border-amber-500/60 cursor-pointer">
                        <option value="all">All Locations</option>
                        <option value="pune">Pune</option>
                        <option value="navi mumbai">Navi Mumbai</option>
                    </select>
                </div>
            </div>

            <!-- Quick Filter Badges -->
            <div class="flex flex-wrap items-center gap-1.5 pt-1 text-xs">
                <span class="text-slate-500 text-[11px] font-medium mr-1">Quick Filters:</span>
                <button onclick="setSearchFilter('Java')" class="px-2.5 py-1 bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800 rounded-lg text-[11px] transition">Java</button>
                <button onclick="setSearchFilter('Senior Software Engineer')" class="px-2.5 py-1 bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800 rounded-lg text-[11px] transition">Senior Engineer</button>
                <button onclick="setSearchFilter('Software Engineer II')" class="px-2.5 py-1 bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800 rounded-lg text-[11px] transition">SDE II</button>
                <button onclick="setSearchFilter('Full Stack')" class="px-2.5 py-1 bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800 rounded-lg text-[11px] transition">Full Stack</button>
                <button onclick="setSearchFilter('AI')" class="px-2.5 py-1 bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800 rounded-lg text-[11px] transition">AI Platform</button>
                <button onclick="setSearchFilter('DevSecOps')" class="px-2.5 py-1 bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800 rounded-lg text-[11px] transition">DevSecOps</button>
                <button onclick="setSearchFilter('')" class="px-2.5 py-1 bg-slate-900/80 hover:bg-slate-800 text-amber-400 border border-slate-800 rounded-lg text-[11px] transition">Reset</button>
            </div>
        </div>

        <!-- Jobs Table Container -->
        <div class="mc-card border border-slate-800 rounded-2xl shadow-xl overflow-hidden">
            <div class="p-4 border-b border-slate-800/80 flex items-center justify-between text-xs text-slate-400">
                <div class="flex items-center gap-2">
                    <span class="font-bold text-white" id="tableCountText">Showing 0 jobs</span>
                    <span id="filterIndicator" class="hidden text-[11px] bg-amber-500/10 text-amber-400 px-2 py-0.5 rounded border border-amber-500/20">Filtered</span>
                </div>
                <div class="text-[11px] text-slate-500">
                    Click 'Autofill' to test in visible browser
                </div>
            </div>

            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse text-xs">
                    <thead>
                        <tr class="bg-slate-900/70 border-b border-slate-800 text-slate-400 text-[11px] font-semibold uppercase tracking-wider">
                            <th class="py-3 px-4 w-12 text-center">#</th>
                            <th class="py-3 px-4">Position / Role</th>
                            <th class="py-3 px-4 w-36">Location</th>
                            <th class="py-3 px-4 w-32">Platform</th>
                            <th class="py-3 px-4 w-28">Status</th>
                            <th class="py-3 px-4 w-28 text-center">Proof</th>
                            <th class="py-3 px-4 w-52 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="mastercardTableBody" class="divide-y divide-slate-800/60 font-medium">
                        <tr>
                            <td colspan="7" class="py-12 text-center text-slate-500">
                                <i class="fa-solid fa-spinner animate-spin text-amber-400 text-xl mb-2"></i>
                                <p>Loading Mastercard positions...</p>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

    </main>

    <!-- Screenshot Proof Modal -->
    <div id="proofModal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-4xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl">
            <div class="p-4 border-b border-slate-800 flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <i class="fa-solid fa-image text-amber-400"></i>
                    <h3 class="font-bold text-sm text-white" id="modalTitle">Application Proof Screenshot</h3>
                </div>
                <button onclick="closeProofModal()" class="text-slate-400 hover:text-white px-2 py-1">
                    <i class="fa-solid fa-xmark text-lg"></i>
                </button>
            </div>
            <div class="p-4 overflow-y-auto flex-1 bg-black/50 text-center">
                <img id="modalImg" src="" alt="Proof Screenshot" class="max-w-full rounded-lg mx-auto shadow-lg border border-slate-800">
            </div>
            <div class="p-3 border-t border-slate-800 bg-slate-900/80 flex justify-end">
                <button onclick="closeProofModal()" class="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-white rounded-lg text-xs font-semibold">
                    Close
                </button>
            </div>
        </div>
    </div>

    <!-- Toast Notification -->
    <div id="toast" class="hidden fixed bottom-5 right-5 z-50 px-4 py-3 rounded-xl shadow-2xl text-xs font-bold border flex items-center gap-2.5 transition transform translate-y-2">
        <i id="toastIcon" class="fa-solid fa-check"></i>
        <span id="toastMsg">Action completed successfully</span>
    </div>

    <script>
        let allJobs = [];

        async function loadMastercardJobs() {
            const icon = document.getElementById('refreshIcon');
            if (icon) icon.classList.add('animate-spin');

            try {
                const res = await fetch('/api/mastercard/jobs?limit=250');
                const data = await res.json();
                allJobs = data.jobs || [];
                updateStats(allJobs);
                applyFilters();
            } catch (err) {
                console.error("Failed to load Mastercard jobs:", err);
                showToast("Failed to fetch Mastercard jobs: " + err.message, "error");
            } finally {
                if (icon) icon.classList.remove('animate-spin');
            }
        }

        function updateStats(jobs) {
            document.getElementById('statTotal').innerText = jobs.length;
            const workdayCount = jobs.filter(j => j.ats_platform === 'workday' || (j.apply_url && j.apply_url.includes('myworkdayjobs'))).length;
            document.getElementById('statWorkday').innerText = workdayCount;
            const appliedCount = jobs.filter(j => j.status && j.status.includes('APPLIED')).length;
            document.getElementById('statApplied').innerText = appliedCount;
            const pendingCount = jobs.filter(j => !j.status || j.status === 'DISCOVERED' || j.status.includes('READY')).length;
            document.getElementById('statPending').innerText = pendingCount;
        }

        function setSearchFilter(val) {
            document.getElementById('searchInput').value = val;
            applyFilters();
        }

        function applyFilters() {
            const search = (document.getElementById('searchInput').value || '').toLowerCase().trim();
            const platform = document.getElementById('platformFilter').value;
            const status = document.getElementById('statusFilter').value;
            const location = document.getElementById('locationFilter').value;

            const isFiltered = search || platform !== 'all' || status !== 'all' || location !== 'all';
            const filterIndicator = document.getElementById('filterIndicator');
            if (filterIndicator) {
                filterIndicator.classList.toggle('hidden', !isFiltered);
            }

            const filtered = allJobs.filter(j => {
                // Search query
                if (search) {
                    const matchTitle = (j.title || '').toLowerCase().includes(search);
                    const matchId = (j.job_id || '').toLowerCase().includes(search);
                    const matchLoc = (j.location || '').toLowerCase().includes(search);
                    if (!matchTitle && !matchId && !matchLoc) return false;
                }

                // Platform
                if (platform === 'workday') {
                    const isWd = j.ats_platform === 'workday' || (j.apply_url && j.apply_url.includes('myworkdayjobs'));
                    if (!isWd) return false;
                } else if (platform === 'linkedin') {
                    if (j.ats_platform !== 'linkedin') return false;
                }

                // Status
                if (status !== 'all') {
                    if (status === 'APPLIED' && (!j.status || !j.status.includes('APPLIED'))) return false;
                    if (status === 'DISCOVERED' && j.status !== 'DISCOVERED') return false;
                    if (status === 'READY_TO_SUBMIT' && (!j.status || !j.status.includes('READY'))) return false;
                    if (status === 'FAILED' && (!j.status || !j.status.includes('FAILED'))) return false;
                }

                // Location
                if (location !== 'all') {
                    const loc = (j.location || '').toLowerCase();
                    if (!loc.includes(location)) return false;
                }

                return true;
            });

            renderTable(filtered);
        }

        function renderTable(jobs) {
            const tbody = document.getElementById('mastercardTableBody');
            const countText = document.getElementById('tableCountText');
            if (countText) countText.innerText = `Showing ${jobs.length} of ${allJobs.length} jobs`;

            if (!jobs || jobs.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="7" class="py-12 text-center text-slate-500">
                            <i class="fa-solid fa-filter text-slate-600 text-xl mb-2"></i>
                            <p>No Mastercard positions match the current filters.</p>
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = jobs.map((j, idx) => {
                const isWorkday = j.ats_platform === 'workday' || (j.apply_url && j.apply_url.includes('myworkdayjobs'));
                const platformBadge = isWorkday 
                    ? `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/30 text-[11px] font-bold"><i class="fa-solid fa-bolt text-[10px]"></i> WORKDAY</span>`
                    : `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/30 text-[11px] font-semibold"><i class="fa-brands fa-linkedin text-[10px]"></i> LINKEDIN</span>`;

                let statusBadge = '';
                const st = j.status || 'DISCOVERED';
                if (st.includes('APPLIED')) {
                    statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 text-[11px] font-bold"><i class="fa-solid fa-check"></i> Applied</span>`;
                } else if (st.includes('READY')) {
                    statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/30 text-[11px] font-bold"><i class="fa-solid fa-eye"></i> Ready</span>`;
                } else if (st.includes('FAILED')) {
                    statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-400 border border-rose-500/30 text-[11px] font-bold"><i class="fa-solid fa-triangle-exclamation"></i> Failed</span>`;
                } else {
                    statusBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700 text-[11px] font-medium"><i class="fa-regular fa-clock"></i> Discovered</span>`;
                }

                // Proof screenshot button
                const hasScreenshot = j.screenshot_path || false;
                const proofBtn = hasScreenshot
                    ? `<button onclick="openProofModal('${encodeURIComponent(j.title)}', '/${j.screenshot_path}')" class="px-2 py-1 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border border-amber-500/30 rounded text-[11px] font-semibold transition flex items-center gap-1 mx-auto"><i class="fa-solid fa-image"></i> View</button>`
                    : `<span class="text-slate-600 text-[11px]">--</span>`;

                return `
                    <tr class="hover:bg-slate-900/60 transition group">
                        <td class="py-3 px-4 text-center text-slate-500 font-mono text-[11px]">${idx + 1}</td>
                        <td class="py-3 px-4">
                            <div class="font-bold text-white group-hover:text-amber-400 transition flex items-center gap-1.5">
                                ${escapeHtml(j.title)}
                            </div>
                            <div class="text-[11px] text-slate-500 font-mono flex items-center gap-2 mt-0.5">
                                <span>${escapeHtml(j.job_id)}</span>
                                ${j.salary_tier ? `<span class="text-emerald-400 font-semibold">• ${escapeHtml(j.salary_tier)}</span>` : ''}
                            </div>
                        </td>
                        <td class="py-3 px-4 text-slate-300">
                            <div class="flex items-center gap-1.5 text-xs">
                                <i class="fa-solid fa-location-dot text-slate-500 text-[11px]"></i>
                                <span>${escapeHtml(j.location || 'Pune, India')}</span>
                            </div>
                        </td>
                        <td class="py-3 px-4">${platformBadge}</td>
                        <td class="py-3 px-4">${statusBadge}</td>
                        <td class="py-3 px-4 text-center">${proofBtn}</td>
                        <td class="py-3 px-4 text-right space-x-1.5 whitespace-nowrap">
                            <button onclick="triggerSingleApply('${j.job_id}', false)" title="Auto-fill all steps and park on Review screen for candidate inspection" class="px-2.5 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40 rounded-lg text-xs font-bold transition inline-flex items-center gap-1 shadow-sm">
                                <i class="fa-solid fa-eye"></i> Review First
                            </button>
                            <button onclick="triggerSingleApply('${j.job_id}', true)" title="Auto-fill and submit application live" class="px-2.5 py-1.5 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/40 rounded-lg text-xs font-bold transition inline-flex items-center gap-1 shadow-sm">
                                <i class="fa-solid fa-paper-plane"></i> Auto-Submit
                            </button>
                            <button onclick="toggleAppliedStatus('${j.job_id}', '${st}')" title="Toggle Applied" class="px-2 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-lg text-xs transition inline-flex items-center">
                                <i class="fa-solid ${st.includes('APPLIED') ? 'fa-arrow-rotate-left text-amber-400' : 'fa-check text-emerald-400'}"></i>
                            </button>
                            <a href="${j.apply_url}" target="_blank" rel="noopener noreferrer" title="Open Job Posting in Workday" class="px-2 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-lg text-xs transition inline-flex items-center">
                                <i class="fa-solid fa-arrow-up-right-from-square"></i>
                            </a>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        async function triggerSingleApply(jobId, isLive) {
            const modeText = isLive ? "Auto-Submit" : "Review First";
            showToast(`Launching ${modeText} for ${jobId}...`, "info");
            try {
                const res = await fetch('/api/mastercard/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        job_id: jobId,
                        dry_run: !isLive,
                        headed: true,
                        batch_tabs: false
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    if (!isLive) {
                        showToast(`👁️ Headed Chrome launched in Review First mode! Form will park on Review step for you.`, "success");
                        showBanner(`Review First active for <b>${jobId}</b>: Browser is filling all steps and will park on the Review screen with your answers ready for inspection.`, "info");
                    } else {
                        showToast(`🚀 Live submission launched for ${jobId}!`, "success");
                        showBanner(`Live Auto-Applier active for <b>${jobId}</b>. Submitting directly...`, "success");
                    }
                    setTimeout(loadMastercardJobs, 5000);
                    setTimeout(loadMastercardJobs, 12000);
                    setTimeout(loadMastercardJobs, 25000);
                    setTimeout(loadMastercardJobs, 45000);
                } else {
                    showToast(data.detail || "Failed to start apply", "error");
                }
            } catch (e) {
                showToast("Network error: " + e.message, "error");
            }
        }

        async function triggerBatchApply() {
            const limit = parseInt(document.getElementById('applyLimit').value, 10);
            const mode = document.getElementById('applyMode').value;
            const batchTabs = document.getElementById('batchTabs').checked;
            const isLive = mode === 'live';

            const btn = document.getElementById('btnBatchApply');
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> Starting...`;

            try {
                const res = await fetch('/api/mastercard/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        limit: limit,
                        dry_run: !isLive,
                        headed: true,
                        batch_tabs: batchTabs
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    showToast(`⚡ Batch of ${limit} Mastercard applications launched!`, "success");
                    showBanner(`Batch started: processing ${limit} jobs in visible Chrome tabs. Check your screen to review prefilled forms.`, "success");
                    setTimeout(loadMastercardJobs, 4000);
                } else {
                    showToast(data.detail || "Failed to start batch", "error");
                }
            } catch (e) {
                showToast("Error: " + e.message, "error");
            } finally {
                btn.disabled = false;
                btn.innerHTML = `<i class="fa-solid fa-play"></i> <span>Start Auto-Apply</span>`;
            }
        }

        async function toggleAppliedStatus(jobId, currentStatus) {
            const newStatus = currentStatus.includes('APPLIED') ? 'DISCOVERED' : 'APPLIED';
            try {
                const res = await fetch('/api/job/status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ job_id: jobId, status: newStatus })
                });
                if (res.ok) {
                    showToast(`Marked ${jobId} as ${newStatus}`, "success");
                    // Update local state
                    const target = allJobs.find(j => j.job_id === jobId);
                    if (target) target.status = newStatus;
                    updateStats(allJobs);
                    applyFilters();
                } else {
                    showToast("Failed to update status", "error");
                }
            } catch (e) {
                showToast("Error: " + e.message, "error");
            }
        }

        function openProofModal(title, imgSrc) {
            document.getElementById('modalTitle').innerText = decodeURIComponent(title);
            document.getElementById('modalImg').src = imgSrc;
            document.getElementById('proofModal').classList.remove('hidden');
        }

        function closeProofModal() {
            document.getElementById('proofModal').classList.add('hidden');
        }

        function showBanner(htmlMsg, type) {
            const b = document.getElementById('statusBanner');
            const txt = document.getElementById('statusBannerText');
            txt.innerHTML = htmlMsg;
            b.className = `p-3.5 rounded-xl border flex items-center justify-between gap-3 text-xs transition ` + 
                (type === 'success' ? 'bg-emerald-950/80 border-emerald-800 text-emerald-300' : 'bg-amber-950/80 border-amber-800 text-amber-300');
            b.classList.remove('hidden');
        }

        function showToast(msg, type = "success") {
            const toast = document.getElementById('toast');
            const msgEl = document.getElementById('toastMsg');
            const icon = document.getElementById('toastIcon');

            msgEl.innerText = msg;
            if (type === "success") {
                toast.className = "fixed bottom-5 right-5 z-50 px-4 py-3 rounded-xl shadow-2xl text-xs font-bold border flex items-center gap-2.5 transition transform translate-y-0 bg-emerald-950 text-emerald-300 border-emerald-700";
                icon.className = "fa-solid fa-check text-emerald-400";
            } else if (type === "error") {
                toast.className = "fixed bottom-5 right-5 z-50 px-4 py-3 rounded-xl shadow-2xl text-xs font-bold border flex items-center gap-2.5 transition transform translate-y-0 bg-rose-950 text-rose-300 border-rose-700";
                icon.className = "fa-solid fa-triangle-exclamation text-rose-400";
            } else {
                toast.className = "fixed bottom-5 right-5 z-50 px-4 py-3 rounded-xl shadow-2xl text-xs font-bold border flex items-center gap-2.5 transition transform translate-y-0 bg-slate-900 text-amber-300 border-amber-600/50";
                icon.className = "fa-solid fa-circle-info text-amber-400";
            }

            toast.classList.remove('hidden');
            setTimeout(() => {
                toast.classList.add('hidden');
            }, 4500);
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        // Initialize on page load
        document.addEventListener('DOMContentLoaded', () => {
            loadMastercardJobs();
            // Automatically poll every 15s to capture live status changes and proof screenshots
            setInterval(loadMastercardJobs, 15000);
        });
    </script>
</body>
</html>
"""
