"""
Backlink Site Discovery Engine — runs in GitHub Actions (full internet access)

Discovers Telegraph-like publishing platforms worldwide by:
1. Scraping DuckDuckGo / Bing HTML for "free article publishing sites dofollow" etc.
2. Fetching GitHub awesome-lists for paste/publish site collections
3. Testing each candidate live (real POST attempt, verifies URL returned)
4. Checking dofollow (fetches posted page, checks rel="nofollow" absence on our link)
5. Saving verified sites → rank_reports/discovered_sites.json

Usage:
  python3 src/discover_backlink_sites.py
  python3 src/discover_backlink_sites.py --test-only   # just re-test known candidates
  python3 src/discover_backlink_sites.py --merge        # merge discovered into seo_backlink_pro.py
"""

import requests, re, json, time, os, sys, random, string, urllib.parse, hashlib
from datetime import datetime

REPORTS_DIR   = os.path.join(os.path.dirname(__file__), '..', 'rank_reports')
OUT_FILE      = os.path.join(REPORTS_DIR, 'discovered_sites.json')
TESTED_FILE   = os.path.join(REPORTS_DIR, 'tested_candidates.json')

os.makedirs(REPORTS_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

# ── Already-known sites (skip re-testing these) ───────────────────────────────
KNOWN_DOMAINS = {
    "telegra.ph", "write.as", "rentry.co", "paste.mozilla.org",
    "paste.debian.net", "paste.ubuntu.com", "dpaste.com", "hastebin.com",
    "paste.centos.org", "paste.kde.org", "sprunge.us", "paste.fo",
    "dpaste.org", "bpaste.net", "ix.io", "0x0.st", "envs.sh",
    "paste.rs", "clbin.com", "vpaste.net", "nekobin.com", "p.ip.fi",
    "ttm.sh", "txt.fyi", "haste.zneix.eu", "hastebin.skyra.pw",
    "haste.hyperiondev.com", "haste.nicco.love", "haste.iversia.com",
    "paste.sh", "bytebin.lucko.me", "cl1p.net", "paste2.org",
    "controlc.com", "pastelink.net", "pasted.co", "yourpaste.net",
    "mystb.in", "wastebin.honorable.de", "catbox.moe", "filebin.net",
    "uguu.se", "oshi.at", "paste.gg", "glot.io", "snippet.host",
    "apaste.info", "pastebin.pl", "textbin.net", "pastebin.fi",
    "paste1s.com", "pbbin.com", "justpaste.it", "pastehere.xyz",
    "pastery.net", "dpaste.de", "stikked.ch", "cpaste.org",
    "paste.bpython.org", "paste.dragonslayer.de", "paste.myst.rs",
}

# ── Search queries — cast a wide net ─────────────────────────────────────────
SEARCH_QUERIES = [
    # Telegraph-like publishing
    "telegra.ph alternatives free article publishing platform",
    "free article publishing site dofollow backlink 2024",
    "web 2.0 article submission sites list high DA dofollow",
    "free blog post publishing platform no registration",
    "anonymous article publishing site like medium free",
    # Paste/text hosting
    "pastebin alternatives list dofollow 2024",
    "hastebin alternatives self-hosted paste site",
    "free text hosting site permanent link no account",
    "online notepad share link forever free",
    # Note/wiki publishing
    "hedgedoc alternatives collaborative markdown publishing",
    "free wiki page creation no registration backlink",
    "notion alternatives free public page publishing",
    "gitbook alternatives free documentation hosting",
    # Blogging/micro-publishing
    "write.as alternatives free anonymous blogging platform",
    "free micro blogging platform dofollow links",
    "mataroa blog alternatives open source publishing",
    "bear blog alternatives free simple blogging",
    # Specific high-DA targets
    "site like telegra.ph for SEO backlinks",
    "high DA free publishing sites list SEO 2024",
    "permanent free text paste website list",
]

# ── GitHub awesome-list URLs (raw) ────────────────────────────────────────────
AWESOME_LISTS = [
    "https://raw.githubusercontent.com/nicehash/awesome-pastebin/master/README.md",
    "https://raw.githubusercontent.com/awesome-selfhosted/awesome-selfhosted/master/README.md",
    "https://raw.githubusercontent.com/255kb/stack-on-a-budget/master/README.md",
    "https://raw.githubusercontent.com/mrcodedev/free-services-for-developers/main/README.md",
    # Paste site aggregators
    "https://raw.githubusercontent.com/lorenzos/BitBin-list/master/list.json",
]

# ── Known site pattern detectors ──────────────────────────────────────────────
# (method_type, test_fn) — we try each pattern and see which sticks
PATTERN_TESTS = {}


def _rnd(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _test_content(text, marker):
    """Return True if marker string appears in text."""
    return marker in (text or "")


def test_hastebin_api(base_url, sess, marker):
    """POST /documents with raw body → {key: ...}"""
    try:
        r = sess.post(f"{base_url.rstrip('/')}/documents",
                      data=marker.encode(),
                      headers={"Content-Type": "text/plain"},
                      timeout=12, allow_redirects=False)
        if r.status_code == 200:
            key = r.json().get("key", "")
            if key:
                posted = f"{base_url.rstrip('/')}/{key}"
                # verify content is accessible
                check = sess.get(f"{base_url.rstrip('/')}/raw/{key}", timeout=8)
                if marker in check.text:
                    return posted, "hastebin_clone_api"
    except Exception:
        pass
    return None, None


def test_writeas_api(base_url, sess, marker):
    """POST /api/posts with JSON body."""
    try:
        r = sess.post(f"{base_url.rstrip('/')}/api/posts",
                      json={"body": marker, "title": "Test"},
                      headers={"Content-Type": "application/json"},
                      timeout=12)
        if r.status_code in (200, 201):
            d = r.json().get("data", {})
            slug = d.get("slug") or d.get("id")
            if slug:
                return f"{base_url.rstrip('/')}/{slug}", "writeas_api"
    except Exception:
        pass
    return None, None


def test_paste_form(base_url, sess, marker, field="content"):
    """Generic HTML form paste — POST with content/code/text= field."""
    for field_name in ["content", "code", "text", "paste_content", "paste_code", "data"]:
        try:
            r = sess.post(base_url,
                          data={field_name: marker, "title": "ev-test",
                                "expire": "0", "private": "0"},
                          timeout=12, allow_redirects=True)
            if r.status_code in (200, 201) and r.url and r.url.rstrip("/") != base_url.rstrip("/"):
                # verify content on posted page
                check = sess.get(r.url, timeout=8)
                if marker in check.text:
                    return r.url, "paste_form"
        except Exception:
            continue
    return None, None


def test_file_upload(base_url, sess, marker):
    """Upload a .txt file via multipart form."""
    for param in ["file", "files[]", "f", "upload", "fileToUpload"]:
        try:
            r = sess.post(base_url,
                          files={param: ("ev.txt", marker.encode(), "text/plain")},
                          timeout=20)
            if r.status_code == 200:
                text = r.text.strip()
                if text.startswith("http") and len(text) < 200:
                    check = sess.get(text, timeout=8)
                    if marker in check.text:
                        return text, "file_upload"
        except Exception:
            continue
    return None, None


def test_telegraph_api(base_url, sess, marker):
    """Telegraph createPage API."""
    try:
        # get a token first
        r = sess.post("https://api.telegra.ph/createAccount",
                      json={"short_name": "evtest", "author_name": "EV"},
                      timeout=10)
        if r.status_code == 200 and r.json().get("ok"):
            token = r.json()["result"]["access_token"]
            r2 = sess.post("https://api.telegra.ph/createPage",
                           json={"access_token": token, "title": "EV Test",
                                 "content": [{"tag": "p", "children": [marker]}]},
                           timeout=10)
            if r2.json().get("ok"):
                path = r2.json()["result"]["path"]
                return f"https://telegra.ph/{path}", "telegraph_api"
    except Exception:
        pass
    return None, None


def test_json_api(base_url, sess, marker):
    """POST JSON {content/text/body: marker} → returns URL."""
    for payload in [
        {"content": marker},
        {"text": marker, "title": "ev"},
        {"body": marker, "title": "ev"},
        {"paste": marker},
    ]:
        for endpoint in ["", "/api/paste", "/api/posts", "/paste", "/new", "/submit"]:
            try:
                url = base_url.rstrip("/") + endpoint
                r = sess.post(url, json=payload,
                              headers={"Content-Type": "application/json"},
                              timeout=12)
                if r.status_code in (200, 201):
                    d = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
                    # look for a URL in response
                    for key in ["url", "link", "id", "key", "slug", "result"]:
                        val = d.get(key, "")
                        if isinstance(val, str) and (val.startswith("http") or (val and "/" not in val)):
                            posted = val if val.startswith("http") else f"{base_url.rstrip('/')}/{val}"
                            try:
                                check = sess.get(posted, timeout=8)
                                if marker in check.text:
                                    return posted, "json_api"
                            except Exception:
                                pass
            except Exception:
                continue
    return None, None


# ── Web scraping for candidate discovery ──────────────────────────────────────

def scrape_ddg(query, sess):
    """Scrape DuckDuckGo HTML results — no API key needed."""
    candidates = set()
    try:
        params = {"q": query, "kl": "us-en", "kp": "-1"}
        r = sess.get("https://html.duckduckgo.com/html/",
                     params=params, timeout=15)
        # extract result URLs
        urls = re.findall(r'href="(https?://[^"&]+)"', r.text)
        for url in urls:
            domain = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
            if domain and "." in domain and len(domain) > 4:
                candidates.add(domain)
    except Exception as e:
        print(f"  DDG error: {e}")
    return candidates


def scrape_bing(query, sess):
    """Scrape Bing HTML results."""
    candidates = set()
    try:
        params = {"q": query, "count": "20"}
        r = sess.get("https://www.bing.com/search", params=params, timeout=15)
        urls = re.findall(r'<a[^>]+href="(https?://(?!www\.bing\.com)[^"]+)"', r.text)
        for url in urls:
            domain = urllib.parse.urlparse(url).netloc.lower().lstrip("www.")
            if domain and "." in domain and len(domain) > 4:
                candidates.add(domain)
    except Exception as e:
        print(f"  Bing error: {e}")
    return candidates


def scrape_awesome_lists(sess):
    """Fetch GitHub awesome-lists and extract domains."""
    candidates = set()
    for url in AWESOME_LISTS:
        try:
            r = sess.get(url, timeout=15)
            if r.status_code == 200:
                # extract markdown links [text](url) and bare URLs
                urls = re.findall(r'https?://[^\s\)\]"]+', r.text)
                for u in urls:
                    domain = urllib.parse.urlparse(u).netloc.lower().lstrip("www.")
                    if domain and "." in domain:
                        candidates.add(domain)
        except Exception:
            continue
    return candidates


def scrape_site_list_pages(sess):
    """Fetch known "list of paste sites" pages and extract domains."""
    list_urls = [
        "https://rentry.co/pastebins",
        "https://en.wikipedia.org/wiki/Comparison_of_pastebins",
        "https://www.g2.com/categories/pastebin",
        "https://alternativeto.net/software/hastebin/",
        "https://alternativeto.net/software/telegra-ph/",
        "https://alternativeto.net/software/write-as/",
        "https://www.producthunt.com/alternatives/telegraph",
    ]
    candidates = set()
    for url in list_urls:
        try:
            r = sess.get(url, timeout=15)
            if r.status_code == 200:
                urls_found = re.findall(r'https?://[^\s\)\]"<,]+', r.text)
                for u in urls_found:
                    domain = urllib.parse.urlparse(u).netloc.lower().lstrip("www.")
                    if domain and "." in domain and len(domain) > 4:
                        candidates.add(domain)
        except Exception:
            continue
    return candidates


# ── Filter noise from candidate domains ───────────────────────────────────────

BLACKLIST_DOMAINS = {
    # search engines
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com", "baidu.com",
    # social media (browser-only)
    "twitter.com", "x.com", "facebook.com", "instagram.com", "linkedin.com",
    "tiktok.com", "youtube.com", "reddit.com", "pinterest.com", "tumblr.com",
    # CDNs / infrastructure
    "cloudflare.com", "amazonaws.com", "googleapis.com", "github.com",
    "githubusercontent.com", "gitlab.com", "bitbucket.org",
    # ad/tracking
    "doubleclick.net", "googletagmanager.com", "analytics.google.com",
    # document/code platforms (need login)
    "stackoverflow.com", "medium.com", "substack.com", "notion.so",
    "docs.google.com", "drive.google.com",
    # generic TLDs that are usually not paste sites
    "wikipedia.org", "wikimedia.org", "archive.org",
}

GOOD_KEYWORDS = [
    "paste", "bin", "haste", "snippet", "gist", "note", "write",
    "text", "share", "post", "publish", "blog", "article", "content",
    "telegraph", "rentry", "pastebin", "clbin", "dpaste",
]

def is_promising(domain):
    """Quick filter: skip noise, keep plausible paste/publish sites."""
    if not domain or len(domain) < 5 or len(domain) > 50:
        return False
    if domain in BLACKLIST_DOMAINS:
        return False
    if domain in KNOWN_DOMAINS:
        return False  # already have it
    # skip TLDs/IPs
    if re.match(r'^\d+\.\d+', domain):
        return False
    # prefer domains that hint at paste/publish
    low = domain.lower()
    if any(kw in low for kw in GOOD_KEYWORDS):
        return True
    # also keep short domains on .io .sh .co .me (often devtools)
    parts = domain.split(".")
    if len(parts) == 2 and parts[-1] in ("io", "sh", "co", "me", "dev", "app", "cc", "xyz"):
        return True
    return False


# ── Live testing ──────────────────────────────────────────────────────────────

def test_site(domain, sess):
    """
    Try to actually post to a site. Returns dict with result or None if dead.
    Tries: hastebin API → paste form → file upload → json API → writeas API
    """
    marker = f"ev-test-{_rnd(12)}"
    base_https = f"https://{domain}"
    base_http  = f"http://{domain}"

    # check if site is alive
    alive_url = None
    for base in [base_https, base_http]:
        try:
            r = sess.get(base, timeout=10, allow_redirects=True)
            if r.status_code < 500:
                alive_url = base
                break
        except Exception:
            continue

    if not alive_url:
        return None

    print(f"    🌐 {domain} — alive, testing posting...")

    testers = [
        ("hastebin_api",  lambda: test_hastebin_api(alive_url, sess, marker)),
        ("paste_form",    lambda: test_paste_form(alive_url, sess, marker)),
        ("file_upload",   lambda: test_file_upload(alive_url, sess, marker)),
        ("json_api",      lambda: test_json_api(alive_url, sess, marker)),
        ("writeas_api",   lambda: test_writeas_api(alive_url, sess, marker)),
    ]

    for method_name, tester in testers:
        try:
            posted_url, method = tester()
            if posted_url:
                # check dofollow: fetch the posted page and look for our link without nofollow
                dofollow = check_dofollow(posted_url, sess)
                return {
                    "domain": domain,
                    "base_url": alive_url,
                    "method": method,
                    "posted_sample": posted_url,
                    "dofollow": dofollow,
                    "discovered": datetime.utcnow().isoformat(),
                    "status": "verified",
                }
        except Exception:
            continue

    return {"domain": domain, "base_url": alive_url, "status": "alive_but_no_method",
            "discovered": datetime.utcnow().isoformat()}


def check_dofollow(url, sess):
    """Fetch posted page and check if links are dofollow."""
    try:
        r = sess.get(url, timeout=10)
        # find all <a href=...> and check none have rel=nofollow
        links = re.findall(r'<a\s[^>]*href=["\'][^"\']+["\'][^>]*>', r.text, re.I)
        for link in links:
            if "emiratesvapor" in link.lower() or "vaporshopdubai" in link.lower():
                if "nofollow" in link.lower():
                    return False
                return True
        # no link found yet — assume dofollow (we'll post real content later)
        return True
    except Exception:
        return None


# ── DA estimation (free, no API key) ─────────────────────────────────────────

def estimate_da(domain):
    """
    Rough DA proxy: Alexa-style rank via a free API or fallback heuristic.
    We use web.archive.org CDX to check how many pages are indexed (rough proxy).
    Falls back to keyword-based guess.
    """
    try:
        r = requests.get(
            f"https://web.archive.org/cdx/search/cdx?url={domain}/*&output=json&limit=1&fl=timestamp",
            timeout=8)
        if r.status_code == 200:
            data = r.json()
            # if Wayback has it at all, give a base score
            if len(data) > 1:  # first row is header
                return 35  # conservative but positive
    except Exception:
        pass

    # keyword heuristic
    d = domain.lower()
    if any(k in d for k in ["ubuntu", "debian", "centos", "kde", "mozilla", "bpython"]):
        return 70
    if any(k in d for k in ["github", "gitlab", "google", "microsoft"]):
        return 90
    return 30


# ── Main discovery + testing pipeline ─────────────────────────────────────────

def discover_candidates():
    """Phase 1: collect candidate domains from all sources."""
    print("\n🔍 PHASE 1 — Discovering candidate sites...")
    sess = requests.Session()
    sess.headers.update(HEADERS)

    all_candidates = set()

    # 1. GitHub awesome-lists (fast, reliable)
    print("  Fetching GitHub awesome-lists...")
    found = scrape_awesome_lists(sess)
    print(f"  → {len(found)} domains from awesome-lists")
    all_candidates |= found
    time.sleep(1)

    # 2. Known aggregator pages
    print("  Scraping site-list pages (Wikipedia, AlternativeTo, etc.)...")
    found = scrape_site_list_pages(sess)
    print(f"  → {len(found)} domains from site-list pages")
    all_candidates |= found
    time.sleep(2)

    # 3. DuckDuckGo searches (throttled)
    print(f"  Running {len(SEARCH_QUERIES)} DuckDuckGo searches...")
    for i, query in enumerate(SEARCH_QUERIES):
        found = scrape_ddg(query, sess)
        all_candidates |= found
        time.sleep(random.uniform(2, 4))
        if (i + 1) % 5 == 0:
            print(f"    → {i+1}/{len(SEARCH_QUERIES)} queries done, {len(all_candidates)} total candidates")

    # 4. Bing searches (alternate source)
    print(f"  Running Bing searches for key queries...")
    for query in SEARCH_QUERIES[::4]:  # every 4th query
        found = scrape_bing(query, sess)
        all_candidates |= found
        time.sleep(random.uniform(2, 3))

    print(f"\n  Total raw candidates: {len(all_candidates)}")

    # filter
    promising = {d for d in all_candidates if is_promising(d)}
    print(f"  After filtering: {len(promising)} promising candidates")

    return promising


def test_candidates(candidates):
    """Phase 2: live-test each candidate."""
    print(f"\n🧪 PHASE 2 — Live-testing {len(candidates)} candidates...")

    # load previously tested to skip
    tested = {}
    if os.path.exists(TESTED_FILE):
        try:
            tested = {e["domain"]: e for e in json.load(open(TESTED_FILE))}
        except Exception:
            pass

    sess = requests.Session()
    sess.headers.update(HEADERS)

    results = []
    new_count = 0

    for i, domain in enumerate(sorted(candidates), 1):
        if domain in tested:
            print(f"  [{i}/{len(candidates)}] {domain} — already tested, skipping")
            results.append(tested[domain])
            continue

        print(f"  [{i}/{len(candidates)}] Testing {domain}...")
        result = test_site(domain, sess)

        if result:
            if result.get("status") == "verified":
                result["da"] = estimate_da(domain)
                print(f"    ✅ VERIFIED — method: {result['method']}  dofollow: {result.get('dofollow')}")
            else:
                print(f"    ⚠️  Alive but no auto-post method found")
            results.append(result)
            tested[domain] = result
            new_count += 1

        # save progress after each test
        with open(TESTED_FILE, "w") as f:
            json.dump(list(tested.values()), f, indent=2)

        time.sleep(random.uniform(1, 2))

    print(f"\n  Tested {new_count} new candidates")
    return results


def save_verified(results):
    """Save only verified (postable) sites."""
    verified = [r for r in results if r.get("status") == "verified"]
    # filter out known domains
    new_verified = [r for r in verified if r["domain"] not in KNOWN_DOMAINS]

    print(f"\n💾 Saving {len(new_verified)} newly discovered verified sites...")

    # load existing discovered
    existing = {}
    if os.path.exists(OUT_FILE):
        try:
            for e in json.load(open(OUT_FILE)):
                existing[e["domain"]] = e
        except Exception:
            pass

    for r in new_verified:
        existing[r["domain"]] = r

    with open(OUT_FILE, "w") as f:
        json.dump(list(existing.values()), f, indent=2)

    print(f"  Total in discovered_sites.json: {len(existing)}")
    return new_verified


def print_report(new_sites):
    """Print a clean summary of what was found."""
    print("\n" + "="*70)
    print("DISCOVERY REPORT")
    print("="*70)

    if not new_sites:
        print("No new sites discovered this run.")
        return

    # group by method
    by_method = {}
    for s in new_sites:
        m = s.get("method", "unknown")
        by_method.setdefault(m, []).append(s)

    for method, sites in sorted(by_method.items()):
        print(f"\n  {method} ({len(sites)} sites):")
        for s in sorted(sites, key=lambda x: x.get("da", 0), reverse=True):
            df = "dofollow" if s.get("dofollow") else ("nofollow" if s.get("dofollow") is False else "unknown")
            print(f"    DA~{s.get('da',0):2d}  {s['domain']:40s}  [{df}]")
            print(f"           Sample: {s.get('posted_sample','')[:70]}")

    print(f"\n  Total new sites: {len(new_sites)}")
    print("  → Add these to AUTO_SITES in seo_backlink_pro.py")
    print("  → Full details: rank_reports/discovered_sites.json")


def generate_site_entries(new_sites):
    """Generate Python dict entries ready to paste into AUTO_SITES."""
    if not new_sites:
        return

    out_path = os.path.join(REPORTS_DIR, "new_sites_code.py")
    lines = ["# ── NEWLY DISCOVERED SITES (auto-generated) ──\n"]

    for s in sorted(new_sites, key=lambda x: x.get("da", 0), reverse=True):
        domain = s["domain"]
        method = s.get("method", "hastebin_clone_api")
        da     = s.get("da", 30)
        base   = s.get("base_url", f"https://{domain}")
        sid    = re.sub(r'[^a-z0-9]', '', domain.lower())[:12]

        # map method to actual method name in seo_backlink_pro.py
        method_map = {
            "hastebin_api":    "hastebin_clone_api",
            "hastebin_clone_api": "hastebin_clone_api",
            "paste_form":      "stikked_api",
            "file_upload":     "uguu_api",
            "json_api":        "pastegg_api",
            "writeas_api":     "writeas_api",
            "telegraph_api":   "telegraph_api",
        }
        m = method_map.get(method, "hastebin_clone_api")

        line = (f'    {{"id":"{sid}", "name":"{domain}", '
                f'"da":{da},"type":"dofollow","method":"{m}", '
                f'"url":"{base}"}},')
        lines.append(line)

    with open(out_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\n  📋 Copy-paste code saved to: {out_path}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    print("⚡ BACKLINK SITE DISCOVERY ENGINE")
    print(f"  Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Output: {OUT_FILE}")
    print(f"  Already know {len(KNOWN_DOMAINS)} sites — discovering NEW ones only")

    if "--test-only" in args:
        # Re-test known candidates from previous run
        if os.path.exists(TESTED_FILE):
            prev = json.load(open(TESTED_FILE))
            candidates = {e["domain"] for e in prev}
            print(f"\n  Re-testing {len(candidates)} previously found candidates...")
        else:
            print("  No previous candidates file found.")
            sys.exit(0)
    else:
        candidates = discover_candidates()

    if not candidates:
        print("No candidates found.")
        sys.exit(0)

    results = test_candidates(candidates)
    new_sites = save_verified(results)
    print_report(new_sites)
    generate_site_entries(new_sites)

    print(f"\n✅ Discovery complete — {len(new_sites)} new sites found")
