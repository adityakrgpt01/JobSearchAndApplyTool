#!/usr/bin/env python3
"""
Hourly Mastercard Autonomous Job Discovery and Auto-Apply Runner.
Designed to be executed hourly via cron or task scheduler.
1. Syncs all live openings from Mastercard Workday CXS API.
2. Audits and marks database statuses (APPLIED, EXPIRED, INVALID).
3. If any qualified unapplied openings exist, launches batch auto-apply.
4. Logs execution status to logs/mastercard_cron.log.
"""

import sys
import os
import asyncio
from datetime import datetime, timezone

from sync_mastercard_jobs import sync_and_audit_database
from apply_all_mastercard import run_batch_applier, get_pending_workday_jobs


def run_hourly_mastercard_cycle():
    os.makedirs("logs", exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'='*70}\n[{now_str}] ⏰ Starting Hourly Mastercard Auto-Discovery & Apply Cycle\n{'='*70}")

    # 1. Sync live jobs & audit database
    try:
        sync_and_audit_database()
    except Exception as e:
        print(f"⚠️ Error during Mastercard sync: {e}")

    # 2. Check for pending qualified openings
    pending = get_pending_workday_jobs()
    print(f"📊 Qualified Workday openings pending application: {len(pending)}")

    # 3. If pending jobs exist, apply sequentially
    if pending:
        print(f"🚀 Launching auto-apply for {len(pending)} pending jobs...")
        try:
            asyncio.run(run_batch_applier(dry_run=False, headless=True))
        except Exception as e:
            print(f"❌ Error during auto-apply: {e}")
    else:
        print("✅ No pending qualified jobs to apply. All openings are up to date.")

    end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{end_str}] 🏁 Hourly Mastercard Cycle Completed.\n{'='*70}\n")


if __name__ == "__main__":
    run_hourly_mastercard_cycle()
