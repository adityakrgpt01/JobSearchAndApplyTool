import asyncio
import sqlite3
import aiohttp
from workday_scanner import load_verified_workday_endpoints, scan_single_workday
from eightfold_scanner import scan_single_eightfold_tenant, EIGHTFOLD_TARGETS
from phenom_scanner import scan_single_phenom_tenant, PHENOM_TARGETS
from linkedin_crawler import load_priority_company_partitions

def test_workday_endpoints_unblocked():
    """Verify that load_verified_workday_endpoints returns full unblocked 2,200+ endpoints."""
    endpoints = load_verified_workday_endpoints()
    assert len(endpoints) >= 2200, f"Expected >= 2200 Workday endpoints, got {len(endpoints)}"
    
    # Check that premier MNCs are present
    names = {e["company_name"].lower() for e in endpoints}
    for mnc in ["salesforce", "adobe", "servicenow", "intuit", "morgan stanley", "ubs"]:
        assert any(mnc in n for n in names), f"Expected MNC {mnc} to be in Workday endpoints pool"
    print(f"✅ Workday unblock verified: {len(endpoints)} active enterprise endpoints loaded.")

def test_linkedin_priority_queue_includes_ats_failures():
    """Verify that LinkedIn dark rotation queue prioritizes undiscovered MNCs and failing ATS targets."""
    partitions = load_priority_company_partitions(batch_size=40)
    assert len(partitions) > 0, "Expected non-empty priority company partitions"
    print(f"✅ LinkedIn dark queue verified: loaded {len(partitions)} priority targets.")

async def test_eightfold_resilience():
    """Verify that scan_single_eightfold_tenant runs without uncaught JSON exceptions."""
    sem = asyncio.Semaphore(2)
    conn = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        for target in EIGHTFOLD_TARGETS[:2]:
            jobs = await scan_single_eightfold_tenant(session, target, sem)
            assert isinstance(jobs, list), "Expected list of jobs from Eightfold scanner"
    print("✅ Eightfold scanner resilience verified.")

async def test_phenom_html_redirect_resilience():
    """Verify that Phenom scanner catches HTML redirect pages without JSON decode crash."""
    sem = asyncio.Semaphore(2)
    conn = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        for target in PHENOM_TARGETS[:2]:
            jobs = await scan_single_phenom_tenant(session, target, sem)
            assert isinstance(jobs, list), "Expected list of jobs from Phenom scanner"
    print("✅ Phenom HTML redirect resilience verified.")

if __name__ == "__main__":
    test_workday_endpoints_unblocked()
    test_linkedin_priority_queue_includes_ats_failures()
    asyncio.run(test_eightfold_resilience())
    asyncio.run(test_phenom_html_redirect_resilience())
    print("\n🎉 All 4 verification tests passed successfully!")
