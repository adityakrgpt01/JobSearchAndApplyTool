#!/usr/bin/env python3
"""
Dedicated Delta Technology Hub Custom Portal Auto-Applier.
"""
from custom_portal_applier_base import CustomPortalBaseApplier

class DeltaApplier(CustomPortalBaseApplier):
    def __init__(self, dry_run=False, headless=True, keep_open=False, profile_path="user_profile.json"):
        super().__init__(
            company_name="Delta Technology Hub",
            slug="delta",
            profile_path=profile_path,
            dry_run=dry_run,
            headless=headless,
            keep_open=keep_open
        )
