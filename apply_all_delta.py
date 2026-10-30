#!/usr/bin/env python3
"""
Batch Auto-Applier for Delta Technology Hub.
"""
import asyncio
import argparse
from delta_applier import DeltaApplier
from custom_portal_applier_base import create_custom_batch_runner

run_batch_applier = create_custom_batch_runner("Delta Technology Hub", DeltaApplier, "delta")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch auto-applier for Delta Technology Hub")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()
    is_dry = not args.live if args.live else args.dry_run
    asyncio.run(run_batch_applier(limit=args.limit, headless=args.headless, dry_run=is_dry))
