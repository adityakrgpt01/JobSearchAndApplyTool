#!/usr/bin/env python3
"""
Job Syncer for Workday via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Workday", "workday.wd5.myworkdayjobs.com", "workday", "workday")

if __name__ == "__main__":
    sync_jobs()
