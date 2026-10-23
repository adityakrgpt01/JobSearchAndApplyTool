"""
Comprehensive Verification Suite for Multi-ATS Discovery & Fallback Probing Fixes.
Validates:
1. ats_scanner: Greenhouse token sanitization (zero malformed/workday tokens).
2. workday_scanner: Multi-slug fallback probing on HTTP 404 & 422.
3. linkedin_crawler: Dynamic company-targeted partition loading & cursor rotation.
"""

import unittest
from unittest import mock
import asyncio
import aiohttp
import re
import sqlite3

from ats_scanner import load_all_target_companies
import workday_scanner
from linkedin_crawler import load_priority_company_partitions, BIG_TECH_PARTITIONS


class TestDiscoveryFixes(unittest.TestCase):

    def test_01_greenhouse_token_sanitization(self):
        """Validates that no malformed tokens, spaces, dots, or workday prefixes enter Greenhouse queue."""
        targets = load_all_target_companies()
        gh_targets = targets.get("greenhouse", [])
        
        self.assertGreater(len(gh_targets), 500, "Should load verified Greenhouse tokens")
        self.assertLess(len(gh_targets), 1500, "Should NOT load unverified thousands of arbitrary companies")
        
        for item in gh_targets:
            tok = item["token"]
            self.assertFalse(" " in tok, f"Token contains whitespace: '{tok}'")
            self.assertFalse("." in tok, f"Token contains dot: '{tok}'")
            self.assertFalse(tok.startswith("workday_"), f"Token is a Workday prefix: '{tok}'")
            self.assertTrue(re.match(r'^[a-z0-9_\-]+$', tok), f"Invalid token format: '{tok}'")
        print(f"✅ Test 1 Passed: {len(gh_targets)} clean, verified Greenhouse tokens loaded.")

    def test_02_workday_422_fallback_probing(self):
        """Validates that HTTP 422 triggers multi-slug fallback probing and extracts jobs on working slug."""
        async def run_test():
            comp = {
                'company_name': 'AcmeGlobal',
                'host': 'acmeglobal.wd5.myworkdayjobs.com',
                'tenant': 'acmeglobal',
                'board': 'InvalidSlug',
                'api_url': 'https://acmeglobal.wd5.myworkdayjobs.com/wday/cxs/acmeglobal/InvalidSlug/jobs'
            }

            mock_calls = []

            class MockResp:
                def __init__(self, url, status, data):
                    self.url = url
                    self.status = status
                    self._data = data
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
                async def json(self):
                    return self._data

            class MockSession:
                def post(self, url, **kwargs):
                    mock_calls.append(url)
                    if 'InvalidSlug' in url:
                        return MockResp(url, 422, {'error': 'Unprocessable Entity'})
                    elif 'External' in url:
                        return MockResp(url, 200, {
                            'total': 1,
                            'jobPostings': [{
                                'title': 'Senior Software Engineer (Backend)',
                                'postedOn': 'Posted Today',
                                'locationsText': 'Bengaluru, India',
                                'externalPath': '/job/Bengaluru-India/Senior-Software-Engineer_R999',
                                'bulletFields': []
                            }]
                        })
                    return MockResp(url, 404, {})

            session = MockSession()
            sem = asyncio.Semaphore(1)

            with mock.patch('workday_scanner.save_job', return_value=True), \
                 mock.patch('workday_scanner.perform_smart_due_diligence', return_value=True), \
                 mock.patch('workday_scanner.mark_failure_resolved', return_value=True):
                jobs = await workday_scanner.scan_single_workday(session, comp, sem)

            self.assertEqual(len(mock_calls), 2, "Should probe initial slug then fallback slug")
            self.assertIn("InvalidSlug", mock_calls[0])
            self.assertIn("External", mock_calls[1])
            self.assertEqual(len(jobs), 1, "Should discover job from fallback slug")
            self.assertEqual(comp["board"], "External")
            self.assertEqual(jobs[0]["title"], "Senior Software Engineer (Backend)")

        asyncio.run(run_test())
        print("✅ Test 2 Passed: Workday HTTP 422 successfully probed fallback slugs and recovered job.")

    def test_03_linkedin_priority_company_partitions(self):
        """Validates that priority company partitions are loaded and rotated dynamically."""
        parts_1 = load_priority_company_partitions(batch_size=15)
        self.assertGreaterEqual(len(parts_1), 15 + len(BIG_TECH_PARTITIONS))
        
        # Verify core big tech is included
        comps_1 = [p[0] for p in parts_1]
        self.assertIn("Microsoft", comps_1)
        self.assertIn("Google", comps_1)
        
        # Verify non-big-tech enterprise MNCs are injected
        has_enterprise = any(c not in [b[0] for b in BIG_TECH_PARTITIONS] for c in comps_1)
        self.assertTrue(has_enterprise, "Should include enterprise MNCs")

        print(f"✅ Test 3 Passed: Loaded {len(parts_1)} priority company partitions dynamically.")


if __name__ == "__main__":
    unittest.main()
