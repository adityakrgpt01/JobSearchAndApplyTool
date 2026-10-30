#!/usr/bin/env python3
"""
Batch Auto-Applier for HashiCorp (Greenhouse).
"""
import asyncio
import argparse
from hashicorp_applier import HashicorpApplier
from greenhouse_applier_base import create_greenhouse_batch_runner

run_batch_applier = create_greenhouse_batch_runner("HashiCorp", HashicorpApplier, "hashicorp")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch auto-applier for HashiCorp")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()
    is_dry = not args.live if args.live else args.dry_run
    asyncio.run(run_batch_applier(limit=args.limit, headless=args.headless, dry_run=is_dry))
