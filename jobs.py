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
from pathlib import Path
from urllib.parse import quote_plus

import bot

CHANNEL = os.environ.get("JOBS_CHANNEL", "")
STATE = Path(os.environ.get("JOBS_STATE", "state-jobs.json"))
EVERY = int(os.environ.get("JOBS_EVERY", "3600"))
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
    jobs.sort(key=lambda j: j["updated"])  # oldest first
    return jobs


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


def format_job(j, d):
    title = re.sub(r"\b([a-z])", lambda m: m.group(1).upper(), j["title"])  # Title Case
    lines = [f"💼 <b>{html.escape(title)}</b>"]
    if j["employer"]:
        lines.append(f"🏢 {html.escape(j['employer'])}")
    if j["location"]:
        lines.append(f"📍 {html.escape(re.sub(r"\s*\((\w\w)\)", r", \1", j['location']).strip())}")
    pay = j["salary"]
    if d.get("hours"):
        pay = f"{pay} · {d['hours']}" if pay else d["hours"]
    if pay:
        lines.append(f"💰 {html.escape(pay)}")
    terms = " · ".join(x for x in (_clean(d.get("type"), ""), d.get("setting", "")) if x)
    if terms:
        terms = re.sub(r"employment(?=\S)", "employment, ", terms).replace("employmentFull", "employment, Full")
        lines.append(f"📄 {html.escape(terms)}")
    req = " · ".join(x for x in (_clean(d.get("education"), "qualification"),
                                   _clean(d.get("experience"), "qualification")) if x)
    if req:
        lines.append(f"🎓 {html.escape(req)}")
    if d.get("tasks"):
        lines.append("\n📝 <b>Main duties</b>")
        lines += [f"• {html.escape(t)}" for t in d["tasks"]]
    elif d.get("source"):
        lines.append(f"\n📝 Full description on {html.escape(d['source'])} (link below)")
    if d.get("until"):
        lines.append(f"\n⏳ Apply by: {html.escape(d['until'][:10])}")
    lines.append(f'🔗 <a href="{html.escape(j["link"], quote=True)}">View the job and apply</a>')
    tags = ["#CanadaJobs"]
    low = j["title"].lower()
    title_tag = _tag(re.split(r"[,(]", j["title"])[0])  # job title, e.g. #ConstructionProjectCoordinator
    if title_tag:
        tags.append(title_tag)
    tags.append(next((t for p, t in ROLE_TAGS if re.search(p, low)), "#ProjectManagement"))
    if "construction" in low:
        tags.append("#Construction")
    m = re.search(r"\((\w\w)\)", j["location"])
    city = _tag(re.sub(r"\s*\(.*$", "", j["location"]))  # city, e.g. #Ottawa
    if city and city != "#VariousLocations":
        tags.append(city)
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


def main():
    check = "--check" in sys.argv
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    st = load()
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
    posted = 0
    for j in new[-MAX_PER_RUN:]:  # newest MAX_PER_RUN, posted oldest first
        if bot.send(token, CHANNEL, format_job(j, details(j["link"]))):
            st["seen"].append(j["id"])
            posted += 1
            time.sleep(3)
    for j in new[:-MAX_PER_RUN]:  # older backlog beyond the cap: skip, don't flood the channel
        st["seen"].append(j["id"])
    bot.log(f"jobs posted: {posted}")
    try:
        run_news(st, token, False)
    except Exception as ex:
        bot.log(f"news failed: {ex}")
    save(st)


if __name__ == "__main__":
    main()
