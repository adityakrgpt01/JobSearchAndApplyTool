#!/usr/bin/env python3
"""
Dedicated Apple Custom Portal Auto-Applier.
"""
from custom_portal_applier_base import CustomPortalBaseApplier

class AppleApplier(CustomPortalBaseApplier):
    def __init__(self, dry_run=False, headless=True, keep_open=False, profile_path="user_profile.json"):
        super().__init__(
            company_name="Apple",
            slug="apple",
            profile_path=profile_path,
            dry_run=dry_run,
            headless=headless,
            keep_open=keep_open
        )
