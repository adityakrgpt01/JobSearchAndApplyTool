#!/usr/bin/env python3
"""
Job Syncer for Danaher Corporation via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Danaher Corporation", "danaher.wd1.myworkdayjobs.com", "danaher", "DanaherJobs")

if __name__ == "__main__":
    sync_jobs()
