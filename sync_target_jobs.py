#!/usr/bin/env python3
"""
Job Syncer for Target via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Target", "target.wd5.myworkdayjobs.com", "target", "targetcareers")

if __name__ == "__main__":
    sync_jobs()
