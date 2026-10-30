#!/usr/bin/env python3
"""
Job Syncer for Everpure.
"""
from custom_portal_applier_base import create_custom_syncer

sync_jobs = create_custom_syncer("Everpure", "https://everpure.com")

if __name__ == "__main__":
    sync_jobs()
