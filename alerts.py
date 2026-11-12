"""
Instant alerts, checked on every loop (every ~2 minutes), outside the normal news queue.

1. Express Entry draw alert -> immigration channel (sources file: `ee_alert: {url: ...}`).
   Posts each new round of invitations within minutes of IRCC publishing it, with a chart of the
   last 10 minimum CRS scores. The data file is downloaded only when it has changed (ETag / Last-Modified).

2. Ottawa urgent alerts -> Ottawa channel (sources file: `ottawa_alerts: true`).
   - Environment Canada weather warnings/watches for Ottawa
   - City of Ottawa winter parking bans, boil-water advisories and states of emergency
   - School bus cancellations (OSTA)
   The City and OSTA websites block GitHub's servers, so those two come from Bing News headlines.

The first run only records what is already out, so nothing old is posted.
"""
import hashlib
import html
import io
import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import bot

TZ = ZoneInfo("America/Toronto")
RLM = "‏"
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
MONTHS_FA = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]
EE_PAGE = ("https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/"
           "express-entry/rounds-invitations.html")


def fa(x):
    return str(x).translate(FA)


def _api(token, method, **kw):
    r = bot.requests.post(f"https://api.telegram.org/bot{token}/{method}", timeout=bot.TIMEOUT, **kw)
    if not r.ok:
        bot.log(f"   telegram {method} error {r.status_code}: {r.text[:200]}")
    return r


def _key(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- Express Entry

EE_PROGRAMS = [  # (words in IRCC's round name, Persian name)
    ("canadian experience", "تجربه کاری کانادا (CEC)"),
    ("provincial nominee", "برنامه استانی (PNP)"),
    ("federal skilled worker", "کارگران ماهر فدرال (FSW)"),
    ("federal skilled trades", "مشاغل فنی فدرال (FST)"),
    ("french", "مهارت زبان فرانسوی"),
    ("healthcare", "مشاغل بهداشت و خدمات اجتماعی"),
    ("health", "مشاغل بهداشت و درمان"),
    ("stem", "مشاغل STEM (علوم، فناوری، مهندسی، ریاضی)"),
    ("trade", "مشاغل فنی و حرفه‌ای"),
    ("transport", "مشاغل حمل‌ونقل"),
    ("agricultur", "مشاغل کشاورزی و صنایع غذایی"),
    ("education", "مشاغل آموزش"),
    ("research", "پژوهشگران"),
    ("senior manager", "مدیران ارشد"),
    ("physician", "پزشکان"),
    ("no program", "عمومی (همه برنامه‌ها)"),
    ("general", "عمومی (همه برنامه‌ها)"),
]


def _program_fa(name):
    low = (name or "").lower()
    for words, fa_name in EE_PROGRAMS:
        if words in low:
            return fa_name
    return "عمومی (همه برنامه‌ها)" if not low else (bot.to_persian(name) or name)


def _num(s):
    try:
        return int(str(s).replace(",", "").strip())
    except ValueError:
        return None


SHORT = [("canadian experience", "CEC"), ("provincial nominee", "PNP"), ("federal skilled worker", "FSW"),
         ("federal skilled trades", "FST"), ("french", "French"), ("health", "Healthcare"), ("stem", "STEM"),
         ("trade", "Trades"), ("transport", "Transport"), ("agricultur", "Agriculture"), ("education", "Education"),
         ("research", "Researchers"), ("senior manager", "Senior managers"), ("physician", "Physicians")]


def _short(name):
    low = (name or "").lower()
    return next((s for w, s in SHORT if w in low), "General" if not low or "no program" in low else name[:14])


def _ee_chart(rounds):
    """PNG of the last 10 draws' minimum CRS score (oldest left). None if matplotlib is missing."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    last = [d for d in rounds[:10] if _num(d.get("drawCRS"))][::-1]
    if not last:
        return None
    crs = [_num(d["drawCRS"]) for d in last]
    labels = [f"#{d.get('drawNumber')}\n{d.get('drawDate', '')[5:]}" for d in last]
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=130)
    colors = ["#c8102e" if i == len(last) - 1 else "#9aa5b1" for i in range(len(last))]
    bars = ax.bar(range(len(last)), crs, color=colors, width=0.62)
    for b, v, d in zip(bars, crs, last):
        ax.text(b.get_x() + b.get_width() / 2, v + 4, str(v), ha="center", va="bottom", fontsize=10, fontweight="bold")
        ax.text(b.get_x() + b.get_width() / 2, min(crs) * 0.5, _short(d.get("drawName")), ha="center", va="center",
                rotation=90, fontsize=7.5, color="white")
    ax.set_xticks(range(len(last)), labels, fontsize=8)
    ax.set_ylim(0, max(crs) * 1.15)
    ax.set_ylabel("Minimum CRS score")
    ax.set_title("Express Entry - last 10 draws (minimum CRS)", fontsize=12, fontweight="bold")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.text(0.99, 0.01, "@canadaimmigration_fa  |  source: IRCC", ha="right", fontsize=8, color="#666")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _ee_text(d, rounds, channel):
    name = (d.get("drawName") or "").strip()
    crs, size = _num(d.get("drawCRS")), _num(d.get("drawSize"))
    try:
        dt = datetime.strptime(d.get("drawDate", "")[:10], "%Y-%m-%d")
        date_fa = f"{fa(dt.day)} {MONTHS_FA[dt.month - 1]} {fa(dt.year)}"
    except ValueError:
        date_fa = d.get("drawDateFull", "")
    lines = [f"{RLM}🚨 <b>قرعه‌کشی جدید اکسپرس انتری (Express Entry)</b>", ""]
    lines.append(f"{RLM}🔢 دور شماره {fa(d.get('drawNumber'))} | 📅 {date_fa}")
    lines.append(f"{RLM}🎯 برنامه: <b>{html.escape(_program_fa(name))}</b>")
    if name:
        lines.append(f"<i>{html.escape(name)}</i>")
    lines.append(f"{RLM}✉️ تعداد دعوت‌نامه (ITA): <b>{fa(f'{size:,}' if size else d.get('drawSize'))}</b>")
    lines.append(f"{RLM}📊 حداقل امتیاز CRS: <b>{fa(crs if crs else d.get('drawCRS'))}</b>")
    # same program's previous draw
    prev = next((r for r in rounds[1:] if (r.get("drawName") or "").strip() == name), None)
    if prev and crs and _num(prev.get("drawCRS")):
        diff = crs - _num(prev["drawCRS"])
        arrow = "🔻" if diff < 0 else ("🔺" if diff > 0 else "➖")
        word = "کاهش" if diff < 0 else ("افزایش" if diff > 0 else "بدون تغییر")
        lines.append(f"{RLM}{arrow} نسبت به قرعه‌کشی قبلی همین برنامه (CRS {fa(prev['drawCRS'])}): "
                     f"{word}{'' if not diff else ' ' + fa(abs(diff)) + ' امتیاز'}")
    if d.get("drawCutOff"):
        lines.append(f"{RLM}⏱ تاریخ و زمان قانون تساوی (tie-breaking): <i>{html.escape(d['drawCutOff'])}</i>")
    lines.append("")
    lines.append(f"{RLM}📈 نمودار: حداقل امتیاز CRS در ۱۰ قرعه‌کشی اخیر")
    lines.append(f'{RLM}🔗 <a href="{EE_PAGE}">منبع رسمی: IRCC</a>')
    lines.append("")
    lines.append(f"#اکسپرس_انتری #قرعه_کشی #مهاجرت_کانادا\n{channel}")
    return "\n".join(lines)


def ee_run(state, token, channel, url, check=False):
    headers = {"User-Agent": "Mozilla/5.0"}
    if state.get("ee_etag"):
        headers["If-None-Match"] = state["ee_etag"]
    if state.get("ee_modified"):
        headers["If-Modified-Since"] = state["ee_modified"]
    try:
        r = bot.requests.get(url, headers=headers, timeout=bot.TIMEOUT)
        if r.status_code == 304:
            return
        r.raise_for_status()
        rounds = r.json().get("rounds", [])
    except Exception as e:
        bot.log(f"   EE alert: data file failed: {e}")
        return
    if not rounds:
        return
    rounds.sort(key=lambda d: _num(d.get("drawNumber")) or 0, reverse=True)
    if check:
        bot.log("\n=== Express Entry alert preview\n" + _ee_text(rounds[0], rounds, channel))
        bot.log(f"   chart: {'ok' if _ee_chart(rounds) else 'matplotlib missing'}")
        return
    state["ee_etag"] = r.headers.get("ETag", "")
    state["ee_modified"] = r.headers.get("Last-Modified", "")
    latest = _num(rounds[0].get("drawNumber")) or 0
    last = state.get("ee_last")
    if last is None:  # first run: start from the next draw
        state["ee_last"] = latest
        bot.log(f"EE alert: starting after draw #{latest}")
        return
    new = [d for d in rounds if (_num(d.get("drawNumber")) or 0) > last][::-1][:3]  # oldest first
    for d in new:
        i = rounds.index(d)
        text = _ee_text(d, rounds[i:], channel)
        png = _ee_chart(rounds[i:])
        if png and len(text) <= 1024:
            ok = _api(token, "sendPhoto", data={"chat_id": channel, "caption": text, "parse_mode": "HTML"},
                      files={"photo": ("crs.png", png, "image/png")}).ok
        else:
            ok = bot.send(token, channel, text)
        if not ok:
            return
        state["ee_last"] = _num(d.get("drawNumber"))
        bot.log(f"EE alert posted: draw #{d.get('drawNumber')}")
        time.sleep(2)


# ---------------------------------------------------------------- Ottawa alerts

BING = "https://www.bing.com/news/search?format=rss&setlang=en-CA&cc=CA&qft=sortbydate%3d%221%22&q="
NEWS_ALERTS = [  # (kind, Bing News query, the headline must match)
    ("city", "%22parking+ban%22+Ottawa",
     re.compile(r"parking ban", re.I)),
    ("city", "Ottawa+%22boil+water%22+OR+%22state+of+emergency%22",
     re.compile(r"boil.water|state of emergency", re.I)),
    ("school", "Ottawa+school+buses+cancelled",
     re.compile(r"(school )?bus(es)?.{0,40}(cancel|not running)|cancel.{0,40}school bus", re.I)),
]
OSTA_PAGE = "https://www.ottawaschoolbus.ca/bus-van-zone/school-bus-delays-and-cancellations/delays-canncellations/"
EC_PAGE = "https://weather.gc.ca/en/location/index.html?coords=45.403,-75.687"


def _ottawa_items():
    """(key, kind, English text, link) for every current urgent item."""
    items = []
    # 1) Environment Canada warnings / watches
    try:
        import weather
        warnings, _ = weather.ec_feed()
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        for w in warnings:
            items.append((_key("ec", w, day), "weather", w, EC_PAGE))
    except Exception as ex:
        bot.log(f"   alerts: EC failed: {ex}")
    # 2) + 3) The City of Ottawa and OSTA websites block GitHub's servers (403), so their announcements are
    # picked up from Bing News instead (news outlets repeat them within minutes). One alert per topic per day.
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    for kind, query, pattern in NEWS_ALERTS:
        for e in bot.fetch_feed(BING + query):
            title = bot.norm(e.get("title", ""))
            pub = e.get("published_parsed")
            if not pattern.search(title) or not pub or time.time() - time.mktime(pub) > 12 * 3600:
                continue
            link = bot.resolve_link(e.get("link", "")) if hasattr(bot, "resolve_link") else e.get("link", "")
            items.append((_key(kind, pattern.pattern, day), kind, title, link or e.get("link", "")))
            break  # newest matching story is enough
    return items


KIND = {
    "weather": ("⛈", "هشدار هواشناسی کانادا", "#هشدار_هواشناسی #آب_و_هوا"),
    "city": ("🏛", "اطلاعیه فوری شهرداری اتاوا", "#شهرداری"),
    "school": ("🚌", "سرویس مدارس اتاوا", "#مدارس #سرویس_مدرسه"),
}


def _ottawa_text(kind, text, link, channel):
    icon, label, tags = KIND[kind]
    fa_text = bot.to_persian(text.capitalize() if text.isupper() else text) or text
    lines = [f"{RLM}🚨 <b>هشدار فوری اتاوا</b>", f"{RLM}{icon} {label}", "",
             f"<b>{html.escape(bot.fa_polish(fa_text))}</b>"]
    if fa_text != text:
        lines.append(f"<i>{html.escape(text)}</i>")
    if kind == "city" and "parking ban" in text.lower():
        lines.append(f"\n{RLM}🚗 در زمان ممنوعیت پارک زمستانی، خودرو را در خیابان پارک نکنید؛ "
                     f"خودروها جریمه و یدک‌کشی می‌شوند.")
    lines.append(f'\n{RLM}🔗 <a href="{html.escape(link, quote=True)}">جزئیات و منبع رسمی</a>')
    lines.append(f"#هشدار_اتاوا {tags} #اخبار_اتاوا\n{channel}")
    return "\n".join(lines)


def ottawa_run(state, token, channel, check=False):
    bot.TRANSLATE = True
    if not check and time.time() - state.get("alerts_checked", 0) < 170:
        return
    state["alerts_checked"] = time.time()
    items = _ottawa_items()
    if check:
        bot.log(f"\n=== Ottawa alerts: {len(items)} current urgent items")
        for k, kind, text, link in items[:5]:
            bot.log(_ottawa_text(kind, text, link, channel))
        return
    seen = state.get("alerts_seen")
    if seen is None:  # first run: what is out now counts as already posted
        state["alerts_seen"] = [k for k, *_ in items]
        bot.log(f"alerts: {len(items)} current items marked as seen")
        return
    for k, kind, text, link in items:
        if k in seen:
            continue
        if bot.send(token, channel, _ottawa_text(kind, text, link, channel)):
            seen.append(k)
            bot.log(f"alert posted: {kind}: {text[:80]}")
            time.sleep(2)
    state["alerts_seen"] = seen[-300:]
