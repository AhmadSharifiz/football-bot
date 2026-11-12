"""
Football Online auto-poster.
Reads Persian news feeds, keeps football items only, and posts
headline + short summary + source link to the Telegram channel.

Env vars:
  TELEGRAM_BOT_TOKEN   bot token from BotFather (keep secret)
  TELEGRAM_CHANNEL     channel username, default @footballonline_fa
  MAX_POSTS_PER_RUN    default 5 (stops channel flooding)

Usage:
  python bot.py            normal run (first run only marks items as seen, posts nothing)
  python bot.py --check    test every source, post nothing
"""
import hashlib
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
STATE_FILE = ROOT / os.environ.get("STATE_FILE", "state.json")
SOURCES_FILE = ROOT / os.environ.get("SOURCES_FILE", "sources.yml")
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@footballonline_fa")
FILTER_MODE = os.environ.get("FILTER_MODE", "football")   # "football" or "none"
TRANSLATE = os.environ.get("TRANSLATE", "0") == "1"       # translate non-Persian text to Persian
ICON = os.environ.get("ICON", "⚽️")
FETCH_SUMMARY = os.environ.get("FETCH_SUMMARY", "0") == "1"  # open the article to get its summary
SUMMARY_LEN = int(os.environ.get("SUMMARY_LEN", "220"))
EXPAND_PR = "immigration" in os.environ.get("SOURCES_FILE", "")  # "PR" means permanent residence there
SHOW_ORIGINAL = os.environ.get("SHOW_ORIGINAL", "1") == "1"  # English headline under translated titles
UA = "Mozilla/5.0 (compatible; FootballOnlineBot/1.0)"
TIMEOUT = 20
MAX_SEEN = 6000

# unambiguous football terms: one match is enough
STRONG_WORDS = [
    "فوتبال", "فوتبالی", "لیگ برتر", "جام حذفی", "سوپرجام", "جام جهانی", "جام ملت‌های آسیا",
    "لیگ قهرمانان", "لیگ نخبگان", "لیگ اروپا", "کنفرانس لیگ", "پرسپولیس", "سپاهان",
    "تراکتور", "گل‌گهر", "ذوب‌آهن", "چادرملو", "سرمربی", "دروازه‌بان", "پنالتی", "داربی",
    "رئال مادرید", "بارسلونا", "منچستر یونایتد", "منچسترسیتی", "منچستر سیتی", "لیورپول",
    "آرسنال", "چلسی", "تاتنهام", "بایرن مونیخ", "دورتموند", "یوونتوس", "اینترمیلان",
    "آث میلان", "ناپولی", "پاری سن ژرمن", "لالیگا", "پریمیرلیگ", "سری آ", "بوندسلیگا",
    "لوشامپیونه", "فیفا", "یوفا", "کنفدراسیون فوتبال آسیا", "مهدی طارمی", "علیرضا جهانبخش",
    "علیرضا بیرانوند", "قلعه‌نویی", "امیر قلعه‌نویی", "لیونل مسی", "کریستیانو رونالدو",
    "امباپه", "هالند", "نقل و انتقالات", "هت‌تریک", "کارت قرمز", "آفساید",
]
# words that also show up outside football: need 2 of these, or 1 plus a strong word
WEAK_WORDS = [
    "استقلال", "فولاد", "ملوان", "خیبر", "آلومینیوم", "پیکان", "نساجی", "هوادار", "صنعت نفت",
    "تیم ملی", "مهاجم", "هافبک", "مدافع", "گل", "بازیکن", "مربی", "ورزشگاه", "یورو",
    "مسی", "رونالدو", "طارمی", "میلان", "اینتر", "قهرمانی", "جدول", "باشگاه", "لیگ",
    "داور", "داوری", "کمک داور", "هفته", "دیدار", "گل‌زنی", "گلزنی", "تمرین", "اردو",
]
# items about these sports are dropped
EXCLUDE_WORDS = ["فوتسال", "فوتبال ساحلی", "والیبال", "بسکتبال", "بسکتبالیست", "کشتی", "کشتی‌گیر", "هندبال",
                 "دوومیدانی", "شنا", "تنیس", "فرمول یک", "جودو", "کاراته", "بوکس", "دوچرخه‌سواری",
                 "تیراندازی", "قایقرانی", "ژیمناستیک", "بدمینتون", "هاکی", "اسکی", "شطرنج", "سنگنوردی",
                 "وزنه‌برداری", "تکواندو", "فوتبال آمریکایی"]


def log(*a):
    print(*a, flush=True)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": [], "initialized": False}


def save_state(state):
    state["seen"] = state["seen"][-MAX_SEEN:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def norm(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = text.replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    return re.sub(r"\s+", " ", text).strip()


def item_key(entry):
    base = entry.get("link") or entry.get("id") or entry.get("title", "")
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def title_key(title):
    t = re.sub(r"[^\w]+", " ", norm(title).lower()).strip()
    return "t:" + hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


_MONTHS = r"(January|February|March|April|May|June|July|August|September|October|November|December)"


def _meta_line(line):
    """True for lines with only a date and/or hashtags (no real words)."""
    s = re.sub(r"#\S+", "", line)
    s = re.sub(rf"\b{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}\b", "", s, flags=re.I)
    return not re.search(r"[^\W\d_]{3,}", s)


def clean_summary(text):
    """Drop feed boilerplate ("The post X appeared first on Y") and expand PR so it isn't translated as public relations."""
    text = re.sub(r"\s*The post .{0,300}? appeared first on .*$", "", text, flags=re.S)
    return re.sub(r"\bPR\b", "permanent residence", text) if EXPAND_PR else text


def _has(word, text):
    w = re.escape(norm(word)).replace(r"\ ", r"\s?")
    return re.search(rf"(?<![\w]){w}(?![\w])", text) is not None


def mentions_any(text, words):
    t = " " + norm(text).lower() + " "
    return any(re.search(rf"(?<![\w]){re.escape(w.lower())}(?![\w])", t) for w in words)


def is_football(text):
    t = norm(text)
    if any(_has(w, t) for w in EXCLUDE_WORDS):
        return False
    strong = sum(_has(w, t) for w in STRONG_WORDS)
    weak = sum(_has(w, t) for w in WEAK_WORDS)
    return strong >= 1 or weak >= 2


def discover_feeds(homepage):
    """Find RSS/Atom <link> tags on the homepage."""
    try:
        r = requests.get(homepage, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        log(f"   homepage failed: {e}")
        return []
    feeds = []
    for tag in re.findall(r"<link[^>]+>", r.text, flags=re.I):
        if re.search(r"application/(rss|atom)\+xml", tag, re.I):
            m = re.search(r'href=["\']([^"\']+)', tag, re.I)
            if m:
                feeds.append(urljoin(homepage, html.unescape(m.group(1))))
    return list(dict.fromkeys(feeds))


def fetch_feed(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
        return feedparser.parse(r.content).entries
    except Exception as e:
        log(f"   feed failed {url}: {e}")
        return []


# promo / ad posts in Telegram channels
AD_MARKERS = ["t.me/+", "joinchat", "تبلیغ", "تبلیغات", "سیگنال", "VIP", "کد تخفیف", "خرید اشتراک",
              "آی‌پی‌تی‌وی", "IPTV", "شرط بندی", "شرط‌بندی", "کازینو", "پیش بینی کن", "جایزه نقدی"]
# boilerplate lines channels append to every post
BOILER = re.compile(r"(آدرس سایت|کانال ما|عضو شوید|join|📲|🆔|@\w+|https?://\S+)", re.I)


def fetch_telegram(channel):
    """Read recent posts from a public channel's web preview (t.me/s/<channel>)."""
    url = f"https://t.me/s/{channel}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        log(f"   channel failed {url}: {e}")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    entries = []
    for m in soup.select(".tgme_widget_message[data-post]"):
        if m.select_one(".tgme_widget_message_forwarded_from"):
            continue  # skip reposts from other channels
        body = m.select_one(".tgme_widget_message_text")
        if not body:
            continue
        raw = body.decode_contents()
        if any(a.lower() in raw.lower() for a in AD_MARKERS):
            continue
        for br in body.find_all("br"):
            br.replace_with("\n")
        lines = [norm(l) for l in body.get_text().split("\n")]
        lines = [l for l in lines if l and not BOILER.search(l)]
        while lines and _meta_line(lines[0]):
            lines.pop(0)  # date / hashtag-only first line
        if len(lines) > 1 and len(lines[0].split()) <= 3 and not lines[0].endswith(("?", "؟", "!")):
            lines.pop(0)  # short label line such as "CIC News" or "خبر فوری"
        if not lines:
            continue
        lines[0] = re.sub(r"^[^\w«\"'(]+", "", lines[0]) or lines[0]
        preview = m.select_one("a.tgme_widget_message_link_preview[href]")
        ext = preview["href"] if preview and preview["href"].startswith("http") else ""
        t = m.select_one("time[datetime]")
        published = None
        if t:
            try:
                published = time.strptime(t["datetime"][:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
        entries.append({
            "title": lines[0][:160],
            "summary": " ".join(lines[1:]),
            "link": ext if (ext and os.environ.get("TG_EXTERNAL_LINK")) else f"https://t.me/{m['data-post']}",
            "tg_link": f"https://t.me/{m['data-post']}",
            "published_parsed": published,
        })
    return entries


def fetch_page_links(url, pattern):
    """News list pages without a feed: every link whose address contains `pattern` becomes an item."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        log(f"   page failed {url}: {e}")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out, seen_links = [], set()
    for a in soup.select("main a[href], a[href]"):
        href = urljoin(url, a["href"])
        title = norm(a.get_text(" "))
        if pattern not in href or href in seen_links or len(title) < 15 or href.rstrip("/") == url.rstrip("/"):
            continue
        seen_links.add(href)
        out.append({"title": title, "summary": "", "link": href, "published_parsed": None})
    return out[:20]


def fetch_ee_rounds(url):
    """Express Entry rounds of invitations (IRCC's own data file behind the rounds page)."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT)
        r.raise_for_status()
        rounds = r.json().get("rounds", [])
    except Exception as e:
        log(f"   Express Entry rounds failed: {e}")
        return []
    out = []
    for d in rounds[:10]:
        name = (d.get("drawName") or "").strip()
        title = (f"Express Entry draw #{d.get('drawNumber')}: {d.get('drawSize')} invitations to apply, "
                 f"minimum CRS score {d.get('drawCRS')}" + (f" ({name})" if name else ""))
        summary = (f"IRCC held Express Entry round #{d.get('drawNumber')} on {d.get('drawDateFull') or d.get('drawDate')}. "
                   f"Program: {name or 'all programs'}. Invitations issued: {d.get('drawSize')}. "
                   f"Lowest-ranked candidate CRS score: {d.get('drawCRS')}.")
        try:
            pub = time.strptime(d.get("drawDate", "")[:10], "%Y-%m-%d")
        except ValueError:
            pub = None
        out.append({"title": title, "summary": summary,
                    "link": "https://www.canada.ca/en/immigration-refugees-citizenship/services/immigrate-canada/express-entry/rounds-invitations.html#ee" + str(d.get("drawNumber")),
                    "published_parsed": pub})
    return out


SEEN_SOURCES = set()  # every feed/channel key that returned posts this run


def collect():
    cfg = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    global SUMMARY_LEN, FETCH_SUMMARY
    if cfg.get("summary_len"):  # per-channel summary length, set in the sources file
        SUMMARY_LEN = int(cfg["summary_len"])
    if cfg.get("fetch_summary"):
        FETCH_SUMMARY = True
    if cfg.get("telegram_external_links"):
        os.environ["TG_EXTERNAL_LINK"] = "1"  # link Telegram items to the article they share
    global TRANSLATE, HASHTAGS, DEFAULT_TAGS, CFG
    CFG = cfg
    if cfg.get("translate"):
        TRANSLATE = True
    HASHTAGS = cfg.get("hashtags") or {}
    DEFAULT_TAGS = cfg.get("default_hashtags") or []
    items = []
    for src in cfg["sources"]:
        log(f"-- {src['name']}")
        if src.get("telegram"):
            batches = [(f"tg:{c}", f"@{c}", fetch_telegram(c)) for c in src["telegram"]]
        elif src.get("page"):
            batches = [(src["page"], src["name"], fetch_page_links(src["page"], src.get("pattern", "")))]
        elif src.get("ee_rounds"):
            batches = [(src["ee_rounds"], src["name"], fetch_ee_rounds(src["ee_rounds"]))]
        else:
            feeds = src.get("feeds") or discover_feeds(src["homepage"])
            if not feeds:
                log("   no feeds found")
                continue
            batches = [(url, src["name"], fetch_feed(url)) for url in feeds]
        for key, label, entries in batches:
            if "news.google.com" in key:
                for e in entries:
                    t = e.get("title", "")
                    if " - " in t:
                        head, outlet = t.rsplit(" - ", 1)
                        e["title"] = head
                        e["_label"] = outlet
                    e["summary"] = ""  # Google News summaries are just link lists
            if "reddit.com" in key:
                for e in entries:
                    raw = e.get("summary", "")
                    m = re.search(r'<a href="([^"]+)">\[link\]</a>', raw)
                    if m and "reddit.com" not in m.group(1) and "redd.it" not in m.group(1):
                        e["link"] = html.unescape(m.group(1))  # the news article the post links to
                    body = re.sub(r"submitted by.*$", "", norm(raw), flags=re.S).strip()
                    e["summary"] = body
                    e["_label"] = "r/ottawa"
            if "bing.com/news" in key:
                from urllib.parse import urlparse, parse_qs
                for e in entries:
                    real = parse_qs(urlparse(e.get("link", "")).query).get("url")
                    if real:
                        e["link"] = real[0]
                    if e.get("news_source"):
                        e["_label"] = e["news_source"]
            if entries:
                SEEN_SOURCES.add(key)
            kept = 0
            for e in entries:
                text = f"{e.get('title', '')} {e.get('summary', '')}"
                if FILTER_MODE == "none":
                    keep = True
                elif FILTER_MODE == "keywords":
                    # non-local sources: an Ottawa place name must be in the headline itself
                    # and nothing that points to the federal government or another Ottawa (Kansas, Illinois...)
                    words = src.get("keywords", cfg.get("keywords", []))
                    where = text if src.get("match_text") else e.get("title", "")
                    keep = (src.get("local") or mentions_any(where, words)) and \
                        not mentions_any(f"{text} {e.get('_label', '')}", cfg.get("exclude", [])) and \
                        (not src.get("require") or mentions_any(text, src["require"]))
                elif src.get("football_only"):
                    keep = not any(_has(w, norm(text)) for w in EXCLUDE_WORDS) and \
                        not mentions_any(e.get("title", ""), src.get("exclude", []))
                else:
                    keep = is_football(text)
                if keep:
                    items.append((e.get("_label", label), e, key))
                    kept += 1
            log(f"   {key}  items={len(entries)} football={kept}")
    return items


_PERSIAN = re.compile(r"[\u0600-\u06FF]")
_TR_CACHE = {}


# ---------- Persian post formatting ----------
RLM = "‏"   # right-to-left mark
ZWNJ = "‌"  # Persian half-space
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z0-9.&'’+\-/]*(?:[ ]+[A-Za-z0-9][A-Za-z0-9.&'’+\-/]*)*")
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# fixes for words the translator gets wrong or spells loosely
FA_FIXES = [
    (r"اکسپرس اینتری|اکسپرس اینتر|ورود سریع|ورود اکسپرس", "اکسپرس انتری"),
    (r"قرعه کشی", "قرعه‌کشی"),
    (r"دعوت نامه", "دعوت‌نامه"),
    (r"نرم افزار", "نرم‌افزار"),
    (r"هنگ کنگ", "هنگ‌کنگ"),
    (r"(?<=مقابل )دوشنبه", "Monday"),
    (r"بین المللی", "بین‌المللی"),
    (r"اقامت دائمی", "اقامت دائم"),
    (r"روابط عمومی(?= ?(اضافی|جدید|کانادا|بیشتر))", "اقامت دائم"),
    (r"\bدوشنبه\.com\b|Monday\.com", "Monday.com"),
    (r"نمودار گانت", "نمودار گانت"),
    (r"\s+([،؛:!؟.])", r"\1"),          # no space before punctuation
    (r"([،؛؟])(?=\S)", r"\1 "),         # one space after Persian punctuation
    (r"\.{3,}|…+", "…"),
]


def fa_polish(text):
    """Tidy a Persian line: half-spaces, common term fixes, Persian digits outside English words,
    and direction marks so English words and numbers don't jumble the line order in Telegram."""
    if not text or not _PERSIAN.search(text):
        return text
    t = text.replace(RLM, "")
    t = re.sub(r"(?<![\w‌])(ن?می) (?=[؀-ۿ])", lambda m: m.group(1) + ZWNJ, t)
    t = re.sub(r"(?<=[؀-ۿ]) (ها|های|هایی|هایم|هایش|تر|ترین|ای)(?![؀-ۿ])",
               lambda m: ZWNJ + m.group(1), t)
    for pat, rep in FA_FIXES:
        t = re.sub(pat, rep, t)
    # Persian digits, but leave numbers that belong to English words (P6, COVID-19) alone
    parts, out = _LATIN_RUN.split(t), []
    runs = _LATIN_RUN.findall(t)
    for i, chunk in enumerate(parts):
        out.append(chunk.translate(_FA_DIGITS))
        if i < len(runs):
            out.append(runs[i] + RLM)  # RLM after an English run keeps following punctuation in place
    t = "".join(out)
    t = re.sub(r"[ \t]+", " ", t).strip()
    return RLM + t  # line always reads right-to-left, even when it starts with an English word


HASHTAGS, DEFAULT_TAGS, CFG = {}, [], {}


def _topic_tags(*texts):
    blob = " ".join(t for t in texts if t)
    for phrase in CFG.get("hashtag_ignore", []):  # names that contain topic words (e.g. the department's full name)
        blob = re.sub(re.escape(norm(phrase)), " ", norm(blob), flags=re.I)
    return [t for t, words in HASHTAGS.items() if mentions_any(blob, [norm(str(w)) for w in words])]


def hashtags_for(titles, bodies=()):
    """Channel default tags + topic tags. Topics named in the headline win; the summary is used only if the
    headline names none, so a passing mention deep in the text doesn't add a tag."""
    tags = _topic_tags(*titles)[:4] or _topic_tags(*bodies)[:3]
    out = []
    for t in DEFAULT_TAGS + tags:
        t = "#" + t.lstrip("#").replace(" ", "_").replace("\u200c", "_")
        if t not in out:
            out.append(t)
    return " ".join(out)


def shorten(text, n):
    """Cut to about n characters, ending at a full sentence when possible."""
    if len(text) <= n:
        return text
    cut = text[:n]
    ends = [m.end() for m in re.finditer(r"[.!?؟。](?=\s|$)", cut)]
    if ends and ends[-1] > n * 0.55:
        return cut[:ends[-1]].strip()
    return cut.rsplit(" ", 1)[0] + "…"


def original_line(title):
    """The English headline, shown small under the Persian title."""
    return f"<i>{html.escape(title)}</i>" if title and not _PERSIAN.search(title) else ""


def _tr_google(text):
    r = requests.get("https://translate.googleapis.com/translate_a/single",
                     params={"client": "gtx", "sl": "auto", "tl": "fa", "dt": "t", "q": text},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])


def _tr_mymemory(text):
    r = requests.get("https://api.mymemory.translated.net/get",
                     params={"q": text[:480], "langpair": "en|fa"},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["responseData"]["translatedText"]


def to_persian(text):
    """Translate to Persian if the text is mostly non-Persian. None if translation failed."""
    if not TRANSLATE or not text or len(_PERSIAN.findall(text)) > len(text) * 0.3:
        return text
    if text in _TR_CACHE:
        return _TR_CACHE[text]
    for engine in (_tr_google, _tr_mymemory):
        try:
            out = engine(text[:1500])
            time.sleep(1)
            if out and _PERSIAN.search(out):
                _TR_CACHE[text] = out
                return out
        except Exception as ex:
            log(f"   translate failed ({engine.__name__}): {ex}")
    return None


def resolve_link(link):
    """Turn a Google News redirect into the publisher's own article URL."""
    if "news.google.com" not in link:
        return link
    try:
        from googlenewsdecoder import gnewsdecoder
        res = gnewsdecoder(link, interval=1)
        if res.get("status") and res.get("decoded_url"):
            return res["decoded_url"]
    except Exception as ex:
        log(f"   link decode failed: {ex}")
    return link


def article_summary(url):
    """The publisher's own short description of the article (og:description / meta description)."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for sel in ['meta[property="og:description"]', 'meta[name="description"]',
                    'meta[name="twitter:description"]']:
            m = soup.select_one(sel)
            if m and m.get("content") and len(m["content"].strip()) > 30:
                return norm(m["content"])
    except Exception as ex:
        log(f"   summary fetch failed: {ex}")
    return ""


def article_paragraph(url):
    """First real paragraph(s) of the article body, about SUMMARY_LEN characters."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        root = soup.find("article") or soup.find("main") or soup
        text = ""
        for p in root.find_all("p"):
            t = norm(p.get_text(" "))
            low = t.lower()
            if len(t) < 80 or "cookie" in low or "subscribe" in low or "@" in t \
                    or "media relations" in low or "contacts" in low:
                continue
            # drop a dateline ("November 10, 2026 - Ottawa -") and paragraphs that repeat what we already have
            t = re.sub(rf"^{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}\s*[—–-]\s*[^—–-]{{1,40}}?[—–-]\s*", "", t)
            if t[:60] in text:
                continue
            text = (text + " " + t).strip()
            if len(text) >= SUMMARY_LEN:
                break
        return text
    except Exception as ex:
        log(f"   article fetch failed: {ex}")
        return ""


def format_post(source, e):
    title = norm(e.get("title", ""))
    summary = norm(e.get("summary", ""))
    link = e.get("link", "")
    if FETCH_SUMMARY and not link.startswith("https://t.me/"):
        link = resolve_link(link)
        if len(summary) < SUMMARY_LEN * 0.6 and "news.google.com" not in link:
            body = article_paragraph(link)
            if len(body) > len(summary):
                summary = body
        if len(summary) < 40 and "news.google.com" not in link:
            summary = article_summary(link) or summary
    if summary.startswith(title):
        summary = summary[len(title):].strip()
    if norm(summary).lower() == norm(title).lower():
        summary = ""
    summary = clean_summary(summary)
    if EXPAND_PR:
        title = re.sub(r"\bPR\b", "permanent residence", title)
    summary = shorten(summary, SUMMARY_LEN)
    original = norm(e.get("title", ""))
    original_summary = summary
    title, summary = to_persian(title), to_persian(summary)
    if title is None or summary is None:
        return None  # translation failed: skip for now, retry next run
    head = f"{ICON} <b>{html.escape(fa_polish(title))}</b>"
    if title != original and SHOW_ORIGINAL:
        head += "\n" + original_line(original)
    parts = [head]
    if summary:
        parts.append(html.escape(fa_polish(summary)))
    parts.append(f'🔗 <a href="{html.escape(link, quote=True)}">{RLM}منبع: {html.escape(source)}{RLM}</a>')
    tags = hashtags_for((original, title), (original_summary, summary))
    parts.append(f"{tags}\n{CHANNEL}" if tags else CHANNEL)
    return "\n\n".join(parts)


def send(token, channel, text):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": channel, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": False},
        timeout=TIMEOUT,
    )
    if r.status_code == 429:
        wait = r.json().get("parameters", {}).get("retry_after", 30)
        time.sleep(wait)
        return send(token, channel, text)
    if not r.ok:
        log(f"   telegram error {r.status_code}: {r.text[:200]}")
    return r.ok


def main():
    check = "--check" in sys.argv
    run_every = int(os.environ.get("RUN_EVERY", "0"))  # seconds between source checks (0 = every call)
    # instant alerts run on every call, before the RUN_EVERY wait (alerts.py)
    cfg0 = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8")) or {}
    if cfg0.get("ee_alert") or cfg0.get("ottawa_alerts"):
        import alerts
        st = load_state()
        try:
            if cfg0.get("ee_alert"):
                alerts.ee_run(st, os.environ.get("TELEGRAM_BOT_TOKEN", ""), CHANNEL, cfg0["ee_alert"]["url"], check)
            if cfg0.get("ottawa_alerts"):
                alerts.ottawa_run(st, os.environ.get("TELEGRAM_BOT_TOKEN", ""), CHANNEL, check)
        except Exception as ex:
            log(f"alerts failed: {ex}")
        if not check:
            save_state(st)
    if not check and run_every:
        st = load_state()
        if time.time() - st.get("last_run", 0) < run_every:
            return
    items = collect()
    log(f"\nfootball items found: {len(items)}")
    if check:
        for src, e, _ in items[:25]:
            log(f" [{src}] {norm(e.get('title', ''))}")
        if TRANSLATE:
            for src, e, _ in items[:3]:
                log(f" translated: {to_persian(norm(e.get('title', '')))}")
        if FETCH_SUMMARY or HASHTAGS:
            g = [x for x in items if "bing.com/news" in x[2]][:1]
            g += [x for x in items if not _PERSIAN.search(x[1].get("title", ""))][:2]
            g += [x for x in items if _PERSIAN.search(x[1].get("title", ""))][:1]
            for src, e, _ in g:
                log(" sample post:\n" + str(format_post(src, e)))
        if CFG.get("daily_fixtures"):
            import fixtures
            fixtures.preview()
        if CFG.get("daily_weather"):
            import weather
            weather.preview()
        if CFG.get("blog_feed"):
            import blog
            blog.preview(CFG["blog_feed"])
        return

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    channel = CHANNEL
    max_posts = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))

    state = load_state()
    seen = set(state["seen"])
    known = set(state.get("sources", []))
    if CFG.get("daily_fixtures"):
        try:
            import fixtures
            fixtures.run(state, token, channel)  # morning fixtures / overnight results, when due
        except Exception as ex:
            log(f"fixtures failed: {ex}")
    if CFG.get("daily_weather"):
        try:
            import weather
            weather.run(state, token, channel)  # morning Ottawa forecast, when due
        except Exception as ex:
            log(f"weather failed: {ex}")
    if CFG.get("blog_feed"):
        try:
            import blog
            blog.run(state, token, CFG["blog_feed"])  # new weblog post -> PM channel
        except Exception as ex:
            log(f"blog failed: {ex}")

    # oldest first so the channel reads in order
    items.sort(key=lambda x: x[1].get("published_parsed") or x[1].get("updated_parsed") or time.gmtime(0))

    def keys_of(e):
        ks = [item_key(e), title_key(e.get("title", ""))]
        if e.get("tg_link"):  # telegram post also known by its t.me address
            ks.append(item_key({"link": e["tg_link"]}))
        return ks

    new = [(s, e, k) for s, e, k in items if not (set(keys_of(e)) & seen)]

    if not state.get("initialized"):
        # first run: don't dump the backlog into the channel (optionally keep the newest few)
        keep = int(os.environ.get("FIRST_RUN_POSTS", "0"))
        backlog = new[:-keep] if keep else new
        for s, e, k in backlog:
            state["seen"] += keys_of(e)
            seen.update(keys_of(e))
        state["initialized"] = True
        state["sources"] = sorted(SEEN_SOURCES)
        known = set(SEEN_SOURCES)
        new = new[len(backlog):]
        log(f"first run: marked {len(backlog)} existing items as seen, posting the newest {len(new)}")
        if not new:
            state["last_run"] = time.time()
            save_state(state)
            return

    # a source added later: skip its existing backlog the first time we see it
    fresh = {k for k in SEEN_SOURCES if k not in known}
    if fresh:
        for s, e, k in new:
            if k in fresh:
                state["seen"] += keys_of(e)
                seen.update(keys_of(e))
        log(f"new sources, backlog skipped: {sorted(fresh)}")
        new = [x for x in new if x[2] not in fresh]
    state["sources"] = sorted(known | SEEN_SOURCES)

    posted = 0
    for s, e, k in new:
        if posted >= max_posts:
            break
        keys = keys_of(e)
        if set(keys) & seen:  # same headline or link from another source this run
            continue
        text = format_post(s, e)
        if text is None:
            continue
        if send(token, channel, text):
            posted += 1
            time.sleep(3)
        state["seen"] += keys
        seen.update(keys)
    state["last_run"] = time.time()
    save_state(state)
    log(f"posted {posted}")


if __name__ == "__main__":
    main()
