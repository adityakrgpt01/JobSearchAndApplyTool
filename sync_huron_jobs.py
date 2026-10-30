#!/usr/bin/env python3
"""
Job Syncer for Huron via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Huron", "huron.wd1.myworkdayjobs.com", "huron", "huroncareers")

if __name__ == "__main__":
    sync_jobs()
