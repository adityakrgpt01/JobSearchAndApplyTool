#!/usr/bin/env python3
"""
Job Syncer for Rockwell Automation via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Rockwell Automation", "rockwellautomation.wd1.myworkdayjobs.com", "rockwellautomation", "External_Rockwell_Automation")

if __name__ == "__main__":
    sync_jobs()
