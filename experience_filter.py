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
    t_lower = title.lower()
    # "Member of Technical Staff" / "MTS" is standard IC title (MTS 1 = SDE-1, MTS 2 = SDE-2, MTS 3 = Sr SDE).
    # Do not treat "Staff" in "Member of Technical Staff" as a Staff Engineer role.
    if "member of technical staff" in t_lower or "member technical staff" in t_lower:
        if any(kw in t_lower for kw in ["lead", "principal", "director", "architect", "manager", "head"]):
            return True
        return False

    clean_title = ' ' + re.sub(r'[/,\-_\(\)\[\]:]', ' ', t_lower).strip() + ' '
    for kw in EXCLUDED_SENIORITY_KEYWORDS:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, clean_title):
            return True
    return False

def extract_years_of_experience(text: str, is_jd: bool = False) -> Tuple[Optional[int], Optional[int]]:
    """
    Extracts min and max years of experience from text if present.
    Returns (min_years, max_years) or (None, None).
    """
    if not text:
        return None, None

    if is_jd:
        # For long JD text, strictly match candidate experience requirement contexts
        # so company history ("15+ years in business") does not trigger false positives.
        candidate_patterns = [
            r'\b(\d{1,2})\s*(?:to|-)\s*(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant|hands-on|industry|work|professional|software|backend|development|engineering)?\s*(?:experience|exp)\b',
            r'\b(\d{1,2})\s*\+\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant|hands-on|industry|work|professional|software|backend|development|engineering)?\s*(?:experience|exp)\b',
            r'(?:minimum|min|at\s+least|more\s+than|>|\+)\s*(?:of\s+)?(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant|hands-on|industry|work|professional|software|backend|development|engineering)?\s*(?:experience|exp)\b',
            r'(?:require[sd]?|looking\s+for|must\s+have)\s*[:\s]*(?:at\s+least\s+)?(\d{1,2})\s*(?:\+|to\s*\d{1,2}|-\s*\d{1,2})?\s*(?:years?|yrs?)'
        ]
        for pat in candidate_patterns:
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

def exceeds_max_experience(text: str, max_allowed_years: int = 6, is_jd: bool = False) -> bool:
    """Returns True if the text indicates a required experience strictly greater than max_allowed_years (e.g. > 6)."""
    if not text:
        return False
    min_y, max_y = extract_years_of_experience(text, is_jd=is_jd)
    if min_y is not None:
        if min_y > max_allowed_years:
            return True
    return False

def is_mobile_or_device_role(title: str) -> bool:
    """Returns True if the title contains android, ios, mobile, flutter, swift, or react native."""
    if not title:
        return False
    t = title.lower()
    if any(k in t for k in ["android", "androis", "mobile", "flutter", "react native", "react-native", "swift"]):
        return True
    if re.search(r'\b(ios)\b', t):
        return True
    return False

def is_qualified_seniority_and_exp(title: str, jd_text: str = "") -> bool:
    """
    Comprehensive gatekeeper:
    - Excludes mobile/iOS/Android roles.
    - Excludes lead, staff, principal, architect titles.
    - Excludes roles requiring > 6 years of experience.
    """
    if is_mobile_or_device_role(title):
        return False

    if is_excluded_seniority(title):
        return False

    if exceeds_max_experience(title, max_allowed_years=6, is_jd=False):
        return False

    if jd_text and exceeds_max_experience(jd_text, max_allowed_years=6, is_jd=True):
        return False

    return True

INDIA_REGIONS = [
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
    "gurgaon", "gurugram", "delhi", "ncr", "noida", "chennai", "kolkata",
    "ahmedabad", "chandigarh", "coimbatore", "kochi", "cochin", "indore",
    "jaipur", "trivandrum", "thiruvananthapuram", "mysore", "mysuru",
    "karnataka", "telangana", "maharashtra", "tamil nadu", "haryana",
    "remote", "anywhere", "work from home", "wfh"
]

FOREIGN_COUNTRIES = [
    "aus", "australia", "nsw", "sydney", "melbourne", "us", "u.s.", "united states",
    "uk", "united kingdom", "london", "canada", "toronto", "vancouver", "germany",
    "berlin", "singapore", "japan", "tokyo", "france", "paris", "netherlands", "amsterdam"
]

DISALLOWED_TITLE_KEYWORDS = [
    # Frontend & UI
    "frontend", "front-end", "front end", "react developer", "angular developer", "vue",
    # Mobile app & device developer exclusions (Android, iOS, Flutter, React Native, Mobile)
    "android", "androis", "mobile", "flutter", "react native", "react-native", "swift",
    "mobile app", "mobile developer", "mobile application", "mobile engineer", "mobile software",
    "ios developer", "android developer", "ios engineer", "android engineer",
    # ServiceNow exclusions (Requirement #5)
    "servicenow", "service now", "service-now",
    # Quality & Testing
    "qa engineer", "qa tester", "qa lead", "quality assurance", "sdet", "test engineer",
    # Student / Junior
    "intern", "internship", "graduate", "trainee",
    # Management / Non-tech
    "director", "vp", "vice president", "engineering manager", "devops",
    "marketing", "sales", "support", "recruiter", "talent", "scrum master"
]

ALLOWED_TITLE_KEYWORDS = [
    "backend", "back end", "back-end", "distributed",
    "sde 2", "sde-2", "sde ii", "sde-ii", "sde", "senior sde",
    "software engineer ii", "senior software engineer", "software development engineer ii",
    "software engineer 2", "senior backend engineer", "senior software developer",
    "software dev engineer ii", "software dev engineer 2", "software dev engineer",
    "software development engineer", "software engineer", "sr software engineer", "sr. software engineer",
    "system software engineer", "systems software engineer", "system software developer",
    "java developer", "java engineer", "java software engineer", "java backend",
    "platform engineer", "systems engineer", "golang", "developer",
    "mts", "mts ii", "member of technical staff", "cloud engineer", "infrastructure engineer",
    "computer scientist"
]

def is_qualified_location(location: str) -> bool:
    """Returns False if location explicitly specifies a foreign country without mentioning India."""
    loc = (location or "").lower()
    if not loc:
        return True
    if "india" not in loc:
        if any(re.search(r'\b' + re.escape(fc) + r'\b', loc) for fc in FOREIGN_COUNTRIES):
            return False
    return any(r in loc for r in INDIA_REGIONS)

def is_qualified_job(title: str, location: str = "", jd_text: str = "") -> bool:
    """Complete qualification gatekeeper checking title, seniority, experience, and location."""
    t = (title or "").lower()
    if not is_qualified_seniority_and_exp(title, jd_text):
        return False
    if any(d in t for d in DISALLOWED_TITLE_KEYWORDS):
        return False
    if not any(a in t for a in ALLOWED_TITLE_KEYWORDS):
        return False
    if location and not is_qualified_location(location):
        return False
    return True
