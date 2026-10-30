#!/usr/bin/env python3
"""
Job Syncer for Light & Wonder via Workday CXS API.
"""
from workday_applier_base import create_workday_syncer

sync_jobs = create_workday_syncer("Light & Wonder", "lnw.wd5.myworkdayjobs.com", "lnw", "lightwonderexternalcareers")

if __name__ == "__main__":
    sync_jobs()
