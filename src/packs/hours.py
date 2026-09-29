"""Opening-hours research for places the app saw without hours (the Worker queue).

Sources, in order: restaurant website JSON-LD/microdata → website text via an LLM with a strict
JSON schema (OpenRouter) → HERE Places. Every source yields typed rules; `build_osm` turns them
into OSM `opening_hours` syntax deterministically, so output is always valid.
"""

from __future__ import annotations

import html as htmllib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass

USER_AGENT = "HungryMapHoursBot/1.0 (+https://github.com/christianweinmayr/hungrymap-packs)"
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_DAY_NAMES = {
    "monday": "Mo", "tuesday": "Tu", "wednesday": "We", "thursday": "Th", "friday": "Fr", "saturday": "Sa", "sunday": "Su",
    "mo": "Mo", "tu": "Tu", "we": "We", "th": "Th", "fr": "Fr", "sa": "Sa", "su": "Su",
    "mon": "Mo", "tue": "Tu", "wed": "We", "thu": "Th", "fri": "Fr", "sat": "Sa", "sun": "Su",
}
HOURS_WORDS = ("öffnungszeiten", "opening hours", "opening times", "hours", "geöffnet", "orari", "horaires",
               "horario", "openingstijden", "otevírací", "godziny otwarcia", "ruhetag", "küchenzeiten")
MAX_PAGE_BYTES = 1_500_000


@dataclass(frozen=True)
class Rule:
    day: str      # "Mo".."Su"
    open: str     # "HH:MM"
    close: str    # "HH:MM" (may be <= open for overnight, or "24:00")


# --------------------------------------------------------------------------- OSM builder

def _norm_time(t: str) -> str | None:
    m = re.match(r"^\s*(\d{1,2})[:.h]?(\d{2})?", str(t))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 24 or mi > 59 or (h == 24 and mi):
        return None
    return f"{h:02d}:{mi:02d}"


def build_osm(rules: list[Rule]) -> str | None:
    """Group identical day schedules into ranges: 'Mo-Fr 11:00-22:00; Sa 12:00-23:00'."""
    per_day: dict[str, list[str]] = {d: [] for d in DAYS}
    for r in rules:
        o, c = _norm_time(r.open), _norm_time(r.close)
        if r.day not in per_day or not o or not c or o == c:
            continue
        span = f"{o}-{c if c != '00:00' else '24:00'}"
        if span not in per_day[r.day]:
            per_day[r.day].append(span)
    for d in per_day:
        per_day[d].sort()
    if not any(per_day.values()):
        return None
    parts: list[str] = []
    i = 0
    while i < 7:
        spans = per_day[DAYS[i]]
        j = i
        while j + 1 < 7 and per_day[DAYS[j + 1]] == spans:
            j += 1
        if spans:
            days = DAYS[i] if i == j else (f"{DAYS[i]},{DAYS[j]}" if j == i + 1 else f"{DAYS[i]}-{DAYS[j]}")
            parts.append(f"{days} {','.join(spans)}")
        i = j + 1
    return "; ".join(parts)


# --------------------------------------------------------------------------- HTTP

_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def allowed(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as r:
                rp.parse(r.read(200_000).decode("utf-8", "replace").splitlines())
            _robots[base] = rp
        except Exception:
            _robots[base] = None  # no robots.txt → allowed
    rp = _robots[base]
    return rp is None or rp.can_fetch(USER_AGENT, url)


_browser = None  # lazily started Playwright Chromium (fallback for JavaScript-rendered sites)


def _render(url: str) -> str | None:
    """Render a JavaScript site (Meteor, React, Wix, …) with headless Chromium. Fallback only:
    installed and started on first use; returns None if Playwright is unavailable."""
    global _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        if _browser is None:
            import subprocess
            import sys
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium-headless-shell"],
                           check=False, capture_output=True, timeout=240)
            _browser = sync_playwright().start().chromium.launch()
        page = _browser.new_page(user_agent=USER_AGENT, locale="de-AT")
        try:
            page.goto(url, wait_until="networkidle", timeout=20_000)
            page.wait_for_timeout(1500)
            return page.content()
        finally:
            page.close()
    except Exception as e:
        print(f"render failed {url}: {e}", flush=True)
        return None


def get(url: str) -> str | None:
    """Fetch a page; falls back to headless rendering when the HTML has almost no visible text."""
    html = _get_raw(url)
    if html is not None and len(visible_text(html)) < 300 and "<html" in html[:2000].lower():
        rendered = _render(url)
        if rendered and len(visible_text(rendered)) > len(visible_text(html)):
            return rendered
    return html


def _get_raw(url: str) -> str | None:
    if not allowed(url):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "json" not in ctype:
                return None
            return r.read(MAX_PAGE_BYTES).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:
        return None


# --------------------------------------------------------------------------- JSON-LD / microdata

def _iter_jsonld(page: str):
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, dict):
                yield x
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))


def _days_from(value) -> list[str]:
    vals = value if isinstance(value, list) else [value]
    out = []
    for v in vals:
        key = str(v).rstrip("/").split("/")[-1].lower()
        if key in _DAY_NAMES:
            out.append(_DAY_NAMES[key])
    return out


def _expand_days(spec: str) -> list[str]:
    """'Mo-Fr' / 'Mo,We' / 'Mo' (schema.org openingHours short syntax)."""
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (_DAY_NAMES.get(x.strip().lower()[:3]) or _DAY_NAMES.get(x.strip().lower()[:2]) for x in part.split("-", 1))
            if a and b:
                i, j = DAYS.index(a), DAYS.index(b)
                out += [DAYS[(i + k) % 7] for k in range((j - i) % 7 + 1)]
        else:
            d = _DAY_NAMES.get(part.lower()[:3]) or _DAY_NAMES.get(part.lower()[:2])
            if d:
                out.append(d)
    return out


def rules_from_jsonld(page: str) -> list[Rule]:
    rules: list[Rule] = []
    for obj in _iter_jsonld(page):
        for spec in (obj.get("openingHoursSpecification") or []) if isinstance(obj.get("openingHoursSpecification"), list) else [obj.get("openingHoursSpecification")] if obj.get("openingHoursSpecification") else []:
            if not isinstance(spec, dict) or spec.get("validFrom") or spec.get("validThrough"):
                continue  # seasonal/special hours: skip
            for d in _days_from(spec.get("dayOfWeek")):
                if spec.get("opens") and spec.get("closes"):
                    rules.append(Rule(d, str(spec["opens"]), str(spec["closes"])))
        oh = obj.get("openingHours")
        for s in (oh if isinstance(oh, list) else [oh] if isinstance(oh, str) else []):
            m = re.match(r"^\s*([A-Za-z,\- ]+)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$", s)
            if m:
                for d in _expand_days(m.group(1)):
                    rules.append(Rule(d, m.group(2), m.group(3)))
    # microdata: <meta itemprop="openingHours" content="Mo-Fr 11:00-22:00">
    for m in re.finditer(r'itemprop=["\']openingHours["\'][^>]*content=["\']([^"\']+)["\']', page, re.I):
        mm = re.match(r"^\s*([A-Za-z,\- ]+)\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", m.group(1))
        if mm:
            for d in _expand_days(mm.group(1)):
                rules.append(Rule(d, mm.group(2), mm.group(3)))
    return rules


# --------------------------------------------------------------------------- website text → LLM

def visible_text(page: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", page)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h\d)[^>]*>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    lines = [re.sub(r"[ \t ]+", " ", l).strip() for l in s.splitlines()]
    return "\n".join(l for l in lines if l)


def hours_snippets(text: str, window: int = 700) -> str:
    """Text around hours keywords (keeps LLM input small and cheap)."""
    low = text.lower()
    spans = []
    for w in HOURS_WORDS:
        for m in re.finditer(re.escape(w), low):
            spans.append((max(0, m.start() - 150), min(len(text), m.start() + window)))
    if not spans:
        return ""
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return "\n---\n".join(text[a:b] for a, b in merged)[:4000]


LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["found", "rules"],
    "properties": {
        "found": {"type": "boolean", "description": "true only if the text states the regular weekly opening hours of this restaurant"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["days", "open", "close"],
                "properties": {
                    "days": {"type": "array", "items": {"type": "string", "enum": DAYS}},
                    "open": {"type": "string", "description": "HH:MM, 24h"},
                    "close": {"type": "string", "description": "HH:MM, 24h; 24:00 for midnight"},
                },
            },
        },
    },
}
LLM_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "mistralai/mistral-nemo"]


def rules_from_llm(snippets: str, name: str, api_key: str) -> list[Rule]:
    body = {
        "models": LLM_MODELS,
        "messages": [
            {"role": "system", "content": "Extract the regular weekly opening hours of the named restaurant from website text. "
                                          "Ignore kitchen-only hours, delivery hours, holiday/seasonal exceptions and other businesses. "
                                          "If the hours are not clearly stated, return found=false and no rules."},
            {"role": "user", "content": f"Restaurant: {name}\n\nWebsite text:\n{snippets}"},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "opening_hours", "strict": True, "schema": LLM_SCHEMA}},
        "temperature": 0,
        "max_tokens": 600,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://github.com/christianweinmayr/hungrymap-packs", "X-Title": "Hungry Map hours"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    content = out["choices"][0]["message"]["content"]
    data = json.loads(content) if isinstance(content, str) else content
    if not data.get("found"):
        return []
    return [Rule(d, r["open"], r["close"]) for r in data.get("rules", []) for d in r.get("days", [])]


# --------------------------------------------------------------------------- HERE

def _similar(a: str, b: str) -> float:
    ta = set(re.findall(r"\w+", a.lower())) - {"restaurant", "cafe", "café", "bar", "gasthaus", "pizzeria"}
    tb = set(re.findall(r"\w+", b.lower())) - {"restaurant", "cafe", "café", "bar", "gasthaus", "pizzeria"}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    return math.hypot((lat2 - lat1) * 111_000, (lon2 - lon1) * 111_000 * math.cos(math.radians(lat1)))


def rules_from_here(name: str, lat: float, lon: float, api_key: str) -> list[Rule]:
    q = urllib.parse.urlencode({"at": f"{lat},{lon}", "q": name, "limit": 5, "apiKey": api_key})
    with urllib.request.urlopen(f"https://discover.search.hereapi.com/v1/discover?{q}", timeout=15) as r:
        items = json.load(r).get("items", [])
    best = None
    for it in items:
        pos = it.get("position") or {}
        if "lat" not in pos or _dist_m(lat, lon, pos["lat"], pos["lng"]) > 150:
            continue
        score = _similar(name, it.get("title", ""))
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, it)
    if not best:
        return []
    rules: list[Rule] = []
    for oh in best[1].get("openingHours", []):
        for s in oh.get("structured", []):
            m = re.match(r"T(\d{2})(\d{2})\d{2}", s.get("start", ""))
            dur = re.match(r"PT(\d{1,2})H(\d{2})M", s.get("duration", ""))
            days = re.search(r"BYDAY:([A-Z,]+)", s.get("recurrence", ""))
            if not (m and dur and days):
                continue
            start = int(m.group(1)) * 60 + int(m.group(2))
            end = start + int(dur.group(1)) * 60 + int(dur.group(2))
            close = f"{(end // 60) % 24:02d}:{end % 60:02d}" if end < 24 * 60 or end % (24 * 60) else "24:00"
            for d in days.group(1).split(","):
                if d.title()[:2] in DAYS:
                    rules.append(Rule(d.title()[:2], f"{m.group(1)}:{m.group(2)}", close))
        break  # first entry = general opening hours
    return rules


# --------------------------------------------------------------------------- orchestration

def candidate_pages(site: str) -> list[str]:
    home = get(site)
    if home is None:
        return []
    pages = [home]
    links = re.findall(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', home, re.S | re.I)
    scored = []
    for href, label in links:
        hay = (href + " " + re.sub("<[^>]+>", " ", label)).lower()
        if any(w in hay for w in ("öffnungszeit", "opening", "hours", "kontakt", "contact", "orari", "horaires", "about", "über uns")):
            url = urllib.parse.urljoin(site, href)
            if urllib.parse.urlsplit(url).netloc == urllib.parse.urlsplit(site).netloc:
                scored.append(url)
    for url in list(dict.fromkeys(scored))[:2]:
        time.sleep(1)  # politeness per domain
        page = get(url)
        if page:
            pages.append(page)
    return pages


def research(place: dict, openrouter_key: str | None, here_key: str | None) -> dict:
    key, name = place["key"], place["name"]
    site = place.get("website")
    try:
        if site:
            pages = candidate_pages(site)
            rules = [r for p in pages for r in rules_from_jsonld(p)]
            if (oh := build_osm(rules)):
                return {"key": key, "status": "found", "opening_hours": oh, "source": "website", "source_url": site}
            if openrouter_key:
                snippets = "\n---\n".join(filter(None, (hours_snippets(visible_text(p)) for p in pages)))[:6000]
                if snippets:
                    if (oh := build_osm(rules_from_llm(snippets, name, openrouter_key))):
                        return {"key": key, "status": "found", "opening_hours": oh, "source": "website", "source_url": site}
        if here_key:
            if (oh := build_osm(rules_from_here(name, place["lat"], place["lon"], here_key))):
                return {"key": key, "status": "found", "opening_hours": oh, "source": "here", "source_url": None}
        return {"key": key, "status": "none"}
    except Exception as e:  # network/model hiccup → retry later
        return {"key": key, "status": "error", "error": str(e)[:200]}


def run_queue(base_url: str, token: str, limit: int, deadline_s: float) -> dict:
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or None
    here_key = os.environ.get("HERE_API_KEY") or None
    auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": USER_AGENT}
    with urllib.request.urlopen(urllib.request.Request(f"{base_url}/v1/queue?limit={limit}", headers=auth), timeout=30) as r:
        places = json.load(r)["places"]
    t0 = time.monotonic()
    results = []
    for p in places:
        if time.monotonic() - t0 > deadline_s:
            break
        res = research(p, openrouter_key, here_key)
        print(f"{res['status']:6} {p['key']:24} {p['name'][:40]:40} {res.get('opening_hours', res.get('error', ''))}", flush=True)
        results.append({k: v for k, v in res.items() if k != "error"})
    if results:
        req = urllib.request.Request(f"{base_url}/v1/results", data=json.dumps({"results": results}).encode(), headers=auth, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    summary = {s: sum(1 for x in results if x["status"] == s) for s in ("found", "none", "error")}
    print("summary", summary, f"of {len(places)} queued, {time.monotonic() - t0:.0f}s", flush=True)
    return summary
