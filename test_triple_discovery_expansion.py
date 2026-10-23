"""
Comprehensive Automated Test Suite for Triple Discovery Expansion & Anti-Ban Engine.
Validates:
1. Eightfold.ai scanner: Endpoint formatting, job card parsing, and SDE-2 qualification.
2. Phenom People scanner: Endpoint formatting, job card parsing, and SDE-2 qualification.
3. Playwright Workday Spider: WAF/Auth-locked target extraction, rotation, and DOM parsing.
4. LinkedIn Universal Bridge: Anti-ban semaphore (2), user-agent rotation, and dark company queueing.
"""

import unittest
from unittest import mock
import asyncio
import aiohttp
import json
import sqlite3

import eightfold_scanner
import phenom_scanner
import playwright_workday_spider
import linkedin_crawler


class TestTripleDiscoveryExpansion(unittest.TestCase):

    def test_01_eightfold_scanner_mock_ingestion(self):
        """Validates that Eightfold scanner correctly parses positions, qualifies titles/locations, and saves jobs."""
        async def run_test():
            comp = {
                "name": "Netflix",
                "tenant": "netflix",
                "domain": "netflix.com",
                "career_base": "https://jobs.netflix.com"
            }

            mock_payload = {
                "positions": [
                    {
                        "id": 892341,
                        "name": "Senior Software Engineer - Core Platform Backend",
                        "locations": ["Bengaluru, Karnataka, India"],
                        "canonicalPositionUrl": "https://jobs.netflix.com/jobs/892341",
                        "department": "Platform Engineering"
                    },
                    {
                        "id": 892342,
                        "name": "Product Marketing Manager",
                        "locations": ["Mumbai, India"],
                        "canonicalPositionUrl": "https://jobs.netflix.com/jobs/892342",
                        "department": "Marketing"
                    }
                ]
            }

            class MockResp:
                def __init__(self, status, data):
                    self.status = status
                    self._data = data
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
                async def json(self):
                    return self._data

            class MockSession:
                def get(self, url, **kwargs):
                    return MockResp(200, mock_payload)

            session = MockSession()
            sem = asyncio.Semaphore(1)

            with mock.patch("eightfold_scanner.save_job", return_value=True), \
                 mock.patch("eightfold_scanner.perform_smart_due_diligence", return_value=True):
                jobs = await eightfold_scanner.scan_single_eightfold_tenant(session, comp, sem)

            self.assertEqual(len(jobs), 1, "Should qualify 1 Senior Backend job and filter out Marketing Manager")
            self.assertEqual(jobs[0]["company_name"], "Netflix")
            self.assertEqual(jobs[0]["title"], "Senior Software Engineer - Core Platform Backend")
            self.assertEqual(jobs[0]["ats_platform"], "eightfold")
            self.assertIn("netflix", jobs[0]["job_id"])

        asyncio.run(run_test())
        print("✅ Test 1 Passed: Eightfold.ai scanner correctly parsed and qualified backend jobs.")

    def test_02_phenom_scanner_mock_ingestion(self):
        """Validates that Phenom People scanner correctly parses jobs, qualifies titles/locations, and saves jobs."""
        async def run_test():
            comp = {
                "name": "Walmart Global Tech",
                "domain": "careers.walmart.com",
                "search_url": "https://careers.walmart.com/api/v1/jobs"
            }

            mock_payload = {
                "jobs": [
                    {
                        "jobId": "WMT-90123",
                        "title": "Senior Software Engineer - Distributed Systems",
                        "city": "Bengaluru",
                        "country": "India",
                        "applyUrl": "https://careers.walmart.com/job/WMT-90123"
                    },
                    {
                        "jobId": "WMT-90124",
                        "title": "Senior Quality Assurance Engineer (SDET)",
                        "city": "Bengaluru",
                        "country": "India",
                        "applyUrl": "https://careers.walmart.com/job/WMT-90124"
                    }
                ]
            }

            class MockResp:
                def __init__(self, status, data):
                    self.status = status
                    self._data = data
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
                async def json(self):
                    return self._data

            class MockSession:
                def get(self, url, **kwargs):
                    return MockResp(200, mock_payload)

            session = MockSession()
            sem = asyncio.Semaphore(1)

            with mock.patch("phenom_scanner.save_job", return_value=True), \
                 mock.patch("phenom_scanner.perform_smart_due_diligence", return_value=True):
                jobs = await phenom_scanner.scan_single_phenom_tenant(session, comp, sem)

            self.assertEqual(len(jobs), 1, "Should qualify 1 Senior Backend job and filter out SDET")
            self.assertEqual(jobs[0]["company_name"], "Walmart Global Tech")
            self.assertEqual(jobs[0]["ats_platform"], "phenom")
            self.assertIn("Bengaluru, India", jobs[0]["location"])

        asyncio.run(run_test())
        print("✅ Test 2 Passed: Phenom People scanner correctly parsed and qualified backend jobs.")

    def test_03_playwright_workday_spider_targets(self):
        """Validates that Playwright Workday spider accurately extracts WAF/Auth-locked targets from failures."""
        targets = playwright_workday_spider.load_stealth_workday_targets(batch_size=10)
        self.assertGreater(len(targets), 0, "Should load targets from scraper_failures")
        self.assertLessEqual(len(targets), 10, "Should enforce batch_size limit")
        
        for t in targets:
            self.assertIn(t["http_status"], [408, 500, 403], "Targets must be in WAF/Auth-locked status")
            self.assertTrue(t["host"].endswith("myworkdayjobs.com"), "Must be a valid Workday host")
            self.assertTrue(t["portal_url"].startswith("https://"), "Must have canonical portal URL")
        print(f"✅ Test 3 Passed: Loaded {len(targets)} WAF/Auth-locked Workday targets for Playwright spider.")

    def test_04_linkedin_anti_ban_and_universal_queue(self):
        """Validates LinkedIn anti-ban concurrency semaphore (2), user-agent pool, and universal queue loading."""
        # Concurrency semaphore check
        self.assertEqual(linkedin_crawler._LINKEDIN_SEMAPHORE._value, 2, "LinkedIn semaphore must be strictly capped at 2")
        self.assertGreaterEqual(len(linkedin_crawler.LINKEDIN_USER_AGENTS), 5, "Must have rotating user-agent pool")
        
        # Universal queue check
        partitions = linkedin_crawler.load_priority_company_partitions(batch_size=40)
        self.assertGreaterEqual(len(partitions), 40, "Should load full batch of 40+ partitions")
        print(f"✅ Test 4 Passed: LinkedIn anti-ban rate limiting and universal queue ({len(partitions)} partitions) verified.")


if __name__ == "__main__":
    unittest.main()
