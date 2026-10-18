"""
Comprehensive LinkedIn to Direct Company Career Portal Converter.
Updates all LinkedIn listings in jobs.db to direct official company career portal links
(Amazon, Google, Microsoft, Meta, Apple, NVIDIA, Walmart, Cisco, JPMorgan, Wells Fargo, Deloitte, Citi, IBM, Target, etc.).
"""

import sqlite3
import re
import urllib.parse

DB_PATH = "jobs.db"

COMPANY_PORTALS = {
    "amazon": "https://www.amazon.jobs/en/search?base_query={q}&loc_query=India",
    "google": "https://www.google.com/about/careers/applications/jobs/results/?q={q}&location=India",
    "microsoft": "https://jobs.careers.microsoft.com/global/en/search?q={q}",
    "cisco": "https://jobs.cisco.com/main/search?q={q}",
    "jpmorgan": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword={q}",
    "jpmorganchase": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword={q}",
    "target": "https://jobs.target.com/search-jobs/{q}",
    "qualcomm": "https://qualcomm.wd5.myworkdayjobs.com/External?q={q}",
    "barclays": "https://search.jobs.barclays/search-jobs/{q}",
    "apple": "https://jobs.apple.com/en-in/search?search={q}",
    "meta": "https://www.metacareers.com/jobs?q={q}",
    "netflix": "https://jobs.netflix.com/search?q={q}",
    "nvidia": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite?q={q}",
    "walmart": "https://careers.walmart.com/results?q={q}",
    "walmart global tech": "https://careers.walmart.com/results?q={q}",
    "wells fargo": "https://www.wellsfargo.com/about/careers/search-jobs?q={q}",
    "deloitte": "https://apply.deloitte.com/careers/SearchJobs/{q}",
    "citi": "https://jobs.citi.com/search-jobs/{q}",
    "ibm": "https://www.ibm.com/careers/us/en/search-results?keywords={q}",
    "expedia": "https://careers.expediagroup.com/jobs/search?q={q}",
    "wipro": "https://careers.wipro.com/careers/jobs?q={q}",
    "globallogic": "https://www.globallogic.com/careers/job-search/?q={q}",
    "optum": "https://careers.unitedhealthgroup.com/search-jobs/{q}",
    "state street": "https://statestreet.wd1.myworkdayjobs.com/Global?q={q}",
    "ust": "https://ust.wd1.myworkdayjobs.com/USTCareers?q={q}",
    "ntt data": "https://careers.inc.nttdata.com/search/?q={q}",
    "ups": "https://www.jobs-ups.com/search-jobs/{q}",
    "nutanix": "https://nutanix.wd1.myworkdayjobs.com/NutanixCareers?q={q}",
    "atlassian": "https://www.atlassian.com/company/careers/job-search?query={q}",
    "snowflake": "https://careers.snowflake.com/us/en/search-results?keywords={q}",
    "cohesity": "https://www.cohesity.com/company/careers/open-positions/?q={q}",
    "siemens": "https://jobs.siemens.com/jobs?page=1&query={q}",
    "ebay": "https://jobs.ebayinc.com/us/en/search-results?keywords={q}",
    "ey": "https://careers.ey.com/ey/search/?q={q}",
    "pwc": "https://jobs.us.pwc.com/search-jobs/{q}",
    "infosys": "https://career.infosys.com/joblist?query={q}",
    "tata consultancy services": "https://www.tcs.com/careers/search-jobs?q={q}",
    "tcs": "https://www.tcs.com/careers/search-jobs?q={q}",
    "accenture": "https://www.accenture.com/in-en/careers/jobsearch?jk={q}",
    "comviva": "https://careers.comviva.com/jobs?q={q}",
    "sonicwall": "https://www.sonicwall.com/careers/?q={q}"
}

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("SELECT job_id, company_name, title, apply_url, ats_platform FROM job_postings WHERE ats_platform = 'linkedin'")
    rows = [dict(r) for r in c.fetchall()]

    print(f"🚀 Found {len(rows)} LinkedIn jobs in database to convert...")

    converted_count = 0
    company_stats = {}

    for r in rows:
        comp = r["company_name"]
        norm_comp = re.sub(r'[^a-z]', '', comp.lower())
        title = r["title"]
        q_enc = urllib.parse.quote_plus(title)

        matched_portal = None
        matched_key = None
        for k, template in COMPANY_PORTALS.items():
            k_norm = re.sub(r'[^a-z]', '', k)
            if k_norm in norm_comp or norm_comp in k_norm:
                matched_portal = template.format(q=q_enc)
                matched_key = k
                break

        if matched_portal:
            converted_count += 1
            company_stats[comp] = company_stats.get(comp, 0) + 1
            
            # Generate clean job ID
            clean_hash = abs(hash(f"{norm_comp}_{title}"))
            new_job_id = f"{matched_key}_{clean_hash}"
            
            # Delete any duplicate row with same new_job_id
            c.execute("DELETE FROM job_postings WHERE job_id = ? AND job_id != ?", (new_job_id, r["job_id"]))
            
            # Update record
            c.execute("""
                UPDATE job_postings
                SET job_id = ?, apply_url = ?, ats_platform = ?
                WHERE job_id = ?
            """, (new_job_id, matched_portal, matched_key, r["job_id"]))

    conn.commit()
    conn.close()

    print("\n✅ Conversion Complete!")
    print(f"• Total Processed: {len(rows)}")
    print(f"• Converted to Direct Company Career Portals: {converted_count}")
    print(f"• Remaining Native LinkedIn Links: {len(rows) - converted_count}")
    print("\nTop Converted Companies:")
    sorted_stats = sorted(company_stats.items(), key=lambda x: x[1], reverse=True)
    for comp, count in sorted_stats[:20]:
        print(f"  - {comp}: {count} jobs updated")

if __name__ == "__main__":
    main()
