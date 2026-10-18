import sqlite3
import time
import random
import re
from ddgs import DDGS
from itertools import islice

def parse_headcount(snippet: str):
    snippet_lower = snippet.lower().replace('.', ' ').replace(',', '')
    match = re.search(r'company size\s*(\d+)\s*(?:-|to)\s*(\d+)\s*employees', snippet_lower)
    if match: return int(match.group(1)), int(match.group(2))
    match2 = re.search(r'(\d+)\s*(?:-|to)\s*(\d+)\s*employees', snippet_lower)
    if match2: return int(match2.group(1)), int(match2.group(2))
    match3 = re.search(r'(\d+)\+\s*employees', snippet_lower)
    if match3: return int(match3.group(1)), int(match3.group(1)) * 10
    match_size = re.search(r'company size\s*(\d+)\s*(?:-|to)\s*(\d+)', snippet_lower)
    if match_size: return int(match_size.group(1)), int(match_size.group(2))
    match_size_plus = re.search(r'company size\s*(\d+)\+', snippet_lower)
    if match_size_plus: return int(match_size_plus.group(1)), int(match_size_plus.group(1)) * 10
    return None, None

def main():
    conn = sqlite3.connect('jobs.db', timeout=10)
    cursor = conn.cursor()
    
    # Exclude those that already have a min (already processed)
    # or failed (we could mark failed ones with -1 later, but for now we'll just try all)
    cursor.execute("SELECT id, company_name FROM companies_intelligence WHERE headcount_min = 0")
    companies = cursor.fetchall()
    
    print(f"Found {len(companies)} companies to update.")
    if not companies: return
        
    ddgs = DDGS()
    updated_count = 0
    batch_size = 50
    
    for i, (comp_id, comp_name) in enumerate(companies):
        query = f'"{comp_name}" "company size" employees'
        print(f"[{i+1}/{len(companies)}] Searching: {query}")
        
        try:
            results = ddgs.text(query, max_results=3)
            min_val, max_val = None, None
            for res in results:
                min_val, max_val = parse_headcount(res.get('body', ''))
                if min_val is not None: break
            
            if min_val is not None and max_val is not None:
                new_range = f"{min_val}+" if max_val >= min_val * 10 else f"{min_val} - {max_val}"
                print(f"  -> Found size for {comp_name}: {new_range}")
                cursor.execute("""
                    UPDATE companies_intelligence 
                    SET headcount_min = ?, headcount_max = ?, headcount_range = ?
                    WHERE id = ?
                """, (min_val, max_val, new_range, comp_id))
                conn.commit()
                updated_count += 1
            else:
                # Mark as -1 so we don't retry forever if it's truly unparseable
                cursor.execute("UPDATE companies_intelligence SET headcount_min = -1 WHERE id = ?", (comp_id,))
                conn.commit()
                print(f"  -> Could not parse.")
                
        except Exception as e:
            print(f"  -> Search failed (Rate Limit?): {e}")
            time.sleep(random.uniform(5.0, 10.0))
            
        time.sleep(random.uniform(2.5, 4.5))
        
        if (i + 1) % batch_size == 0:
            print(f"--- Completed batch of {batch_size}. Sleeping for 15s to prevent rate limits... ---")
            time.sleep(15)
        
    conn.close()
    print(f"Update complete! Successfully parsed {updated_count} out of {len(companies)}.")

if __name__ == '__main__':
    main()
