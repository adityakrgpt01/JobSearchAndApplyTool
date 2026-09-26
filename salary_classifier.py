"""
Salary Classifier & Tier Benchmarking Engine.
Maps roles & companies into 40-50LPA, 50-60LPA, 60-70LPA, 70+LPA.
"""

from typing import Dict, Any, Tuple
import re

# Benchmark company tiers for 4-6 YoE Senior Backend / SDE-2 in India
COMPANY_TIER_BENCHMARKS = {
    # 70+ LPA (HFTs, Global Remote US Tech, Top Tier-1 Tech / Staff / High equity)
    "rippling": {"tier": "70+LPA", "est_base": "₹55L - ₹65L", "est_ctc": "₹75L - ₹95L (inc. Stocks)", "risk": "LOW"},
    "rubrik": {"tier": "70+LPA", "est_base": "₹50L - ₹60L", "est_ctc": "₹70L - ₹90L", "risk": "LOW"},
    "databricks": {"tier": "70+LPA", "est_base": "₹55L - ₹65L", "est_ctc": "₹80L - ₹1Cr", "risk": "LOW"},
    "snowflake": {"tier": "70+LPA", "est_base": "₹55L - ₹65L", "est_ctc": "₹80L - ₹1Cr", "risk": "LOW"},
    "stripe": {"tier": "70+LPA", "est_base": "₹55L - ₹65L", "est_ctc": "₹80L - ₹1.1Cr", "risk": "LOW"},
    "coinbase": {"tier": "70+LPA", "est_base": "₹50L - ₹60L", "est_ctc": "₹70L - ₹90L", "risk": "MODERATE"},
    "tower research": {"tier": "70+LPA", "est_base": "₹60L - ₹75L", "est_ctc": "₹90L - ₹1.3Cr", "risk": "LOW"},
    "graviton": {"tier": "70+LPA", "est_base": "₹60L - ₹75L", "est_ctc": "₹90L - ₹1.2Cr", "risk": "LOW"},
    "de shaw": {"tier": "70+LPA", "est_base": "₹55L - ₹65L", "est_ctc": "₹80L - ₹1Cr", "risk": "LOW"},

    # 60-70 LPA (Tier-1 Giants & High-Paying Product Unicorns)
    "uber": {"tier": "60-70LPA", "est_base": "₹45L - ₹52L", "est_ctc": "₹62L - ₹72L", "risk": "LOW"},
    "atlassian": {"tier": "60-70LPA", "est_base": "₹42L - ₹50L", "est_ctc": "₹60L - ₹75L", "risk": "LOW"},
    "google": {"tier": "60-70LPA", "est_base": "₹42L - ₹50L", "est_ctc": "₹65L - ₹75L (L4)", "risk": "LOW"},
    "salesforce": {"tier": "60-70LPA", "est_base": "₹40L - ₹48L", "est_ctc": "₹60L - ₹68L", "risk": "LOW"},
    "linkedin": {"tier": "60-70LPA", "est_base": "₹42L - ₹48L", "est_ctc": "₹60L - ₹70L", "risk": "LOW"},
    "browserstack": {"tier": "60-70LPA", "est_base": "₹42L - ₹52L", "est_ctc": "₹60L - ₹70L", "risk": "LOW"},

    # 50-60 LPA (Tier-2 Giants & Fast-Scaling Series C/D)
    "microsoft": {"tier": "50-60LPA", "est_base": "₹38L - ₹45L", "est_ctc": "₹52L - ₹62L (L61/L62)", "risk": "LOW"},
    "amazon": {"tier": "50-60LPA", "est_base": "₹36L - ₹44L", "est_ctc": "₹50L - ₹62L (SDE-2)", "risk": "MODERATE"},
    "phonepe": {"tier": "50-60LPA", "est_base": "₹38L - ₹46L", "est_ctc": "₹50L - ₹65L", "risk": "LOW"},
    "razorpay": {"tier": "50-60LPA", "est_base": "₹38L - ₹45L", "est_ctc": "₹50L - ₹60L", "risk": "LOW"},
    "swiggy": {"tier": "50-60LPA", "est_base": "₹36L - ₹44L", "est_ctc": "₹50L - ₹60L", "risk": "LOW"},
    "zepto": {"tier": "50-60LPA", "est_base": "₹38L - ₹46L", "est_ctc": "₹50L - ₹65L", "risk": "MODERATE"},
    "intuit": {"tier": "50-60LPA", "est_base": "₹36L - ₹42L", "est_ctc": "₹50L - ₹58L", "risk": "LOW"},
    "servicenow": {"tier": "50-60LPA", "est_base": "₹36L - ₹42L", "est_ctc": "₹50L - ₹58L", "risk": "LOW"},
    "adobe": {"tier": "50-60LPA", "est_base": "₹35L - ₹42L", "est_ctc": "₹50L - ₹60L", "risk": "LOW"},
    "cred": {"tier": "50-60LPA", "est_base": "₹40L - ₹48L", "est_ctc": "₹52L - ₹65L", "risk": "LOW"},

    # 40-50 LPA (Well-Funded Startups & High Product MNCs)
    "walmart": {"tier": "40-50LPA", "est_base": "₹30L - ₹36L", "est_ctc": "₹42L - ₹50L", "risk": "LOW"},
    "paypal": {"tier": "40-50LPA", "est_base": "₹32L - ₹38L", "est_ctc": "₹44L - ₹52L", "risk": "LOW"},
    "gojek": {"tier": "40-50LPA", "est_base": "₹32L - ₹38L", "est_ctc": "₹45L - ₹52L", "risk": "LOW"},
    "meesho": {"tier": "40-50LPA", "est_base": "₹32L - ₹38L", "est_ctc": "₹44L - ₹52L", "risk": "LOW"},
    "urban company": {"tier": "40-50LPA", "est_base": "₹30L - ₹36L", "est_ctc": "₹40L - ₹48L", "risk": "LOW"},
    "makemytrip": {"tier": "40-50LPA", "est_base": "₹28L - ₹35L", "est_ctc": "₹40L - ₹48L", "risk": "LOW"},
    "curefit": {"tier": "40-50LPA", "est_base": "₹30L - ₹36L", "est_ctc": "₹42L - ₹48L", "risk": "LOW"},
    "dream11": {"tier": "40-50LPA", "est_base": "₹32L - ₹38L", "est_ctc": "₹45L - ₹52L", "risk": "LOW"},
}

def extract_salary_from_text(text: str) -> Tuple[str, str]:
    """Extract explicit salary if mentioned in the JD (e.g. $140,000 or ₹50,00,000)."""
    # Check for USD ranges (US Remote)
    usd_match = re.search(r'\$(\d{2,3}),?(\d{3})?\s*(?:-|to)\s*\$?(\d{2,3}),?(\d{3})?', text, re.IGNORECASE)
    if usd_match:
        # E.g. $130,000 - $180,000 USD is > 1 Crore INR -> 70+LPA
        return "70+LPA", f"US Remote: {usd_match.group(0)}"
    
    # Check for INR Lakhs mentions
    inr_match = re.search(r'(?:₹|INR|Rs\.?)\s*(\d{2})\s*(?:-|to)\s*(\d{2})\s*(?:lakhs?|lpa|lac)', text, re.IGNORECASE)
    if inr_match:
        min_l = int(inr_match.group(1))
        max_l = int(inr_match.group(2))
        avg = (min_l + max_l) / 2
        if avg >= 70:
            return "70+LPA", f"₹{min_l}L - ₹{max_l}L CTC"
        elif avg >= 60:
            return "60-70LPA", f"₹{min_l}L - ₹{max_l}L CTC"
        elif avg >= 50:
            return "50-60LPA", f"₹{min_l}L - ₹{max_l}L CTC"
        else:
            return "40-50LPA", f"₹{min_l}L - ₹{max_l}L CTC"
            
    return None, None

def classify_salary(company_name: str, jd_text: str = "") -> Tuple[str, str]:
    """Returns (salary_tier, estimated_ctc) based on explicit text or company benchmark."""
    # 1. Try explicit JD extraction
    if jd_text:
        tier, ctc = extract_salary_from_text(jd_text)
        if tier:
            return tier, ctc

    # 2. Try company benchmark
    norm = company_name.strip().lower()
    for known_company, data in COMPANY_TIER_BENCHMARKS.items():
        if known_company in norm or norm in known_company:
            return data["tier"], data["est_ctc"]

    # 3. Default for Senior Backend / SDE-2 at product companies
    return "40-50LPA", "₹40L - ₹50L (Standard Product Benchmark)"
