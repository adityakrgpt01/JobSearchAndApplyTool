"""
Dedicated Visa Careers Hub & Stealth Auto-Applier UI.
Renders a specialized dark-themed dashboard for Visa Workday applications.
"""

def get_visa_dashboard_html() -> str:
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Visa Careers Hub & Stealth Auto-Applier</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        @keyframes pulse-glow-visa {
            0%, 100% { box-shadow: 0 0 15px rgba(26, 31, 113, 0.4), 0 0 30px rgba(247, 182, 0, 0.2); }
            50% { box-shadow: 0 0 25px rgba(26, 31, 113, 0.7), 0 0 45px rgba(247, 182, 0, 0.5); }
        }
        .visa-glow { animation: pulse-glow-visa 3s infinite; }
        .visa-gradient { background: linear-gradient(135deg, #1A1F71 0%, #0056B3 50%, #F7B600 100%); }
        .visa-gradient-text {
            background: linear-gradient(135deg, #3B82F6 0%, #F7B600 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .visa-card { background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(12px); }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: #0B0F17; }
        ::-webkit-scrollbar-thumb { background: #1E293B; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #334155; }
    </style>
</head>
<body class="bg-[#080D1A] text-slate-100 min-h-screen font-sans antialiased selection:bg-blue-600 selection:text-white">

    <!-- Top Navigation Bar -->
    <header class="border-b border-slate-800/80 bg-[#0C1322]/90 backdrop-blur sticky top-0 z-40">
        <div class="max-w-7xl mx-auto px-4 py-3.5 flex flex-wrap items-center justify-between gap-4">
            <div class="flex items-center gap-3">
                <a href="/" class="px-3 py-1.5 bg-slate-800/80 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold flex items-center gap-2 transition border border-slate-700/60">
                    <i class="fa-solid fa-arrow-left"></i> All Companies
                </a>
                <a href="/mastercard" class="px-3 py-1.5 bg-slate-800/80 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold flex items-center gap-2 transition border border-slate-700/60">
                    <span class="w-2 h-2 rounded-full bg-red-500"></span> Mastercard
                </a>
                <a href="/lseg" class="px-3 py-1.5 bg-slate-800/80 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold flex items-center gap-2 transition border border-slate-700/60">
                    <span class="w-2 h-2 rounded-full bg-cyan-400"></span> LSEG
                </a>
                <div class="h-5 w-px bg-slate-700/60 hidden sm:block"></div>
                <div class="flex items-center gap-2.5">
                    <div class="px-2.5 py-1 bg-[#1A1F71] text-[#F7B600] font-black text-sm italic rounded border border-[#F7B600]/40 shadow-md">
                        VISA
                    </div>
                    <div>
                        <h1 class="text-lg font-extrabold tracking-tight text-white flex items-center gap-2">
                            Visa <span class="visa-gradient-text">Careers Hub</span>
                        </h1>
                        <p class="text-[11px] text-slate-400 font-medium">Workday Autonomous Auto-Applier • Bengaluru & India Tech Hubs</p>
                    </div>
                </div>
            </div>

            <div class="flex items-center gap-2.5">
                <button onclick="syncLiveJobs()" id="syncBtn" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-xs font-bold rounded-lg transition flex items-center gap-2">
                    <i class="fa-solid fa-rotate"></i>
                    <span>Sync Live CXS</span>
                </button>
                <button onclick="triggerBatchApply(true)" class="px-3.5 py-2 bg-gradient-to-r from-amber-600 to-yellow-600 hover:from-amber-500 hover:to-yellow-500 text-white text-xs font-bold rounded-lg shadow transition flex items-center gap-2">
                    <i class="fa-solid fa-eye"></i>
                    <span>Dry-Run (1 Job)</span>
                </button>
                <button onclick="triggerBatchApply(false)" class="px-4 py-2 bg-gradient-to-r from-blue-700 via-indigo-600 to-amber-500 hover:opacity-90 text-white text-xs font-bold rounded-lg shadow-lg flex items-center gap-2 transition border border-amber-400/40 visa-glow">
                    <i class="fa-solid fa-bolt"></i>
                    <span>Auto-Apply All Visa Jobs</span>
                </button>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-4 py-6 space-y-6">
        <!-- Stats Overview Cards -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div class="visa-card p-4 rounded-xl border border-slate-800 shadow relative overflow-hidden">
                <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Total Recorded</div>
                <div id="statTotal" class="text-2xl font-black text-white mt-1">--</div>
                <div class="text-[10px] text-slate-500 mt-1">Live CXS + DB records</div>
                <div class="absolute -right-2 -bottom-2 text-4xl text-slate-800/40"><i class="fa-solid fa-database"></i></div>
            </div>

            <div class="visa-card p-4 rounded-xl border border-emerald-500/30 bg-emerald-950/10 shadow relative overflow-hidden">
                <div class="text-[11px] font-semibold text-emerald-400 uppercase tracking-wider">Applied With Proof</div>
                <div id="statApplied" class="text-2xl font-black text-emerald-400 mt-1">--</div>
                <div class="text-[10px] text-emerald-500/70 mt-1">Confirmed submitted</div>
                <div class="absolute -right-2 -bottom-2 text-4xl text-emerald-500/10"><i class="fa-solid fa-circle-check"></i></div>
            </div>

            <div class="visa-card p-4 rounded-xl border border-amber-500/30 bg-amber-950/10 shadow relative overflow-hidden">
                <div class="text-[11px] font-semibold text-amber-400 uppercase tracking-wider">Pending Qualified</div>
                <div id="statPending" class="text-2xl font-black text-amber-400 mt-1">--</div>
                <div class="text-[10px] text-amber-500/70 mt-1">SWE II / Senior ready</div>
                <div class="absolute -right-2 -bottom-2 text-4xl text-amber-500/10"><i class="fa-solid fa-fire"></i></div>
            </div>

            <div class="visa-card p-4 rounded-xl border border-slate-800 shadow relative overflow-hidden">
                <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Filtered / Closed</div>
                <div id="statInvalid" class="text-2xl font-black text-slate-400 mt-1">--</div>
                <div class="text-[10px] text-slate-500 mt-1">Out-of-scope or expired</div>
                <div class="absolute -right-2 -bottom-2 text-4xl text-slate-800/40"><i class="fa-solid fa-ban"></i></div>
            </div>
        </div>

        <!-- Filter Tabs & Controls -->
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
            <div class="flex items-center gap-1.5 bg-slate-900/80 p-1 rounded-xl border border-slate-800">
                <button onclick="setFilter('ALL')" class="tab-btn px-3 py-1.5 rounded-lg text-xs font-semibold transition bg-blue-600 text-white" data-filter="ALL">All Jobs</button>
                <button onclick="setFilter('DISCOVERED')" class="tab-btn px-3 py-1.5 rounded-lg text-xs font-semibold transition text-slate-400 hover:text-white" data-filter="DISCOVERED">Pending Qualified</button>
                <button onclick="setFilter('APPLIED')" class="tab-btn px-3 py-1.5 rounded-lg text-xs font-semibold transition text-slate-400 hover:text-white" data-filter="APPLIED">Applied</button>
                <button onclick="setFilter('EXPIRED')" class="tab-btn px-3 py-1.5 rounded-lg text-xs font-semibold transition text-slate-400 hover:text-white" data-filter="EXPIRED">Expired (404)</button>
                <button onclick="setFilter('INVALID')" class="tab-btn px-3 py-1.5 rounded-lg text-xs font-semibold transition text-slate-400 hover:text-white" data-filter="INVALID">Out-of-Scope</button>
            </div>

            <div class="flex items-center gap-2">
                <input type="text" id="searchInput" oninput="renderTable()" placeholder="Search title or requisition ID..." class="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 w-64">
            </div>
        </div>

        <!-- Jobs Table -->
        <div class="visa-card rounded-xl border border-slate-800 overflow-hidden shadow-2xl">
            <div class="overflow-x-auto">
                <table class="w-full text-left text-xs">
                    <thead class="bg-slate-900/90 text-slate-400 uppercase tracking-wider text-[10px] border-b border-slate-800">
                        <tr>
                            <th class="px-4 py-3">Requisition</th>
                            <th class="px-4 py-3">Position Title</th>
                            <th class="px-4 py-3">Location</th>
                            <th class="px-4 py-3">Status</th>
                            <th class="px-4 py-3">Review Proof</th>
                            <th class="px-4 py-3 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="jobsTableBody" class="divide-y divide-slate-800/60">
                        <tr><td colspan="6" class="px-4 py-8 text-center text-slate-500">Loading Visa openings...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- Proof Modal -->
    <div id="proofModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-4xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl">
            <div class="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
                <div class="font-bold text-sm text-white flex items-center gap-2">
                    <i class="fa-solid fa-camera text-blue-400"></i>
                    <span id="modalTitle">Application Review Proof</span>
                </div>
                <button onclick="closeModal()" class="text-slate-400 hover:text-white px-2 py-1 rounded-lg text-sm">&times;</button>
            </div>
            <div class="p-4 overflow-y-auto flex-1 bg-black/40 flex justify-center">
                <img id="modalImg" src="" alt="Proof" class="rounded-lg shadow-lg max-w-full h-auto">
            </div>
        </div>
    </div>

    <script>
        let allJobs = [];
        let currentFilter = 'ALL';

        async function fetchJobs() {
            try {
                const res = await fetch('/api/visa/jobs?limit=500');
                const data = await res.json();
                allJobs = data.jobs || [];
                updateStats();
                renderTable();
            } catch (e) {
                console.error("Failed to fetch jobs:", e);
            }
        }

        function updateStats() {
            document.getElementById('statTotal').innerText = allJobs.length;
            document.getElementById('statApplied').innerText = allJobs.filter(j => j.status === 'APPLIED').length;
            document.getElementById('statPending').innerText = allJobs.filter(j => j.status === 'DISCOVERED').length;
            document.getElementById('statInvalid').innerText = allJobs.filter(j => j.status === 'INVALID' || j.status === 'EXPIRED').length;
        }

        function setFilter(filter) {
            currentFilter = filter;
            document.querySelectorAll('.tab-btn').forEach(btn => {
                if (btn.dataset.filter === filter) {
                    btn.className = 'tab-btn px-3 py-1.5 rounded-lg text-xs font-semibold transition bg-blue-600 text-white';
                } else {
                    btn.className = 'tab-btn px-3 py-1.5 rounded-lg text-xs font-semibold transition text-slate-400 hover:text-white';
                }
            });
            renderTable();
        }

        function renderTable() {
            const query = document.getElementById('searchInput').value.toLowerCase();
            const filtered = allJobs.filter(j => {
                const matchesFilter = (currentFilter === 'ALL') || (j.status === currentFilter);
                const matchesQuery = (j.title || '').toLowerCase().includes(query) || (j.job_id || '').toLowerCase().includes(query);
                return matchesFilter && matchesQuery;
            });

            const tbody = document.getElementById('jobsTableBody');
            if (!filtered.length) {
                tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-8 text-center text-slate-500">No matching Visa positions found.</td></tr>';
                return;
            }

            tbody.innerHTML = filtered.map(j => {
                let statusBadge = '';
                if (j.status === 'APPLIED') {
                    statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">APPLIED</span>';
                } else if (j.status === 'DISCOVERED') {
                    statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-400 border border-amber-500/30">QUALIFIED</span>';
                } else if (j.status === 'EXPIRED') {
                    statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-500/20 text-slate-400 border border-slate-500/30">EXPIRED</span>';
                } else {
                    statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30">OUT OF SCOPE</span>';
                }

                let proofCell = '<span class="text-slate-600 text-[11px]">—</span>';
                if (j.screenshot_path) {
                    proofCell = `<button onclick="viewProof('${j.screenshot_path}', '${j.job_id}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-blue-400 rounded text-[11px] font-medium border border-slate-700 flex items-center gap-1.5"><i class="fa-solid fa-image"></i> View Proof</button>`;
                }

                return `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="px-4 py-3 font-mono text-[11px] text-blue-300 font-bold">${j.job_id}</td>
                        <td class="px-4 py-3 font-medium text-slate-200">${j.title}</td>
                        <td class="px-4 py-3 text-slate-400 text-[11px]">${j.location || 'India'}</td>
                        <td class="px-4 py-3">${statusBadge}</td>
                        <td class="px-4 py-3">${proofCell}</td>
                        <td class="px-4 py-3 text-right">
                            <div class="flex items-center justify-end gap-1.5">
                                <a href="${j.apply_url}" target="_blank" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-[11px] transition" title="Open Job Portal"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>
                                ${j.status === 'DISCOVERED' ? `<button onclick="applySingle('${j.job_id}')" class="px-2.5 py-1 bg-blue-600 hover:bg-blue-500 text-white font-bold rounded text-[11px] transition">Apply</button>` : ''}
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        async function syncLiveJobs() {
            const btn = document.getElementById('syncBtn');
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Syncing...';
            btn.disabled = true;
            try {
                const res = await fetch('/api/visa/sync', { method: 'POST' });
                await fetchJobs();
            } finally {
                btn.innerHTML = '<i class="fa-solid fa-rotate"></i> <span>Sync Live CXS</span>';
                btn.disabled = false;
            }
        }

        async function triggerBatchApply(dryRun) {
            if (!confirm(dryRun ? 'Run 1-Job Dry Run on Visa Workday?' : 'Auto-apply to ALL pending qualified Visa positions?')) return;
            const res = await fetch('/api/visa/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dry_run: dryRun, headed: dryRun, limit: dryRun ? 1 : 100 })
            });
            const data = await res.json();
            alert(data.status || 'Application process initiated');
            setTimeout(fetchJobs, 2000);
        }

        async function applySingle(jobId) {
            const res = await fetch('/api/visa/apply', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_id: jobId, dry_run: false, headed: false })
            });
            alert('Application started for ' + jobId);
            setTimeout(fetchJobs, 2000);
        }

        function viewProof(path, id) {
            const cleanPath = path.startsWith('/') ? path : '/' + path;
            document.getElementById('modalImg').src = cleanPath;
            document.getElementById('modalTitle').innerText = 'Application Review Proof: ' + id;
            document.getElementById('proofModal').classList.remove('hidden');
        }

        function closeModal() {
            document.getElementById('proofModal').classList.add('hidden');
        }

        fetchJobs();
    </script>
</body>
</html>
"""
