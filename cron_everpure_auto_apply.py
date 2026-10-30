#!/usr/bin/env python3
"""
Hourly Everpure Autonomous Job Discovery and Auto-Apply Runner.
Designed to be executed hourly via cron, task scheduler, or background daemon loop.
"""

import sys
import os
import asyncio
import argparse
import time
from datetime import datetime

def run_hourly_everpure_cycle(dry_run=False, limit=5, headless=True):
    os.makedirs("logs", exist_ok=True)
    os.makedirs("screenshots", exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 70)
    print(f"[{now_str}] ⏰ Starting Hourly Everpure Auto-Discovery & Apply Cycle")
    print("=" * 70)

    # 1. Sync live jobs
    try:
        sync_mod = __import__("sync_everpure_jobs")
        sync_fn = getattr(sync_mod, "sync_jobs", None) or getattr(sync_mod, "sync_and_audit_database", None) or getattr(sync_mod, "sync_and_audit_everpure_database", None)
        if sync_fn:
            sync_fn()
        else:
            print("⚠️ No sync function found in sync_everpure_jobs")
    except Exception as e:
        print(f"⚠️ Error during Everpure sync: {e}")

    # 2. Batch apply
    try:
        apply_mod = __import__("apply_all_everpure")
        apply_fn = getattr(apply_mod, "run_batch_applier", None) or getattr(apply_mod, "run_batch", None)
        if apply_fn:
            if asyncio.iscoroutinefunction(apply_fn):
                asyncio.run(apply_fn(limit=limit, headless=headless, dry_run=dry_run))
            else:
                apply_fn(limit=limit, headless=headless, dry_run=dry_run)
        else:
            print("⚠️ No batch apply function found in apply_all_everpure")
    except Exception as e:
        print(f"❌ Error during Everpure auto-apply: {e}")

    end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{end_str}] 🏁 Hourly Everpure Cycle Completed.")
    print("=" * 70)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hourly Everpure Auto-Discovery & Applier")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run dry run without live submit")
    parser.add_argument("--live", action="store_true", help="Run live submission")
    parser.add_argument("--limit", type=int, default=5, help="Max applications per cycle")
    parser.add_argument("--loop", action="store_true", help="Run continuously in an hourly loop")
    parser.add_argument("--interval", type=int, default=3600, help="Loop interval in seconds (default: 3600)")
    parser.add_argument("--headed", action="store_true", help="Run browser headed")

    args = parser.parse_args()
    is_dry = not args.live if args.live else args.dry_run
    headless = not args.headed

    if args.loop:
        print(f"⏰ Starting continuous hourly Everpure cron daemon (interval={args.interval}s, dry_run={is_dry})...")
        while True:
            try:
                run_hourly_everpure_cycle(dry_run=is_dry, limit=args.limit, headless=headless)
            except Exception as e:
                print(f"❌ Error in Everpure cycle: {e}")
            print(f"⏳ Sleeping {args.interval}s until next Everpure cycle...")
            time.sleep(args.interval)
    else:
        run_hourly_everpure_cycle(dry_run=is_dry, limit=args.limit, headless=headless)
