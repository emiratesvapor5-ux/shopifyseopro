"""
UAE Keyword Rank Checker — runs in GitHub Actions (no UAE IP blocks)

Checks keyword rankings on:
  1. Google.ae  (gl=ae — UAE-targeted results)
  2. Bing UAE   (cc=AE&setmkt=en-AE)
  3. DuckDuckGo (kl=ae-en — reliable fallback, no CAPTCHA)

Usage:
  python3 src/check_rankings.py
  python3 src/check_rankings.py --keywords "elf bar uae,lost mary dubai,vape shop dubai"
  python3 src/check_rankings.py --top 50   # check top 50 positions (default: 100)

Output:
  rank_reports/rankings_YYYYMMDD.json   — full data
  rank_reports/rankings_latest.json     — always overwritten (latest run)
  rank_reports/rankings_report.md       — human-readable summary
"""

import requests, re, json, os, sys, time, random, urllib.parse
from datetime import datetime, timezone

REPORTS = os.path.join(os.path.dirname(__file__), '..', 'rank_reports')
os.makedirs(REPORTS, exist_ok=True)

TODAY     = datetime.now(timezone.utc).strftime('%Y%m%d')
OUT_FILE  = os.path.join(REPORTS, f'rankings_{TODAY}.json')
LATEST    = os.path.join(REPORTS, 'rankings_latest.json')
REPORT_MD = os.path.join(REPORTS, 'rankings_report.md')

TARGET_DOMAINS = {
    "emiratesvapor.ae",
    "www.emiratesvapor.ae",
    "emirates-vapor.myshopify.com",
    "vaporshopdubai.ae",
    "www.vaporshopdubai.ae",
}

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

# ── Core keyword list — focus keywords from GOD SEO + brand terms ─────────────
DEFAULT_KEYWORDS = [
    # Brand + store terms
    "vape shop dubai",
    "vape shop uae",
    "buy vape dubai",
    "buy vape uae",
    "online vape shop uae",
    "vape delivery dubai",
    "emirates vapor",
    "emiratesvapor",

    # High-volume disposable keywords
    "elf bar uae",
    "elf bar dubai",
    "lost mary dubai",
    "lost mary uae",
    "al fakher crown bar uae",
    "al fakher vape dubai",
    "tugboat vape uae",
    "fummo vape uae",
    "air bar vape dubai",
    "hqd vape uae",
    "nasty juice uae",
    "pod salt uae",

    # Device keywords
    "geek vape uae",
    "voopoo drag uae",
    "vaporesso uae",
    "smok vape dubai",
    "aspire vape uae",
    "myle vape dubai",
    "uwell caliburn uae",

    # Category keywords
    "disposable vape dubai",
    "disposable vape uae",
    "vape pods dubai",
    "e-liquid dubai",
    "nicotine pouches uae",
    "vape coils dubai",
    "pod system uae",

    # Long-tail buying intent
    "buy elf bar dubai",
    "lost mary 3500 puffs dubai",
    "al fakher crown bar 15000 uae",
    "same day vape delivery dubai",
    "cash on delivery vape uae",
    "authentic vape dubai",
    "esma certified vape uae",

    # Competitor comparison intent
    "best vape shop dubai",
    "best online vape uae",
    "cheapest vape dubai",
    "vape shop near me dubai",
]


def _ua():
    return random.choice(UA_LIST)


def _sess():
    s = requests.Session()
    s.headers.update({
        "User-Agent": _ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-AE,en;q=0.9,ar;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "DNT": "1",
    })
    return s


def _domain_from_url(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _is_target(url):
    d = _domain_from_url(url)
    return d in TARGET_DOMAINS or any(t in d for t in TARGET_DOMAINS)


def _find_position(results, keyword):
    """Find our domain's position in a result list. Returns (pos, url) or (None, None)."""
    for i, r in enumerate(results, 1):
        if _is_target(r.get("url", "")):
            return i, r["url"]
    return None, None


# ── Google.ae scraper ─────────────────────────────────────────────────────────

def scrape_google_ae(keyword, sess, top=100):
    """
    Scrape Google.ae for UAE-specific results.
    Uses gl=ae (country=UAE) + hl=en + cr=countryAE for proper geo-targeting.
    Returns list of {pos, title, url, snippet}
    """
    results = []
    start = 0
    pages_needed = (top + 9) // 10  # 10 results per page

    for page in range(min(pages_needed, 10)):  # max 10 pages = 100 results
        params = {
            "q":   keyword,
            "gl":  "ae",          # country = UAE
            "hl":  "en",          # language = English
            "cr":  "countryAE",   # restrict to UAE content where possible
            "num": "10",
            "start": str(start),
            "pws": "0",           # disable personalised results
            "safe": "off",
        }
        try:
            r = sess.get("https://www.google.ae/search", params=params,
                         timeout=15, allow_redirects=True)

            if r.status_code == 429:
                print(f"  Google.ae: rate limited (429) — backing off 30s")
                time.sleep(30)
                continue

            if "sorry/index" in r.url or "captcha" in r.text.lower():
                print(f"  Google.ae: CAPTCHA triggered for '{keyword}' — stopping")
                break

            # Extract organic results
            html = r.text
            # Google result blocks: <div class="g"> containing h3 + cite
            # Multiple patterns for robustness (Google changes HTML frequently)
            blocks = re.findall(
                r'<div[^>]+class="[^"]*(?:g|tF2Cxc)[^"]*"[^>]*>(.*?)</div>\s*</div>',
                html, re.S)

            page_results = []
            # Simpler URL extraction - find all hrefs that are real result links
            result_urls = re.findall(
                r'<a\s+href="(https://(?!www\.google)[^"&]+)"[^>]*>\s*<[^>]+>\s*(.*?)</[^>]+>\s*</a>',
                html, re.S)

            seen = set()
            for url, title_html in result_urls:
                url = url.split("&")[0]  # strip tracking params
                if url in seen: continue
                # Skip Google's own pages, ads, image packs
                if any(x in url for x in ['google.', 'youtube.com', 'gstatic.', '#']):
                    continue
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                if not title or len(title) < 3: continue
                seen.add(url)
                page_results.append({"url": url, "title": title})

            results.extend(page_results)
            start += 10

            if len(page_results) < 5:  # fewer than 5 results = last page
                break

            time.sleep(random.uniform(3, 6))  # polite delay between pages

        except Exception as e:
            print(f"  Google.ae error: {e}")
            break

    return results[:top]


# ── Bing UAE scraper ──────────────────────────────────────────────────────────

def scrape_bing_uae(keyword, sess, top=100):
    """
    Scrape Bing with UAE market targeting.
    cc=AE + setmkt=en-AE gives UAE-specific rankings.
    Bing is much more scraping-friendly than Google.
    """
    results = []
    first = 1
    pages_needed = (top + 9) // 10

    for page in range(min(pages_needed, 10)):
        params = {
            "q":      keyword,
            "cc":     "AE",
            "setmkt": "en-AE",
            "setlang":"EN",
            "count":  "10",
            "first":  str(first),
            "safeSearch": "Off",
        }
        try:
            r = sess.get("https://www.bing.com/search", params=params,
                         timeout=15, allow_redirects=True)

            if r.status_code == 429:
                print(f"  Bing: rate limited — backing off 20s")
                time.sleep(20)
                continue

            html = r.text
            # Bing: <li class="b_algo"> blocks
            # Extract URLs from result anchors
            result_urls = re.findall(
                r'<h2><a[^>]+href="(https?://(?!www\.bing\.com|go\.microsoft)[^"]+)"[^>]*>(.*?)</a></h2>',
                html, re.S)

            page_results = []
            seen = set()
            for url, title_html in result_urls:
                if url in seen: continue
                if any(x in url for x in ['bing.com', 'microsoft.com', '#']):
                    continue
                title = re.sub(r'<[^>]+>', '', title_html).strip()
                if not title: continue
                seen.add(url)
                page_results.append({"url": url, "title": title})

            results.extend(page_results)
            first += 10

            if len(page_results) < 3:
                break

            time.sleep(random.uniform(2, 4))

        except Exception as e:
            print(f"  Bing error: {e}")
            break

    return results[:top]


# ── DuckDuckGo scraper (UAE locale) ──────────────────────────────────────────

def scrape_ddg_uae(keyword, sess, top=50):
    """
    DuckDuckGo HTML with UAE locale — no CAPTCHA, very reliable.
    kl=ae-en = UAE + English results.
    """
    results = []
    try:
        params = {
            "q":   keyword,
            "kl":  "ae-en",  # UAE + English
            "kp":  "-1",     # safe search off
        }
        r = sess.get("https://html.duckduckgo.com/html/",
                     params=params, timeout=15)
        html = r.text

        # DDG: <a class="result__a" href="...">title</a>
        result_links = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.S)

        seen = set()
        for url, title_html in result_links:
            # DDG returns redirect URLs — extract real URL
            if "uddg=" in url:
                m = re.search(r'uddg=([^&]+)', url)
                if m: url = urllib.parse.unquote(m.group(1))
            if url in seen or not url.startswith("http"): continue
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            seen.add(url)
            results.append({"url": url, "title": title})

        # Also try the main DDG results
        alt_links = re.findall(
            r'href="(https?://(?!duckduckgo\.com)[^"]+)"[^>]*class="[^"]*result__url[^"]*"',
            html, re.S)
        for url in alt_links:
            if url not in seen:
                seen.add(url)
                results.append({"url": url, "title": ""})

    except Exception as e:
        print(f"  DDG error: {e}")

    return results[:top]


# ── Main ranking check ────────────────────────────────────────────────────────

def check_keyword(keyword, top=100):
    """Check one keyword across all 3 engines. Returns dict of results."""
    print(f"\n  📍 '{keyword}'")
    sess = _sess()
    entry = {
        "keyword": keyword,
        "date":    datetime.now(timezone.utc).isoformat(),
        "google_ae": {"position": None, "url": None, "results": []},
        "bing_uae":  {"position": None, "url": None, "results": []},
        "ddg_uae":   {"position": None, "url": None, "results": []},
    }

    # Google.ae
    try:
        g_results = scrape_google_ae(keyword, sess, top)
        pos, url = _find_position(g_results, keyword)
        entry["google_ae"] = {
            "position": pos,
            "url":      url,
            "results":  [{"pos": i+1, "url": r["url"], "title": r["title"]}
                         for i, r in enumerate(g_results[:10])],
        }
        status = f"#{pos}" if pos else "not in top 100"
        print(f"    Google.ae → {status}" + (f"  {url}" if url else ""))
    except Exception as e:
        print(f"    Google.ae → ERROR: {e}")
    time.sleep(random.uniform(4, 8))

    # Bing UAE
    try:
        b_results = scrape_bing_uae(keyword, sess, top)
        pos, url = _find_position(b_results, keyword)
        entry["bing_uae"] = {
            "position": pos,
            "url":      url,
            "results":  [{"pos": i+1, "url": r["url"], "title": r["title"]}
                         for i, r in enumerate(b_results[:10])],
        }
        status = f"#{pos}" if pos else "not in top 100"
        print(f"    Bing UAE  → {status}" + (f"  {url}" if url else ""))
    except Exception as e:
        print(f"    Bing UAE → ERROR: {e}")
    time.sleep(random.uniform(3, 6))

    # DuckDuckGo UAE
    try:
        d_results = scrape_ddg_uae(keyword, sess)
        pos, url = _find_position(d_results, keyword)
        entry["ddg_uae"] = {
            "position": pos,
            "url":      url,
            "results":  [{"pos": i+1, "url": r["url"], "title": r["title"]}
                         for i, r in enumerate(d_results[:10])],
        }
        status = f"#{pos}" if pos else "not in top 50"
        print(f"    DDG UAE   → {status}" + (f"  {url}" if url else ""))
    except Exception as e:
        print(f"    DDG UAE → ERROR: {e}")
    time.sleep(random.uniform(2, 4))

    return entry


def generate_report(results):
    """Generate a readable markdown report."""
    ranked_google = [(r["keyword"], r["google_ae"]["position"], r["google_ae"]["url"])
                     for r in results if r["google_ae"]["position"]]
    ranked_bing   = [(r["keyword"], r["bing_uae"]["position"], r["bing_uae"]["url"])
                     for r in results if r["bing_uae"]["position"]]

    ranked_google.sort(key=lambda x: x[1])
    ranked_bing.sort(key=lambda x: x[1])

    top3_g   = [x for x in ranked_google if x[1] <= 3]
    top10_g  = [x for x in ranked_google if 4 <= x[1] <= 10]
    top50_g  = [x for x in ranked_google if 11 <= x[1] <= 50]
    top100_g = [x for x in ranked_google if x[1] > 50]

    top3_b   = [x for x in ranked_bing if x[1] <= 3]
    top10_b  = [x for x in ranked_bing if 4 <= x[1] <= 10]

    date = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines = [
        f"# Emirates Vapor — UAE Keyword Rankings",
        f"**Date:** {date}  |  **Keywords checked:** {len(results)}",
        f"**Source:** GitHub Actions (Google.ae gl=ae + Bing cc=AE)",
        "",
        "---",
        "",
        "## 🟢 Google.ae Rankings",
        f"**Ranking:** {len(ranked_google)}/{len(results)} keywords found",
        "",
    ]

    if top3_g:
        lines.append("### Top 3 (Page 1)")
        for kw, pos, url in top3_g:
            lines.append(f"- **#{pos}** `{kw}` → {url}")
        lines.append("")

    if top10_g:
        lines.append("### Positions 4–10 (Page 1)")
        for kw, pos, url in top10_g:
            lines.append(f"- **#{pos}** `{kw}` → {url}")
        lines.append("")

    if top50_g:
        lines.append("### Positions 11–50 (Pages 2–5)")
        for kw, pos, url in top50_g:
            lines.append(f"- #{pos} `{kw}`")
        lines.append("")

    if top100_g:
        lines.append("### Positions 51–100")
        for kw, pos, url in top100_g:
            lines.append(f"- #{pos} `{kw}`")
        lines.append("")

    not_found_g = [r["keyword"] for r in results if not r["google_ae"]["position"]]
    if not_found_g:
        lines.append(f"### ❌ Not in Top 100 ({len(not_found_g)} keywords)")
        for kw in not_found_g:
            lines.append(f"- `{kw}`")
        lines.append("")

    lines += [
        "---",
        "",
        "## 🔵 Bing UAE Rankings",
        f"**Ranking:** {len(ranked_bing)}/{len(results)} keywords found",
        "",
    ]

    if top3_b:
        lines.append("### Top 3")
        for kw, pos, url in top3_b:
            lines.append(f"- **#{pos}** `{kw}` → {url}")
        lines.append("")

    if top10_b:
        lines.append("### Positions 4–10")
        for kw, pos, url in top10_b:
            lines.append(f"- **#{pos}** `{kw}` → {url}")
        lines.append("")

    for kw, pos, url in [(x[0],x[1],x[2]) for x in ranked_bing if x[1] > 10]:
        lines.append(f"- #{pos} `{kw}`")
    if ranked_bing: lines.append("")

    not_found_b = [r["keyword"] for r in results if not r["bing_uae"]["position"]]
    if not_found_b:
        lines.append(f"### ❌ Not in Top 100 ({len(not_found_b)} keywords)")
        for kw in not_found_b:
            lines.append(f"- `{kw}`")
        lines.append("")

    lines += [
        "---",
        "",
        "## 📊 Quick Summary",
        f"| Engine | Top 3 | Top 10 | Top 50 | Top 100 | Not found |",
        f"|--------|-------|--------|--------|---------|-----------|",
        f"| Google.ae | {len(top3_g)} | {len(top3_g)+len(top10_g)} | {len(ranked_google)} | {len(ranked_google)} | {len(not_found_g)} |",
        f"| Bing UAE  | {len(top3_b)} | {len(top3_b)+len(top10_b)} | {len([x for x in ranked_bing if x[1]<=50])} | {len(ranked_bing)} | {len(not_found_b)} |",
        "",
        "---",
        "*Generated by check_rankings.py — runs weekly via GitHub Actions*",
    ]

    return "\n".join(lines)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    # Parse --keywords "kw1,kw2"
    keywords = list(DEFAULT_KEYWORDS)
    if "--keywords" in args:
        i = args.index("--keywords")
        if i + 1 < len(args):
            extra = [k.strip() for k in args[i+1].split(",") if k.strip()]
            keywords = extra + [k for k in keywords if k not in extra]

    # Parse --top N
    top = 100
    if "--top" in args:
        i = args.index("--top")
        if i + 1 < len(args):
            top = int(args[i + 1])

    # Load previous results to compare
    prev = {}
    if os.path.exists(LATEST):
        try:
            for r in json.load(open(LATEST)):
                prev[r["keyword"]] = r
        except Exception:
            pass

    print("🔍 UAE KEYWORD RANK CHECKER")
    print(f"  Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Keywords: {len(keywords)}")
    print(f"  Engines: Google.ae (gl=ae) · Bing (cc=AE) · DuckDuckGo (kl=ae-en)")
    print(f"  Checking top {top} positions")
    print(f"  Tracking: {', '.join(TARGET_DOMAINS)}")
    print()

    all_results = []

    for i, keyword in enumerate(keywords, 1):
        print(f"[{i}/{len(keywords)}] Checking...")
        result = check_keyword(keyword, top)

        # Show position change vs last run
        if keyword in prev:
            old_g = prev[keyword].get("google_ae", {}).get("position")
            new_g = result["google_ae"]["position"]
            if old_g and new_g:
                diff = old_g - new_g
                if diff > 0:
                    print(f"    📈 Google: improved {diff} spots (was #{old_g})")
                elif diff < 0:
                    print(f"    📉 Google: dropped {abs(diff)} spots (was #{old_g})")

        all_results.append(result)

        # Save progress after every keyword (in case of timeout)
        json.dump(all_results, open(OUT_FILE, "w"), indent=2)
        json.dump(all_results, open(LATEST, "w"), indent=2)

        # Longer delay between keywords to avoid detection
        if i < len(keywords):
            delay = random.uniform(8, 15)
            print(f"    (waiting {delay:.0f}s before next keyword)")
            time.sleep(delay)

    # Final save + report
    json.dump(all_results, open(OUT_FILE, "w"), indent=2)
    json.dump(all_results, open(LATEST, "w"), indent=2)

    report = generate_report(all_results)
    with open(REPORT_MD, "w") as f:
        f.write(report)

    print("\n" + "="*65)
    print("RANKING SUMMARY")
    print("="*65)

    g_found = [(r["keyword"], r["google_ae"]["position"]) for r in all_results if r["google_ae"]["position"]]
    b_found = [(r["keyword"], r["bing_uae"]["position"]) for r in all_results if r["bing_uae"]["position"]]
    g_found.sort(key=lambda x: x[1])
    b_found.sort(key=lambda x: x[1])

    print(f"\nGoogle.ae: {len(g_found)}/{len(all_results)} keywords ranking")
    for kw, pos in g_found[:15]:
        icon = "🥇" if pos == 1 else ("🥈" if pos == 2 else ("🥉" if pos == 3 else ("✅" if pos <= 10 else "📍")))
        print(f"  {icon} #{pos:3d}  {kw}")

    print(f"\nBing UAE: {len(b_found)}/{len(all_results)} keywords ranking")
    for kw, pos in b_found[:10]:
        icon = "🥇" if pos == 1 else ("✅" if pos <= 10 else "📍")
        print(f"  {icon} #{pos:3d}  {kw}")

    print(f"\n📄 Full report: {REPORT_MD}")
    print(f"📊 Raw data:    {OUT_FILE}")


if __name__ == "__main__":
    main()
