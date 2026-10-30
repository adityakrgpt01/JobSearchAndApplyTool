#!/usr/bin/env python3
"""
Job Syncer for Nike via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Nike", "nike.wd1.myworkdayjobs.com", "nike", "Nike")

if __name__ == "__main__":
    sync_jobs()
