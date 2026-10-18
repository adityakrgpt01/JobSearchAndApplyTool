import unittest
import sqlite3
import os
import database

# Override the database path for testing
TEST_DB = "test_jobs.db"
import database
database.DB_PATH = TEST_DB

# Mock out experience filters so all inserted jobs are considered qualified
database.is_qualified_job = lambda *args, **kwargs: True
database.is_qualified_seniority_and_exp = lambda *args, **kwargs: True

class TestFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create fresh test DB schema
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
        database.init_db(TEST_DB)
        cls.insert_test_data()
        
    @classmethod
    def tearDownClass(cls):
        # Clean up
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)

    @classmethod
    def insert_test_data(cls):
        conn = sqlite3.connect(TEST_DB)
        c = conn.cursor()
        
        # 1. Company: BigCorp (Large, High salary)
        c.execute("""
            INSERT INTO companies_intelligence (company_name, normalized_name, headcount_min, headcount_max, headcount_range, salary_max_lpa, salary_benchmark_tier)
            VALUES ('BigCorp', 'bigcorp', 5001, 10000, '5001 - 10000', 85.0, '70+LPA')
        """)
        
        # 2. Company: MidScale (Mid size, Medium salary)
        c.execute("""
            INSERT INTO companies_intelligence (company_name, normalized_name, headcount_min, headcount_max, headcount_range, salary_max_lpa, salary_benchmark_tier)
            VALUES ('MidScale', 'midscale', 1001, 5000, '1001 - 5000', 65.0, '60-70LPA')
        """)
        
        # 3. Company: Startup (Small, string fallback size, string fallback salary)
        c.execute("""
            INSERT INTO companies_intelligence (company_name, normalized_name, headcount_min, headcount_max, headcount_range, salary_max_lpa, salary_benchmark_tier)
            VALUES ('Startup', 'startup', 0, 0, '50 - 100', 0.0, '40-50LPA')
        """)
        
        # Insert jobs
        jobs = [
            # BigCorp Job: Discovered, Tier 1, Remote
            ('job1', 'BigCorp', 'Senior Backend Engineer', 'Remote', 1, 'ashby', '70+LPA', 'DISCOVERED', 'http://url1'),
            # BigCorp Job: Applied, Tier 1, On-site
            ('job2', 'BigCorp', 'Backend Engineer', 'New York', 0, 'ashby', '70+LPA', 'APPLIED', 'http://url2'),
            # MidScale Job: Discovered, Tier 2, Remote
            ('job3', 'MidScale', 'Software Engineer', 'Remote', 1, 'greenhouse', '60-70LPA', 'DISCOVERED', 'http://url3'),
            # Startup Job: Rejected, Startup Tier, On-site
            ('job4', 'Startup', 'Fullstack Engineer', 'San Francisco', 0, 'lever', '40-50LPA', 'REJECTED', 'http://url4'),
        ]
        
        for j in jobs:
            c.execute("""
                INSERT INTO job_postings (job_id, company_name, title, location, is_remote, ats_platform, salary_tier, status, apply_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, j)
            
        conn.commit()
        conn.close()

    def test_no_filters(self):
        jobs = database.list_jobs(db_path=TEST_DB)
        self.assertEqual(len(jobs), 4, "Should return all 4 jobs when no filters applied")

    def test_single_filter_status(self):
        jobs = database.list_jobs(status="DISCOVERED", db_path=TEST_DB)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(set(j['job_id'] for j in jobs), {'job1', 'job3'})

    def test_multiple_status_filter(self):
        jobs = database.list_jobs(status="APPLIED,REJECTED", db_path=TEST_DB)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(set(j['job_id'] for j in jobs), {'job2', 'job4'})

    def test_salary_tier_filter_numerical(self):
        # BigCorp has salary_max_lpa = 85.0 -> tier 70+LPA
        jobs = database.list_jobs(salary_tier="70+LPA", db_path=TEST_DB)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(set(j['company_name'] for j in jobs), {'BigCorp'})

    def test_salary_tier_filter_fallback(self):
        # Startup has salary_max_lpa = 0, but salary_benchmark_tier = '40-50LPA'
        jobs = database.list_jobs(salary_tier="40-50LPA", db_path=TEST_DB)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['company_name'], 'Startup')

    def test_company_size_filter_numerical(self):
        # MidScale is 1001-5000
        jobs = database.list_jobs(company_size="1000-5000", db_path=TEST_DB)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['company_name'], 'MidScale')
        
    def test_company_size_filter_fallback(self):
        # Startup is 50-100 via string fallback
        jobs = database.list_jobs(company_size="50-100", db_path=TEST_DB)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['company_name'], 'Startup')

    def test_complex_combination_1(self):
        # Tier 1, Remote, Discovered
        jobs = database.list_jobs(
            salary_tier="70+LPA",
            remote_only=True,
            status="DISCOVERED", db_path=TEST_DB
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['job_id'], 'job1')

    def test_complex_combination_2(self):
        # 1000-5000 OR 5000+, Ashby platform
        jobs = database.list_jobs(
            company_size="1000-5000,5000+",
            remote_only=False,
            platform="ashby", db_path=TEST_DB
        )
        self.assertEqual(len(jobs), 2)
        self.assertEqual(set(j['job_id'] for j in jobs), {'job1', 'job2'})

    def test_search_query_combination(self):
        # Search "backend", Tier 70+ OR 60-70
        jobs = database.list_jobs(
            search_query="backend",
            salary_tier="70+LPA,60-70LPA", db_path=TEST_DB
        )
        # BigCorp job1, job2 are Backend.
        self.assertEqual(len(jobs), 2)
        self.assertEqual(set(j['job_id'] for j in jobs), {'job1', 'job2'})

    def test_status_applied_filter_excluded(self):
        # When applied is unchecked, status='ready,review_required,not_applied,failed,discovered'
        jobs = database.list_jobs(
            status="ready,review_required,not_applied,failed,discovered",
            db_path=TEST_DB
        )
        applied_jobs = [j for j in jobs if j['status'] == 'APPLIED']
        self.assertEqual(len(applied_jobs), 0, "No APPLIED jobs should be returned when applied is not in status")

    def test_status_none_returns_empty(self):
        # When all statuses are unchecked
        jobs = database.list_jobs(
            status="none",
            db_path=TEST_DB
        )
        self.assertEqual(len(jobs), 0, "No jobs should be returned when status is none")

if __name__ == '__main__':
    unittest.main()

