#!/usr/bin/env python3
"""
Job Syncer for UiPath via Ashby API.
"""
from ashby_applier_base import create_ashby_syncer

sync_jobs = create_ashby_syncer("UiPath", "uipath")

if __name__ == "__main__":
    sync_jobs()
