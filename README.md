# Bing Keyword Pro

Professional **Bing keyword research + site audit** tool, powered by the official **Bing Webmaster Tools API**.
Search volume, broad volume, weekly trend, Keyword Difficulty (KD), competition, intent, keyword ideas, questions,
bulk analysis, 48 markets + Worldwide, ranking-keyword audit with positions, and URL index check & submit.
Multiple Bing Webmaster API keys (one per account) are supported and stay saved until you remove them.

---

## हिंदी में: शुरू कैसे करें

1. **Python 3.8+** इंस्टॉल करें: https://www.python.org/downloads/ (Windows में "Add Python to PATH" टिक करें)।
2. यह repo डाउनलोड करें: GitHub पर **Code → Download ZIP**, या `git clone <repo-url>`।
3. चलाएं:
   - Windows: `run_windows.bat` पर डबल क्लिक
   - Mac/Linux: `./run_mac_linux.sh` या `python3 app.py`
4. ब्राउज़र में अपने आप `http://127.0.0.1:8765` खुलेगा।
5. **API Accounts** टैब में नाम + Bing Webmaster API key डालें → **Add & verify**।
   हर वेबसाइट/अकाउंट की अलग key जोड़ सकते हैं। Key तब तक सेव रहती है जब तक आप **Remove** या **Replace key** न करें।

**API key कहां मिलेगी:** bing.com/webmasters → Settings (gear icon) → API access → API Key → Generate.

कोई extra पैकेज नहीं चाहिए, सिर्फ Python standard library.

---

## Features

| Tab | What it does |
|---|---|
| **Keyword Overview** | Monthly exact search volume, broad volume, weekly trend chart (~6 months), 4-week trend %, KD, competition, intent, opportunity score, volume split by country |
| **Keyword Ideas** | Bing related keywords + Bing autosuggest (A-Z, question & modifier expansion), each with real Bing volume, KD, competition, intent. Filters, sort, copy, CSV |
| **Questions** | Question keywords (what / how / why / kya / kaise …) from your ideas |
| **Bulk Analyzer** | Paste up to 1,000 keywords, get volume + KD + competition + intent for all |
| **Site Audit** | For every site in your connected accounts: ranking keywords with latest & average Bing position, position change, impressions, clicks, CTR; top pages; striking-distance keywords (pos 4-20); which pages rank for a keyword; crawl issues; pages in index; submit quota |
| **URL Index** | Paste URLs or a sitemap → Bing index/crawl status per URL (HTTP, last crawled, discovered, impressions, clicks). Submit not-indexed URLs to Bing (Webmaster URL submission) or via IndexNow. **⚡ Fast Index**: one click sends URLs to Bing Webmaster (within daily quota) + IndexNow to Bing, Yandex, Seznam, Naver, Yep; can pick only new URLs from sitemap `lastmod` and skips URLs already sent |
| **API Accounts** | Add / rename / replace / remove multiple Bing Webmaster API keys. Sites are auto-mapped to the right key |
| **Settings** | Worldwide market list, per-site IndexNow keys, clear cache |

Markets: 48 country-language markets plus **Worldwide** (sum of the markets you tick in Settings).

---

## Data notes (important, honest)

- **Search volume** = Bing's own keyword impressions from the Webmaster Tools keyword research API (exact match), normalised to a 30-day month. **Broad volume** = searches that contain the term.
- **KD and Competition are modelled estimates.** Bing does not publish a KD score through any API. The tool scores KD 0-100 from volume, head-term length, broad/exact spread, autosuggest density and commercial modifiers. Use it to compare keywords, not as an absolute number.
- **CPC is not included**: that data needs the Microsoft Advertising API (developer token + OAuth), not a Webmaster key.
- **Index status** comes from `GetUrlInfo`: "Indexed / crawled" means Bing crawled the URL with HTTP 200. Bing's API does not expose a strict "in index" flag per URL.
- Bing data lags a few days; the tool ends date ranges 3 days before today.
- Results are cached 7 days (keywords) / 6 hours (site data) in `cache.db` to save API quota. Clear it in Settings.

---

## Security

- API keys are saved **in your own browser** (localStorage) and sent to the server only with each request. The server never writes them to disk, so they stay until you click Remove, even when a free host restarts. On a new browser/device add them once again.
- `config.json` (old local mode) is in `.gitignore`, **never commit it**.
- By default the server listens only on `127.0.0.1` (your own computer).

## Hosting online (free on Render)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/mevaji64/Bing-keyword-pro)

`render.yaml` deploys the Docker image on Render's free plan. Free services sleep after 15 minutes idle and take about a minute to wake.

## Hosting with Docker (any host)

Docker (Render, Railway, a VPS, etc.):

```bash
docker build -t bing-keyword-pro .
docker run -d -p 8765:8765 -v $(pwd)/data:/app/data -e BKP_PASSWORD=choose-a-strong-password bing-keyword-pro
```

Environment variables:

| Var | Default | Meaning |
|---|---|---|
| `BKP_HOST` | `127.0.0.1` | `0.0.0.0` to listen publicly |
| `PORT` / `BKP_PORT` | `8765` | Port |
| `BKP_PASSWORD` | empty | Password prompt for the whole tool. **Always set when online** |
| `BKP_DATA_DIR` | app folder | Where `config.json` (keys) and `cache.db` live. Use a persistent volume so keys survive restarts |

GitHub Pages cannot run this tool, because Bing's API blocks direct browser calls (no CORS). It needs this small Python server, locally or on a host.

## API methods used

`GetKeyword`, `GetKeywordStats`, `GetRelatedKeywords`, `GetUserSites`, `GetQueryStats`, `GetPageStats`, `GetQueryPageStats`,
`GetRankAndTrafficStats`, `GetCrawlStats`, `GetCrawlIssues`, `GetUrlInfo`, `GetUrlTrafficInfo`, `GetUrlSubmissionQuota`,
`SubmitUrl`, `SubmitUrlBatch` (JSON endpoint `https://ssl.bing.com/webmaster/api.svc/json/`), Bing autosuggest, IndexNow.

License: MIT


---

## ⚡ Fast Index + रोज़ ऑटो इंडेक्स

**टूल में:** URL Index टैब → sitemap डालें → "Sirf naye: pichhle 1 din" चुनें → **⚡ Fast Index**।
यह URLs को Bing Webmaster (daily quota तक) और IndexNow से Bing, Yandex, Seznam, Naver, Yep को एक साथ भेजता है।
IndexNow के लिए Settings में site की key डालें और `https://आपकी-साइट/<key>.txt` फ़ाइल (अंदर सिर्फ key) साइट के root पर अपलोड करें।

**रोज़ अपने आप (GitHub Actions, फ्री):** repo → Settings → Secrets and variables → Actions → New repository secret →
नाम `BKP_AUTO`, value:

```json
{
  "bing_api_key": "आपकी Bing Webmaster API key",
  "days": 2,
  "sites": [
    {"site": "https://example.com/", "sitemap": "https://example.com/sitemap.xml", "indexnow_key": "आपकी IndexNow key"}
  ]
}
```

कई अकाउंट हों तो `"bing_api_keys": ["key1", "key2"]` लिखें। Workflow रोज़ 9:00 AM IST चलता है (Actions → Auto Fast Index → Run workflow से अभी भी चला सकते हैं)।
Sitemap में `lastmod` होना ज़रूरी है, वरना नए URL पहचाने नहीं जाएंगे। Secret private रहता है, public repo में दिखता नहीं।

Note: Bing/IndexNow सिर्फ crawl जल्दी करवाते हैं; index करना या न करना search engine तय करता है।
