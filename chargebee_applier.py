#!/usr/bin/env python3
"""
Dedicated Chargebee Ashby Auto-Applier.
"""
from ashby_applier_base import AshbyBaseApplier

class ChargebeeApplier(AshbyBaseApplier):
    def __init__(self, dry_run=False, headless=True, keep_open=False, profile_path="user_profile.json"):
        super().__init__(
            company_name="Chargebee",
            board_slug="chargebee",
            slug="chargebee",
            profile_path=profile_path,
            dry_run=dry_run,
            headless=headless,
            keep_open=keep_open
        )
