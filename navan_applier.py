#!/usr/bin/env python3
"""
Dedicated Navan Greenhouse Auto-Applier.
"""
from greenhouse_applier_base import GreenhouseBaseApplier

class NavanApplier(GreenhouseBaseApplier):
    def __init__(self, dry_run=False, headless=True, keep_open=False, profile_path="user_profile.json"):
        super().__init__(
            company_name="Navan",
            board_slug="tripactions",
            slug="navan",
            profile_path=profile_path,
            dry_run=dry_run,
            headless=headless,
            keep_open=keep_open
        )
