#!/usr/bin/env python3
"""
Job Syncer for DocuSign via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("DocuSign", "docusign.wd1.myworkdayjobs.com", "docusign", "Careers")

if __name__ == "__main__":
    sync_jobs()
