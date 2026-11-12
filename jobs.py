"""
Project Management Jobs Canada: new Canadian jobs in project management, construction, coordination and
scheduling, posted to a Telegram channel (English): title, employer, location, salary, short summary, link.

Source: Job Bank (Government of Canada) official job-search feeds, which include Job Bank's own postings and
its partner sites. Checked once an hour; each job is posted once.
Also posts recent news about Canadian construction projects (trade magazines + news search), up to 4 an hour.

Run from the Ottawa workflow loop:  JOBS_CHANNEL="@yourchannel" python jobs.py
Env: TELEGRAM_BOT_TOKEN, JOBS_CHANNEL, JOBS_STATE (default state-jobs.json), JOBS_EVERY (seconds, default 3600),
     JOBS_MAX_PER_RUN (default 15).   python jobs.py --check  prints sample posts and posts nothing.
"""
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

import bot

CHANNEL = os.environ.get("JOBS_CHANNEL", "")
STATE = Path(os.environ.get("JOBS_STATE", "state-jobs.json"))
EVERY = int(os.environ.get("JOBS_EVERY", "3600"))
TZ = ZoneInfo("America/Toronto")
SUMMARY_TIME = os.environ.get("JOBS_SUMMARY_TIME", "21:00")  # end-of-day list of the day's jobs, Ottawa time
MAX_PER_RUN = int(os.environ.get("JOBS_MAX_PER_RUN", "15"))
FEED = "https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed?fage=2&sort=D&rows=100&searchstring="

# Job Bank searches (one feed each, checked hourly).
QUERIES = [
    # project management
    "project manager", "senior project manager", "assistant project manager", "project coordinator",
    "project administrator", "project director", "program manager", "portfolio manager", "PMO",
    "project controls", "project planner", "planner scheduler", "scheduler", "cost controller", "cost engineer",
    "contract administrator", "contracts manager", "document controller", "project engineer",
    # construction
    "construction manager", "construction project manager", "construction coordinator",
    "construction superintendent", "site superintendent", "site supervisor construction", "site engineer",
    "field engineer", "estimator", "quantity surveyor", "preconstruction", "development manager",
    "owner's representative", "capital projects", "commissioning manager", "BIM coordinator",
]
# the job title must match one of these (and none of SKIP)
KEEP = re.compile(
    r"project (manager|management|coordinator|administrator|director|engineer|control|planner|lead|officer|"
    r"supervisor|estimator|scheduler|analyst|specialist|superintendent|executive)|program manager|portfolio manager|"
    r"\bpmo\b|construction|superintendent|schedul|estimator|quantity surveyor|contracts? (administrator|manager)|"
    r"cost (controller|engineer|manager|control)|document control|preconstruction|pre-construction|"
    r"development (manager|coordinator)|owner'?s rep|capital (project|planning)|site (supervisor|engineer|manager)|"
    r"field engineer|commissioning (manager|agent|coordinator|lead)|bim (coordinator|manager)|planner", re.I)
SKIP = re.compile(
    r"dishwasher|cook\b|cashier|cleaner|driver|labourer|labour|helper|carpenter|electrician|plumber|welder|"
    r"operator|painter|roofer|installer|mechanic|apprentice|patient|medical|dental|clinic|auto ?body|collision|"
    r"insurance|financial planner|meal planner|event planner|wedding|travel|production scheduler|warehouse", re.I)

PROVINCES = {
    "ON": "Ontario", "QC": "Quebec", "BC": "BritishColumbia", "AB": "Alberta", "MB": "Manitoba",
    "SK": "Saskatchewan", "NS": "NovaScotia", "NB": "NewBrunswick", "NL": "Newfoundland", "PE": "PEI",
    "YT": "Yukon", "NT": "NorthwestTerritories", "NU": "Nunavut",
}
ROLE_TAGS = [
    (r"schedul|planner", "#Scheduling"), (r"coordinator", "#ProjectCoordinator"),
    (r"superintendent", "#Superintendent"), (r"estimat", "#Estimating"),
    (r"contract admin", "#ContractAdministration"), (r"project control", "#ProjectControls"),
    (r"project engineer", "#ProjectEngineer"), (r"manager", "#ProjectManager"),
]


def load():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seen": [], "last_run": 0}


def save(st):
    st["seen"] = st["seen"][-5000:]
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def _field(summary, name):
    m = re.search(rf"<strong>{name}:</strong>\s*(.*?)(?:<br\s*/?>|$)", summary or "", re.S)
    return bot.norm(re.sub(r"<[^>]+>", " ", m.group(1))) if m else ""


def collect():
    jobs, ids = [], set()
    for q in QUERIES:
        for e in bot.fetch_feed(FEED + quote_plus(q)):
            link = (e.get("link") or "").split(";")[0]
            title = bot.norm(e.get("title", ""))
            jid = link.rstrip("/").split("/")[-1]
            if not jid or jid in ids or not KEEP.search(title) or SKIP.search(title):
                continue
            ids.add(jid)
            s = e.get("summary", "")
            jobs.append({"id": jid, "title": title, "link": link, "updated": e.get("updated", ""),
                         "location": _field(s, "Location"), "employer": _field(s, "Employer"),
                         "salary": _field(s, "Salary")})
    jobs += collect_adzuna(ids)
    jobs.sort(key=lambda j: j["updated"])  # oldest first
    return jobs


# Adzuna (optional): used only when the ADZUNA_APP_ID and ADZUNA_APP_KEY secrets exist. Its terms ask that each
# listing be labelled "Adzuna" with a link to Adzuna; format_job does that.
ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/ca/search/1"
ADZUNA_QUERIES = ["construction project manager", "project coordinator construction", "construction scheduler",
                  "construction estimator"]
PROVINCE_CODES = {v.lower(): k for k, v in {
    "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia", "AB": "Alberta", "MB": "Manitoba",
    "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island", "YT": "Yukon", "NT": "Northwest Territories", "NU": "Nunavut"}.items()}


def collect_adzuna(ids):
    app_id, key = os.environ.get("ADZUNA_APP_ID", "").strip(), os.environ.get("ADZUNA_APP_KEY", "").strip()
    if not app_id or not key:
        return []
    out = []
    for q in ADZUNA_QUERIES:
        try:
            r = bot.requests.get(ADZUNA_URL, params={"app_id": app_id, "app_key": key, "what": q, "max_days_old": 2,
                                                     "results_per_page": 50, "sort_by": "date",
                                                     "content-type": "application/json"}, timeout=bot.TIMEOUT)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as ex:
            bot.log(f"   adzuna {q} failed: {ex}")
            continue
        for a in results:
            title = bot.norm(re.sub(r"<[^>]+>", "", a.get("title", "")))
            jid = "az" + str(a.get("id", ""))
            if jid in ids or not KEEP.search(title) or SKIP.search(title):
                continue
            ids.add(jid)
            area = (a.get("location") or {}).get("area") or []
            prov = PROVINCE_CODES.get((area[1] if len(area) > 1 else "").lower(), "")
            city = area[-1] if len(area) > 2 else ""
            loc = f"{city} ({prov})" if city and prov else ((a.get("location") or {}).get("display_name") or "")
            sal = ""
            if a.get("salary_min") and str(a.get("salary_is_predicted", "0")) == "0":
                lo, hi = a["salary_min"], a.get("salary_max") or a["salary_min"]
                sal = f"${lo:,.0f} to ${hi:,.0f} annually" if lo != hi else f"${lo:,.0f} annually"
            out.append({"id": jid, "title": title, "link": a.get("redirect_url", ""), "updated": a.get("created", ""),
                        "location": loc, "employer": bot.norm((a.get("company") or {}).get("display_name", "")),
                        "salary": sal, "snippet": bot.norm(re.sub(r"<[^>]+>", " ", a.get("description", ""))),
                        "adzuna": True})
    bot.log(f"   adzuna: {len(out)} matching jobs")
    return out


def details(link):
    """Extra facts from the posting page (schema.org fields Job Bank puts on every posting)."""
    out = {}
    try:
        from bs4 import BeautifulSoup
        r = bot.requests.get(link, headers={"User-Agent": bot.UA}, timeout=bot.TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        def prop(name):
            el = soup.find(attrs={"property": name})
            return bot.norm(el.get_text(" ")) if el else ""
        out["type"] = prop("employmentType")
        out["hours"] = prop("workHours")
        out["education"] = prop("educationRequirements")
        out["experience"] = prop("experienceRequirements")
        out["until"] = prop("validThrough")
        resp = soup.find(attrs={"property": "responsibilities"})
        if resp:
            tasks = [bot.norm(li.get_text(" ")) for li in resp.find_all("li")]
            out["tasks"] = [t for t in tasks if t][:4]
        page = soup.get_text(" ")
        for kw, label in (("Hybrid", "Hybrid"), ("Remote work available", "Remote"), ("On site", "On site")):
            if kw in page:
                out["setting"] = label
                break
        m = re.search(r"View the full job posting on\s+([\w.\-]+)", page)
        if m:
            out["source"] = m.group(1)
    except Exception as ex:
        bot.log(f"   jobs: details failed {link}: {ex}")
    return out


def _tag(text):
    """'saint-jean-sur-richelieu' -> '#SaintJeanSurRichelieu' (letters and digits only, accents removed)."""
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    words = re.findall(r"[A-Za-z0-9]+", plain)
    return "#" + "".join(w[:1].upper() + w[1:].lower() for w in words) if words else ""


def _clean(s, label):
    return bot.norm(re.sub(rf"^{label}\s*", "", s or "", flags=re.I))


def _place(loc):
    return re.sub(r"\s*\((\w\w)\)", r", \1", loc or "").strip()


def annual_pay(salary, hours=""):
    """(low, high) yearly pay from Job Bank's salary text, e.g. '$55.56 hourly' -> (115565, 115565). None if unknown."""
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", salary or "")]
    if not nums:
        return None
    low, high = nums[0], nums[-1]
    s = (salary or "").lower()
    if "hour" in s:
        low, high = low * 2080, high * 2080
    elif "week" in s:
        low, high = low * 52, high * 52
    elif "month" in s:
        low, high = low * 12, high * 12
    return (low, high) if low >= 20000 else None


def is_hot(j):
    pay = annual_pay(j.get("salary", ""))
    return bool((pay and pay[1] >= 100000) or re.search(r"\b(senior|sr\.?|director|principal|lead|head of)\b",
                                                      j["title"], re.I))


def format_job(j, d):
    title = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), j["title"])  # Title Case
    hot = is_hot(j)
    lines = (["🔥 <b>HOT JOB</b>"] if hot else []) + [f"💼 <b>{html.escape(title)}</b>"]
    if j["employer"] and j["employer"].lower() not in ("not available", "n/a"):
        lines.append(f"🏢 {html.escape(j['employer'])}")
    locs = j.get("locations") or ([j["location"]] if j["location"] else [])
    if locs:
        lines.append(f"📍 {html.escape(' · '.join(_place(x) for x in locs))}")
    pay = j["salary"]
    if d.get("hours"):
        pay = f"{pay} · {d['hours']}" if pay else d["hours"]
    if pay:
        lines.append(f"💰 {html.escape(pay)}")
    terms = " · ".join(x for x in (_clean(d.get("type"), ""), d.get("setting", "")) if x)
    if terms:
        terms = re.sub(r"employment\s*(?=(Full|Part) time)", "employment, ", terms)
        lines.append(f"📄 {html.escape(terms)}")
    req = " · ".join(x for x in (_clean(d.get("education"), "qualification"),
                                   _clean(d.get("experience"), "qualification")) if x)
    if req:
        lines.append(f"🎓 {html.escape(req)}")
    if d.get("tasks"):
        lines.append("\n📝 <b>Main duties</b>")
        lines += [f"• {html.escape(t)}" for t in d["tasks"]]
    elif j.get("snippet"):
        lines.append("\n📝 " + html.escape(bot.shorten(j["snippet"], 350)))
    elif d.get("source"):
        lines.append(f"\n📝 Full description on {html.escape(d['source'])} (link below)")
    if d.get("until"):
        lines.append(f"\n⏳ Apply by: {html.escape(d['until'][:10])}")
    lines.append(f'🔗 <a href="{html.escape(j["link"], quote=True)}">View the job and apply</a>')
    if j.get("adzuna"):
        lines.append('🔎 Jobs by <a href="https://www.adzuna.ca">Adzuna</a>')
    tags = ["#CanadaJobs"] + (["#HotJob"] if hot else [])
    low = j["title"].lower()
    title_tag = _tag(re.split(r"[,(]", j["title"])[0])  # job title, e.g. #ConstructionProjectCoordinator
    if title_tag:
        tags.append(title_tag)
    tags.append(next((t for p, t in ROLE_TAGS if re.search(p, low)), "#ProjectManagement"))
    if "construction" in low:
        tags.append("#Construction")
    if d.get("setting") in ("Remote", "Hybrid"):
        tags.append("#" + d["setting"])
    for loc in locs[:4]:
        city = _tag(re.sub(r"\s*\(.*$", "", loc))  # city, e.g. #Ottawa
        if city and city != "#VariousLocations":
            tags.append(city)
    for loc in locs[:4]:
        m = re.search(r"\((\w\w)\)", loc)
        if m and m.group(1) in PROVINCES:
            tags.append("#" + PROVINCES[m.group(1)])
    lines.append("\n" + " ".join(dict.fromkeys(tags)) + (f"\n{CHANNEL}" if CHANNEL else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- Canadian construction project news
NEWS_FEEDS = [  # (source name, feed)
    ("ReNew Canada", "https://www.renewcanada.net/feed/"),
    ("On-Site Magazine", "https://www.on-sitemag.com/feed/"),
    ("Construction Canada", "https://www.constructioncanada.net/feed/"),
    ("Canadian Architect", "https://www.canadianarchitect.com/feed/"),
    ("", "https://www.bing.com/news/search?format=rss&setlang=en-CA&cc=CA&q=%22construction+project%22+Canada"),
    ("", "https://www.bing.com/news/search?format=rss&setlang=en-CA&cc=CA&q=Canada+infrastructure+project+contract+awarded"),
]
NEWS_MAX_PER_RUN = int(os.environ.get("NEWS_MAX_PER_RUN", "4"))
NEWS_MAX_AGE = 3 * 24 * 3600
CANADA = re.compile(r"canad|ontario|quebec|qu\u00e9bec|british columbia|\bb\.?c\.?\b|alberta|manitoba|saskatchewan|"
                    r"nova scotia|new brunswick|newfoundland|prince edward|yukon|nunavut|northwest territories|ottawa|"
                    r"toronto|montreal|montr\u00e9al|vancouver|calgary|edmonton|winnipeg|halifax|hamilton|mississauga|"
                    r"gatineau|kitchener|london, ont|saskatoon|regina|victoria|surrey|brampton|metrolinx|infrastructure ontario", re.I)
BUILD = re.compile(r"construct|build|project|infrastructure|contract|tender|transit|hospital|bridge|highway|housing|"
                   r"tower|condo|development|renovat|groundbreaking|ground broken|rfp|rfq|p3|reactor|pipeline|"
                   r"subway|lrt|rail|airport|school|plant", re.I)
NEWS_SKIP = re.compile(r"webinar|podcast|sponsored|advertorial|award nominations|subscribe|job posting|obituar", re.I)
NEWS_TAGS = [(r"transit|subway|lrt|rail|metrolinx", "#Transit"), (r"hospital|health", "#Healthcare"),
             (r"housing|condo|residential|tower|apartment", "#Housing"), (r"bridge|highway|road", "#Roads"),
             (r"nuclear|reactor|energy|hydro|power", "#Energy"), (r"school|universit|college", "#Education"),
             (r"infrastructure", "#Infrastructure")]


def collect_news():
    items, keys = [], set()
    now = time.time()
    for source, url in NEWS_FEEDS:
        for e in bot.fetch_feed(url):
            title = bot.norm(e.get("title", ""))
            pub = e.get("published_parsed") or e.get("updated_parsed")
            if not title or not pub or now - time.mktime(pub) > NEWS_MAX_AGE or NEWS_SKIP.search(title):
                continue
            summary = bot.norm(re.sub(r"<[^>]+>", " ", bot.clean_summary(e.get("summary", ""))))
            text = f"{title} {summary}"
            if not BUILD.search(text) or (not source and not CANADA.search(text)):
                continue  # trade magazines are Canadian; general news must mention Canada
            key = bot.title_key(title)
            if key in keys:
                continue
            keys.add(key)
            src = source or bot.norm((e.get("source") or {}).get("title", "")) or "News"
            items.append({"key": key, "title": title, "summary": summary, "link": e.get("link", ""),
                          "source": src, "time": time.mktime(pub)})
    items.sort(key=lambda n: n["time"])  # oldest first
    return items


def format_news(n):
    summary = n["summary"]
    if len(summary) < 120:  # thin feed summary: use the article's own description or first paragraph
        summary = bot.article_summary(n["link"]) or bot.article_paragraph(n["link"]) or summary
    summary = bot.shorten(summary, 500)
    tags = ["#ConstructionNews", "#CanadaConstruction"]
    low = f"{n['title']} {summary}".lower()
    tags += [t for p, t in NEWS_TAGS if re.search(p, low)][:2]
    lines = [f"🏗 <b>{html.escape(n['title'])}</b>"]
    if summary and summary[:60].lower() != n["title"][:60].lower():
        lines.append("\n" + html.escape(summary))
    lines.append(f'\n🔗 <a href="{html.escape(n["link"], quote=True)}">Read more</a> | {html.escape(n["source"])}')
    lines.append("\n" + " ".join(dict.fromkeys(tags)) + (f"\n{CHANNEL}" if CHANNEL else ""))
    return "\n".join(lines)


def run_news(st, token, check):
    items = collect_news()
    bot.log(f"news: {len(items)} Canadian construction stories in the last 3 days")
    if check:
        for n in items[-3:]:
            bot.log("\n=== news preview\n" + format_news(n))
        return
    seen = st.get("news_seen")
    if seen is None:  # first run: post only the newest few
        seen = [n["key"] for n in items[:-NEWS_MAX_PER_RUN]]
    new = [n for n in items if n["key"] not in set(seen)]
    posted = 0
    for n in new[-NEWS_MAX_PER_RUN:]:
        if bot.send(token, CHANNEL, format_news(n)):
            seen.append(n["key"])
            posted += 1
            time.sleep(3)
    seen += [n["key"] for n in new[:-NEWS_MAX_PER_RUN]]
    st["news_seen"] = seen[-2000:]
    bot.log(f"news posted: {posted}")


# ---------------------------------------------------------------- end-of-day list
def _send_get_id(token, text, preview=True):
    """Send and return the Telegram message id (needed to link to the post), or None."""
    r = bot.requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": CHANNEL, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": not preview}, timeout=bot.TIMEOUT)
    if r.status_code == 429:
        time.sleep(r.json().get("parameters", {}).get("retry_after", 30))
        return _send_get_id(token, text, preview)
    if not r.ok:
        bot.log(f"   telegram error {r.status_code}: {r.text[:200]}")
        return None
    return r.json()["result"]["message_id"]


def _remember(st, j, msg_id):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    day = st.setdefault("today", {"date": today, "jobs": []})
    if day["date"] != today:
        st["today"] = day = {"date": today, "jobs": []}
    city = re.sub(r"\s*\((\w\w)\)", r", \1", j["location"]).strip() or "Various locations"
    title = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), j["title"])
    day["jobs"].append({"title": title, "city": city, "employer": j["employer"], "msg": msg_id})


def daily_list(st, token):
    """At SUMMARY_TIME, post today's jobs as a simple list grouped by city, each linking to its post."""
    now = datetime.now(TZ)
    h, m = map(int, SUMMARY_TIME.split(":"))
    today = now.strftime("%Y-%m-%d")
    if (now.hour, now.minute) < (h, m) or st.get("summary_day") == today:
        return
    day = st.get("today") or {}
    jobs = day.get("jobs", []) if day.get("date") == today else []
    st["summary_day"] = today
    if not jobs:
        return
    handle = CHANNEL.lstrip("@")
    by_city = {}
    for j in jobs:
        by_city.setdefault(j["city"], []).append(j)
    head = (f"📋 <b>Today's jobs: {now:%B} {now.day}, {now.year}</b>\n"
            f"{len(jobs)} new job{'s' if len(jobs) != 1 else ''} posted today, by city. Tap a job to open its post.")
    blocks = []
    for city in sorted(by_city, key=lambda c: (c.split(", ")[-1], c)):  # by province, then city
        lines = [f"📍 <b>{html.escape(city)}</b>"]
        for j in by_city[city]:
            emp = j.get("employer") or ""
            emp = f" · {html.escape(emp)}" if emp and emp.lower() not in ("not available", "n/a") else ""
            lines.append(f'• <a href="https://t.me/{handle}/{j["msg"]}">{html.escape(j["title"])}</a>{emp}')
        blocks.append("\n".join(lines))
    foot = f"#DailyJobList #CanadaJobs\n{CHANNEL}"
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + len(foot) > 3900:
            msgs.append(cur)
            cur = "📋 <b>Today's jobs (continued)</b>"
        cur += "\n\n" + b
    msgs.append(cur + "\n\n" + foot)
    for t in msgs:
        _send_get_id(token, t, preview=False)
        time.sleep(2)
    bot.log(f"daily job list posted: {len(jobs)} jobs, {len(msgs)} message(s)")


# ---------------------------------------------------------------- weekly salary snapshot (Sundays)
SALARY_DAY, SALARY_TIME = 6, os.environ.get("JOBS_SALARY_TIME", "18:00")  # Sunday 6 PM Ottawa time
ROLE_NAMES = [(r"schedul|planner", "Scheduler / Planner"), (r"coordinator", "Project Coordinator"),
              (r"superintendent|site supervisor", "Superintendent"), (r"estimat|quantity surveyor", "Estimator / QS"),
              (r"contract", "Contract Administrator"), (r"project control|cost", "Project Controls"),
              (r"engineer", "Project / Site Engineer"), (r"construction manager", "Construction Manager"),
              (r"director|program manager|portfolio", "Director / Program Manager"),
              (r"manager", "Project Manager")]


def _record_pay(st, j):
    pay = annual_pay(j.get("salary", ""))
    m = re.search(r"\((\w\w)\)", j.get("location", ""))
    if not pay or not m:
        return
    role = next((n for p, n in ROLE_NAMES if re.search(p, j["title"], re.I)), None)
    if role:
        st.setdefault("pay", []).append({"t": time.time(), "role": role, "prov": m.group(1),
                                         "low": round(pay[0]), "high": round(pay[1])})


def salary_snapshot(st, token):
    now = datetime.now(TZ)
    h, m = map(int, SALARY_TIME.split(":"))
    week = now.strftime("%G-W%V")
    if now.weekday() != SALARY_DAY or (now.hour, now.minute) < (h, m) or st.get("salary_week") == week:
        return
    st["salary_week"] = week
    rows = [p for p in st.get("pay", []) if time.time() - p["t"] < 7 * 86400]
    st["pay"] = rows
    groups = {}
    for p in rows:
        groups.setdefault((p["role"], p["prov"]), []).append(p)
    groups = {k: v for k, v in groups.items() if len(v) >= 2}
    if not groups:
        return
    def k(x):
        return f"${x / 1000:.0f}K"
    lines = [f"💰 <b>Weekly salary snapshot</b>",
             f"Posted pay in this week's jobs ({len(rows)} jobs with a salary), yearly, by role and province.\n"]
    for role in [n for _, n in ROLE_NAMES]:
        rs = sorted((pv, v) for (r, pv), v in groups.items() if r == role)
        if not rs:
            continue
        lines.append(f"<b>{role}</b>")
        for pv, v in rs:
            lo = sum(p["low"] for p in v) / len(v)
            hi = sum(p["high"] for p in v) / len(v)
            rng = k(lo) if abs(hi - lo) < 1000 else f"{k(lo)} to {k(hi)}"
            lines.append(f"• {PROVINCES.get(pv, pv)}: {rng} <i>({len(v)} jobs)</i>")
        lines.append("")
    lines.append("<i>Averages of the pay employers listed on Job Bank this week; hourly pay counted as 2,080 hours a year. "
                 "A guide, not an official survey.</i>")
    lines.append(f"\n#SalarySnapshot #CanadaJobs\n{CHANNEL}")
    _send_get_id(token, "\n".join(lines)[:4000], preview=False)
    bot.log(f"salary snapshot posted: {len(groups)} groups")


def main():
    check = "--check" in sys.argv
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    st = load()
    if not check and CHANNEL:
        before = (st.get("summary_day"), st.get("salary_week"))
        try:
            daily_list(st, token)  # checked on every call, so it goes out right at SUMMARY_TIME
            salary_snapshot(st, token)
        except Exception as ex:
            bot.log(f"daily job list / salary snapshot failed: {ex}")
        if (st.get("summary_day"), st.get("salary_week")) != before:
            save(st)
    if not check and time.time() - st.get("last_run", 0) < EVERY:
        return
    jobs = collect()
    bot.log(f"jobs: {len(jobs)} matching jobs in the last 2 days")
    if check:
        for j in jobs[-3:]:
            bot.log("\n=== job preview\n" + format_job(j, details(j["link"])))
        run_news(st, token, True)
        return
    if not CHANNEL:
        bot.log("jobs: JOBS_CHANNEL not set, nothing posted")
        return
    st["last_run"] = time.time()
    seen = set(st["seen"])
    new = [j for j in jobs if j["id"] not in seen]
    # the same employer + title in several cities -> one post listing all the cities;
    # and the same employer + title already posted in the last 3 days -> not posted again
    pairs = {k: t for k, t in st.get("pairs", {}).items() if time.time() - t < 3 * 86400}
    groups, order = {}, []
    for j in new:
        key = f"{j['employer'].lower()}|{j['title'].lower()}" if j["employer"] else j["id"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(j)
    posted = 0
    batch = order[-MAX_PER_RUN:]  # newest MAX_PER_RUN posts, oldest first
    for key in order:
        g = groups[key]
        if key in batch and key not in pairs:
            first = dict(g[-1])
            first["locations"] = list(dict.fromkeys(x["location"] for x in g if x["location"]))
            d = {} if first.get("adzuna") else details(first["link"])
            msg_id = _send_get_id(token, format_job(first, d))
            if not msg_id:
                continue
            pairs[key] = time.time()
            for j in g:
                _remember(st, j, msg_id)
            _record_pay(st, first)
            posted += 1
            time.sleep(3)
        st["seen"] += [j["id"] for j in g]  # posted, a repeat, or older backlog beyond the cap
    st["pairs"] = pairs
    bot.log(f"jobs posted: {posted}")
    try:
        run_news(st, token, False)
    except Exception as ex:
        bot.log(f"news failed: {ex}")
    save(st)


if __name__ == "__main__":
    main()
