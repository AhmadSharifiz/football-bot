"""
Daily football fixtures (morning) and results (after the last European games), in Persian, Tehran time.
Data: ESPN's public scoreboard (no key needed). Grouped by country and competition.

Called from bot.py when the sources file has `daily_fixtures: true`.
  FIXTURES_TIME (default 08:00 Tehran)  -> today's list
  RESULTS_TIME  (default 02:00 Tehran)  -> results of the previous Tehran day
"""
import html
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import bot

TEHRAN = ZoneInfo("Asia/Tehran")
FIXTURES_TIME = os.environ.get("FIXTURES_TIME", "08:00")
RESULTS_TIME = os.environ.get("RESULTS_TIME", "02:00")
API = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard"
LIMIT = 3900

# (flag + country, ESPN league code, competition name), in the order they appear in the post
LEAGUES = [
    ("🌏 آسیا", "afc.champions", "لیگ نخبگان آسیا"),
    ("🌏 آسیا", "afc.cup", "لیگ قهرمانان آسیا ۲"),
    ("🇪🇺 اروپا", "uefa.champions", "لیگ قهرمانان اروپا"),
    ("🇪🇺 اروپا", "uefa.europa", "لیگ اروپا"),
    ("🇪🇺 اروپا", "uefa.europa.conf", "لیگ کنفرانس"),
    ("🇪🇺 اروپا", "uefa.nations", "لیگ ملت‌ها"),
    ("🌍 ملی", "fifa.world", "جام جهانی"),
    ("🌍 ملی", "fifa.worldq.afc", "مقدماتی جام جهانی (آسیا)"),
    ("🌍 ملی", "fifa.worldq.uefa", "مقدماتی جام جهانی (اروپا)"),
    ("🌍 ملی", "afc.asian.cup", "جام ملت‌های آسیا"),
    ("🌍 ملی", "uefa.euro", "یورو"),
    ("🌍 ملی", "fifa.friendly", "دوستانه ملی"),
    ("🏴󠁧󠁢󠁥󠁮󠁧󠁿 انگلیس", "eng.1", "لیگ برتر"),
    ("🏴󠁧󠁢󠁥󠁮󠁧󠁿 انگلیس", "eng.2", "چمپیونشیپ"),
    ("🏴󠁧󠁢󠁥󠁮󠁧󠁿 انگلیس", "eng.fa", "جام حذفی"),
    ("🏴󠁧󠁢󠁥󠁮󠁧󠁿 انگلیس", "eng.league_cup", "جام اتحادیه"),
    ("🇪🇸 اسپانیا", "esp.1", "لالیگا"),
    ("🇪🇸 اسپانیا", "esp.copa_del_rey", "کوپا دل ری"),
    ("🇮🇹 ایتالیا", "ita.1", "سری آ"),
    ("🇮🇹 ایتالیا", "ita.coppa_italia", "کوپا ایتالیا"),
    ("🇩🇪 آلمان", "ger.1", "بوندسلیگا"),
    ("🇩🇪 آلمان", "ger.dfb_pokal", "جام حذفی"),
    ("🇫🇷 فرانسه", "fra.1", "لوشامپیونه"),
    ("🇫🇷 فرانسه", "fra.coupe_de_france", "جام حذفی"),
    ("🇳🇱 هلند", "ned.1", "اردیویسی"),
    ("🇵🇹 پرتغال", "por.1", "لیگ برتر"),
    ("🇹🇷 ترکیه", "tur.1", "سوپرلیگ"),
    ("🇸🇦 عربستان", "ksa.1", "لیگ حرفه‌ای"),
    ("🇺🇸 آمریکا", "usa.1", "MLS"),
    ("🇧🇷 برزیل", "bra.1", "سری آ"),
    ("🇦🇷 آرژانتین", "arg.1", "لیگ حرفه‌ای"),
]

# Iranian leagues are not on ESPN: TheSportsDB (free test key) has them
SPORTSDB = "https://www.thesportsdb.com/api/v1/json/123/eventsday.php"
SPORTSDB_LEAGUES = [
    ("🇮🇷 ایران", 4742, "لیگ برتر خلیج فارس"),
    ("🇮🇷 ایران", 4741, "لیگ آزادگان"),
]
ORDER = [(c, comp) for c, _, comp in SPORTSDB_LEAGUES] + [(c, comp) for c, _, comp in LEAGUES]

# common Persian spellings for well-known teams (others are machine-translated)
TEAM_FA = {
    "Manchester United": "منچستریونایتد", "Manchester City": "منچسترسیتی", "Liverpool": "لیورپول",
    "Arsenal": "آرسنال", "Chelsea": "چلسی", "Tottenham Hotspur": "تاتنهام", "Newcastle United": "نیوکاسل",
    "Aston Villa": "استون‌ویلا", "West Ham United": "وستهام", "Everton": "اورتون", "Brighton & Hove Albion": "برایتون",
    "Real Madrid": "رئال مادرید", "Barcelona": "بارسلونا", "Atlético Madrid": "اتلتیکو مادرید",
    "Athletic Club": "اتلتیک بیلبائو", "Real Sociedad": "رئال سوسیداد", "Sevilla": "سویا", "Villarreal": "ویارئال",
    "Real Betis": "رئال بتیس", "Valencia": "والنسیا",
    "Bayern Munich": "بایرن مونیخ", "Borussia Dortmund": "دورتموند", "Bayer Leverkusen": "بایرلورکوزن",
    "RB Leipzig": "لایپزیگ", "Eintracht Frankfurt": "آینتراخت فرانکفورت",
    "Juventus": "یوونتوس", "Internazionale": "اینتر", "Inter Milan": "اینتر", "AC Milan": "میلان",
    "Napoli": "ناپولی", "AS Roma": "رم", "Lazio": "لاتزیو", "Atalanta": "آتالانتا",
    "Paris Saint-Germain": "پاری‌سن‌ژرمن", "Marseille": "مارسی", "Monaco": "موناکو", "Lyon": "لیون",
    "Ajax": "آژاکس", "PSV Eindhoven": "آیندهوون", "Feyenoord": "فاینورد",
    "Benfica": "بنفیکا", "FC Porto": "پورتو", "Sporting CP": "اسپورتینگ لیسبون",
    "Galatasaray": "گالاتاسرای", "Fenerbahce": "فنرباغچه", "Besiktas": "بشیکتاش",
    "Al Hilal": "الهلال", "Al Nassr": "النصر", "Al Ittihad": "الاتحاد", "Al Ahli": "الاهلی",
    "Persepolis": "پرسپولیس", "Esteghlal": "استقلال", "Sepahan": "سپاهان", "Tractor": "تراکتور",
    "Gol Gohar Sirjan": "گل‌گهر سیرجان", "Zob Ahan": "ذوب‌آهن", "Paykan": "پیکان", "Foolad": "فولاد",
    "Nassaji Mazandaran": "نساجی مازندران", "Esteghlal Khuzestan": "استقلال خوزستان", "Malavan": "ملوان",
    "Kheybar Khorramabad": "خیبر خرم‌آباد", "Aluminium Arak": "آلومینیوم اراک", "Chadormalu": "چادرملو",
    "Chadormalou": "چادرملو", "Shams Azar": "شمس آذر", "Havadar": "هوادار", "Mes Rafsanjan": "مس رفسنجان",
    "Fajr Sepasi": "فجر سپاسی", "Sepahan Isfahan": "سپاهان", "Persepolis FC": "پرسپولیس", "Tractor FC": "تراکتور",
    "Iran": "ایران", "England": "انگلیس", "Spain": "اسپانیا", "Germany": "آلمان", "France": "فرانسه",
    "Italy": "ایتالیا", "Portugal": "پرتغال", "Brazil": "برزیل", "Argentina": "آرژانتین",
    "Netherlands": "هلند", "Belgium": "بلژیک", "Japan": "ژاپن", "South Korea": "کره جنوبی",
    "Saudi Arabia": "عربستان", "Qatar": "قطر", "United States": "آمریکا", "Mexico": "مکزیک",
}

WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
RLM = "‏"


def fa(x):
    return str(x).translate(FA)


def jalali(d):
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm = 1 + days // 31 if days < 186 else 7 + (days - 186) // 30
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    return jy, jm, jd


def date_fa(d):
    jy, jm, jd = jalali(d)
    return f"{WEEKDAYS[d.weekday()]} {fa(jd)} {MONTHS[jm - 1]} {fa(jy)}"


def _espn(code, ymd):
    try:
        r = bot.requests.get(API.format(code), params={"dates": ymd},
                             headers={"User-Agent": bot.UA}, timeout=bot.TIMEOUT)
        return r.json().get("events", []) if r.status_code == 200 else []
    except Exception as ex:
        bot.log(f"   fixtures {code} failed: {ex}")
        return []


def _sportsdb(league, ymd):
    try:
        r = bot.requests.get(SPORTSDB, params={"d": ymd, "l": league},
                             headers={"User-Agent": bot.UA}, timeout=bot.TIMEOUT)
        return (r.json() or {}).get("events") or [] if r.status_code == 200 else []
    except Exception as ex:
        bot.log(f"   fixtures sportsdb {league} failed: {ex}")
        return []


def fetch_day(day):
    """All matches whose kickoff falls on `day` (a Tehran calendar date)."""
    days = [day - timedelta(days=1), day, day + timedelta(days=1)]  # covers every timezone the APIs use
    out = []
    for country, code, comp in LEAGUES:
        for d in days:
            for ev in _espn(code, f"{d:%Y%m%d}"):
                try:
                    ko = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(TEHRAN)
                    teams = {c["homeAway"]: c for c in ev["competitions"][0]["competitors"]}
                    st = ev.get("status", {}).get("type", {})
                    out.append({
                        "id": "e" + ev["id"], "country": country, "comp": comp, "ko": ko,
                        "home": teams["home"]["team"].get("displayName", ""),
                        "away": teams["away"]["team"].get("displayName", ""),
                        "hs": teams["home"].get("score"), "as": teams["away"].get("score"),
                        "state": st.get("state", ""), "done": st.get("completed", False),
                        "detail": st.get("shortDetail") or st.get("detail") or "",
                    })
                except Exception:
                    continue
    for country, league, comp in SPORTSDB_LEAGUES:
        for d in days[:2]:
            for ev in _sportsdb(league, f"{d:%Y-%m-%d}"):
                try:
                    ts = ev.get("strTimestamp") or f"{ev['dateEvent']}T{ev.get('strTime') or '00:00:00'}"
                    ko = datetime.fromisoformat(ts.replace("Z", "")[:19]).replace(tzinfo=timezone.utc).astimezone(TEHRAN)
                    status = (ev.get("strStatus") or "").strip()
                    done = status in ("FT", "AET", "PEN", "Match Finished") and ev.get("intHomeScore") is not None
                    out.append({
                        "id": "s" + str(ev["idEvent"]), "country": country, "comp": comp, "ko": ko,
                        "home": ev.get("strHomeTeam", ""), "away": ev.get("strAwayTeam", ""),
                        "hs": ev.get("intHomeScore"), "as": ev.get("intAwayScore"),
                        "state": "post" if done else ("in" if status in ("1H", "2H", "HT", "LIVE") else "pre"),
                        "done": done, "detail": status,
                    })
                except Exception:
                    continue
    seen, uniq = set(), []
    for m in sorted(out, key=lambda m: m["ko"]):
        if m["id"] not in seen and m["ko"].date() == day and m["home"] and m["away"]:
            seen.add(m["id"])
            uniq.append(m)
    return uniq


def persian_names(names, cache):
    """Team names in Persian: known list, then cache, then one batched translation."""
    todo = [n for n in names if n not in TEAM_FA and n not in cache]
    if todo:
        out = bot.to_persian("\n".join(todo)) if bot.TRANSLATE else None
        lines = [l.strip() for l in (out or "").split("\n")]
        if len(lines) == len(todo):
            for n, f in zip(todo, lines):
                if f:
                    cache[n] = f
    return {n: TEAM_FA.get(n) or cache.get(n) or n for n in names}


def _sections(matches, names, results):
    order = {k: i for i, k in enumerate(ORDER)}
    groups = {}
    for m in matches:
        groups.setdefault((m["country"], m["comp"]), []).append(m)
    blocks = []
    for key in sorted(groups, key=lambda k: order.get(k, 999)):
        country, comp = key
        lines = [f"{RLM}<b>{country} | {comp}</b>"]
        for m in groups[key]:
            h, a = html.escape(names[m["home"]]), html.escape(names[m["away"]])
            if results:
                if m["done"] and m["hs"] is not None:
                    lines.append(f"{RLM}{h}  <b>{fa(m['hs'])} - {fa(m['as'])}</b>  {a}")
                elif "postpon" in m["detail"].lower() or "cancel" in m["detail"].lower():
                    lines.append(f"{RLM}{h} - {a}  ⛔️ لغو / به تعویق افتاد")
                elif m["state"] == "in":
                    lines.append(f"{RLM}{h}  {fa(m['hs'])} - {fa(m['as'])}  {a}  🔴 در جریان")
                else:
                    lines.append(f"{RLM}{h} - {a}  ⏳ هنوز برگزار نشده")
            else:
                lines.append(f"{RLM}🕒 {fa(m['ko'].strftime('%H:%M'))}  {h} 🆚 {a}")
        blocks.append("\n".join(lines))
    return blocks


def build(day, results, cache):
    matches = fetch_day(day)
    if not matches:
        return []
    names = persian_names(sorted({m["home"] for m in matches} | {m["away"] for m in matches}), cache)
    if results:
        head = f"{RLM}✅ <b>نتایج بازی‌های {date_fa(day)}</b>\n{RLM}⏰ ساعت‌ها به وقت تهران"
        tags = "#نتایج_بازی‌ها #فوتبال"
    else:
        head = f"{RLM}📅 <b>برنامه بازی‌های امروز، {date_fa(day)}</b>\n{RLM}⏰ همه ساعت‌ها به وقت تهران"
        tags = "#برنامه_بازی‌ها #فوتبال"
    foot = f"{tags.replace(chr(0x200c), '_')}\n{bot.CHANNEL}"
    msgs, cur = [], head
    for b in _sections(matches, names, results):
        if len(cur) + len(b) + len(foot) > LIMIT:
            msgs.append(cur)
            cur = head.split("\n")[0] + " (ادامه)"
        cur += "\n\n" + b
    msgs.append(cur + "\n\n" + foot)
    return msgs


def _due(now, hhmm):
    h, m = map(int, hhmm.split(":"))
    return (now.hour, now.minute) >= (h, m)


def _pin(token, channel, state, msg_id):
    """Pin the newest list quietly and unpin the previous one, so only the latest fixtures/results stay pinned."""
    api = f"https://api.telegram.org/bot{token}"
    p = bot.requests.post(f"{api}/pinChatMessage", json={"chat_id": channel, "message_id": msg_id,
                                                          "disable_notification": True}, timeout=bot.TIMEOUT)
    if not p.ok:
        bot.log(f"   pin failed: {p.text[:200]}")
        return
    old = state.get("fixtures_pin")
    if old and old != msg_id:
        bot.requests.post(f"{api}/unpinChatMessage", json={"chat_id": channel, "message_id": old}, timeout=bot.TIMEOUT)
    state["fixtures_pin"] = msg_id


def _send_all(token, channel, msgs, state=None):
    first = None
    for i, t in enumerate(msgs):
        r = bot.requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": channel, "text": t, "parse_mode": "HTML",
                                    "disable_web_page_preview": True}, timeout=bot.TIMEOUT)
        if not r.ok:
            bot.log(f"   telegram error {r.status_code}: {r.text[:200]}")
            break
        if first is None:
            first = r.json()["result"]["message_id"]
        time.sleep(2)
    if first is not None and state is not None:
        _pin(token, channel, state, first)
    return first is not None  # anything out: don't repeat it


def run(state, token, channel):
    now = datetime.now(TEHRAN)
    today = now.date()
    cache = state.setdefault("team_fa", {})
    if "results_day" not in state:  # first run: start from the next scheduled post, no catch-up
        state["results_day"] = str(today - timedelta(days=1)) if _due(now, RESULTS_TIME) else ""
        state["fixtures_day"] = str(today) if _due(now, FIXTURES_TIME) else ""
        return
    # results for yesterday's games, after the last European matches have finished
    if _due(now, RESULTS_TIME) and state.get("results_day") != str(today - timedelta(days=1)):
        msgs = build(today - timedelta(days=1), True, cache)
        if not msgs or _send_all(token, channel, msgs, state):
            state["results_day"] = str(today - timedelta(days=1))
            bot.log(f"results posted: {len(msgs)} message(s)")
    if _due(now, FIXTURES_TIME) and state.get("fixtures_day") != str(today):
        msgs = build(today, False, cache)
        if not msgs or _send_all(token, channel, msgs, state):
            state["fixtures_day"] = str(today)
            bot.log(f"fixtures posted: {len(msgs)} message(s)")


def preview():
    """--check: print today's fixtures and yesterday's results without posting."""
    today = datetime.now(TEHRAN).date()
    cache = {}
    for day, res in ((today, False), (today - timedelta(days=1), True)):
        msgs = build(day, res, cache)
        bot.log(f"\n=== {'results' if res else 'fixtures'} {day}: {len(msgs)} message(s)")
        for t in msgs:
            bot.log(t)
