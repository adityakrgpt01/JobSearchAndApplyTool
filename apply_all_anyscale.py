#!/usr/bin/env python3
"""
Batch Auto-Applier for Anyscale (Ashby).
"""
import asyncio
import argparse
from anyscale_applier import AnyscaleApplier
from ashby_applier_base import create_ashby_batch_runner

run_batch_applier = create_ashby_batch_runner("Anyscale", AnyscaleApplier, "anyscale")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch auto-applier for Anyscale")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()
    is_dry = not args.live if args.live else args.dry_run
    asyncio.run(run_batch_applier(limit=args.limit, headless=args.headless, dry_run=is_dry))
