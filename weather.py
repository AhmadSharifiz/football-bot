"""
Daily Ottawa weather post in Persian (called from bot.py when the sources file has `daily_weather: true`).
Numbers: Open-Meteo (free, no key). Warnings + official text forecast: Environment Canada's Ottawa feed.
WEATHER_TIME (default 06:30, Ottawa time).
"""
import html
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import bot

TZ = ZoneInfo("America/Toronto")
WEATHER_TIME = os.environ.get("WEATHER_TIME", "06:30")
OPEN_METEO = ("https://api.open-meteo.com/v1/forecast?latitude=45.4215&longitude=-75.6972"
              "&daily=weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_min,"
              "apparent_temperature_max,precipitation_probability_max,rain_sum,snowfall_sum,"
              "wind_speed_10m_max,wind_gusts_10m_max,sunrise,sunset,uv_index_max"
              "&hourly=temperature_2m,weather_code,precipitation_probability"
              "&timezone=America%2FToronto&forecast_days=2")
EC_FEED = "https://weather.gc.ca/rss/weather/45.403_-75.687_e.xml"
RLM = "‏"
FA = str.maketrans("0123456789-", "۰۱۲۳۴۵۶۷۸۹−")
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
MONTHS_EN = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]

# WMO weather codes -> (emoji, Persian)
WMO = {
    0: ("☀️", "آفتابی و صاف"), 1: ("🌤", "عمدتاً آفتابی"), 2: ("⛅️", "نیمه‌ابری"), 3: ("☁️", "ابری"),
    45: ("🌫", "مه"), 48: ("🌫", "مه یخ‌زده"),
    51: ("🌦", "نم‌نم باران"), 53: ("🌦", "نم‌نم باران"), 55: ("🌧", "نم‌نم باران شدید"),
    56: ("🧊", "نم‌نم باران یخ‌زن"), 57: ("🧊", "نم‌نم باران یخ‌زن شدید"),
    61: ("🌧", "باران سبک"), 63: ("🌧", "باران"), 65: ("🌧", "باران شدید"),
    66: ("🧊", "باران یخ‌زن"), 67: ("🧊", "باران یخ‌زن شدید"),
    71: ("🌨", "برف سبک"), 73: ("🌨", "برف"), 75: ("❄️", "برف سنگین"), 77: ("🌨", "دانه‌های برف"),
    80: ("🌦", "رگبار پراکنده"), 81: ("🌧", "رگبار"), 82: ("⛈", "رگبار شدید"),
    85: ("🌨", "بارش پراکنده برف"), 86: ("❄️", "بارش شدید برف"),
    95: ("⛈", "رعد و برق"), 96: ("⛈", "رعد و برق با تگرگ"), 99: ("⛈", "رعد و برق با تگرگ شدید"),
}


def fa(x):
    return str(x).translate(FA)


def deg(x):
    return fa(round(x)) + "°"


def wmo(code):
    return WMO.get(int(code), ("🌡", "متغیر"))


def ec_feed():
    """(warnings list, official forecast text for today) from Environment Canada."""
    entries = bot.fetch_feed(EC_FEED)
    warnings, forecast = [], []
    for e in entries:
        title = bot.norm(e.get("title", ""))
        cat = " ".join(t.get("term", "") for t in e.get("tags", []))
        if "Warnings" in cat or "warning" in title.lower() or "watch" in title.lower() or "statement" in title.lower():
            if not title.lower().startswith("no watches"):
                warnings.append(title.split(",")[0])
        elif "Forecast" in cat and len(forecast) < 2:  # today + tonight
            forecast.append(bot.norm(re.sub(r"Forecast issued.*$", "", e.get("summary", ""), flags=re.S)))
    return warnings, forecast


def advice(d, i):
    tips = []
    tmin, tmax = d["temperature_2m_min"][i], d["temperature_2m_max"][i]
    feels = d["apparent_temperature_min"][i]
    snow, rain = d["snowfall_sum"][i] or 0, d["rain_sum"][i] or 0
    code = int(d["weather_code"][i])
    if code in (56, 57, 66, 67) or (tmin <= 0 <= tmax and rain > 0):
        tips.append("احتمال یخ‌زدگی جاده‌ها و پیاده‌روها؛ با احتیاط رانندگی کنید")
    if snow >= 5:
        tips.append("برف قابل توجه؛ زمان بیشتری برای رفت‌وآمد در نظر بگیرید")
    elif snow > 0:
        tips.append("بارش برف؛ کفش و لاستیک مناسب زمستانی")
    if rain >= 2 and snow == 0:
        tips.append("چتر همراه داشته باشید")
    if feels <= -20:
        tips.append("سرمای شدید؛ پوشش کامل صورت و دست‌ها")
    elif feels <= -10:
        tips.append("لباس گرم، کلاه و دستکش فراموش نشود")
    if tmax >= 30:
        tips.append("هوای گرم؛ آب کافی بنوشید")
    if (d["wind_gusts_10m_max"][i] or 0) >= 60:
        tips.append("باد شدید؛ مراقب اشیای سست در فضای باز باشید")
    return tips


def build(day=None):
    r = bot.requests.get(OPEN_METEO, timeout=bot.TIMEOUT)
    r.raise_for_status()
    data = r.json()
    d, h = data["daily"], data["hourly"]
    today = (day or datetime.now(TZ).date()).isoformat()
    if today not in d["time"]:
        return ""
    i = d["time"].index(today)
    emoji, desc = wmo(d["weather_code"][i])
    dt = datetime.fromisoformat(today)
    from fixtures import jalali, MONTHS
    jy, jm, jd = jalali(dt.date())
    lines = [f"{RLM}{emoji} <b>پیش‌بینی هوای اتاوا</b>",
             f"{RLM}🗓 {WEEKDAYS[dt.weekday()]} {fa(dt.day)} {MONTHS_EN[dt.month - 1]} | {fa(jd)} {MONTHS[jm - 1]} {fa(jy)}"]
    try:
        warnings, forecast = ec_feed()
    except Exception as ex:
        bot.log(f"   EC feed failed: {ex}")
        warnings, forecast = [], []
    for w in warnings[:3]:
        lines.append(f"\n{RLM}⚠️ <b>هشدار هواشناسی کانادا:</b> {html.escape(bot.to_persian(w.capitalize()) or w)}")
    lines.append(f"\n{RLM}{emoji} {desc}")
    lines.append(f"{RLM}🌡 بیشینه {deg(d['temperature_2m_max'][i])} | کمینه {deg(d['temperature_2m_min'][i])}")
    lines.append(f"{RLM}🥶 دمای احساسی: {deg(d['apparent_temperature_min'][i])} تا {deg(d['apparent_temperature_max'][i])}")
    # parts of the day from the hourly forecast
    parts = []
    for name, hr in (("صبح", 8), ("ظهر", 13), ("عصر", 17), ("شب", 21)):
        key = f"{today}T{hr:02d}:00"
        if key in h["time"]:
            j = h["time"].index(key)
            parts.append(f"{name} {wmo(h['weather_code'][j])[0]} {deg(h['temperature_2m'][j])}")
    if parts:
        lines.append(f"{RLM}🕒 " + " | ".join(parts))
    p = d["precipitation_probability_max"][i] or 0
    precip = f"{RLM}☔️ احتمال بارش: {fa(p)}٪"
    if (d["rain_sum"][i] or 0) >= 0.5:
        precip += f" | باران {fa(round(d['rain_sum'][i]))} میلی‌متر"
    if (d["snowfall_sum"][i] or 0) >= 0.5:
        precip += f" | برف {fa(round(d['snowfall_sum'][i]))} سانتی‌متر"
    lines.append(precip)
    wind = f"{RLM}💨 باد تا {fa(round(d['wind_speed_10m_max'][i]))} کیلومتر بر ساعت"
    if (d["wind_gusts_10m_max"][i] or 0) >= 40:
        wind += f"، تندباد تا {fa(round(d['wind_gusts_10m_max'][i]))}"
    lines.append(wind)
    rise, sset = d["sunrise"][i][11:16].lstrip("0"), d["sunset"][i][11:16]
    lines.append(f"{RLM}🌅 طلوع {fa(rise)} | 🌇 غروب {fa(sset)}")
    tips = advice(d, i)
    if tips:
        lines.append("\n" + "\n".join(f"{RLM}💡 {t}" for t in tips))
    if forecast:
        lines.append(f"\n{RLM}📋 <b>پیش‌بینی رسمی محیط زیست کانادا:</b>")
        for label, text in zip(("روز", "شب"), forecast):
            fa_text = bot.to_persian(re.sub(r"\bplus (\d)", r"+\1", re.sub(r"\bminus (\d)", r"-\1", text)))
            if fa_text:
                lines.append(f"{RLM}• {label}: {html.escape(bot.fa_polish(fa_text))}")
    if i + 1 < len(d["time"]):
        e2, d2 = wmo(d["weather_code"][i + 1])
        lines.append(f"\n{RLM}🔜 فردا: {e2} {d2}، {deg(d['temperature_2m_min'][i + 1])} تا {deg(d['temperature_2m_max'][i + 1])}")
    lines.append(f"\n{RLM}🔗 <a href=\"https://weather.gc.ca/en/location/index.html?coords=45.403,-75.687\">محیط زیست کانادا</a>")
    lines.append("#آب_و_هوا #هوای_اتاوا #اخبار_اتاوا\n" + bot.CHANNEL)
    return "\n".join(lines)


def run(state, token, channel):
    now = datetime.now(TZ)
    h, m = map(int, WEATHER_TIME.split(":"))
    if "weather_day" not in state:  # first run: start with tomorrow's post, no late post today
        state["weather_day"] = str(now.date()) if (now.hour, now.minute) >= (h, m) else ""
        return
    if (now.hour, now.minute) < (h, m) or state.get("weather_day") == str(now.date()):
        return
    text = build(now.date())
    if not text:
        return
    api = f"https://api.telegram.org/bot{token}"
    r = bot.requests.post(f"{api}/sendMessage", json={"chat_id": channel, "text": text, "parse_mode": "HTML",
                                                       "disable_web_page_preview": True}, timeout=bot.TIMEOUT)
    if not r.ok:
        bot.log(f"   telegram error {r.status_code}: {r.text[:200]}")
        return
    state["weather_day"] = str(now.date())
    bot.log("weather posted")
    # pin today's forecast quietly and unpin yesterday's (other pinned posts stay pinned)
    msg_id = r.json()["result"]["message_id"]
    p = bot.requests.post(f"{api}/pinChatMessage", json={"chat_id": channel, "message_id": msg_id,
                                                          "disable_notification": True}, timeout=bot.TIMEOUT)
    if not p.ok:
        bot.log(f"   pin failed (bot needs the 'Pin messages' / 'Edit messages' admin right): {p.text[:200]}")
        return
    old = state.get("weather_pin")
    if old and old != msg_id:
        bot.requests.post(f"{api}/unpinChatMessage", json={"chat_id": channel, "message_id": old}, timeout=bot.TIMEOUT)
    state["weather_pin"] = msg_id


def preview():
    bot.log("\n=== weather preview\n" + build())
