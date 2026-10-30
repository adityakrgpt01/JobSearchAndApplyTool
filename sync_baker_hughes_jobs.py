#!/usr/bin/env python3
"""
Job Syncer for Baker Hughes via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Baker Hughes", "bakerhughes.wd5.myworkdayjobs.com", "bakerhughes", "BakerHughes")

if __name__ == "__main__":
    sync_jobs()
