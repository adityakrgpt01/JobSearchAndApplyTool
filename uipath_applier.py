#!/usr/bin/env python3
"""
Dedicated UiPath Ashby Auto-Applier.
"""
from ashby_applier_base import AshbyBaseApplier

class UiPathApplier(AshbyBaseApplier):
    def __init__(self, dry_run=False, headless=True, keep_open=False, profile_path="user_profile.json"):
        super().__init__(
            company_name="UiPath",
            board_slug="uipath",
            slug="uipath",
            profile_path=profile_path,
            dry_run=dry_run,
            headless=headless,
            keep_open=keep_open
        )
