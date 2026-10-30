#!/usr/bin/env python3
"""
Job Syncer for Teradata via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Teradata", "teradata.wd1.myworkdayjobs.com", "teradata", "Teradata_Careers")

if __name__ == "__main__":
    sync_jobs()
