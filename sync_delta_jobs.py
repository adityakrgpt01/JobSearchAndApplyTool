#!/usr/bin/env python3
"""
Job Syncer for Delta Technology Hub.
"""
from custom_portal_applier_base import create_custom_syncer

sync_jobs = create_custom_syncer("Delta Technology Hub", "https://delta.com")

if __name__ == "__main__":
    sync_jobs()
