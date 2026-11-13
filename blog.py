"""
New weblog post -> Telegram (English: title, summary, link).
Called from bot.py when the sources file has a `blog_feed:` block, e.g.
  blog_feed: {url: "https://asharifi.wordpress.com/feed/", channel: "@sharifizcom", times: ["20:02", "09:02"]}
Checks twice a day, 8:02 PM and 9:02 AM (Ottawa time), and posts each new article once. Existing articles are never re-posted.
"""
import html
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import bot

TZ = ZoneInfo("America/Toronto")
SUMMARY_LEN = 450


def _summary(e):
    desc = bot.norm(e.get("summary", ""))
    desc = re.sub(r"\s*(\[…\]|\[\.\.\.\]|Continue reading.*|Read more.*)$", "", desc).strip()
    if len(desc) >= 80:
        return bot.shorten(desc, SUMMARY_LEN)
    content = ""
    for c in e.get("content", []) or []:
        content = bot.norm(c.get("value", ""))
        if content:
            break
    return bot.shorten(content or desc, SUMMARY_LEN)


def _tag(word):
    w = re.sub(r"[^\w]+", "", word.title())
    return f"#{w}" if w else ""


def format_post(e, channel):
    title = bot.norm(e.get("title", ""))
    link = e.get("link", "")
    summary = _summary(e)
    cats = [t.get("term", "") for t in e.get("tags", []) if t.get("term") and t.get("term") != "Uncategorized"]
    tags = " ".join(dict.fromkeys([_tag(c) for c in cats] + ["#ProjectManagement", "#ConstructionManagement"]))
    parts = [f"📝 <b>{html.escape(title)}</b>"]
    if summary:
        parts.append(html.escape(summary))
    parts.append(f'🔗 <a href="{html.escape(link, quote=True)}">Read the full article</a>')
    parts.append(f"{tags.strip()}\n{channel}")
    return "\n\n".join(parts)


def run(state, token, cfg):
    """Checks the blog at each time in cfg["times"] (Ottawa time, default 20:02 and 09:02) and posts any article
    not posted yet. If nothing new is out at check time, it looks again every 15 minutes for up to 3 hours."""
    url, channel = cfg["url"], cfg["channel"]
    times = cfg.get("times") or [cfg.get("time", "20:02")]
    now = datetime.now(TZ)
    today = str(now.date())
    if "blog_seen" not in state:  # first run (any time of day): articles already on the blog count as posted
        entries = bot.fetch_feed(url)
        if entries:
            state["blog_seen"] = [e.get("link", "") for e in entries]
            bot.log(f"blog: {len(entries)} existing posts marked as seen")
        return
    done = state.setdefault("blog_done", {})  # check time -> date it found and posted something
    mins = now.hour * 60 + now.minute
    active = [t for t in times
              if 0 <= mins - (int(t[:2]) * 60 + int(t[3:])) < 180 and done.get(t) != today]
    if not active:
        return
    if time.time() - state.get("blog_checked", 0) < 900:
        return
    state["blog_checked"] = time.time()
    entries = bot.fetch_feed(url)
    if not entries:
        return
    seen = set(state["blog_seen"])
    new = [e for e in entries if e.get("link") and e["link"] not in seen]
    for e in reversed(new):  # oldest first
        if bot.send(token, channel, format_post(e, channel)):
            state["blog_seen"].append(e["link"])
            bot.log(f"blog posted: {e.get('title', '')[:80]}")
            time.sleep(2)
    if new:
        for t in active:
            done[t] = today  # done for this check time today
    state["blog_seen"] = state["blog_seen"][-200:]


def preview(cfg):
    entries = bot.fetch_feed(cfg["url"])
    if entries:
        bot.log("\n=== blog post preview\n" + format_post(entries[0], cfg["channel"]))
