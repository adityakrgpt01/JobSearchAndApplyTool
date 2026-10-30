#!/usr/bin/env python3
"""
Job Syncer for MSCI via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("MSCI", "msci.wd5.myworkdayjobs.com", "msci", "Careers")

if __name__ == "__main__":
    sync_jobs()
