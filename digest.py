"""
Daily project-management / construction-management digest.
Once a day (after POST_TIME, Ottawa time) posts ONE message with up to 10 English articles:
Persian title (linked) + short Persian summary + source.

Env: TELEGRAM_BOT_TOKEN, DIGEST_CHANNEL (default @sharifizcom), POST_TIME (default 07:30)
Usage: python digest.py          post if it's time and today's digest hasn't gone out
       python digest.py --check  test every source and print a sample digest, post nothing
"""
import html
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

os.environ["TRANSLATE"] = "1"
import bot  # reuses feed reading, translation and Telegram sending
import yaml

ROOT = Path(__file__).parent
STATE_FILE = ROOT / os.environ.get("DIGEST_STATE", "state-pm.json")
SOURCES_FILE = ROOT / os.environ.get("DIGEST_SOURCES", "sources-pm.yml")
TITLE = os.environ.get("DIGEST_TITLE", "خلاصه روزانه مدیریت پروژه و مدیریت ساخت")
CHANNEL = os.environ.get("DIGEST_CHANNEL", "@sharifizcom")
POST_TIME = os.environ.get("POST_TIME", "07:30")
TZ = ZoneInfo("America/Toronto")
ITEMS = 10
PER_SOURCE = 2
MAX_AGE_DAYS = int(os.environ.get("DIGEST_MAX_AGE", "14"))
SUMMARY_LEN = int(os.environ.get("DIGEST_SUMMARY_LEN", "750"))
LIMIT = 4000  # Telegram allows 4096 characters per message


def log(*a):
    print(*a, flush=True)


def jalali(d):
    """Gregorian date -> Jalali (year, month, day)."""
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


MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(n):
    return str(n).translate(FA_DIGITS)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": [], "last_date": ""}


def save_state(state):
    state["seen"] = state["seen"][-3000:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def collect():
    cfg = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    words = cfg.get("keywords", [])
    exclude = cfg.get("exclude", [])
    now = time.time()
    items = []
    for src in cfg["sources"]:
        for url in src["feeds"]:
            entries = bot.fetch_feed(url)
            kept = 0
            for e in entries:
                if "bing.com/news" in url:
                    real = parse_qs(urlparse(e.get("link", "")).query).get("url")
                    if real:
                        e["link"] = real[0]
                    label = e.get("news_source") or src["name"]
                else:
                    label = src["name"]
                t = e.get("published_parsed") or e.get("updated_parsed")
                if not t:
                    continue  # undated items are often old evergreen pages
                ts = time.mktime(t)
                if now - ts > MAX_AGE_DAYS * 86400:
                    continue
                if not src.get("focused") and not bot.mentions_any(e.get("title", ""), src.get("keywords", words)):
                    continue
                if exclude and bot.mentions_any(f"{e.get('title', '')} {e.get('summary', '')}", exclude):
                    continue
                items.append({"src": label, "feed": src["name"], "topic": src.get("topic", "pm"),
                              "e": e, "ts": ts})
                kept += 1
            log(f"   {src['name']:<34} items={len(entries):<3} kept={kept}  {url[:70]}")
    return items


def pick(items, seen):
    fresh = [x for x in items
             if bot.item_key(x["e"]) not in seen and bot.title_key(x["e"].get("title", "")) not in seen]
    fresh.sort(key=lambda x: -x["ts"])
    chosen, per_src, titles = [], {}, set()
    for topic in ("cm", "pm", "cm", "pm", "cm", "pm", "cm", "pm", "cm", "pm") * 3:
        if len(chosen) >= ITEMS:
            break
        for x in fresh:
            tk = bot.title_key(x["e"].get("title", ""))
            if x["topic"] == topic and x not in chosen and tk not in titles \
                    and per_src.get(x["feed"], 0) < PER_SOURCE:
                chosen.append(x)
                titles.add(tk)
                per_src[x["feed"]] = per_src.get(x["feed"], 0) + 1
                break
    # top up from anything left if one topic ran short
    for x in fresh:
        if len(chosen) >= ITEMS:
            break
        tk = bot.title_key(x["e"].get("title", ""))
        if x not in chosen and tk not in titles and per_src.get(x["feed"], 0) < PER_SOURCE:
            chosen.append(x)
            titles.add(tk)
            per_src[x["feed"]] = per_src.get(x["feed"], 0) + 1
    return chosen


def article_paragraph(url):
    """First real paragraph(s) of the article body, about SUMMARY_LEN characters."""
    try:
        r = bot.requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=bot.TIMEOUT)
        r.raise_for_status()
        soup = bot.BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        root = soup.find("article") or soup.find("main") or soup
        text = ""
        for p in root.find_all("p"):
            t = bot.norm(p.get_text(" "))
            if len(t) < 80 or "cookie" in t.lower() or "subscribe" in t.lower():
                continue
            text = (text + " " + t).strip()
            if len(text) >= SUMMARY_LEN:
                break
        return text
    except Exception as ex:
        log(f"   article fetch failed: {ex}")
        return ""


def item_link(e):
    for k in ("link", "id", "guid"):
        v = (e.get(k) or "").strip()
        if v.startswith("http"):
            return v
    for l in e.get("links", []):
        if (l.get("href") or "").startswith("http"):
            return l["href"]
    return ""


def render(chosen, today):
    """Returns (list of message texts, items used). Splits into several messages if over Telegram's limit."""
    jy, jm, jd = jalali(today)
    head = (f"📊 <b>{TITLE}</b>\n"
            f"🗓 {fa(jd)} {MONTHS[jm - 1]} {fa(jy)}\n")
    foot = "@sharifizcom\n#مدیریت_پروژه #مدیریت_ساخت #کنترل_پروژه"
    blocks = []
    for x in chosen:
        e = x["e"]
        title = bot.norm(e.get("title", ""))
        link = item_link(e)
        if not link:
            continue  # never post an item without a working link
        summary = bot.norm(e.get("summary", "") or "")
        if summary.lower().startswith(title.lower()):
            summary = summary[len(title):].strip(" -–:")
        if len(summary) < SUMMARY_LEN * 0.6:
            body = article_paragraph(link)
            if len(body) > len(summary):
                summary = body
        if len(summary) < 40:
            summary = bot.article_summary(link) or summary
        summary = bot.clean_summary(summary)
        summary = bot.shorten(summary, SUMMARY_LEN)
        t_fa, s_fa = bot.to_persian(title), (bot.to_persian(summary) if summary else "")
        if not t_fa or s_fa is None:
            continue  # translation failed: leave it for another day
        blocks.append((x, t_fa, s_fa, link))
    texts, cur, used = [], head, []
    for n, (x, t_fa, s_fa, link) in enumerate(blocks, 1):
        title_en = bot.norm(x["e"].get("title", ""))
        block = f"{bot.RLM}{fa(n)}. <b><a href=\"{html.escape(link, quote=True)}\">{html.escape(bot.fa_polish(t_fa))}</a></b>"
        if t_fa != title_en:
            block += "\n" + bot.original_line(title_en)
        if s_fa:
            block += f"\n{html.escape(bot.fa_polish(s_fa))}"
        block += f"\n{bot.RLM}<i>— {html.escape(x['src'])}</i>{bot.RLM}"
        if len(bot.norm(cur + block)) > LIMIT - 200:
            texts.append(cur.rstrip())
            cur = ""
        cur += "\n" + block + "\n"
        used.append(x)
    texts.append((cur + "\n" + foot).strip())
    return texts, used


def main():
    check = "--check" in sys.argv
    now = datetime.now(TZ)
    today = now.date().isoformat()
    state = load_state()
    if not check:
        hh, mm = map(int, POST_TIME.split(":"))
        if state.get("last_date") == today or (now.hour, now.minute) < (hh, mm):
            return
    log("-- project management digest")
    items = collect()
    log(f"candidates: {len(items)}")
    chosen = pick(items, set(state["seen"]))
    if not chosen:
        log("nothing new today")
        return
    texts, used = render(chosen, now.date())
    if check:
        for x in used:
            log(f" [{x['src']}] {bot.norm(x['e'].get('title', ''))[:100]}  {item_link(x['e'])[:80]}")
        for t in texts:
            log(f"\nsample message ({len(t)} chars):\n{t}")
        return
    if len(used) < 3:
        log("fewer than 3 items, skipping today")
        return
    for t in texts:
        r = bot.requests.post(
            f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
            json={"chat_id": CHANNEL, "text": t, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=bot.TIMEOUT,
        )
        if not r.ok:
            log(f"telegram error {r.status_code}: {r.text[:300]}")
            if t is texts[0]:
                return  # nothing went out: try again next loop
            break  # part already posted: don't repeat it
        time.sleep(2)
    for x in used:
        state["seen"] += [bot.item_key(x["e"]), bot.title_key(x["e"].get("title", ""))]
    state["last_date"] = today
    save_state(state)
    log(f"digest posted to {CHANNEL}: {len(used)} items")


if __name__ == "__main__":
    main()
