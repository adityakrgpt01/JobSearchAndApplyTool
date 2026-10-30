#!/usr/bin/env python3
"""
Job Syncer for NatWest Group via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("NatWest Group", "natwest.wd3.myworkdayjobs.com", "natwest", "NatWest_Careers")

if __name__ == "__main__":
    sync_jobs()
