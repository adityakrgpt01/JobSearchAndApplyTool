#!/usr/bin/env python3
"""
Job Syncer for Ciena via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Ciena", "ciena.wd5.myworkdayjobs.com", "ciena", "Careers")

if __name__ == "__main__":
    sync_jobs()
