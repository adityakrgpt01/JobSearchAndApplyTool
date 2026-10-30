#!/usr/bin/env python3
"""
Dedicated Anyscale Ashby Auto-Applier.
"""
from ashby_applier_base import AshbyBaseApplier

class AnyscaleApplier(AshbyBaseApplier):
    def __init__(self, dry_run=False, headless=True, keep_open=False, profile_path="user_profile.json"):
        super().__init__(
            company_name="Anyscale",
            board_slug="anyscale",
            slug="anyscale",
            profile_path=profile_path,
            dry_run=dry_run,
            headless=headless,
            keep_open=keep_open
        )
