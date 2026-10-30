#!/usr/bin/env python3
"""
Hourly Enterprise Autonomous Discovery & Auto-Apply Runner.
Coordinated Enterprise Targets (11 Companies):
1. Mastercard (Workday: mastercard.wd1.myworkdayjobs.com)
2. Visa (Workday: visa.wd5.myworkdayjobs.com)
3. LSEG (Workday: lseg.wd3.myworkdayjobs.com)
4. Salesforce (Workday: salesforce.wd12.myworkdayjobs.com)
5. Adobe (Workday: adobe.wd5.myworkdayjobs.com)
6. NVIDIA (Workday: nvidia.wd5.myworkdayjobs.com)
7. DoorDash (Greenhouse: boards-api.greenhouse.io)
8. Airbnb (Greenhouse / Careers: careers.airbnb.com)
9. ServiceNow (SmartRecruiters: jobs.smartrecruiters.com)
10. Booking Holdings (iCIMS / Official Booking: jobs.booking.com)
11. Wayfair (SmartRecruiters: jobs.smartrecruiters.com)
"""

import sys
import os
import asyncio
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("enterprise_cron")

def run_enterprise_cycle(dry_run=False, limit_per_company=5):
    os.makedirs("logs", exist_ok=True)
    os.makedirs("screenshots", exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'='*80}\n[{now_str}] ⏰ Starting Hourly Enterprise Discovery & Auto-Apply Cycle (38 Companies)\n{'='*80}")

    companies = [
        # Original 11 Enterprise Targets
        ("Mastercard", "sync_mastercard_jobs", "apply_all_mastercard", True),
        ("Visa", "sync_visa_jobs", "apply_all_visa", True),
        ("LSEG", "sync_lseg_jobs", "apply_all_lseg", True),
        ("Salesforce", "sync_salesforce_jobs", "apply_all_salesforce", True),
        ("Adobe", "sync_adobe_jobs", "apply_all_adobe", True),
        ("NVIDIA", "sync_nvidia_jobs", "apply_all_nvidia", True),
        ("DoorDash", "sync_doordash_jobs", "apply_all_doordash", True),
        ("Airbnb", "sync_airbnb_jobs", "apply_all_airbnb", True),
        ("ServiceNow", "sync_servicenow_jobs", "apply_all_servicenow", False),
        ("Booking Holdings", "sync_booking_jobs", "apply_all_booking", False),
        ("Wayfair", "sync_wayfair_jobs", "apply_all_wayfair", False),
        # 15 Workday Targets
        ("Rockwell Automation", "sync_rockwell_jobs", "apply_all_rockwell", True),
        ("Danaher Corporation", "sync_danaher_jobs", "apply_all_danaher", True),
        ("Target", "sync_target_jobs", "apply_all_target", True),
        ("Workday", "sync_workday_corp_jobs", "apply_all_workday_corp", True),
        ("Nike", "sync_nike_jobs", "apply_all_nike", True),
        ("DocuSign", "sync_docusign_jobs", "apply_all_docusign", True),
        ("Candescent", "sync_candescent_jobs", "apply_all_candescent", True),
        ("NatWest Group", "sync_natwest_jobs", "apply_all_natwest", True),
        ("Ecolab", "sync_ecolab_jobs", "apply_all_ecolab", True),
        ("Ciena", "sync_ciena_jobs", "apply_all_ciena", True),
        ("Baker Hughes", "sync_baker_hughes_jobs", "apply_all_baker_hughes", True),
        ("Light & Wonder", "sync_light_wonder_jobs", "apply_all_light_wonder", True),
        ("Teradata", "sync_teradata_jobs", "apply_all_teradata", True),
        ("Huron", "sync_huron_jobs", "apply_all_huron", True),
        ("MSCI", "sync_msci_jobs", "apply_all_msci", True),
        # 4 Greenhouse Targets
        ("Twilio", "sync_twilio_jobs", "apply_all_twilio", True),
        ("HashiCorp", "sync_hashicorp_jobs", "apply_all_hashicorp", True),
        ("Netskope", "sync_netskope_jobs", "apply_all_netskope", True),
        ("Navan", "sync_navan_jobs", "apply_all_navan", True),
        # 3 Ashby Targets
        ("UiPath", "sync_uipath_jobs", "apply_all_uipath", True),
        ("Anyscale", "sync_anyscale_jobs", "apply_all_anyscale", True),
        ("Chargebee", "sync_chargebee_jobs", "apply_all_chargebee", True),
        # 5 Custom / Enterprise Targets
        ("Oracle", "sync_oracle_jobs", "apply_all_oracle", True),
        ("Apple", "sync_apple_jobs", "apply_all_apple", True),
        ("SAP Labs", "sync_sap_jobs", "apply_all_sap", True),
        ("Delta Technology Hub", "sync_delta_jobs", "apply_all_delta", True),
        ("Everpure", "sync_everpure_jobs", "apply_all_everpure", True),
    ]

    for idx, (name, sync_mod_name, apply_mod_name, is_async) in enumerate(companies, 1):
        print(f"\n[{idx}/{len(companies)}] 🏢 Executing {name} Cycle...")
        # 1. Sync
        try:
            sync_mod = __import__(sync_mod_name)
            sync_fn = getattr(sync_mod, "sync_jobs", None) or getattr(sync_mod, "sync_and_audit_database", None) or getattr(sync_mod, "sync_and_audit_visa_database", None) or getattr(sync_mod, "sync_and_audit_lseg_database", None) or getattr(sync_mod, "sync_and_audit_nvidia_database", None)
            if sync_fn:
                sync_fn()
            else:
                print(f"⚠️ No sync function found for {name}")
        except Exception as e:
            print(f"⚠️ Error during {name} sync: {e}")

        # 2. Apply
        try:
            apply_mod = __import__(apply_mod_name)
            apply_fn = getattr(apply_mod, "run_batch_applier", None) or getattr(apply_mod, "run_batch", None)
            if apply_fn:
                if is_async:
                    asyncio.run(apply_fn(limit=limit_per_company, headless=True, dry_run=dry_run))
                else:
                    apply_fn(dry_run=dry_run, limit=limit_per_company, headless=True)
            else:
                print(f"⚠️ No batch apply function found for {name}")
        except Exception as e:
            print(f"❌ Error during {name} apply: {e}")

    end_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{end_str}] 🏁 Hourly Enterprise Cycle Completed Across All {len(companies)} Targets.\n{'='*80}\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hourly Enterprise Auto-Discovery & Applier")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run dry run validation without live submission")
    parser.add_argument("--live", action="store_true", help="Run live submissions")
    parser.add_argument("--limit", type=int, default=5, help="Max applications per company per cycle")
    parser.add_argument("--loop", action="store_true", help="Run continuously in an hourly loop")
    parser.add_argument("--interval", type=int, default=3600, help="Loop interval in seconds (default: 3600)")
    
    args = parser.parse_args()
    is_dry = not args.live if args.live else args.dry_run
    
    if args.loop:
        import time
        print(f"⏰ Starting continuous hourly enterprise cron daemon (interval={args.interval}s, dry_run={is_dry})...")
        while True:
            try:
                run_enterprise_cycle(dry_run=is_dry, limit_per_company=args.limit)
            except Exception as e:
                print(f"❌ Error in enterprise cycle: {e}")
            print(f"\n⏳ Sleeping for {args.interval}s until next hourly cycle ({args.interval // 60} minutes)...")
            time.sleep(args.interval)
    else:
        run_enterprise_cycle(dry_run=is_dry, limit_per_company=args.limit)

