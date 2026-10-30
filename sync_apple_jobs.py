#!/usr/bin/env python3
"""
Job Syncer for Apple.
"""
from custom_portal_applier_base import create_custom_syncer

sync_jobs = create_custom_syncer("Apple", "https://jobs.apple.com")

if __name__ == "__main__":
    sync_jobs()
