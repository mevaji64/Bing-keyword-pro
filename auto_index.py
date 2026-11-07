#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto Fast-Index: roz sitemap ke naye URLs (lastmod) Bing Webmaster + IndexNow (Bing, Yandex, Seznam, Naver, Yep) ko bhejta hai.
GitHub Actions me chalta hai (.github/workflows/auto-index.yml). Settings repo ke secret BKP_AUTO me JSON ke roop me:
{
  "bing_api_key": "BING_WEBMASTER_API_KEY",
  "days": 2,
  "sites": [
    {"site": "https://example.com/", "sitemap": "https://example.com/sitemap.xml", "indexnow_key": "abc123..."}
  ]
}
"""
import json, os, sys, urllib.parse
import app

def main():
    raw = os.environ.get("BKP_AUTO", "").strip()
    if not raw:
        print("BKP_AUTO secret set nahi hai, kuch nahi kiya.")
        return 0
    cfg = json.loads(raw)
    keys = cfg.get("bing_api_keys") or ([cfg["bing_api_key"]] if cfg.get("bing_api_key") else [])
    accounts = [{"id": f"auto{i}", "name": f"auto{i}", "apikey": k} for i, k in enumerate(keys)]
    days = cfg.get("days", 2)
    fails = 0
    for s in cfg.get("sites", []):
        site = app.norm_site(s["site"])
        host = urllib.parse.urlparse(site).netloc
        app.REQ.ov = {"accounts": accounts, "active": accounts[0]["id"] if accounts else None,
                      "indexnow_keys": {host: s["indexnow_key"]} if s.get("indexnow_key") else {}}
        try:
            acct = app.account_for_site(site) if accounts else None
            r = app.fast_index(site, s.get("urls") or [], s.get("sitemap"), s.get("days", days), int(s.get("cap", 500)),
                               None, acct, use_bing=bool(acct), use_indexnow=bool(s.get("indexnow_key")))
            print(f"== {site}: {r['total']} URLs")
            if r.get("bing"):
                print("   Bing Webmaster:", r["bing"])
            for e in (r.get("indexnow") or {}).get("engines", []):
                print(f"   IndexNow {e['engine']}: HTTP {e['status']} {e['note']} ({e['sent']})")
            for w in r.get("warnings", []):
                print("   !", w)
        except Exception as e:
            fails += 1
            print(f"== {site}: ERROR {e}")
        finally:
            app.REQ.ov = None
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
