"""
Seniority and Experience Qualification Module.
Filters out:
1. Seniority titles: Lead, Staff, Principal, Architect, Director, VP, Head, Manager.
2. Experience requirements: Roles requiring > 6 years of experience (e.g. 7+ yrs, 8-10 yrs, 10+ yrs).
"""

import re
from typing import Optional, Tuple

EXCLUDED_SENIORITY_KEYWORDS = [
    "lead",
    "tech lead",
    "team lead",
    "technical lead",
    "staff",
    "principal",
    "architect",
    "architecture",
    "director",
    "vp",
    "vice president",
    "engineering manager",
    "head of"
]

def is_excluded_seniority(title: str) -> bool:
    """Returns True if title contains lead, staff, principal, architect or other management/high-level titles."""
    if not title:
        return False
    clean_title = ' ' + re.sub(r'[/,\-_\(\)\[\]:]', ' ', title.lower()).strip() + ' '
    for kw in EXCLUDED_SENIORITY_KEYWORDS:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, clean_title):
            return True
    return False

def extract_years_of_experience(text: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Extracts min and max years of experience from text if present.
    Returns (min_years, max_years) or (None, None).
    """
    if not text:
        return None, None

    patterns = [
        # E.g. "8 to 10 years", "7-9 yrs"
        r'\b(\d{1,2})\s*(?:to|-)\s*(\d{1,2})\s*(?:years?|yrs?)\b',
        # E.g. "7+ years", "8+ yrs"
        r'\b(\d{1,2})\s*\+\s*(?:years?|yrs?)\b',
        # E.g. "minimum 7 years", "at least 8 years", "> 6 years"
        r'(?:minimum|min|at\s+least|more\s+than|>|\+)\s*(?:of\s+)?(\d{1,2})\s*(?:years?|yrs?)\b',
        # E.g. "7 years of experience"
        r'\b(\d{1,2})\s*(?:years?|yrs?)\s*(?:of)?\s*(?:relevant|hands-on|industry|work|professional)?\s*(?:experience|exp)\b'
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            try:
                min_y = int(groups[0])
                max_y = int(groups[1]) if len(groups) > 1 and groups[1] else min_y
                return min_y, max_y
            except (ValueError, TypeError):
                continue

    return None, None

def exceeds_max_experience(text: str, max_allowed_years: int = 6) -> bool:
    """Returns True if the text indicates a required experience strictly greater than max_allowed_years (e.g. > 6)."""
    if not text:
        return False
    min_y, max_y = extract_years_of_experience(text)
    if min_y is not None:
        if min_y > max_allowed_years:
            return True
    return False

def is_qualified_seniority_and_exp(title: str, jd_text: str = "") -> bool:
    """
    Comprehensive gatekeeper:
    - Excludes lead, staff, principal, architect titles.
    - Excludes roles requiring > 6 years of experience.
    """
    if is_excluded_seniority(title):
        return False

    if exceeds_max_experience(title, max_allowed_years=6):
        return False

    if jd_text and exceeds_max_experience(jd_text, max_allowed_years=6):
        return False

    return True
