import sqlite3
import time
import random
import re
from ddgs import DDGS

def parse_salary_max(snippet: str):
    """
    Looks for patterns like '₹69.6 Lakhs to ₹111.0 Lakhs' or '₹25.1 Lakhs'
    Returns the maximum LPA found as a float, or None.
    """
    # Regex to find all occurrences of something like "₹111.0 Lakhs" or "₹ 45 Lakhs"
    # We'll just grab the numbers and take the max.
    matches = re.findall(r'₹\s*([\d\.]+)\s*Lakhs?', snippet, flags=re.IGNORECASE)
    
    if matches:
        try:
            # Convert all found matches to float and take the maximum
            max_val = max([float(m) for m in matches])
            return max_val
        except ValueError:
            return None
    return None

def get_tier_from_max(max_lpa: float):
    if max_lpa >= 70.0:
        return "70+LPA"
    elif max_lpa >= 60.0:
        return "60-70LPA"
    elif max_lpa >= 50.0:
        return "50-60LPA"
    else:
        # Defaults below 50 to 40-50LPA or we can be more granular, but DB uses 40-50LPA as base
        return "40-50LPA"

def main():
    conn = sqlite3.connect('jobs.db', timeout=10)
    cursor = conn.cursor()
    
    # Exclude those that already have a salary_max_lpa > 0
    # Also exclude if we marked it -1 (unparseable)
    cursor.execute("SELECT id, company_name FROM companies_intelligence WHERE salary_max_lpa = 0.0")
    companies = cursor.fetchall()
    
    print(f"Found {len(companies)} companies to update salary for.")
    if not companies: return
        
    ddgs = DDGS()
    updated_count = 0
    batch_size = 50
    
    for i, (comp_id, comp_name) in enumerate(companies):
        query = f'site:ambitionbox.com "{comp_name}" ("Senior Backend Engineer" OR "Backend Engineer" OR "Software Engineer") salary'
        print(f"[{i+1}/{len(companies)}] Searching: {query}")
        
        try:
            results = ddgs.text(query, max_results=3)
            
            max_lpa = None
            for res in results:
                max_lpa = parse_salary_max(res.get('body', ''))
                if max_lpa is not None: 
                    break
            
            if max_lpa is not None:
                new_tier = get_tier_from_max(max_lpa)
                print(f"  -> Found Max Salary for {comp_name}: {max_lpa} LPA -> Tier: {new_tier}")
                
                cursor.execute("""
                    UPDATE companies_intelligence 
                    SET salary_max_lpa = ?, salary_benchmark_tier = ?
                    WHERE id = ?
                """, (max_lpa, new_tier, comp_id))
                
                cursor.execute("""
                    UPDATE job_postings
                    SET salary_tier = ?
                    WHERE LOWER(company_name) = LOWER(?)
                """, (new_tier, comp_name))
                
                conn.commit()
                updated_count += 1
            else:
                # Mark as -1 so we don't retry forever
                cursor.execute("UPDATE companies_intelligence SET salary_max_lpa = -1.0 WHERE id = ?", (comp_id,))
                conn.commit()
                print(f"  -> Could not parse salary.")
                
        except Exception as e:
            print(f"  -> Search failed (Rate Limit?): {e}")
            time.sleep(random.uniform(5.0, 10.0))
            
        time.sleep(random.uniform(2.5, 4.5))
        
        if (i + 1) % batch_size == 0:
            print(f"--- Completed batch of {batch_size}. Sleeping for 15s to prevent rate limits... ---")
            time.sleep(15)
        
    conn.close()
    print(f"Salary update complete! Successfully parsed {updated_count} out of {len(companies)}.")

if __name__ == '__main__':
    main()
