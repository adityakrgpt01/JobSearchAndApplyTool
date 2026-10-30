"""
Routes a job to its dedicated auto-applier.

NVIDIA and Mastercard stay on their existing paths and are never matched here.
"""

import asyncio
import re
from typing import Any, Dict, Optional, Tuple

# (company word, url fragment, module, class, fill method)
_ASYNC_ROUTES = [
    (["visa"], ["visa.wd5"], "visa_applier", "VisaApplier", "fill_workday_form"),
    (["lseg", "london stock"], ["lseg.wd3"], "lseg_applier", "LsegApplier", "fill_workday_form"),
    (["salesforce"], ["salesforce.wd12"], "salesforce_applier", "SalesforceApplier", "fill_workday_form"),
    (["adobe"], ["adobe.wd5"], "adobe_applier", "AdobeApplier", "fill_workday_form"),
    (["rockwell"], ["rockwellautomation.wd1"], "rockwell_applier", "RockwellApplier", "fill_workday_form"),
    (["danaher"], ["danaher.wd1"], "danaher_applier", "DanaherApplier", "fill_workday_form"),
    (["target"], ["target.wd5"], "target_applier", "TargetApplier", "fill_workday_form"),
    (["workday"], ["workday.wd5"], "workday_corp_applier", "WorkdayCorpApplier", "fill_workday_form"),
    (["nike"], ["nike.wd1"], "nike_applier", "NikeApplier", "fill_workday_form"),
    (["docusign"], ["docusign.wd1"], "docusign_applier", "DocusignApplier", "fill_workday_form"),
    (["candescent"], ["candescent.wd501"], "candescent_applier", "CandescentApplier", "fill_workday_form"),
    (["natwest", "nat west"], ["rbs.wd3"], "natwest_applier", "NatWestApplier", "fill_workday_form"),
    (["ecolab"], ["ecolab.wd1"], "ecolab_applier", "EcolabApplier", "fill_workday_form"),
    (["ciena"], ["ciena.wd5"], "ciena_applier", "CienaApplier", "fill_workday_form"),
    (["baker hughes", "bakerhughes"], ["bakerhughes.wd5"], "baker_hughes_applier", "BakerHughesApplier", "fill_workday_form"),
    (["light & wonder", "light and wonder"], ["lnw.wd5"], "light_wonder_applier", "LightWonderApplier", "fill_workday_form"),
    (["teradata"], ["teradata.wd1"], "teradata_applier", "TeradataApplier", "fill_workday_form"),
    (["huron"], ["huron.wd1"], "huron_applier", "HuronApplier", "fill_workday_form"),
    (["msci"], ["msci.wd5"], "msci_applier", "MsciApplier", "fill_workday_form"),
    (["twilio"], ["greenhouse.io/twilio", "job-boards.greenhouse.io/twilio"], "twilio_applier", "TwilioApplier", "fill_form"),
    (["hashicorp"], ["greenhouse.io/hashicorp"], "hashicorp_applier", "HashicorpApplier", "fill_form"),
    (["netskope"], ["greenhouse.io/netskope"], "netskope_applier", "NetskopeApplier", "fill_form"),
    (["navan", "tripactions"], ["greenhouse.io/tripactions", "greenhouse.io/navan"], "navan_applier", "NavanApplier", "fill_form"),
    (["doordash", "door dash"], ["doordash", "greenhouse.io/doordash"], "doordash_applier", "DoorDashApplier", "fill_form"),
    (["airbnb"], ["airbnb"], "airbnb_applier", "AirbnbApplier", "fill_form"),
    (["uipath"], ["ashbyhq.com/uipath"], "uipath_applier", "UiPathApplier", "fill_form"),
    (["anyscale"], ["ashbyhq.com/anyscale"], "anyscale_applier", "AnyscaleApplier", "fill_form"),
    (["chargebee"], ["ashbyhq.com/chargebee"], "chargebee_applier", "ChargebeeApplier", "fill_form"),
    (["oracle"], ["oraclecloud", "oracle.com"], "oracle_applier", "OracleApplier", "fill_form"),
    (["apple"], ["jobs.apple.com"], "apple_applier", "AppleApplier", "fill_form"),
    (["sap"], ["jobs.sap.com", "sap.com"], "sap_applier", "SapApplier", "fill_form"),
    (["delta"], ["delta.com"], "delta_applier", "DeltaApplier", "fill_form"),
    (["everpure"], ["everpure"], "everpure_applier", "EverpureApplier", "fill_form"),
]

_SYNC_ROUTES = [
    (["servicenow"], ["smartrecruiters.com"], "servicenow_applier", "apply_to_servicenow_job", "ServiceNow"),
    (["booking"], ["jobs.booking.com", "booking.com"], "booking_applier", "apply_to_booking_job", "Booking Holdings"),
    (["wayfair"], ["smartrecruiters.com"], "wayfair_applier", "apply_to_wayfair_job", "Wayfair"),
]


def _word(name: str, token: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", name) is not None


_AGGREGATORS = ("linkedin.com", "instahyre.com", "naukri.com", "indeed.com")


def _excluded(company: str, url: str) -> bool:
    blob = f"{company} {url}"
    return "nvidia" in blob or "mastercard" in blob


def _match_route(company: str, url: str, routes):
    if _excluded(company, url):
        return None
    aggregator = any(host in url for host in _AGGREGATORS)
    for row in routes:
        tokens, urls = row[0], row[1]
        # "workday" as a company token must not swallow every Workday URL.
        company_hit = any(_word(company, token) for token in tokens)
        url_hit = any(fragment in url for fragment in urls)
        if tokens == ["workday"]:
            if _word(company, "workday") or "workday.wd5" in url:
                return row
            continue
        if tokens == ["wayfair"]:
            if company_hit or ("wayfair" in url):
                return row
            continue
        if tokens == ["servicenow"]:
            if company_hit or "servicenow" in url:
                return row
            continue
        if company_hit or url_hit:
            # A LinkedIn card for Rockwell is not the Workday application.
            if aggregator and not url_hit:
                continue
            return row
    return None


def _build(row, dry_run: bool, headless: bool, keep_open: bool):
    module_name, class_name = row[2], row[3]
    mod = __import__(module_name, fromlist=[class_name])
    cls = getattr(mod, class_name)
    return cls(dry_run=dry_run, headless=headless, keep_open=keep_open)


def _as_tuple(result) -> Tuple[bool, str]:
    if isinstance(result, tuple) and len(result) >= 2:
        return bool(result[0]), str(result[1])
    if isinstance(result, dict):
        status = str(result.get("status") or "")
        ok = status in ("SUCCESS", "APPLIED") or "DRY" in status or status.startswith("READY")
        if result.get("mode") == "DRY_RUN":
            message = "Review page validated"
        else:
            message = str(
                result.get("message")
                or result.get("error")
                or status
                or "Application step finished"
            )
        return ok, message
    return bool(result), str(result)


_INDIA = ("india", "pune", "bengaluru", "bangalore", "hyderabad", "mumbai", "gurgaon", "gurugram", "noida", "chennai")


def _company_route(company: str):
    """Match a tracked company even when the card URL is still a LinkedIn link."""
    if _excluded(company, ""):
        return None
    for routes in (_ASYNC_ROUTES, _SYNC_ROUTES):
        for row in routes:
            tokens = row[0]
            if tokens == ["workday"]:
                if _word(company, "workday"):
                    return row
                continue
            if any(_word(company, token) for token in tokens):
                return row
    return None


def _workday_host(fragment: str) -> Optional[str]:
    host = fragment.split("/")[0]
    if "myworkdayjobs.com" in host:
        return host
    if re.search(r"\.wd\d+$", host):
        return host + ".myworkdayjobs.com"
    return None


def _boards_for_host(host: str):
    from database import get_db_connection

    boards = []
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT apply_url FROM job_postings WHERE apply_url LIKE ? AND apply_url LIKE '%/job/%' LIMIT 40",
            (f"%{host}%",),
        ).fetchall()
    finally:
        conn.close()
    for (url,) in rows:
        match = re.search(r"myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/job/", url or "")
        if match and match.group(1) not in boards:
            boards.append(match.group(1))
    if "rockwellautomation" in host and "External_Rockwell_Automation" not in boards:
        boards.insert(0, "External_Rockwell_Automation")
    return boards


def _norm_title(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    text = re.sub(r"\bsr\b", "senior", text)
    return re.sub(r"\s+", " ", text).strip()


def _score_posting(want: str, title: str, location: str) -> float:
    """Only an exact title match counts. A shorter title must not grab a different role."""
    left, right = _norm_title(want), _norm_title(title)
    if not left or left != right:
        return 0
    score = 100.0
    blob = f"{location} {title}".lower()
    if any(place in blob for place in _INDIA):
        score += 15
    return score


def _search_workday(host: str, board: str, title: str) -> Optional[str]:
    import json
    import urllib.request

    tenant = host.split(".")[0]
    endpoint = f"https://{host}/wday/cxs/{tenant}/{board}/jobs"
    best_url = None
    best_score = 0.0
    offset = 0
    for _ in range(3):
        payload = json.dumps({
            "appliedFacets": {},
            "limit": 20,
            "offset": offset,
            "searchText": title,
        }).encode()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode())
        posts = data.get("jobPostings") or []
        for post in posts:
            score = _score_posting(title, post.get("title") or "", post.get("locationsText") or "")
            path = post.get("externalPath") or ""
            if path and score > best_score:
                best_score = score
                best_url = f"https://{host}/en-US/{board}{path}"
        total = int(data.get("total") or 0)
        offset += 20
        if not posts or offset >= total:
            break
    if best_score < 80:
        return None
    return best_url


def _linkedin_page_title(url: str) -> Optional[str]:
    """Read the public job title. Search cards often drop a qualifier such as '(.Net)'."""
    import urllib.request

    match = re.search(r"(\d{8,})(?:/)?$", (url or "").split("?")[0].rstrip("/"))
    if not match:
        return None
    guest = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{match.group(1)}"
    request = urllib.request.Request(guest, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", "ignore")
    found = re.search(r"<h2[^>]*top-card-layout__title[^>]*>\s*([^<]+)", html, re.I)
    if not found:
        found = re.search(r"<h2[^>]*>\s*([^<]+)", html, re.I)
    if not found:
        return None
    title = re.sub(r"\s+", " ", found.group(1)).strip()
    if not title or len(title) > 140:
        return None
    return title


def _same_posting_applied(job_id: str, apply_url: str) -> bool:
    from database import get_db_connection

    match = re.search(r"(/job/.+)$", apply_url.split("?")[0].rstrip("/"), re.I)
    key = (match.group(1) if match else apply_url).lower()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT 1 FROM job_postings
            WHERE job_id != ? AND lower(apply_url) LIKE ? AND status LIKE '%APPLIED%'
            LIMIT 1
            """,
            (job_id, f"%{key}%"),
        ).fetchall()
    finally:
        conn.close()
    return bool(rows)


def _persist_workday_target(job_id: str, apply_url: str, title: Optional[str] = None):
    from database import get_db_connection

    conn = get_db_connection()
    try:
        if title:
            conn.execute(
                "UPDATE job_postings SET apply_url = ?, ats_platform = 'workday', title = ? WHERE job_id = ?",
                (apply_url, title, job_id),
            )
        else:
            conn.execute(
                "UPDATE job_postings SET apply_url = ?, ats_platform = 'workday' WHERE job_id = ?",
                (apply_url, job_id),
            )
        conn.commit()
    finally:
        conn.close()


async def resolve_linkedin_card(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Point a LinkedIn card at the company's Workday posting before Auto-Fill runs."""
    url = (job.get("apply_url") or "").lower()
    if "linkedin.com" not in url:
        return None
    row = _company_route((job.get("company_name") or "").lower())
    if row is None:
        return None
    host = next((_workday_host(fragment) for fragment in row[1] if _workday_host(fragment)), None)
    if not host:
        return None

    boards = await asyncio.to_thread(_boards_for_host, host)
    title = job.get("title") or ""

    async def search(query: str) -> Optional[str]:
        for board in boards:
            try:
                found_url = await asyncio.to_thread(_search_workday, host, board, query)
            except Exception as exc:
                print(f"[LinkedIn card] {host}/{board} search failed: {exc}")
                continue
            if found_url:
                return found_url
        return None

    found = await search(title)
    if not found:
        try:
            page_title = await asyncio.to_thread(_linkedin_page_title, job.get("apply_url") or "")
        except Exception as exc:
            print(f"[LinkedIn card] Could not read the LinkedIn title: {exc}")
            page_title = None
        if page_title and _norm_title(page_title) != _norm_title(title):
            print(f"[LinkedIn card] '{title}' is '{page_title}' on the LinkedIn page.")
            title = page_title
            job["title"] = page_title
            found = await search(title)
    if not found:
        print(f"[LinkedIn card] No {job.get('company_name')} posting matched '{title}'.")
        return None

    print(f"[LinkedIn card] {job.get('job_id')} -> {found}")
    job["apply_url"] = found
    job["ats_platform"] = "workday"
    _persist_workday_target(job["job_id"], found, job.get("title"))
    if _same_posting_applied(job["job_id"], found):
        from database import update_job_status

        message = "LinkedIn card matches a Workday posting that is already submitted."
        update_job_status(job["job_id"], "APPLIED", message)
        return {"job_id": job["job_id"], "status": "APPLIED", "message": message}
    return None


async def delegate_tracked_fill(page, job: Dict[str, Any], dry_run: bool, headless: bool, keep_open: bool):
    """
    Fill the current page with the company's dedicated applier.
    Returns (ok, message), or None when this job is NVIDIA, Mastercard, or untracked.
    """
    company = (job.get("company_name") or "").lower()
    url = (job.get("apply_url") or "").lower()
    row = _match_route(company, url, _ASYNC_ROUTES)
    if row is None:
        return None
    applier = _build(row, dry_run, headless, keep_open)
    fill_name = row[4]
    print(f"[Auto Apply] Using {row[3]} for {job.get('company_name')}.")
    result = await getattr(applier, fill_name)(page, job)
    return _as_tuple(result)


async def run_sync_tracked_job(job: Dict[str, Any], dry_run: bool, headless: bool, keep_open: bool) -> Optional[Dict[str, Any]]:
    """Run ServiceNow, Booking, or Wayfair. Returns None for every other company."""
    company = (job.get("company_name") or "").lower()
    url = (job.get("apply_url") or "").lower()
    row = _match_route(company, url, _SYNC_ROUTES)
    if row is None:
        return None
    module_name, fn_name, label = row[2], row[3], row[4]
    mod = __import__(module_name, fromlist=[fn_name])
    fn = getattr(mod, fn_name)
    job_id = job["job_id"]
    print(f"[Auto Apply] Using {label} applier for {job.get('company_name')}.")
    headed = False if keep_open else headless
    ok = await asyncio.to_thread(
        fn,
        job.get("apply_url"),
        job_id,
        dry_run,
        headed,
        keep_open,
    )
    status = "READY_TO_SUBMIT (DRY_RUN)" if ok and dry_run else ("APPLIED" if ok else "FAILED")
    message = f"{label} application {'validated' if dry_run else 'finished'}" if ok else f"{label} application failed"
    from database import update_job_status
    shot = f"screenshots/{row[2].split('_')[0]}_{job_id}.png"
    update_job_status(job_id, status, message, shot)
    return {"job_id": job_id, "status": status, "message": message, "screenshot": shot}
