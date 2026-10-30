#!/usr/bin/env python3
"""
Job Syncer for Ecolab via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Ecolab", "ecolab.wd1.myworkdayjobs.com", "ecolab", "ecolab_external")

if __name__ == "__main__":
    sync_jobs()
