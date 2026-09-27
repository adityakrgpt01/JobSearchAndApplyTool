"""
LLM Answer Engine & Intelligent Form Resolver.
Extracts questions from job application forms and synthesizes accurate,
context-aware answers using LLM (Gemini / OpenAI) with intelligent heuristic fallback
based on user_profile.json and resume.pdf.
"""

import os
import json
import re
from typing import Dict, Any, List, Optional

class FormLLMEngine:
    def __init__(self, profile_data: Dict[str, Any]):
        self.profile = profile_data
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.client = None
        if self.api_key and os.getenv("GEMINI_API_KEY"):
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[LLM Engine] Gemini init note: {e}")

    def answer_question(self, question_text: str, options: Optional[List[str]] = None) -> str:
        """
        Synthesizes an answer for any dynamic custom question.
        Uses LLM if API key is present; otherwise falls back to smart contextual heuristics.
        """
        q_clean = question_text.lower().strip()
        
        # 1. Try smart deterministic match first for high reliability & speed
        quick_ans = self._match_heuristics(q_clean, options)
        if quick_ans is not None:
            return quick_ans

        # 2. If Gemini API is available, query LLM
        if self.client:
            try:
                prompt = f"""
You are an expert job applicant answering application questions.
Candidate Profile:
- Full Name: {self.profile['personal']['first_name']} {self.profile['personal']['last_name']}
- Experience: {self.profile['professional']['years_of_experience']} years as SDE II / Senior Backend Engineer
- Core Skills: {', '.join(self.profile['professional']['primary_skills'])}
- Current Employer: {self.profile['professional']['work_experience'][0]['company']}
- Education: {self.profile['professional']['education']['degree']} from {self.profile['professional']['education']['institution']}
- Current Location: {self.profile['personal']['current_city']}, {self.profile['personal']['current_country']}
- Notice Period: {self.profile['professional']['notice_period_days']} days
- Expected CTC: {self.profile['professional']['expected_ctc_lpa']} LPA

Question: "{question_text}"
{"Available Options: " + str(options) if options else ""}

Return ONLY the direct, concise answer or the exact option text that best fits the candidate. Do not provide commentary.
"""
                resp = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
                text = resp.text.strip()
                if options and text not in options:
                    # Find closest option
                    for opt in options:
                        if text.lower() in opt.lower() or opt.lower() in text.lower():
                            return opt
                return text
            except Exception as e:
                print(f"[LLM Engine] Error generating LLM answer: {e}")

        # 3. Default fallback based on question pattern
        return self._generate_fallback(q_clean, options)

    def _match_heuristics(self, q: str, options: Optional[List[str]] = None) -> Optional[str]:
        pers = self.profile.get("personal", {})
        prof = self.profile.get("professional", {})
        custom = self.profile.get("custom_answers", {})

        # LinkedIn Profile
        if any(w in q for w in ["linkedin", "linked in", "linkedin profile"]):
            return pers.get("linkedin_url", "")

        # GitHub Profile
        if any(w in q for w in ["github", "git hub", "github profile"]):
            return pers.get("github_url", "")

        # Current Company
        if any(w in q for w in ["current company", "most recent company", "current employer", "current (or most recent)"]):
            exp = prof.get("work_experience", [])
            if exp and len(exp) > 0 and exp[0].get("company"):
                return exp[0]["company"]
            return "73 Strings"

        # How did you hear about this job
        if any(w in q for w in ["how did you hear", "hear about this", "source of application"]):
            if options:
                for opt in options:
                    if any(s in opt.lower() for s in ["linkedin", "job board", "career site", "company website", "online"]):
                        return opt
                return options[0]
            return "Company Career Portal / LinkedIn"

        # Authorization & Sponsorship
        if "authorized to work" in q or "legally authorized" in q:
            if "india" in q:
                return self._pick_option(["yes", "authorized"], options, "Yes")
            if any(c in q for c in ["u.s.", "united states", "us", "uk", "eu", "canada"]):
                # Remote / requires authorization
                return self._pick_option(["no", "not authorized"], options, "No")
            return self._pick_option(["yes", "authorized"], options, "Yes")

        if "sponsorship" in q or "visa sponsorship" in q or "require sponsorship" in q:
            return self._pick_option(["no", "will not require"], options, "No")

        # Notice period
        if any(w in q for w in ["notice period", "days notice", "how soon can you start"]):
            days = str(prof.get("notice_period_days", 30))
            if options:
                for opt in options:
                    if days in opt or "1 month" in opt.lower() or "30" in opt:
                        return opt
                return options[0]
            return f"{days} days"

        # Years of experience
        if any(w in q for w in ["years of experience", "total experience", "yoe"]):
            yoe = str(prof.get("years_of_experience", 5))
            if options:
                for opt in options:
                    if yoe in opt or "4-6" in opt or "5+" in opt:
                        return opt
                return options[0]
            return str(yoe)

        # Expected compensation / CTC
        if any(w in q for w in ["expected ctc", "expected salary", "salary expectation", "compensation"]):
            ctc = prof.get("expected_ctc_lpa", 55.0)
            return f"₹{ctc} LPA"

        # City / Location
        if any(w in q for w in ["city", "current location", "where are you located", "location (city)"]):
            return pers.get("current_city", "Bengaluru")

        # Gender / Equal Opportunity
        if "gender" in q and not "transgender" in q:
            return self._pick_option(["male", "man"], options, "Male")

        if "transgender" in q:
            return self._pick_option(["no", "decline", "prefer not"], options, "No")

        if "sexual orientation" in q:
            return self._pick_option(["heterosexual", "straight", "decline", "prefer not"], options, "Heterosexual / Straight")

        if "disability" in q:
            return self._pick_option(["no, i do not", "no", "do not have a disability", "prefer not"], options, "No, I do not have a disability")

        if "veteran" in q or "military" in q:
            return self._pick_option(["not a protected veteran", "no", "not a veteran", "prefer not"], options, "I am not a protected veteran")

        return None

    def _pick_option(self, keywords: List[str], options: Optional[List[str]], default_val: str) -> str:
        if not options:
            return default_val
        for kw in keywords:
            for opt in options:
                if kw.lower() in opt.lower():
                    return opt
        return options[0]

    def _generate_fallback(self, q: str, options: Optional[List[str]]) -> str:
        if options and len(options) > 0:
            # Check for non-committal or affirmative option
            for opt in options:
                o_low = opt.lower()
                if "prefer not" in o_low or "decline" in o_low or "n/a" in o_low:
                    return opt
            return options[0]
        return self.profile.get("custom_answers", {}).get("summary_for_recruiter", "Senior Backend Engineer with 5 YoE in distributed systems, microservices, and Java/Spring.")
