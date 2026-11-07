#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bing Keyword Pro  -  Professional Bing keyword research + site audit tool
Data source : Bing Webmaster Tools API (JSON)  +  Bing Autosuggest
Run         : python app.py      (then open http://127.0.0.1:8765)
No external packages needed (Python 3.8+ standard library only).
"""
import json, os, re, sys, time, math, sqlite3, threading, webbrowser, string
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor as _TPE
import threading, hashlib

# Per-request settings sent by the browser (keys are stored in the user's browser, not on the server)
REQ = threading.local()

class ThreadPoolExecutor(_TPE):
    """Thread pool that carries the current request's browser settings into worker threads."""
    def _wrap(self, fn):
        ov = getattr(REQ, "ov", None)
        def run(*a, **k):
            REQ.ov = ov
            try:
                return fn(*a, **k)
            finally:
                REQ.ov = None
        return run
    def submit(self, fn, *a, **k):
        return super().submit(self._wrap(fn), *a, **k)
    def map(self, fn, *its, **k):
        return super().map(self._wrap(fn), *its, **k)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(APP_DIR, "static")
DATA_DIR = os.environ.get("BKP_DATA_DIR", APP_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
DB_FILE = os.path.join(DATA_DIR, "cache.db")
PORT = int(os.environ.get("PORT", os.environ.get("BKP_PORT", "8765")))
HOST = os.environ.get("BKP_HOST", "127.0.0.1")          # use 0.0.0.0 on a server
PASSWORD = os.environ.get("BKP_PASSWORD", "")            # set this when hosting online
API_BASE = "https://ssl.bing.com/webmaster/api.svc/json/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BingKeywordPro/1.0"
CACHE_TTL = 7 * 24 * 3600          # keyword data cache: 7 days
SITE_CACHE_TTL = 6 * 3600          # site data cache: 6 hours
API_WORKERS = 4                    # parallel Bing API calls (be gentle with quota)

# --------------------------------------------------------------------------- markets
MARKETS = [
    ("us", "en-US", "United States"), ("gb", "en-GB", "United Kingdom"), ("in", "en-IN", "India (English)"),
    ("in", "hi-IN", "India (Hindi)"), ("ca", "en-CA", "Canada (English)"), ("ca", "fr-CA", "Canada (French)"),
    ("au", "en-AU", "Australia"), ("nz", "en-NZ", "New Zealand"), ("ie", "en-IE", "Ireland"),
    ("za", "en-ZA", "South Africa"), ("sg", "en-SG", "Singapore"), ("my", "en-MY", "Malaysia"),
    ("ph", "en-PH", "Philippines"), ("id", "id-ID", "Indonesia"), ("pk", "en-PK", "Pakistan"),
    ("ae", "en-AE", "UAE (English)"), ("sa", "ar-SA", "Saudi Arabia"), ("eg", "ar-EG", "Egypt"),
    ("de", "de-DE", "Germany"), ("at", "de-AT", "Austria"), ("ch", "de-CH", "Switzerland (German)"),
    ("ch", "fr-CH", "Switzerland (French)"), ("fr", "fr-FR", "France"), ("be", "nl-BE", "Belgium (Dutch)"),
    ("be", "fr-BE", "Belgium (French)"), ("nl", "nl-NL", "Netherlands"), ("es", "es-ES", "Spain"),
    ("it", "it-IT", "Italy"), ("pt", "pt-PT", "Portugal"), ("br", "pt-BR", "Brazil"),
    ("mx", "es-MX", "Mexico"), ("ar", "es-AR", "Argentina"), ("cl", "es-CL", "Chile"),
    ("co", "es-CO", "Colombia"), ("us", "es-US", "United States (Spanish)"), ("se", "sv-SE", "Sweden"),
    ("no", "nb-NO", "Norway"), ("dk", "da-DK", "Denmark"), ("fi", "fi-FI", "Finland"),
    ("pl", "pl-PL", "Poland"), ("cz", "cs-CZ", "Czech Republic"), ("tr", "tr-TR", "Turkey"),
    ("ru", "ru-RU", "Russia"), ("jp", "ja-JP", "Japan"), ("kr", "ko-KR", "South Korea"),
    ("cn", "zh-CN", "China"), ("tw", "zh-TW", "Taiwan"), ("hk", "zh-HK", "Hong Kong"),
]
# "Worldwide" = sum of these major Bing markets (editable in Settings)
DEFAULT_WORLD = ["us|en-US", "gb|en-GB", "in|en-IN", "ca|en-CA", "au|en-AU", "de|de-DE", "fr|fr-FR",
                 "jp|ja-JP", "br|pt-BR", "es|es-ES", "it|it-IT", "nl|nl-NL", "mx|es-MX", "cn|zh-CN",
                 "se|sv-SE", "pl|pl-PL", "tr|tr-TR", "za|en-ZA", "sg|en-SG", "ie|en-IE", "nz|en-NZ",
                 "ch|de-CH", "be|nl-BE", "at|de-AT", "ph|en-PH", "my|en-MY", "id|id-ID", "sa|ar-SA",
                 "kr|ko-KR", "tw|zh-TW"]

# --------------------------------------------------------------------------- config / db
_lock = threading.Lock()

def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg.setdefault("accounts", [])
    cfg.setdefault("active", None)
    cfg.setdefault("world_markets", DEFAULT_WORLD)
    cfg.setdefault("indexnow_keys", {})
    # migrate old single-key format
    if cfg.get("apikey") and not cfg["accounts"]:
        cfg["accounts"].append({"id": "acc1", "name": "Main account", "apikey": cfg["apikey"]})
        cfg["active"] = "acc1"
    cfg.pop("apikey", None)
    ov = getattr(REQ, "ov", None)
    if isinstance(ov, dict):
        for k in ("accounts", "active", "world_markets", "indexnow_keys", "serpapi_key"):
            if ov.get(k) is not None:
                cfg[k] = ov[k]
        cfg["accounts"] = [a for a in cfg["accounts"] if isinstance(a, dict) and a.get("apikey")]
    if cfg["accounts"] and not any(a["id"] == cfg["active"] for a in cfg["accounts"]):
        cfg["active"] = cfg["accounts"][0]["id"]
    return cfg

def get_account(account_id=None):
    cfg = load_config()
    accs = cfg["accounts"]
    for a in accs:
        if a["id"] == (account_id or cfg["active"]):
            return a
    return accs[0] if accs else None

def mask(k):
    return (k[:4] + "…" + k[-4:]) if len(k) > 8 else "…"

def save_config(cfg):
    with _lock:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

def db():
    con = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT, t REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, q TEXT, market TEXT, t REAL)")
    return con

def cache_get(key, ttl):
    con = db()
    try:
        row = con.execute("SELECT v,t FROM cache WHERE k=?", (key,)).fetchone()
        if row and time.time() - row[1] < ttl:
            return json.loads(row[0])
    finally:
        con.close()
    return None

def cache_set(key, val):
    with _lock:
        con = db()
        try:
            con.execute("REPLACE INTO cache (k,v,t) VALUES (?,?,?)", (key, json.dumps(val), time.time()))
            con.commit()
        finally:
            con.close()

def add_history(kind, q, market):
    with _lock:
        con = db()
        try:
            con.execute("INSERT INTO history (kind,q,market,t) VALUES (?,?,?,?)", (kind, q, market, time.time()))
            con.commit()
        finally:
            con.close()

# --------------------------------------------------------------------------- Bing API
class BingError(Exception):
    pass

def _http(url, data=None, headers=None, timeout=40):
    h = {"User-Agent": UA, "Accept": "application/json, */*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def bing_api(method, params=None, post=None, ttl=CACHE_TTL, use_cache=True, account=None, apikey=None):
    acc = None if apikey else get_account(account)
    key = (apikey or (acc or {}).get("apikey") or "").strip()
    if not key:
        raise BingError("API key missing. 'API Accounts' tab me Bing Webmaster API key jodein.")
    params = dict(params or {})
    ck = "api:" + method + ":" + (hashlib.sha256(key.encode()).hexdigest()[:16] + ":" if (account or apikey) else "") + json.dumps(params, sort_keys=True)
    if use_cache and post is None:
        c = cache_get(ck, ttl)
        if c is not None:
            return c
    params["apikey"] = key
    url = API_BASE + method + "?" + urllib.parse.urlencode(params)
    data, headers = None, None
    if post is not None:
        data = json.dumps(post).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
    last = None
    for attempt in range(3):
        try:
            body = _http(url, data, headers)
            res = json.loads(body) if body.strip() else {}
            val = res.get("d", res) if isinstance(res, dict) else res
            if use_cache and post is None:
                cache_set(ck, val)
            return val
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try:
                j = json.loads(txt)
                msg = j.get("Message") or txt
            except Exception:
                msg = txt[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1)); last = msg; continue
            raise BingError(f"{method}: {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
            time.sleep(1.5 * (attempt + 1))
    raise BingError(f"{method}: network error ({last})")

def parse_wcf_date(s):
    if not s:
        return None
    m = re.search(r"/Date\((-?\d+)([+-]\d{4})?\)/", str(s))
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

def date_range(months):
    end = datetime.now(timezone.utc).date() - timedelta(days=3)   # Bing data lags a few days
    days = int(round(30.44 * months))
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat(), days

# --------------------------------------------------------------------------- scoring
COMMERCIAL = set("""buy price prices cost cheap cheapest best top review reviews vs versus compare comparison deal deals
discount coupon coupons offer offers sale order purchase shop shopping store hire service services agency company
software tool tools app apps hosting insurance loan loans lawyer attorney credit mortgage casino bet betting vpn
course courses rent rental booking book hotel hotels flight flights ticket tickets near kharido kimat sasta""".split())
TRANSACTIONAL = set("buy order purchase coupon coupons discount deal deals cheap price download subscribe booking book rent hire kharido sasta kimat".split())
INFORMATIONAL = set("""what how why when where who which is are can does do guide tutorial tips meaning definition
example examples ideas learn kya kaise kyon kab kahan kaun matlab""".split())
NAVIGATIONAL = set("login signin sign-in website official app download com net org portal account dashboard".split())
QUESTION_WORDS = ("what", "how", "why", "when", "where", "who", "which", "is ", "are ", "can ", "does ", "do ",
                  "will ", "should ", "kya", "kaise", "kyon", "kab", "kahan", "kaun")

def intent_of(q):
    w = re.findall(r"[\w\-]+", q.lower())
    s = set(w)
    if s & NAVIGATIONAL or re.search(r"\.(com|net|org|in|io)\b", q.lower()):
        return "Navigational"
    if s & TRANSACTIONAL:
        return "Transactional"
    if s & COMMERCIAL:
        return "Commercial"
    if s & INFORMATIONAL or q.lower().startswith(QUESTION_WORDS):
        return "Informational"
    return "Informational" if len(w) >= 4 else "Commercial"

def is_question(q):
    ql = q.lower().strip()
    return ql.startswith(QUESTION_WORDS) or ql.endswith("?")

def kd_score(q, monthly, broad_monthly, suggest_count=None):
    """Keyword Difficulty estimate 0-100 (Bing does not publish KD, so this is a modelled estimate)."""
    words = len(q.split())
    vol = min(1.0, math.log10(monthly + 1) / 5.0)                      # 100k/mo -> 1.0
    head = {1: 1.0, 2: 0.75, 3: 0.5, 4: 0.3}.get(words, 0.15)
    ratio = (broad_monthly / monthly) if monthly > 0 else 1.0
    spread = min(1.0, math.log10(ratio + 1) / 1.5)
    sugg = min(1.0, (suggest_count if suggest_count is not None else 4) / 8.0)
    comm = 1.0 if set(q.lower().split()) & COMMERCIAL else 0.0
    kd = 100 * (0.40 * vol + 0.22 * head + 0.13 * spread + 0.10 * sugg + 0.15 * comm)
    return int(max(1, min(100, round(kd))))

def kd_label(kd):
    return ("Very easy" if kd < 15 else "Easy" if kd < 30 else "Possible" if kd < 50 else
            "Difficult" if kd < 70 else "Hard" if kd < 85 else "Very hard")

def competition(q, monthly, kd):
    comm = 1.0 if set(q.lower().split()) & COMMERCIAL else 0.0
    score = 0.5 * comm + 0.3 * min(1.0, math.log10(monthly + 1) / 5) + 0.2 * kd / 100
    level = "High" if score >= 0.55 else "Medium" if score >= 0.3 else "Low"
    return round(score, 2), level

def enrich(row, suggest_count=None):
    m, b = row.get("volume", 0), row.get("broad", 0)
    kd = kd_score(row["keyword"], m, b, suggest_count)
    cs, cl = competition(row["keyword"], m, kd)
    row.update({"kd": kd, "kd_label": kd_label(kd), "competition": cl, "comp_index": cs,
                "intent": intent_of(row["keyword"]), "question": is_question(row["keyword"]),
                "words": len(row["keyword"].split()),
                "opportunity": int(round(min(100, (math.log10(m + 1) * 22) * (1 - kd / 120))))})
    return row

# --------------------------------------------------------------------------- keyword data
def kw_volume(q, country, lang, months=1):
    start, end, days = date_range(months)
    d = bing_api("GetKeyword", {"q": q, "country": country, "language": lang, "startDate": start, "endDate": end})
    imp = (d or {}).get("Impressions", 0) or 0
    br = (d or {}).get("BroadImpressions", 0) or 0
    f = 30.44 / days
    return {"keyword": q, "volume": int(round(imp * f)), "broad": int(round(br * f)),
            "period_impressions": imp, "period_days": days}

def kw_trend(q, country, lang):
    d = bing_api("GetKeywordStats", {"q": q, "country": country, "language": lang}) or []
    out = []
    for x in d:
        out.append({"date": parse_wcf_date(x.get("Date")), "impressions": x.get("Impressions", 0) or 0,
                    "broad": x.get("BroadImpressions", 0) or 0})
    out.sort(key=lambda r: r["date"] or "")
    return out

def kw_related(q, country, lang, months=1):
    start, end, days = date_range(months)
    d = bing_api("GetRelatedKeywords", {"q": q, "country": country, "language": lang,
                                         "startDate": start, "endDate": end}) or []
    f = 30.44 / days
    return [{"keyword": x.get("Query", ""), "volume": int(round((x.get("Impressions", 0) or 0) * f)),
             "broad": int(round((x.get("BroadImpressions", 0) or 0) * f)), "source": "Bing related"}
            for x in d if x.get("Query")]

def suggest(q, mkt):
    ck = "sug:" + mkt + ":" + q
    c = cache_get(ck, CACHE_TTL)
    if c is not None:
        return c
    try:
        url = "https://api.bing.com/osjson.aspx?" + urllib.parse.urlencode({"query": q, "mkt": mkt})
        res = json.loads(_http(url, timeout=15))
        out = [s for s in (res[1] if len(res) > 1 else []) if isinstance(s, str)]
    except Exception:
        out = []
    cache_set(ck, out)
    return out

def expand_suggestions(seed, mkt, deep=True):
    queries = [seed]
    if deep:
        queries += [f"{seed} {c}" for c in string.ascii_lowercase]
        queries += [f"{w} {seed}" for w in ("what", "how", "why", "best", "is", "can", "where", "which", "when", "who")]
        queries += [f"{seed} {w}" for w in ("for", "vs", "near me", "without", "with", "free", "price", "2026")]
    found = {}
    with ThreadPoolExecutor(8) as ex:
        for qq, res in zip(queries, ex.map(lambda x: suggest(x, mkt), queries)):
            for s in res:
                s = s.strip().lower()
                if s and s != seed.lower():
                    found.setdefault(s, 0)
                    found[s] += 1
    return found

def market_split(market):
    """market = 'us|en-US' or 'world'"""
    if market == "world":
        return [tuple(m.split("|")) for m in load_config().get("world_markets", DEFAULT_WORLD)]
    c, l = market.split("|")
    return [(c, l)]

def overview(q, market, months):
    markets = market_split(market)
    def one(m):
        c, l = m
        try:
            v = kw_volume(q, c, l, months)
            t = kw_trend(q, c, l)
            return {"country": c, "language": l, **v, "trend": t, "error": None}
        except BingError as e:
            return {"country": c, "language": l, "keyword": q, "volume": 0, "broad": 0, "trend": [], "error": str(e)}
    with ThreadPoolExecutor(API_WORKERS) as ex:
        per = list(ex.map(one, markets))
    errs = [p["error"] for p in per if p["error"]]
    if errs and len(errs) == len(per):
        raise BingError(errs[0])
    total = sum(p["volume"] for p in per)
    broad = sum(p["broad"] for p in per)
    # merge trends by date
    tr = {}
    for p in per:
        for w in p["trend"]:
            if w["date"]:
                t = tr.setdefault(w["date"], {"date": w["date"], "impressions": 0, "broad": 0})
                t["impressions"] += w["impressions"]; t["broad"] += w["broad"]
    trend = sorted(tr.values(), key=lambda r: r["date"])
    # if GetKeyword returned 0 but trend exists, derive monthly from last 4 weeks
    if total == 0 and trend:
        last = trend[-4:]
        total = int(round(sum(x["impressions"] for x in last) / len(last) * 4.345))
        broad = int(round(sum(x["broad"] for x in last) / len(last) * 4.345))
    change = None
    if len(trend) >= 8:
        a = sum(x["impressions"] for x in trend[-4:]); b = sum(x["impressions"] for x in trend[-8:-4])
        change = round((a - b) / b * 100, 1) if b else None
    yoy_peak = max((x["impressions"] for x in trend), default=0)
    sug_count = len(suggest(q, markets[0][1]))
    row = enrich({"keyword": q, "volume": total, "broad": broad}, sug_count)
    countries = sorted([{"country": p["country"], "language": p["language"], "volume": p["volume"],
                         "broad": p["broad"], "error": p["error"]} for p in per], key=lambda r: -r["volume"])
    add_history("overview", q, market)
    return {"metrics": row, "trend": trend, "trend_change_4w": change, "peak_week": yoy_peak,
            "countries": countries, "months": months, "market": market}

# --------------------------------------------------------------------------- SERP top 10 (via SerpApi Bing engine)
SERP_TTL = 24 * 3600

def serp_top(q, market, n=10, fresh=False):
    key = (load_config().get("serpapi_key") or "").strip()
    lang = "en-US" if market == "world" else market.split("|")[1]
    bing_url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": q, "setmkt": lang})
    if not key:
        raise BingError("SERP ke liye Settings me SerpApi key daalein (free: 250 searches/month).")
    ck = "serp:" + lang + ":" + q
    c = None if fresh else cache_get(ck, SERP_TTL)
    if c is not None:
        return {**c, "cached": True}
    rows, seen, first, calls = [], set(), 1, 0
    while len(rows) < n and calls < 2:
        params = {"engine": "bing", "q": q, "mkt": lang, "api_key": key}
        if first > 1:
            params["first"] = first
        try:
            d = json.loads(_http("https://serpapi.com/search.json?" + urllib.parse.urlencode(params), timeout=60))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8", "replace")).get("error")
            except Exception:
                msg = None
            raise BingError("SerpApi: " + (msg or ("HTTP %s" % e.code)))
        calls += 1
        if d.get("error"):
            if rows:
                break
            raise BingError("SerpApi: " + str(d["error"]))
        org = d.get("organic_results") or []
        if not org:
            break
        for o in org:
            u = o.get("link") or ""
            if not u.startswith("http") or u in seen:
                continue
            seen.add(u)
            rows.append({"position": len(rows) + 1, "url": u, "title": o.get("title") or "",
                         "domain": urllib.parse.urlparse(u).netloc.lower().replace("www.", "", 1),
                         "snippet": (o.get("snippet") or "")[:240], "date": o.get("date") or ""})
            if len(rows) >= n:
                break
        first += len(org)
    out = {"keyword": q, "market": lang, "rows": rows, "bing_url": bing_url,
           "fetched": datetime.now().strftime("%Y-%m-%d %H:%M"), "searches_used": calls}
    if rows:
        cache_set(ck, out)
    return {**out, "cached": False}

def ideas(q, market, months, limit, deep, include_related=True, include_suggest=True):
    markets = market_split(market)
    primary = markets[0]
    pool = {}
    notes = []
    if include_related:
        for c, l in (markets if market != "world" else markets[:6]):
            try:
                for r in kw_related(q, c, l, months):
                    k = r["keyword"].lower()
                    p = pool.setdefault(k, {"keyword": k, "source": "Bing related", "sugg": 0})
            except BingError as e:
                notes.append(str(e))
    sug = {}
    if include_suggest:
        sug = expand_suggestions(q, primary[1], deep)
        for k, n in sug.items():
            p = pool.setdefault(k, {"keyword": k, "source": "Bing autosuggest", "sugg": 0})
            p["sugg"] = n
            if p["source"] == "Bing related":
                p["source"] = "Related + Autosuggest"
    keys = list(pool.keys())
    # prioritise related ones and those seen most in suggestions
    keys.sort(key=lambda k: (pool[k]["source"] == "Bing autosuggest", -pool[k]["sugg"], len(k)))
    keys = keys[:limit]

    def vol_all(k):
        tot, br, err = 0, 0, None
        for c, l in markets:
            try:
                v = kw_volume(k, c, l, months); tot += v["volume"]; br += v["broad"]
            except BingError as e:
                err = str(e)
        return k, tot, br, err
    rows = []
    workers = API_WORKERS if market != "world" else 2
    with ThreadPoolExecutor(workers) as ex:
        for k, tot, br, err in ex.map(vol_all, keys):
            r = {"keyword": k, "volume": tot, "broad": br, "source": pool[k]["source"]}
            enrich(r, min(8, pool[k]["sugg"] * 2) if pool[k]["sugg"] else None)
            rows.append(r)
            if err and len(notes) < 3:
                notes.append(err)
    rows.sort(key=lambda r: -r["volume"])
    add_history("ideas", q, market)
    return {"seed": q, "rows": rows, "total_found": len(pool), "notes": list(dict.fromkeys(notes))[:3]}

def bulk(keywords, market, months):
    markets = market_split(market)
    kws = list(dict.fromkeys([k.strip().lower() for k in keywords if k.strip()]))[:1000]
    def one(k):
        tot, br, err = 0, 0, None
        for c, l in markets:
            try:
                v = kw_volume(k, c, l, months); tot += v["volume"]; br += v["broad"]
            except BingError as e:
                err = str(e)
        r = enrich({"keyword": k, "volume": tot, "broad": br, "source": "Bulk"})
        r["error"] = err
        return r
    with ThreadPoolExecutor(API_WORKERS if market != "world" else 2) as ex:
        rows = list(ex.map(one, kws))
    add_history("bulk", f"{len(kws)} keywords", market)
    return {"rows": rows}

# --------------------------------------------------------------------------- site audit
def norm_site(s):
    s = (s or "").strip()
    if s and not s.startswith("http"):
        s = "https://" + s
    if s and not s.endswith("/"):
        s += "/"
    return s

def user_sites(fresh=False):
    out, errors = [], []
    for a in load_config()["accounts"]:
        try:
            d = bing_api("GetUserSites", ttl=600, account=a["id"], use_cache=not fresh) or []
            for x in d:
                out.append({"url": x.get("Url"), "verified": x.get("IsVerified"),
                            "account": a["id"], "account_name": a["name"]})
        except BingError as e:
            errors.append(f'{a["name"]}: {e}')
    return {"sites": out, "errors": errors}

def account_for_site(site):
    """Find which saved API account owns this site."""
    s = site.rstrip("/").lower().replace("http://", "").replace("https://", "").replace("www.", "")
    for x in user_sites()["sites"]:
        u = (x["url"] or "").rstrip("/").lower().replace("http://", "").replace("https://", "").replace("www.", "")
        if u == s:
            return x["account"]
    return None

def site_queries(site, acct=None):
    d = bing_api("GetQueryStats", {"siteUrl": site}, ttl=SITE_CACHE_TTL, account=acct) or []
    agg = {}
    for x in d:
        q = x.get("Query") or ""
        a = agg.setdefault(q, {"query": q, "impressions": 0, "clicks": 0, "_pos": 0.0, "_cpos": 0.0,
                               "_cw": 0, "weeks": 0, "last_date": None, "positions": []})
        imp = x.get("Impressions", 0) or 0
        clk = x.get("Clicks", 0) or 0
        pos = x.get("AvgImpressionPosition", 0) or 0
        cpos = x.get("AvgClickPosition", 0) or 0
        dt = parse_wcf_date(x.get("Date"))
        a["impressions"] += imp; a["clicks"] += clk
        if pos and pos > 0:
            a["_pos"] += pos * max(imp, 1); a["_cw"] += max(imp, 1)
            a["positions"].append((dt, pos))
        if cpos and cpos > 0:
            a["_cpos"] += cpos * max(clk, 1)
        a["weeks"] += 1
        if dt and (a["last_date"] is None or dt > a["last_date"]):
            a["last_date"] = dt
    rows = []
    for a in agg.values():
        pos = a["_pos"] / a["_cw"] if a["_cw"] else None
        cpos = a["_cpos"] / a["clicks"] if a["clicks"] else None
        ps = sorted([p for p in a["positions"] if p[0]])
        latest = ps[-1][1] if ps else pos
        first = ps[0][1] if ps else pos
        rows.append({"query": a["query"], "impressions": a["impressions"], "clicks": a["clicks"],
                     "ctr": round(a["clicks"] / a["impressions"] * 100, 2) if a["impressions"] else 0,
                     "avg_position": round(pos, 1) if pos else None,
                     "latest_position": round(latest, 1) if latest else None,
                     "position_change": round(first - latest, 1) if (first and latest) else None,
                     "avg_click_position": round(cpos, 1) if cpos else None,
                     "last_date": a["last_date"], "intent": intent_of(a["query"])})
    rows.sort(key=lambda r: -r["impressions"])
    return rows

def site_pages(site, acct=None):
    d = bing_api("GetPageStats", {"siteUrl": site}, ttl=SITE_CACHE_TTL, account=acct) or []
    agg = {}
    for x in d:
        u = x.get("Query") or ""
        a = agg.setdefault(u, {"url": u, "impressions": 0, "clicks": 0, "_pos": 0.0, "_w": 0})
        imp = x.get("Impressions", 0) or 0
        a["impressions"] += imp; a["clicks"] += x.get("Clicks", 0) or 0
        p = x.get("AvgImpressionPosition", 0) or 0
        if p > 0:
            a["_pos"] += p * max(imp, 1); a["_w"] += max(imp, 1)
    rows = [{"url": a["url"], "impressions": a["impressions"], "clicks": a["clicks"],
             "ctr": round(a["clicks"] / a["impressions"] * 100, 2) if a["impressions"] else 0,
             "avg_position": round(a["_pos"] / a["_w"], 1) if a["_w"] else None} for a in agg.values()]
    rows.sort(key=lambda r: -r["impressions"])
    return rows

def site_query_pages(site, query, acct=None):
    d = bing_api("GetQueryPageStats", {"siteUrl": site, "query": query}, ttl=SITE_CACHE_TTL, account=acct) or []
    agg = {}
    for x in d:
        u = x.get("Query") or ""
        a = agg.setdefault(u, {"url": u, "impressions": 0, "clicks": 0, "_pos": 0.0, "_w": 0})
        imp = x.get("Impressions", 0) or 0
        a["impressions"] += imp; a["clicks"] += x.get("Clicks", 0) or 0
        p = x.get("AvgImpressionPosition", 0) or 0
        if p > 0:
            a["_pos"] += p * max(imp, 1); a["_w"] += max(imp, 1)
    return sorted([{"url": a["url"], "impressions": a["impressions"], "clicks": a["clicks"],
                    "avg_position": round(a["_pos"] / a["_w"], 1) if a["_w"] else None}
                   for a in agg.values()], key=lambda r: -r["impressions"])

def site_summary(site, acct=None):
    out = {"site": site}
    try:
        t = bing_api("GetRankAndTrafficStats", {"siteUrl": site}, ttl=SITE_CACHE_TTL, account=acct) or []
        traffic = sorted([{"date": parse_wcf_date(x.get("Date")), "impressions": x.get("Impressions", 0) or 0,
                           "clicks": x.get("Clicks", 0) or 0} for x in t], key=lambda r: r["date"] or "")
        out["traffic"] = traffic
    except BingError as e:
        out["traffic"] = []; out["traffic_error"] = str(e)
    try:
        c = bing_api("GetCrawlStats", {"siteUrl": site}, ttl=SITE_CACHE_TTL, account=acct) or []
        crawl = sorted([{"date": parse_wcf_date(x.get("Date")), "in_index": x.get("InIndex", 0) or 0,
                         "crawled": x.get("CrawledPages", 0) or 0, "errors": x.get("CrawlErrors", 0) or 0,
                         "in_links": x.get("InLinks", 0) or 0,
                         "blocked_robots": x.get("BlockedByRobotsTxt", 0) or 0,
                         "code_2xx": x.get("Code2xx", 0) or 0, "code_4xx": x.get("Code4xx", 0) or 0,
                         "code_5xx": x.get("Code5xx", 0) or 0} for x in c], key=lambda r: r["date"] or "")
        out["crawl"] = crawl
    except BingError as e:
        out["crawl"] = []; out["crawl_error"] = str(e)
    try:
        q = bing_api("GetUrlSubmissionQuota", {"siteUrl": site}, ttl=60, account=acct) or {}
        out["quota"] = {"daily": q.get("DailyQuota"), "monthly": q.get("MonthlyQuota")}
    except BingError as e:
        out["quota"] = None; out["quota_error"] = str(e)
    return out

CRAWL_ISSUE_FLAGS = {1: "Code 301", 2: "Code 302", 4: "Code 4xx", 8: "Code 5xx", 16: "Blocked by robots.txt",
                     32: "Contains malware", 64: "Important URL blocked by robots.txt", 128: "DNS errors",
                     256: "Timeout errors"}

def crawl_issues(site, acct=None):
    d = bing_api("GetCrawlIssues", {"siteUrl": site}, ttl=SITE_CACHE_TTL, account=acct) or []
    out = []
    for x in d:
        flags = x.get("Issues", 0) or 0
        names = [n for b, n in CRAWL_ISSUE_FLAGS.items() if flags & b] or ([str(flags)] if flags else [])
        out.append({"url": x.get("Url"), "issues": ", ".join(names), "http_code": x.get("HttpCode"),
                    "in_links": x.get("InLinks")})
    return out

def url_status(site, url, acct=None):
    try:
        d = bing_api("GetUrlInfo", {"siteUrl": site, "url": url}, ttl=SITE_CACHE_TTL, account=acct) or {}
    except BingError as e:
        msg = str(e)
        return {"url": url, "status": "Not in Bing index", "detail": msg.split(":", 1)[-1].strip(), "http": None,
                "last_crawled": None, "discovered": None, "indexed": False}
    http = d.get("HttpStatus")
    lc = parse_wcf_date(d.get("LastCrawledDate"))
    disc = parse_wcf_date(d.get("DiscoveryDate"))
    if lc and lc.startswith("0001"):
        lc = None
    if http == 200 and lc:
        st, idx = "Indexed / crawled", True
    elif lc:
        st, idx = f"Crawled (HTTP {http})", False
    elif disc:
        st, idx = "Discovered, not crawled", False
    else:
        st, idx = "Not in Bing index", False
    traffic = None
    try:
        t = bing_api("GetUrlTrafficInfo", {"siteUrl": site, "url": url}, ttl=SITE_CACHE_TTL, account=acct) or {}
        traffic = {"impressions": t.get("Impressions"), "clicks": t.get("Clicks")}
    except BingError:
        pass
    return {"url": url, "status": st, "indexed": idx, "http": http, "last_crawled": lc, "discovered": disc,
            "doc_size": d.get("DocumentSize"), "anchors": d.get("AnchorCount"), "traffic": traffic, "detail": ""}

def _parse_lastmod(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        if len(v) == 10:
            return datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def sitemap_entries(sitemap_url, cap=2000, _depth=0):
    """Returns [(url, lastmod_datetime_or_None)] from a sitemap or sitemap index."""
    out = []
    try:
        xml = _http(sitemap_url, timeout=40)
    except Exception as e:
        raise BingError(f"Sitemap fetch failed: {e}")
    xml = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", xml, count=0)
    try:
        root = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return [(u, None) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)[:cap]]
    if root.tag.endswith("sitemapindex") and _depth < 2:
        for sm in root.iter("sitemap"):
            if len(out) >= cap:
                break
            loc = sm.find("loc")
            if loc is None or not loc.text:
                continue
            try:
                out += sitemap_entries(loc.text.strip(), cap - len(out), _depth + 1)
            except BingError:
                pass
    else:
        for u in root.iter("url"):
            loc = u.find("loc")
            if loc is not None and loc.text:
                lm = u.find("lastmod")
                out.append((loc.text.strip(), _parse_lastmod(lm.text if lm is not None else "")))
        if not out:
            out = [(l.text.strip(), None) for l in root.iter("loc") if l.text]
    return out[:cap]

def sitemap_urls(sitemap_url, cap=2000, days=None):
    ents = sitemap_entries(sitemap_url, 50000 if days else cap)
    if days:
        cut = datetime.now(timezone.utc) - timedelta(days=float(days))
        ents = sorted([e for e in ents if e[1] and e[1] >= cut], key=lambda e: e[1], reverse=True)
    return [u for u, _ in ents][:cap]

def check_urls(site, urls, acct=None):
    urls = list(dict.fromkeys([u.strip() for u in urls if u.strip()]))[:2000]
    with ThreadPoolExecutor(API_WORKERS) as ex:
        rows = list(ex.map(lambda u: url_status(site, u, acct), urls))
    return {"rows": rows, "indexed": sum(1 for r in rows if r["indexed"]), "total": len(rows)}

def submit_urls(site, urls, acct=None):
    urls = [u.strip() for u in urls if u.strip()]
    if not urls:
        raise BingError("Koi URL nahi diya.")
    results = []
    for i in range(0, len(urls), 500):
        chunk = urls[i:i + 500]
        if len(chunk) == 1:
            bing_api("SubmitUrl", post={"siteUrl": site, "url": chunk[0]}, use_cache=False, account=acct)
        else:
            bing_api("SubmitUrlBatch", post={"siteUrl": site, "urlList": chunk}, use_cache=False, account=acct)
        results += chunk
    return {"submitted": len(results)}

INDEXNOW_ENGINES = [
    ("Bing", "https://www.bing.com/indexnow"),
    ("Yandex", "https://yandex.com/indexnow"),
    ("Seznam", "https://search.seznam.cz/indexnow"),
    ("Naver", "https://searchadvisor.naver.com/indexnow"),
    ("Yep", "https://indexnow.yep.com/indexnow"),
    ("IndexNow.org", "https://api.indexnow.org/indexnow"),
]

def _indexnow_key(site):
    host0 = urllib.parse.urlparse(site).netloc
    return (load_config().get("indexnow_keys", {}).get(host0) or "").strip()

def indexnow_keyfile_ok(site, key):
    """Checks that https://site/<key>.txt is live and contains the key (engines reject otherwise)."""
    try:
        return key in _http(f"{site.rstrip('/')}/{key}.txt", timeout=15)
    except Exception:
        return False

def _indexnow_post(endpoint, body):
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:160]
    except Exception as e:
        return 0, str(e)[:160]

def indexnow_submit(site, urls, engines="all"):
    key = _indexnow_key(site)
    if not key:
        raise BingError("Is site ki IndexNow key Settings me daalein (aur key file apni site ke root par upload karein).")
    urls = list(dict.fromkeys([u.strip() for u in urls if u and u.strip()]))
    if not urls:
        raise BingError("Koi URL nahi diya.")
    host = urllib.parse.urlparse(site).netloc
    eng = INDEXNOW_ENGINES if engines == "all" else [e for e in INDEXNOW_ENGINES if e[0] == "IndexNow.org"]
    meaning = {200: "OK", 202: "Accepted (key check pending)", 400: "Bad request", 403: "Key invalid / key file nahi mili",
               422: "URL site se match nahi", 429: "Too many requests"}
    res = []
    def one(e):
        name, ep = e
        sent, last, err = 0, 0, ""
        for i in range(0, len(urls), 10000):
            chunk = urls[i:i + 10000]
            body = {"host": host, "key": key, "keyLocation": f"{site.rstrip('/')}/{key}.txt", "urlList": chunk}
            last, err = _indexnow_post(ep, body)
            if last in (200, 202):
                sent += len(chunk)
            else:
                break
        return {"engine": name, "status": last, "ok": last in (200, 202), "sent": sent,
                "note": meaning.get(last, err or "Error")}
    with ThreadPoolExecutor(len(eng)) as ex:
        res = list(ex.map(one, eng))
    ok = [r for r in res if r["ok"]]
    return {"status": ok[0]["status"] if ok else (res[0]["status"] if res else 0),
            "submitted": max([r["sent"] for r in res] or [0]), "engines": res}

def fast_index(site, urls=None, sitemap=None, days=None, cap=500, skip=None, acct=None, use_bing=True, use_indexnow=True):
    """One-click: collect URLs (new ones from sitemap lastmod), submit to Bing Webmaster within quota + IndexNow to all engines."""
    urls = [u.strip() for u in (urls or []) if u and u.strip()]
    if sitemap:
        urls += sitemap_urls(sitemap, int(cap or 500), days=days or None)
    skip = set(skip or [])
    urls = [u for u in dict.fromkeys(urls) if u not in skip][: int(cap or 500)]
    out = {"urls": urls, "total": len(urls), "bing": None, "indexnow": None, "warnings": []}
    if not urls:
        out["warnings"].append("Koi naya URL nahi mila" + (f" (pichhle {days} din ke lastmod me)" if days else "") + ".")
        return out
    if use_bing:
        try:
            q = bing_api("GetUrlSubmissionQuota", {"siteUrl": site}, use_cache=False, account=acct) or {}
            daily = q.get("DailyQuota")
            n = len(urls) if daily is None else max(0, min(len(urls), int(daily)))
            if n:
                submit_urls(site, urls[:n], acct)
            out["bing"] = {"submitted": n, "left_out": len(urls) - n, "daily_quota_before": daily}
            if n < len(urls):
                out["warnings"].append(f"Bing daily quota khatam: {len(urls) - n} URLs kal submit karein (IndexNow se sab gaye).")
        except BingError as e:
            out["bing"] = {"submitted": 0, "error": str(e)}
    if use_indexnow:
        key = _indexnow_key(site)
        if not key:
            out["warnings"].append("IndexNow key nahi hai: Settings me key daalein, tab Yandex/Seznam/Naver ko bhi jayega.")
        else:
            if not indexnow_keyfile_ok(site, key):
                out["warnings"].append(f"Key file {site.rstrip('/')}/{key}.txt site par nahi mili. Isko upload karein, warna engines URLs reject kar denge.")
            try:
                out["indexnow"] = indexnow_submit(site, urls, "all")
            except BingError as e:
                out["indexnow"] = {"error": str(e)}
    return out

# --------------------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "BingKeywordPro/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("  " + (fmt % args) + "\n")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        if not PASSWORD:
            return True
        import base64
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                user_pw = base64.b64decode(h[6:]).decode("utf-8")
                if user_pw.split(":", 1)[-1] == PASSWORD:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Bing Keyword Pro"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._auth_ok():
            return
        p = urllib.parse.urlparse(self.path)
        if p.path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if p.path == "/api/config":
            c = load_config()
            return self._send(200, {"has_key": bool(c["accounts"]), "active": c["active"],
                                    "accounts": [{"id": a["id"], "name": a["name"], "masked": mask(a["apikey"]),
                                                  "added": a.get("added", "")} for a in c["accounts"]],
                                    "world_markets": c["world_markets"], "indexnow_keys": c["indexnow_keys"]})
        if p.path == "/api/markets":
            return self._send(200, [{"country": c, "language": l, "name": n} for c, l, n in MARKETS])
        if p.path == "/api/history":
            con = db()
            try:
                rows = [] if os.environ.get("BKP_HOST", "127.0.0.1") != "127.0.0.1" else \
                    con.execute("SELECT kind,q,market,t FROM history ORDER BY id DESC LIMIT 100").fetchall()
            finally:
                con.close()
            return self._send(200, [{"kind": r[0], "q": r[1], "market": r[2],
                                     "time": datetime.fromtimestamp(r[3]).strftime("%Y-%m-%d %H:%M")} for r in rows])
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth_ok():
            return
        p = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        REQ.ov = data.pop("_client", None)
        try:
            return self._post(p, data)
        finally:
            REQ.ov = None

    def _post(self, p, data):
        try:
            if p == "/api/config":
                c = load_config()
                if "world_markets" in data and data["world_markets"]:
                    c["world_markets"] = data["world_markets"]
                if "indexnow_site" in data:
                    h = urllib.parse.urlparse(norm_site(data["indexnow_site"])).netloc
                    if data.get("indexnow_key"):
                        c["indexnow_keys"][h] = data["indexnow_key"].strip()
                    else:
                        c["indexnow_keys"].pop(h, None)
                save_config(c)
                return self._send(200, {"ok": True})
            if p == "/api/verify-key":
                key = (data.get("apikey") or "").strip()
                if len(key) < 16:
                    raise BingError("API key sahi nahi lag rahi.")
                sites = bing_api("GetUserSites", apikey=key, use_cache=False) or []
                return self._send(200, {"ok": True, "sites": [x.get("Url") for x in sites]})
            if p == "/api/accounts/add":
                key = (data.get("apikey") or "").strip()
                name = (data.get("name") or "").strip() or "Account"
                if len(key) < 16:
                    raise BingError("API key sahi nahi lag rahi.")
                sites = bing_api("GetUserSites", apikey=key, use_cache=False) or []   # validates the key
                c = load_config()
                if any(a["apikey"] == key for a in c["accounts"]):
                    raise BingError("Yeh API key pehle se judi hui hai.")
                aid = "acc" + str(int(time.time() * 1000))
                c["accounts"].append({"id": aid, "name": name, "apikey": key,
                                      "added": datetime.now().strftime("%Y-%m-%d")})
                if not c["active"]:
                    c["active"] = aid
                save_config(c)
                return self._send(200, {"ok": True, "id": aid, "sites": [x.get("Url") for x in sites]})
            if p == "/api/accounts/update":
                c = load_config()
                for a in c["accounts"]:
                    if a["id"] == data.get("id"):
                        if data.get("name"):
                            a["name"] = data["name"].strip()
                        if data.get("apikey"):
                            k = data["apikey"].strip()
                            bing_api("GetUserSites", apikey=k, use_cache=False)
                            a["apikey"] = k
                save_config(c)
                return self._send(200, {"ok": True})
            if p == "/api/accounts/remove":
                c = load_config()
                c["accounts"] = [a for a in c["accounts"] if a["id"] != data.get("id")]
                if c["active"] == data.get("id"):
                    c["active"] = c["accounts"][0]["id"] if c["accounts"] else None
                save_config(c)
                return self._send(200, {"ok": True})
            if p == "/api/accounts/active":
                c = load_config(); c["active"] = data.get("id"); save_config(c)
                return self._send(200, {"ok": True})
            if p == "/api/test":
                sites = bing_api("GetUserSites", use_cache=False, account=data.get("id")) or []
                return self._send(200, {"ok": True, "sites": [x.get("Url") for x in sites]})
            if p == "/api/clear-cache":
                with _lock:
                    con = db(); con.execute("DELETE FROM cache"); con.commit(); con.close()
                return self._send(200, {"ok": True})
            q = (data.get("keyword") or "").strip()
            market = data.get("market") or "us|en-US"
            months = max(1, min(6, int(data.get("months") or 1)))
            if p == "/api/overview":
                if not q: raise BingError("Keyword daalein.")
                return self._send(200, overview(q.lower(), market, months))
            if p == "/api/serp":
                if not q: raise BingError("Keyword daalein.")
                return self._send(200, serp_top(q.lower(), market, fresh=bool(data.get("fresh"))))
            if p == "/api/ideas":
                if not q: raise BingError("Keyword daalein.")
                return self._send(200, ideas(q.lower(), market, months, max(10, min(500, int(data.get("limit") or 100))),
                                             bool(data.get("deep", True)), bool(data.get("related", True)),
                                             bool(data.get("suggest", True))))
            if p == "/api/bulk":
                return self._send(200, bulk(data.get("keywords") or [], market, months))
            site = norm_site(data.get("site"))
            if p == "/api/sites":
                return self._send(200, user_sites(bool(data.get("fresh"))))
            if not site and p.startswith("/api/site"):
                raise BingError("Site URL chunein / daalein (jaisa Bing Webmaster me verified hai).")
            acct = data.get("account") or (account_for_site(site) if site else None)
            if p.startswith("/api/site") and site and not acct:
                raise BingError("Yeh site kisi bhi judi hui API key ke account me nahi mili. Pehle us account ki API key jodein.")
            if p == "/api/site/summary":
                return self._send(200, site_summary(site, acct))
            if p == "/api/site/queries":
                return self._send(200, {"rows": site_queries(site, acct)})
            if p == "/api/site/pages":
                return self._send(200, {"rows": site_pages(site, acct)})
            if p == "/api/site/query-pages":
                return self._send(200, {"rows": site_query_pages(site, data.get("query", ""), acct)})
            if p == "/api/site/crawl-issues":
                return self._send(200, {"rows": crawl_issues(site, acct)})
            if p == "/api/site/check-urls":
                urls = data.get("urls") or []
                if data.get("sitemap"):
                    urls = sitemap_urls(data["sitemap"].strip(), int(data.get("cap") or 500), days=data.get("days") or None)
                return self._send(200, check_urls(site, urls, acct))
            if p == "/api/site/submit":
                return self._send(200, submit_urls(site, data.get("urls") or [], acct))
            if p == "/api/site/indexnow":
                return self._send(200, indexnow_submit(site, data.get("urls") or [], data.get("engines") or "all"))
            if p == "/api/site/fast-index":
                return self._send(200, fast_index(site, data.get("urls") or [], (data.get("sitemap") or "").strip() or None,
                                                  data.get("days") or None, int(data.get("cap") or 500), data.get("skip") or [],
                                                  acct, data.get("bing", True) is not False, data.get("indexnow", True) is not False))
            return self._send(404, {"error": "not found"})
        except BingError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

def main():
    db().close()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    if HOST != "127.0.0.1" and not PASSWORD:
        print("  WARNING: server public hai aur BKP_PASSWORD set nahi hai. Password zaroor set karein.")
    print("=" * 60)
    print("  Bing Keyword Pro is running ->", url)
    print("  Band karne ke liye is window me Ctrl+C dabayein.")
    print("=" * 60)
    if "--no-browser" not in sys.argv and HOST == "127.0.0.1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
