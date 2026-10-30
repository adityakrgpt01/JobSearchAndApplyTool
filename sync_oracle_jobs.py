#!/usr/bin/env python3
"""
Job Syncer for Oracle.
"""
from custom_portal_applier_base import create_custom_syncer

sync_jobs = create_custom_syncer("Oracle", "https://careers.oracle.com")

if __name__ == "__main__":
    sync_jobs()
