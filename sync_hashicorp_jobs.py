#!/usr/bin/env python3
"""
Job Syncer for HashiCorp via Greenhouse API.
"""
from greenhouse_applier_base import create_greenhouse_syncer

sync_jobs = create_greenhouse_syncer("HashiCorp", "hashicorp")

if __name__ == "__main__":
    sync_jobs()
