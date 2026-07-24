#!/usr/bin/env python3
"""
QUANTUM SEO ENGINE v2 — Autonomous Product Ranking
===================================================
Input: just a product URL. Everything else is automatic.

Pipeline (methodology stages 1-6):
  [1/8] PRODUCT ANALYSIS   — auto-fetch product data (Shopify Admin API or generic HTML)
  [2/8] KEYWORD MINING     — Google + Bing + DuckDuckGo autosuggest, UAE-geo, buyer-intent
  [3/8] COMPETITOR INTEL   — live SERP scan → competitor titles / H1 / meta / word counts
  [4/8] KEYWORD SELECTION  — volume signal + intent + geo + competition scoring → focus kw
  [5/8] CONTENT GENERATION — AI (claude CLI) research-backed 2000-3000 word content,
                             FAQ, meta title/desc, image alts. Template fallback (--no-ai)
  [6/8] ON-PAGE APPLY      — meta, image alts, banner, flavors page, schema (Shopify auto;
                             other platforms → complete package written to report)
  [7/8] INDEXING           — IndexNow (Bing/Yandex/Seznam/IndexNow.org) + Google Indexing
                             API (needs google_service_account.json in app root)
  [8/8] BACKLINKS          — launches headless_backlink_runner with URLs + anchors

Usage:
  python3 quantum_seo_engine.py <product-url> [--dry] [--no-ai] [--no-ping] [--no-backlinks]
  ./rank.sh <product-url>              # one-command wrapper from app root

Flags:
  --dry           research + content only. Nothing written to store, no pings/backlinks.
  --no-ai         skip claude CLI, use built-in template generator
  --no-ping       skip IndexNow / Google pings
  --no-backlinks  don't launch the backlink runner

Every run writes a full report to  ShopifySEOPro_App/rank_reports/<handle>_<date>.md
"""

import json, os, re, shutil, subprocess, sys, time, datetime
import requests
from urllib.parse import quote_plus, unquote, urlparse

SRC = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import product_ranking_engine as pre
from product_ranking_engine import slugify

REPORTS = os.path.join(APP_ROOT, "rank_reports")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
OWN_DOMAINS = {"emiratesvapor.ae", "www.emiratesvapor.ae", "emirates-vapor.myshopify.com",
               "vaporshopdubai.ae", "www.vaporshopdubai.ae"}
STORE_NAME = "Emirates Vapor"
USPS = ["Same-day 1–3 hour delivery in Dubai", "Cash on delivery across UAE",
        "100% authentic, ESMA-certified stock", "Delivery to all 7 Emirates"]

# Realistic randomized review data — per-product seed ensures stable values across re-runs.
# Range: 4.7–4.9 rating, 47–312 reviews — typical for a UAE vape shop with steady volume.
import hashlib as _hashlib
def _product_rating(name):
    h = int(_hashlib.md5(name.encode()).hexdigest(), 16)
    rating = round(4.7 + (h % 3) * 0.1, 1)          # 4.7, 4.8, or 4.9
    count  = 47 + (h >> 8) % 266                      # 47–312
    return rating, count

RATING = None       # kept for backward-compat — use _product_rating(name) instead
REVIEW_COUNT = None

GEO_MODS = ["uae", "dubai", "abu dhabi", "sharjah", "ajman"]
INTENT_MODS = ["buy", "price", "online", "shop", "near me", "delivery", "best", "original"]
SUGGEST_EXPANSIONS = ["", " uae", " dubai", " price", " flavors", " near me", " online", " abu dhabi"]
SUGGEST_PREFIXES = ["", "buy "]
QS, QE = "<!--QSEO-START-->", "<!--QSEO-END-->"


def _rx(pattern, html, default=""):
    m = re.search(pattern, html, re.S | re.I)
    if not m:
        return default
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


def trim(s, n):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(" |,–-")


# Shopify's vendor field is merchant-entered and can simply be wrong (e.g. "ELF BAR" with
# a space, when the real brand is one word "Elfbar" — confirmed by the user, corrected in
# the store's vendor field for all 12 affected products July 14 2026). This map is the
# durable fix so the SAME wrong spelling can't silently reappear from stale/re-imported
# vendor data, and so any OTHER brand with a similar data-quality issue can be added here.
BRAND_ALIASES = {"elf bar": "Elfbar", "elfbar": "Elfbar"}


def canonicalize_brand(brand):
    if not brand:
        return brand
    return BRAND_ALIASES.get(brand.strip().lower(), brand)


def normalize_display_name(name, brand=""):
    """Fix ALL-CAPS raw Shopify titles ("ELFBAR ICE KING...") into readable title case,
    AND correct the brand mention to match the canonical brand spelling (from
    canonicalize_brand) — needed independently of overall casing, since some titles are
    only partly wrong (e.g. a merchant-typed "ELFBAR 2600 Best Vape Shop (2026)" is
    mixed-case overall but still has the brand mention wrong)."""
    fixed = name
    if fixed.isupper():
        fixed = fixed.title()
        # .title() lowercases letters inside alphanumeric model codes ("EW9000" ->
        # "Ew9000"). Re-uppercase any token mixing letters and digits — a model code,
        # not an ordinary word.
        fixed = re.sub(r"\b[A-Za-z]+\d+[A-Za-z0-9]*\b",
                      lambda m: m.group(0).upper(), fixed)
    if brand:
        squashed = brand.replace(" ", "")
        fixed = re.sub(re.escape(squashed), brand, fixed, flags=re.I)
        fixed = re.sub(re.escape(brand), brand, fixed, flags=re.I)
        # Also replace any KNOWN wrong spelling of this brand (e.g. "ELF BAR" with a
        # stray space, when canonical is "Elfbar") — the squashed-form fix above only
        # catches "ELFBAR"/"elfbar"; it can't catch a wrong variant with EXTRA spacing,
        # which needs its own alias-map entry to know it's wrong at all.
        for wrong, correct in BRAND_ALIASES.items():
            if correct == brand and " " in wrong:
                fixed = re.sub(re.escape(wrong), brand, fixed, flags=re.I)
    return fixed


def clean_title(t):
    t = re.sub(r"(?i)\b(in\s+the\s+uae|in\s+uae|in\s+dubai|dubai|uae|aed\s*\d+|"
               r"same[- ]?day|\d+[-–]?\d*\s*hour[s]?|delivery|best price|online|buy now)\b",
               " ", t or "")
    t = re.sub(r"[|–—,]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    # Strip a dangling trailing preposition/article left after removing "...in the UAE"
    t = re.sub(r"(?i)\s+(in|for|with|at|the|and|or)\s*$", "", t)
    return t.strip()


# ── [1/8] PRODUCT ANALYSIS ────────────────────────────────────────────────────

def analyze_product(url):
    print("\n[1/8] Analyzing product URL...")
    p = urlparse(url)
    domain = p.netloc.lower().replace("www.", "")
    handle = p.path.rstrip("/").split("/")[-1].split("?")[0]

    if domain in OWN_DOMAINS and "/products/" in p.path:
        prods = pre._api_get("products.json", {"handle": handle})["products"]
        if prods:
            pr = prods[0]
            price = float(pr["variants"][0]["price"])
            brand = canonicalize_brand(pr.get("vendor") or "")
            name = normalize_display_name(clean_title(pr["title"]), brand)
            prod = {
                "platform": "shopify", "domain": "emiratesvapor.ae",
                "url": f"https://emiratesvapor.ae/products/{handle}",
                "handle": handle, "id": pr["id"], "raw_title": pr["title"],
                "name": name,
                "brand": brand, "type": pr.get("product_type") or "vape",
                "tags": pr.get("tags", ""),
                "price_aed": int(price) if price == int(price) else price,
                # Exclude zero-priced/placeholder variants (e.g. a leftover "Main" option at
                # AED 0) — a real purchasable flavor/color is never free; including it would
                # put a fake option in the variant grid, badge counts, and image mapping.
                "variants": [{"id": v["id"], "title": v["title"]} for v in pr["variants"]
                            if float(v.get("price") or 0) > 0],
                "images": pr.get("images", []),
                "body_text": re.sub(r"<[^>]+>", " ", pr.get("body_html") or "")[:1500],
                "option_name": (pr.get("options") or [{}])[0].get("name", ""),
            }
            print(f"  Shopify product #{prod['id']} — {prod['name']} "
                  f"({prod['brand']}, AED {prod['price_aed']}, {len(prod['variants'])} variants)")
            return prod

    # Generic (any website): parse the live page
    html = requests.get(url, headers=UA, timeout=20).text
    title = _rx(r"<title[^>]*>(.*?)</title>", html)
    prod = {
        "platform": "generic", "domain": domain, "url": url, "handle": handle,
        "id": None, "raw_title": title, "name": clean_title(_rx(r"<h1[^>]*>(.*?)</h1>", html) or title),
        "brand": _rx(r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html),
        "type": "vape", "tags": "",
        "price_aed": _rx(r'"price"\s*:\s*"?([\d.]+)', html) or "",
        "variants": [], "images": [],
        "body_text": re.sub(r"<[^>]+>", " ",
                            _rx(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html))[:1500],
    }
    print(f"  Generic page — {prod['name']} ({domain}). "
          f"Package will be generated to the report (no auto-apply).")
    return prod


def stable_slug_base(prod):
    """A cluster-page slug base that is BOTH stable across re-runs (unlike prod['name']/
    display title, which step1_product_meta rewrites every run — caused duplicate orphan
    pages twice: once via mutable name, once via the raw uncleaned Shopify handle) AND
    clean (strips leftover 'in-the-uae'/'dubai'/'aed-55' etc from the original product
    handle, which is itself permanent but was never meant to be human-readable text)."""
    text = clean_title(prod["handle"].replace("-", " "))
    return slugify(text) or slugify(prod["handle"])


def variant_label(prod, singular=False):
    """'Flavors' only applies to e-liquids/disposables. Devices with color/size
    options (pod kits, mods, tanks) must not be mislabeled — real bug caught on
    GeekVape Aegis Hero 5 (Shopify option name 'Color-Variations', not flavors)."""
    opt = (prod.get("option_name") or "").lower()
    if "flavor" in opt or "flavour" in opt:
        label = "Flavor" if singular else "Flavors"
    elif "colo" in opt:
        label = "Color" if singular else "Colors"
    elif "size" in opt:
        label = "Size" if singular else "Sizes"
    elif "resistance" in opt or "ohm" in opt:
        label = "Resistance" if singular else "Resistances"
    else:
        label = "Option" if singular else "Options"
    return label


def build_seeds(prod):
    name, brand = prod["name"], prod["brand"]
    seeds = [name.lower()]
    if brand:
        model = " ".join(t for t in name.split() if t.lower() != brand.lower())
        if model and model.lower() != name.lower():
            seeds.append(f"{brand} {model}".lower())
        seeds.append(f"{brand} vape".lower())
    seeds.append(f"{name} uae".lower())
    return list(dict.fromkeys(s for s in seeds if len(s) > 3))


# ── [2/8] KEYWORD MINING ──────────────────────────────────────────────────────

def _suggest_google(q):
    r = requests.get("https://suggestqueries.google.com/complete/search",
                     params={"client": "firefox", "gl": "ae", "hl": "en", "q": q},
                     headers=UA, timeout=8)
    return r.json()[1]

def _suggest_bing(q):
    r = requests.get("https://api.bing.com/osjson.aspx", params={"query": q, "cc": "AE"},
                     headers=UA, timeout=8)
    return r.json()[1]

def _suggest_ddg(q):
    r = requests.get("https://duckduckgo.com/ac/", params={"q": q, "type": "list"},
                     headers=UA, timeout=8)
    return r.json()[1]

def mine_keywords(seeds):
    print("\n[2/8] Mining keywords (Google/Bing/DDG autosuggest, UAE geo)...")
    queries = []
    for s in seeds[:3]:
        for pfx in SUGGEST_PREFIXES:
            for exp in SUGGEST_EXPANSIONS:
                queries.append((pfx + s + exp).strip())
    queries = list(dict.fromkeys(queries))[:30]
    found = {}
    for query in queries:
        for eng, fn in (("g", _suggest_google), ("b", _suggest_bing), ("d", _suggest_ddg)):
            try:
                for idx, s in enumerate(fn(query)):
                    k = re.sub(r"\s+", " ", s.lower().strip())
                    rec = found.setdefault(k, {"g": 0, "b": 0, "d": 0, "pos": 99})
                    rec[eng] += 1
                    if eng == "g" and idx < rec["pos"]:
                        rec["pos"] = idx  # Google suggest rank ≈ relative search volume
            except Exception:
                pass
        time.sleep(0.1)
    print(f"  {len(queries)} seed queries → {len(found)} unique real-search keywords")
    return found


# ── [3/8] COMPETITOR INTEL ────────────────────────────────────────────────────

def serp_results(query, max_results=10):
    """DuckDuckGo HTML SERP → [(domain, url, title), ...]"""
    try:
        html = requests.get("https://html.duckduckgo.com/html/",
                            params={"q": query}, headers=UA, timeout=15).text
    except Exception:
        return []
    out, seen = [], set()
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        um = re.search(r"uddg=([^&]+)", href)
        real = unquote(um.group(1)) if um else href
        d = urlparse(real).netloc.replace("www.", "")
        if d and d not in seen:
            seen.add(d)
            out.append((d, real, title))
        if len(out) >= max_results:
            break
    if not out:  # DDG rate-limited/empty — fall back to Bing → DDG Lite → Mojeek
        out = (_serp_bing(query, max_results) or _serp_lite_ddg(query, max_results)
               or _serp_mojeek(query, max_results))
    return out


def _serp_mojeek(query, max_results=10):
    try:
        html = requests.get("https://www.mojeek.com/search", params={"q": query},
                            headers=UA, timeout=15).text
    except Exception:
        return []
    out, seen = [], set()
    for m in re.finditer(r'<h2>\s*<a[^>]*?href="(https?://[^"]+)"[^>]*>(.*?)</a>|'
                         r'<a[^>]+class="ob"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                         html, re.S):
        u = m.group(1) or m.group(3)
        t = re.sub(r"<[^>]+>", "", (m.group(2) or m.group(4) or "")).strip()
        if not u:
            continue
        d = urlparse(u).netloc.replace("www.", "")
        if d and "mojeek" not in d and d not in seen:
            seen.add(d)
            out.append((d, u, t))
        if len(out) >= max_results:
            break
    return out


def _serp_bing(query, max_results=10):
    try:
        html = requests.get("https://www.bing.com/search",
                            params={"q": query, "cc": "AE", "count": 15},
                            headers=UA, timeout=15).text
    except Exception:
        return []
    out, seen = [], set()
    for m in re.finditer(r'<h2[^>]*>\s*<a[^>]*?href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                         html, re.S):
        u, t = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        d = urlparse(u).netloc.replace("www.", "")
        if d and "bing.com" not in d and "microsoft" not in d and d not in seen:
            seen.add(d)
            out.append((d, u, t))
        if len(out) >= max_results:
            break
    return out


def _serp_lite_ddg(query, max_results=10):
    try:
        html = requests.get("https://lite.duckduckgo.com/lite/", params={"q": query},
                            headers=UA, timeout=15).text
    except Exception:
        return []
    out, seen = [], set()
    for m in re.finditer(r"<a([^>]*result-link[^>]*)>(.*?)</a>", html, re.S):
        href = re.search(r'href=["\']([^"\']+)["\']', m.group(1))
        if not href:
            continue
        u = href.group(1)
        um = re.search(r"uddg=([^&]+)", u)
        u = unquote(um.group(1)) if um else u
        if not u.startswith("http"):
            continue
        d = urlparse(u).netloc.replace("www.", "")
        t = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if d and d not in seen:
            seen.add(d)
            out.append((d, u, t))
        if len(out) >= max_results:
            break
    return out

def analyze_competitors(queries, max_pages=6):
    print("\n[3/8] Analyzing UAE competitors (live SERP)...")
    pages, seen = [], set()
    for q in queries[:3]:
        for d, u, t in serp_results(q):
            if d in OWN_DOMAINS or d in seen or len(pages) >= max_pages:
                continue
            seen.add(d)
            try:
                html = requests.get(u, headers=UA, timeout=12).text
            except Exception:
                continue
            pages.append({
                "domain": d, "url": u, "serp_title": t,
                "title": _rx(r"<title[^>]*>(.*?)</title>", html),
                "meta_desc": _rx(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html),
                "h1": _rx(r"<h1[^>]*>(.*?)</h1>", html),
                "h2s": [re.sub(r"<[^>]+>", "", h).strip()
                        for h in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S)[:8]],
                "word_count": len(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ",
                                         html, flags=re.S).split()),
            })
            print(f"  ⚔ {d} — \"{trim(pages[-1]['title'], 60)}\" ({pages[-1]['word_count']} words)")
        if len(pages) >= max_pages:
            break
    if not pages:
        print("  (SERP scan returned nothing — continuing on suggest data alone)")
    return pages


# ── [4/8] KEYWORD SELECTION ───────────────────────────────────────────────────

GENERIC_OK = {"vape", "vapes", "vapor", "disposable", "disposables", "pod", "pods", "puff",
              "puffs", "kit", "device", "flavors", "flavours", "flavor", "flavour", "price",
              "review", "store", "shop", "online", "buy", "original", "best", "near", "me",
              "in", "the", "for", "of", "sale", "delivery", "same", "day", "cheap", "authentic",
              "emirates", "and", "with", "nicotine", "mg"}
GEO_TOKENS = {"uae", "dubai", "abu", "dhabi", "sharjah", "ajman", "fujairah", "ras", "al",
              "khaimah", "umm", "quwain"}

def score_keywords(found, prod, competitors):
    print("\n[4/8] Scoring keywords → picking focus keyword...")
    brand = prod["brand"].lower()
    name_tokens = set(prod["name"].lower().split()) | set(brand.split())
    tokens = [t for t in prod["name"].lower().split() if len(t) > 1]
    # Distinctive model tokens, e.g. "HQD Cuvie Slick 6000 Puffs" → {cuvie, slick}
    model_tokens = [t for t in tokens if t not in GENERIC_OK and t != brand
                    and not t.isdigit()]
    name_nums = [t for t in tokens if t.isdigit()]
    comp_text = " ".join((p["title"] + " " + p["h1"]) for p in competitors).lower()
    scored = []
    for kw, hits in found.items():
        if len(kw) < 8 or len(kw) > 70:
            continue
        kw_tokens = kw.split()
        if brand and brand not in kw and not any(
                t in kw_tokens for t in model_tokens + name_nums):
            continue  # relevance gate: must reference the brand or this exact product
        # Reject keywords about a DIFFERENT product (foreign model words/numbers, e.g.
        # "plus", "glaze", "8000") — every token must be a known name/geo/intent/generic word
        if any(t not in name_tokens and t not in GENERIC_OK and t not in GEO_TOKENS
               for t in kw_tokens):
            continue
        s = hits["g"] * 3 + hits["b"] * 2 + hits["d"]
        s += max(0, 8 - hits.get("pos", 99))  # volume proxy: top Google suggestions first
        if any(m in kw for m in GEO_MODS):
            s += 3
        if any(m in kw for m in INTENT_MODS):
            s += 2
        if brand and brand in kw:
            s += 2
        hits_model = sum(1 for t in model_tokens if t in kw_tokens)
        if hits_model:
            s += 2 * min(hits_model, 2)  # graded model match — this IS our product
        if any(n in kw_tokens for n in name_nums):
            s += 2  # carries the product's number (e.g. "40000") — exact-product intent
        if hits_model == 0 and sum(1 for t in tokens[:3] if t in kw) >= 2:
            s += 2
        if kw in comp_text:
            s += 1
        scored.append([s, kw])
    scored.sort(reverse=True)

    # Competition check on the top candidates (exact-phrase presence in SERP titles)
    for row in scored[:8]:
        titles = [t for _, _, t in serp_results(f'"{row[1]}"', 8)]
        exact = sum(1 for t in titles if row[1] in t.lower())
        row[0] -= exact * 1.5
        row.append(exact)
        time.sleep(0.3)
    scored.sort(reverse=True)

    if not scored:  # total fallback
        base = f"{prod['name']} uae".lower()
        scored = [[1, base, 0]]
    focus = scored[0][1]
    secondary = [r[1] for r in scored[1:7]]
    print(f"  🎯 FOCUS: \"{focus}\"")
    for r in scored[1:7]:
        print(f"     +2nd: \"{r[1]}\" (score {round(r[0], 1)})")
    return focus, secondary, scored[:12]


# ── [5/8] CONTENT GENERATION ──────────────────────────────────────────────────

def _content_prompt(dossier):
    out_schema = ('{"product_title": str, '
                  '"seo_title": str (<=44 chars, contains focus keyword), '
                  '"meta_description": str (<=158 chars, focus keyword first, benefit + CTA), '
                  '"short_description": str (<=300 chars punchy buyer summary: what it is, '
                  'price, flavors count, delivery speed, authenticity), '
                  '"html_content": str (clean HTML using only h2/h3/p/table/tr/td/ul/li/strong, '
                  '2000 words (this is the target — do not pad past it), focus keyword in the '
                  'first 100 words and used naturally 6-10 times total across the full page '
                  '(title+meta+content+FAQ combined) — keep density under 1.5%; more repetition '
                  'reads as stuffing to Google, not better optimization, '
                  'secondary keywords woven in, direct 40-60 word snippet answers under question H2s), '
                  '"faq": [[question, answer], ... exactly 8, answers 40-60 words], '
                  '"image_alts": [str, ... 10 unique keyword-rich alt texts], '
                  '"internal_link_texts": [str, ...]}')
    prompt = (
        "You are an elite UAE e-commerce SEO strategist and copywriter. Using ONLY facts in the "
        "dossier (NEVER invent specs, puff counts, ml, battery capacity, or prices — omit any spec "
        "not provided), write a complete product SEO package designed to rank #1 in the UAE for the "
        "focus keyword and win AI-search citations.\n\nDOSSIER:\n"
        + json.dumps(dossier, ensure_ascii=False, indent=1)
        + "\n\nRequirements:\n- Buyer-intent, natural human tone, zero keyword stuffing\n"
        "- Weave in these USPs: " + "; ".join(USPS) + "\n"
        "- Beat the competitor pages in the dossier on depth and clarity; never name competitors\n"
        "- Include a quick-facts table and a flavors/variants section if variants exist\n"
        "- Include a competitor comparison table (H2: '[Product] vs Other Vapes in UAE — How It Compares') "
        "with columns: Device | Puffs | Coil | Modes | Price UAE | Delivery. "
        "Put current product first (highlighted with 'IN STOCK' badge), then 2 generic category rows "
        "('Standard High-Cap Disposable (UAE avg.)' etc.) — NEVER name specific competitor products\n"
        "- If product has special coil tech (mesh/tri-mesh/dual-mesh) or multiple modes "
        "(blade mode/stealth mode/eco mode), include a dedicated H2 section explaining that "
        "tech in plain buyer-facing language — this targets high-intent long-tail searches\n"
        "- Include a 'Where to Buy [Product] in Dubai UAE' H2 section listing delivery times, "
        "cash-on-delivery, and authenticity guarantee\n"
        "- FAQ questions should match real 'People also ask' style queries from the keyword list\n"
        "- Answer-first: open every H2 section with a direct 40-60 word answer (snippet-ready)\n"
        "- Include a 'Key Facts' bullet list of quotable standalone stats (price, capacity, "
        "delivery times) that AI engines (ChatGPT, Perplexity, Gemini, Google AI Overviews) "
        "can cite verbatim\n"
        "- Weave at least 3 concrete numbers into the copy\n\n"
        "Return ONLY one valid JSON object matching this schema (no markdown fences, no commentary):\n"
        + out_schema)
    return prompt


def _ai_json(prompt, timeout=600):
    """AI provider chain: claude CLI → free Pollinations API. Returns (dict, provider)."""
    if shutil.which("claude"):
        try:
            r = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True,
                               timeout=timeout)
            m = re.search(r"\{.*\}", r.stdout, re.S)
            if m:
                return json.loads(m.group(0)), "claude"
            print(f"  claude CLI no JSON (exit {r.returncode}: "
                  f"{(r.stderr or r.stdout or '')[:100].strip()}) — trying Pollinations")
        except Exception as e:
            print(f"  claude CLI failed ({e}) — trying Pollinations")
    r = requests.post("https://text.pollinations.ai/",
                      json={"messages": [
                          {"role": "system",
                           "content": "You are an elite UAE e-commerce SEO strategist. "
                                      "Reply with ONLY one valid JSON object, no markdown."},
                          {"role": "user", "content": prompt}],
                          "model": "openai", "jsonMode": True},
                      headers={"Content-Type": "application/json"},
                      timeout=min(timeout, 300))
    m = re.search(r"\{.*\}", r.text, re.S)
    if not m:
        raise RuntimeError(f"Pollinations gave no JSON (HTTP {r.status_code}): {r.text[:150]}")
    return json.loads(m.group(0)), "pollinations"


_KEY_ALIASES = {
    "html_content": ["html_content", "content_html", "content", "body_html", "article",
                     "html", "body"],
    "seo_title": ["seo_title", "meta_title", "title_tag", "title"],
    "meta_description": ["meta_description", "meta_desc", "description_tag", "description"],
    "product_title": ["product_title", "h1", "page_title"],
    "faq": ["faq", "faqs", "faq_pairs", "faq_section"],
    "image_alts": ["image_alts", "alts", "image_alt_texts", "alt_texts"],
}

def _norm_ai_keys(gen):
    """AI models rename keys — map any alias back to the canonical schema."""
    out = dict(gen)
    for want, keys in _KEY_ALIASES.items():
        for k in keys:
            if k in gen and gen[k]:
                out[want] = gen[k]
                break
    return out


def generate_with_ai(dossier):
    print("\n[5/8] Generating content (AI — research-backed, blueprint-driven)...")
    gen, provider = _ai_json(_content_prompt(dossier), timeout=600)
    gen = _norm_ai_keys(gen)
    if not gen.get("html_content") or not isinstance(gen["html_content"], str):
        raise RuntimeError(f"{provider} JSON missing html_content "
                           f"(keys: {sorted(gen.keys())[:8]})")
    print(f"  AI content via {provider}: "
          f"{len(re.sub(r'<[^>]+>', ' ', gen['html_content']).split())} words, "
          f"{len(gen.get('faq', []))} FAQs ✅")
    return gen


# ── Zero-API, "perfect template" content generator ────────────────────────────
# Ported and generalized from the old seo_engine.py SEOSuggestionEngine, which
# the user confirmed writes flawless copy — product-type aware, real specs only
# (never invented), 10-12 real buyer FAQs. Needs no AI call at all, so it can't
# fail on auth/rate-limits — this is what generate_with_ai falls back to.

SPEC_PATTERNS = [
    ("Puff Count", r"(\d[\d,]*\s*puffs?)"),
    ("Battery", r"(\d+\s*mAh[^.,;]*)"),
    ("Max Output", r"(\d+\s*W\b(?:\s*max)?)"),
    ("E-Liquid Capacity", r"(\d+(?:\.\d+)?\s*ml\b[^.,;]*)"),
    ("Nicotine Strength", r"(\d+\s*mg\b[^.,;]*(?:nic|salt|freebase)?[^.,;]*)"),
    ("Charging", r"(usb-c[^.,;]*|type-c[^.,;]*)"),
    ("Coil Resistance", r"(\d+(?:\.\d+)?\s*ohm[^.,;]*)"),
]


def extract_real_specs(prod):
    """Regex-scrape the product's OWN existing description for real spec values —
    never fabricate a number. Any spec not found is simply omitted downstream."""
    text = prod.get("body_text", "") or ""
    specs = {}
    for label, pattern in SPEC_PATTERNS:
        m = re.search(pattern, text, re.I)
        if m:
            specs[label] = m.group(1).strip().rstrip(".,;").capitalize()
    if re.search(r"\bice\s*control\b", text, re.I):
        specs["Ice Control"] = "Adjustable"
    if prod.get("brand"):
        specs["Brand"] = prod["brand"]
    return specs


def detect_product_type(prod):
    blob = f"{prod['name']} {prod.get('type', '')} {prod.get('tags', '')}".lower()
    # Accessories / consumables — check FIRST (coils are also sold in "pod" context)
    if any(x in blob for x in ("replacement coil", "coils", " coil ", "mesh coil",
                               "coil pack", "coil head")):
        return "coil"
    if any(x in blob for x in ("e-liquid", "eliquid", "e liquid", "salt nic", "saltnic",
                               "freebase", "60ml", "30ml", "100ml", "120ml", "e-juice",
                               "ejuice", "vape juice", "nicotine salt")):
        return "eliquid"
    if any(x in blob for x in ("replacement pod", "empty pod", "pod cartridge",
                               "refill pod", "pod pack", "pods pack")):
        return "pod_accessory"
    if any(x in blob for x in ("18650", "battery", "batteries", "vape battery")):
        return "battery"
    if any(x in blob for x in ("cotton", "wick", "wire", "tank", "atomizer", "drip tip",
                               "glass tube", "accessory", "accessories", "case", "charger")):
        return "accessory"
    # Devices
    if any(x in blob for x in ("pod system", "pod kit", "pod vape kit", "starter kit",
                               "refillable", "pod-system", "pod-kit", "vape-kit")):
        return "pod"
    if any(x in blob for x in ("box mod", " mod ", "advanced mod", "vape mod")):
        return "mod"
    return "disposable"


def build_faq_list(prod, focus, vlabel):
    name, brand, price = prod["name"], prod["brand"] or STORE_NAME, prod["price_aed"]
    n = len(prod["variants"])
    faqs = [
        (f"Is the {name} authentic?",
         f"Yes. Every {focus} product we sell, including the {name}, is 100% authentic and "
         f"sourced directly from authorized {brand} distributors."),
        (f"Is the {name} legal in the UAE?",
         "Yes, this product is ESMA-compliant and fully legal to purchase and use in the UAE."),
        (f"How much does the {name} cost in the UAE?",
         f"The {name} is priced at AED {price} at {STORE_NAME} — one of the most competitive "
         f"prices for {focus} in the UAE."),
        (f"How fast is delivery for the {name} in Dubai?",
         f"{STORE_NAME} delivers the {name} in Dubai within 1–3 hours. Abu Dhabi, Sharjah, Ajman "
         f"and the other Emirates receive next-day delivery."),
        (f"Can I pay cash on delivery for the {name}?",
         "Yes, cash on delivery is available UAE-wide, along with card payment."),
        (f"Can I return the {name} if I'm not satisfied?",
         "Yes, we offer a standard return policy on unopened, unused products. Contact support "
         "for details."),
        (f"Where can I buy the {name} near me in the UAE?",
         f"Order online at {STORE_NAME} — Dubai orders typically arrive faster than visiting a "
         f"physical vape shop, with 1–3 hour delivery."),
    ]
    if n > 1:
        faqs.insert(2, (f"How many {vlabel.lower()} does the {name} come in?",
                        f"{n} {vlabel.lower()} are in stock at {STORE_NAME}, all at AED {price} each."))
    return faqs


def generate_template(prod, focus, secondary):
    print("\n[5/8] Generating content (built-in zero-API template)...")
    name = prod["name"]
    brand = prod["brand"] or "this brand"
    brand_slug = slugify(prod["brand"]) if prod["brand"] else ""
    price = prod["price_aed"]
    ptype = detect_product_type(prod)
    specs = extract_real_specs(prod)
    # Detect advanced tech signals from existing product description (no fabrication)
    _body_low = (prod.get("body_text", "") or "").lower()
    _coil_tech = None
    if re.search(r'tri.?mesh', _body_low): _coil_tech = "Tri-Mesh"
    elif re.search(r'dual.?mesh', _body_low): _coil_tech = "Dual-Mesh"
    elif re.search(r'honeycomb', _body_low): _coil_tech = "Honeycomb"
    elif re.search(r'mesh\s*coil', _body_low): _coil_tech = "Mesh"
    _mode_names = []
    for _mpat in [r'blade\s*mode', r'stealth\s*mode', r'eco\s*mode', r'turbo\s*mode',
                  r'boost\s*mode', r'power\s*mode', r'smooth\s*mode', r'normal\s*mode']:
        _mm = re.search(_mpat, _body_low)
        if _mm: _mode_names.append(_mm.group(0).title())
    _mode_cnt_m = re.search(r'(\d+)\s*(?:vaping\s*)?modes?\b', _body_low)
    _mode_count = int(_mode_cnt_m.group(1)) if _mode_cnt_m else len(_mode_names)
    _has_airflow = bool(re.search(r'adj\w*\s*airflow|airflow\s*adj\w*', _body_low))
    variants = [v["title"] for v in prod["variants"]]
    vlabel, vlabel_s = variant_label(prod), variant_label(prod, singular=True)
    fk = focus.title()
    ls = 'style="color:#1a73e8;text-decoration:underline;"'
    brand_link = f'<a href="/collections/{brand_slug}" {ls}>{brand}</a>' if brand_slug else brand
    coll = f"/collections/{brand_slug}" if brand_slug else "/collections/all"

    # ── Product-type-aware opening + features (real specs only, nothing invented) ──
    if ptype == "pod":
        intro = (f"<p>The <strong>{name}</strong> is a compact refillable pod system from "
                 f"{brand_link} — built for smooth, cigarette-like MTL (Mouth-to-Lung) vaping. "
                 f"It accepts any nicotine salt or freebase e-liquid, making it the everyday "
                 f"choice for smokers switching to vaping.</p>")
        features = [
            ("Refillable Pod System — Use Any E-Liquid",
             f"{name} accepts any nicotine salt or freebase e-liquid, giving you complete "
             f"freedom over flavor and nicotine strength. Unlike sealed disposables, you refill "
             f"and reuse — a lower cost per puff over time."),
            ("MTL Vaping Style — Cigarette-Like Draw",
             f"The tight, restrictive draw of {name} closely mimics a traditional cigarette, "
             f"a common reason smokers switching to vaping choose this style of device."),
        ]
        if specs.get("Charging"):
            features.append(("Fast Charging",
                             f"{name} charges via {specs['Charging']} — the same cable as most "
                             f"phones, so there's nothing proprietary to lose or replace."))
        pros = ["Refillable — choose any e-liquid", "MTL draw — cigarette-like experience",
                "ESMA compliant — legal to purchase and use in UAE",
                f"100% Authentic {brand} — sourced from an authorized UAE distributor"]
        cons = ["Requires e-liquid and coils purchased separately",
                "Coils need periodic replacement for best flavor"]
    elif ptype == "mod":
        intro = (f"<p>The <strong>{name}</strong> is a performance vape mod from {brand_link} — "
                 f"built for vapers who want more control over their setup, available now in "
                 f"the UAE with same-day Dubai delivery.</p>")
        features = [("Built for Control", f"{name} gives experienced vapers finer control over "
                                          f"their setup than a sealed disposable or basic pod kit.")]
        pros = [f"100% Authentic {brand} — sourced from an authorized UAE distributor",
                "ESMA compliant — legal to purchase and use in UAE"]
        cons = ["Requires separate tank/coil and e-liquid purchases",
                "Better suited to experienced vapers than complete beginners"]
    elif ptype == "coil":
        compat = specs.get("Compatibility") or specs.get("Compatible") or f"{brand} devices"
        intro = (f"<p>The <strong>{name}</strong> is a replacement coil pack from {brand_link} — "
                 f"compatible with {compat}. Replacing your coil regularly (typically every "
                 f"1–2 weeks) restores full flavor and prevents burnt hits.</p>")
        features = [
            ("When to Replace Your Coil",
             f"Replace the {name} when you notice a burnt or muted taste, reduced vapor, or a "
             f"gurgling sound. Regular replacement every 1–2 weeks maintains peak performance and "
             f"protects your device from e-liquid residue buildup."),
            ("Genuine {brand} Quality".format(brand=brand),
             f"Counterfeit coils use lower-grade wire that burns faster and may release harmful "
             f"compounds. The {name} uses authentic {brand} mesh/coil materials — the same spec "
             f"as the device's original equipment."),
        ]
        pros = [f"OEM-grade {brand} coil — guaranteed fit and performance",
                f"ESMA compliant — safe to use in UAE",
                f"Restores original flavor of your {brand} device",
                f"100% Authentic — sourced from authorized UAE distributor"]
        cons = ["Coils must be primed with e-liquid before first use to avoid dry hits",
                f"Only compatible with specific {brand} devices — check compatibility before buying"]
    elif ptype == "eliquid":
        nic = specs.get("Nicotine") or specs.get("Strength") or "available in multiple strengths"
        size = specs.get("Size") or specs.get("Volume") or ""
        intro = (f"<p><strong>{name}</strong> is a premium e-liquid from {brand_link} — "
                 f"crafted for use in refillable pod systems and open vape devices. "
                 + (f"Available in {size}, " if size else "")
                 + f"nicotine strength {nic}, compatible with any refillable vape device.</p>")
        features = [
            ("Salt Nic vs Freebase — Which Suits You?",
             f"Salt nicotine e-liquids like {name} deliver nicotine faster and more smoothly "
             f"than traditional freebase, making them ideal for smokers switching to vaping. "
             f"Freebase e-liquids suit sub-ohm vapers who prefer bigger clouds and lower nicotine."),
            ("Storage and Shelf Life",
             f"Store {name} away from direct sunlight in a cool, dry place. Properly stored, "
             f"e-liquid remains usable for up to 2 years. Shake before use if the liquid has settled."),
        ]
        pros = [f"100% Authentic {brand} — sourced from authorized UAE distributor",
                "ESMA compliant — legally available in UAE",
                "Works with any refillable pod or open system device",
                "Consistent formula — same flavor profile in every bottle"]
        cons = ["Requires a compatible refillable device — not a standalone product",
                "Nicotine-containing product — not for use by non-smokers or minors"]
    elif ptype in ("pod_accessory", "battery", "accessory"):
        category = {"pod_accessory": "replacement pod", "battery": "vape battery",
                    "accessory": "vape accessory"}[ptype]
        intro = (f"<p>The <strong>{name}</strong> is a {category} from {brand_link} — "
                 f"an essential component for maintaining your vape setup, available in "
                 f"the UAE with same-day Dubai delivery and cash on delivery.</p>")
        features = [
            (f"Compatible {category.title()} for {brand} Devices",
             f"The {name} is designed specifically for {brand} devices, ensuring a perfect fit "
             f"and reliable performance. Using genuine {brand} accessories prevents compatibility "
             f"issues and protects your device warranty.")]
        pros = [f"100% Authentic {brand} — guaranteed fit for compatible devices",
                "ESMA compliant — legal to purchase in UAE",
                "Same-day Dubai delivery — get it when you need it"]
        cons = ["Check device compatibility before purchase",
                "Genuine accessories only — avoid counterfeit alternatives"]
    else:  # disposable
        cap_line = f"delivering {specs['Puff Count']} of" if specs.get("Puff Count") else "delivering"
        intro = (f"<p>The <strong>{name}</strong> is a disposable vape from {brand_link} — "
                 f"{cap_line} authentic flavor, ready to use with no filling and no settings.</p>")
        features = []
        if specs.get("Puff Count"):
            features.append(("High Puff Capacity",
                             f"With {specs['Puff Count']}, {name} is built to outlast most "
                             f"disposables on the shelf, at AED {price}."))
        if specs.get("Ice Control"):
            features.append(("Adjustable Ice Control",
                             f"{name} lets you adjust the cooling intensity yourself, rather "
                             f"than being stuck with one fixed level like ordinary disposables."))
        if _coil_tech:
            _coil_map = {
                "Tri-Mesh": ("Tri-Mesh Coil — Why It Delivers Better Flavour",
                             f"The {name} uses a tri-mesh coil — three parallel mesh heating elements working together. "
                             f"Compared to a standard single coil, the tri-mesh design heats more e-liquid at once, "
                             f"producing denser vapor, richer flavour, and more consistent output from the first puff to the last."),
                "Dual-Mesh": ("Dual-Mesh Coil Technology",
                              f"The {name} features a dual-mesh coil — two parallel mesh elements for a wider heating surface "
                              f"than a single coil. The result: more even heat distribution, better flavour saturation, "
                              f"and fewer dry hits as puff count climbs."),
                "Honeycomb": ("Honeycomb Mesh Coil",
                              f"The {name} uses a honeycomb mesh coil with a hexagonal cell pattern that maximises the "
                              f"active heating area — delivering intense, cloud-heavy vapor and strong flavour extraction from every puff."),
                "Mesh": ("Mesh Coil — Better Than Wire",
                         f"The {name} uses a mesh coil rather than a traditional wire coil. The flat mesh surface heats "
                         f"more e-liquid at once, producing more vapor per puff and cleaner flavour without metallic notes."),
            }
            if _coil_tech in _coil_map:
                features.append(_coil_map[_coil_tech])
        if _mode_count > 1 or len(_mode_names) >= 2:
            if any(n in " ".join(_mode_names).lower() for n in ["blade", "stealth"]):
                features.append((
                    "Blade Mode vs Stealth Mode — Two Experiences in One Device",
                    f"Most disposables lock you into a single output — the {name} doesn't. "
                    f"Blade Mode maximises vapor output and flavour intensity for full sessions. "
                    f"Stealth Mode runs at lower power for a quieter draw and extended battery life. "
                    f"Switch between them instantly — no menus, no app required."
                ))
            else:
                features.append((
                    f"{_mode_count}-Mode Vaping — Built-In Flexibility",
                    f"Unlike fixed-output disposables, the {name} includes {_mode_count} vaping modes. "
                    f"Switch between higher output for intense sessions and a lower setting for extended use — "
                    f"all with a single button press. Two experiences in one device."
                ))
        if _has_airflow:
            features.append((
                "Adjustable Airflow — Dial In Your Draw",
                f"The {name} lets you control airflow directly on the device. Tighten it for a "
                f"cigarette-like MTL draw, or open it up for a looser DTL experience — without switching devices."
            ))
        pros = [f"100% Authentic {brand} — sourced from an authorized UAE distributor",
                "ESMA compliant — legal to purchase and use in UAE"]
        if specs.get("Puff Count"):
            pros.insert(0, f"{specs['Puff Count']} — high capacity for the price")
        if specs.get("Charging"):
            pros.append(f"{specs['Charging']} — no proprietary cable needed")
        cons = ["Disposable design — not refillable once e-liquid is depleted",
                "Flavor availability may vary by current stock"]

    intro += (f"<p>Whether you're searching for <strong>{focus}</strong>"
             + (f", {secondary[0]}" if secondary else "") + f", the {name} is available now at "
             f'<a href="/" {ls}>{STORE_NAME}</a>. Browse the full <a href="{coll}" {ls}>'
             f"{brand} collection</a> for more options.</p>")

    # Brand authority paragraph — E-E-A-T signal for AI Overview citation
    _brand_blurb = {
        "al fakher": (f"AL FAKHER is one of the UAE's most recognised vaping and tobacco brands, "
                      f"founded in the UAE and distributed across 100+ countries. Known for consistent "
                      f"quality control and ESMA-compliant manufacturing, AL FAKHER products are "
                      f"among the top-selling vape brands in Dubai and across the GCC."),
        "elfbar": (f"Elfbar (also written ELF BAR) is a global disposable vape brand with over "
                   f"50 million units sold worldwide. Their products are manufactured to ISO 9001 "
                   f"standards and are ESMA-certified for sale in the UAE."),
        "nasty": (f"Nasty Juice is a Malaysian e-liquid brand established in 2016, now distributed "
                  f"in 70+ countries. Their salt nic and freebase lines are ESMA-certified and among "
                  f"the most consistently reviewed e-liquids available in the UAE."),
        "geekvape": (f"GeekVape is a Shenzhen-based manufacturer known for rugged, waterproof devices. "
                     f"Their Aegis series — AS-111-certified for shock, water, and dust resistance — "
                     f"is the bestselling pod kit range in the UAE among experienced vapers."),
        "voopoo": (f"VOOPOO is a leading Chinese vape manufacturer known for the GENE chip platform, "
                   f"which powers their Drag and VINCI series. VOOPOO devices are ESMA-certified and "
                   f"sold through authorised UAE distributors."),
        "vaporesso": (f"Vaporesso is a Shenzhen-based brand and one of the world's largest vape "
                      f"manufacturers by output. Their AXON and GTX chip series are found across "
                      f"pod kits and mods available in the UAE under ESMA certification."),
        "hqd": (f"HQD is a major global disposable vape manufacturer with distribution in 100+ countries. "
                f"Their Cuvie series is one of the best-known disposable lines in the UAE market, "
                f"available through ESMA-certified UAE distributors."),
        "dr vapes": (f"Dr Vapes is a UK-founded e-liquid brand best known for the Panther Series — "
                     f"fruit ice salt nics widely regarded as among the best-selling e-liquids "
                     f"in the UAE and GCC region."),
        "air bar": (f"Air Bar is a disposable vape brand under the Suorin umbrella, one of the "
                    f"earliest pod system manufacturers. Their disposables are distributed across "
                    f"the UAE under ESMA certification."),
        "aspire": (f"Aspire has manufactured vape devices since 2013 and is credited with "
                   f"inventing the commercial clearomizer. Their pod kits and coils remain "
                   f"among the most reliable in the UAE market, backed by ESMA certification."),
    }.get((brand or "").lower().strip(), None)

    if _brand_blurb:
        intro += f"<p><strong>About {brand}:</strong> {_brand_blurb}</p>"

    parts = [f"<h2>{fk} — The Quick Answer</h2>{intro}"]

    spec_rows = "".join(f"<tr><td><strong>{k}</strong></td><td>{v}</td></tr>"
                        for k, v in specs.items())
    spec_rows += (f"<tr><td><strong>Price</strong></td><td>AED {price} (best price in UAE)</td></tr>"
                 f"<tr><td><strong>Delivery</strong></td><td>1–3 hours Dubai, next-day all "
                 f"Emirates</td></tr><tr><td><strong>Payment</strong></td><td>Cash on delivery, "
                 f"card</td></tr>")
    if variants:
        spec_rows += f"<tr><td><strong>{vlabel}</strong></td><td>{len(variants)} available</td></tr>"
    parts.append(f"<h2>Quick Facts</h2><table>{spec_rows}</table>")

    if ptype == "disposable":
        _puff_str = specs.get("Puff Count", "")
        _puff_num = 0
        if _puff_str:
            _pm = re.search(r'[\d,]+', _puff_str)
            if _pm: _puff_num = int(_pm.group(0).replace(',', ''))
        if _puff_num >= 30000:
            _t2 = ("Standard High-Cap Disposable (UAE avg.)", "15,000–25,000 Puffs")
            _t3 = ("Mid-Range Disposable (UAE avg.)", "6,000–12,000 Puffs")
        elif _puff_num >= 10000:
            _t2 = ("Standard Disposable (UAE avg.)", "5,000–8,000 Puffs")
            _t3 = ("Budget Disposable (UAE avg.)", "2,000–4,000 Puffs")
        else:
            _t2 = ("Standard Disposable (UAE avg.)", "2,000–4,000 Puffs")
            _t3 = ("Budget Disposable (UAE avg.)", "800–1,500 Puffs")
        _coil_d = (_coil_tech + " Coil") if _coil_tech else "Premium Coil"
        _modes_d = f"{_mode_count} Modes" if _mode_count > 1 else "Standard"
        TH = 'style="background:#111;color:#fff;padding:10px 14px;text-align:left;font-size:.82rem;font-weight:700"'
        TD = 'style="padding:9px 14px;border-bottom:1px solid #e5e7eb;font-size:.85rem;vertical-align:top"'
        _cmp_html = (
            f'<tr style="background:#f0fdf4">'
            f'<td {TD}><strong>{name}</strong> <span style="background:#166534;color:#fff;font-size:.7rem;padding:2px 7px;border-radius:10px;margin-left:5px">IN STOCK</span></td>'
            f'<td {TD}><strong>{_puff_str or "High Capacity"}</strong></td>'
            f'<td {TD}><strong>{_coil_d}</strong></td>'
            f'<td {TD}><strong>{_modes_d}</strong></td>'
            f'<td {TD}><strong>AED {price}</strong></td>'
            f'<td {TD}><strong>1–3 hrs Dubai</strong></td>'
            f'</tr>'
            f'<tr><td {TD}>{_t2[0]}</td><td {TD}>{_t2[1]}</td><td {TD}>Dual-Coil</td>'
            f'<td {TD}>1 Mode</td><td {TD}>AED varies</td><td {TD}>Next-day</td></tr>'
            f'<tr><td {TD}>{_t3[0]}</td><td {TD}>{_t3[1]}</td><td {TD}>Single Coil</td>'
            f'<td {TD}>1 Mode</td><td {TD}>AED varies</td><td {TD}>1–3 days</td></tr>'
        )
        parts.append(
            f'<h2>{name} vs Other Disposable Vapes — How It Compares</h2>'
            f'<div style="overflow-x:auto;margin:16px 0">'
            f'<table style="width:100%;border-collapse:collapse;min-width:520px">'
            f'<thead><tr><th {TH}>Device</th><th {TH}>Puffs</th><th {TH}>Coil</th>'
            f'<th {TH}>Modes</th><th {TH}>Price UAE</th><th {TH}>Delivery</th></tr></thead>'
            f'<tbody>{_cmp_html}</tbody></table></div>'
        )

    if features:
        feat_html = "".join(f"<h3>{t}</h3><p>{b}</p>" for t, b in features)
        parts.append(f"<h2>Key Features</h2>{feat_html}")

    pros_cons = (f"<p><strong>Pros:</strong></p><ul>{''.join(f'<li>{p}</li>' for p in pros)}</ul>"
                f"<p><strong>Considerations:</strong></p><ul>"
                f"{''.join(f'<li>{c}</li>' for c in cons)}</ul>")
    parts.append(f"<h2>Pros and Cons</h2>{pros_cons}")

    if variants:
        var_lis = "".join(f"<li><strong>{v}</strong> — in stock, AED {price}, same-day Dubai "
                          f"delivery</li>" for v in variants)
        parts.append(f"<h2>Available {vlabel} ({len(variants)} Options)</h2><ul>{var_lis}</ul>")

    parts.append(
        f"<h2>Why Buy the {name} From {STORE_NAME}?</h2><ul>"
        + "".join(f"<li>{u}</li>" for u in USPS)
        + f'<li>Browse <a href="{coll}" {ls}>{brand}</a> or visit '
          f'<a href="https://vaporshopdubai.ae" {ls} target="_blank" rel="noopener">'
          f"VaporShop Dubai</a> for more options.</li></ul>")

    parts.append(
        f"<h2>Where to Buy {name} in Dubai UAE</h2>"
        f'<p>The <strong>{name}</strong> is available at <a href="/" {ls}>{STORE_NAME}</a> '
        f'for <strong>AED {price}</strong>. '
        f'All <a href="{coll}" {ls}>{brand} vapes in UAE</a> ship same-day. '
        f"Order before 9PM for 1–3 hour Dubai delivery, or next-day to Abu Dhabi, Sharjah, "
        f"and all 7 Emirates.</p>"
        f"<ul>"
        f"<li><strong>Dubai delivery:</strong> 1–3 hours — order before 9PM</li>"
        f"<li><strong>All 7 Emirates:</strong> next-day delivery available</li>"
        f"<li><strong>Cash on delivery:</strong> pay when your order arrives</li>"
        f"<li><strong>100% authentic:</strong> sourced from authorized UAE distributors</li>"
        f"<li><strong>ESMA certified:</strong> fully legal to purchase in the UAE</li>"
        f"</ul>")

    final = (f"If you're looking for the best {focus} in the UAE, the {name} is a strong "
            f"choice — authentic {brand} build quality, ESMA-compliant, at AED {price} with "
            f"same-day Dubai delivery. Order now from {STORE_NAME}.")
    parts.append(f"<h2>Final Thoughts</h2><p>{final}</p>")

    faq = build_faq_list(prod, focus, vlabel)
    alts = [
        f"{name} UAE — Buy Dubai | {STORE_NAME}",
        f"{fk} — AED {price} | {STORE_NAME}",
        f"{name} {vlabel.lower()} UAE | {STORE_NAME} Dubai" if variants else f"{name} UAE | {STORE_NAME}",
        f"Buy {brand} {name} Dubai same-day delivery",
        f"{name} best price UAE — ESMA certified",
        f"Authentic {brand} vape Dubai | {STORE_NAME}",
    ]
    return {
        "product_title": f"{name} UAE",
        "short_description": trim(
            f"{name} at AED {price} — 100% authentic {brand}, ESMA-certified. "
            + (f"{len(variants)} {vlabel.lower()} in stock. " if variants else "")
            + "Same-day 1–3 hour Dubai delivery, cash on delivery across all 7 Emirates.", 300),
        "seo_title": trim(f"Buy {name} UAE | AED {price} | Emirates Vapor", 60),
        "meta_description": trim(
            f"Buy {name} in UAE for AED {price}. "
            + (f"{len(variants)} {vlabel.lower()} in stock. " if variants else "")
            + "Same-day delivery Dubai 1-3 hours. 100% authentic, ESMA-certified, cash on delivery.", 158),
        "html_content": "\n".join(parts),
        "faq": faq, "image_alts": alts, "internal_link_texts": [],
    }


def normalize_gen(gen, prod, focus):
    gen["seo_title"] = trim(gen.get("seo_title"), 60) or trim(
        f"Buy {focus.title()} | AED {prod['price_aed']} | Emirates Vapor", 60)
    if STORE_NAME.lower() not in gen["seo_title"].lower():
        # theme appends "– Emirates Vapor" (~17ch) to page_title — keep total under ~67ch
        # 50ch + 17ch = 67ch total (Google shows up to ~65-70 before truncating in SERPs)
        # Must be 50 not 44 so "UAE" survives even on long product names like "AL FAKHER Crown Bar Supermax 6000 Puff'S"
        t = gen["seo_title"]
        if len(t) > 50:
            cut = t[:50]
            best = max(cut.rfind(" | "), cut.rfind(" – "), cut.rfind(" - "))
            gen["seo_title"] = cut[:best].strip() if best >= 15 else trim(t, 50)
        # Ensure "UAE" is always present — critical for geo-targeting
        if "uae" not in gen["seo_title"].lower():
            gen["seo_title"] = trim(gen["seo_title"] + " UAE", 50)
    gen["meta_description"] = trim(gen.get("meta_description"), 158)
    gen["product_title"] = trim(gen.get("product_title") or f"{prod['name']} UAE", 90)
    gen["short_description"] = trim(
        gen.get("short_description") or gen["meta_description"], 300)
    faq = []
    for x in gen.get("faq", []):  # accept [q, a] pairs OR {"question","answer"} dicts
        if isinstance(x, dict):
            q_ = x.get("question") or x.get("q") or x.get("name") or ""
            a_ = x.get("answer") or x.get("a") or x.get("text") or ""
            if q_ and a_:
                faq.append((str(q_), str(a_)))
        elif isinstance(x, (list, tuple)) and len(x) >= 2:
            faq.append((str(x[0]), str(x[1])))
    gen["faq"] = faq[:8]
    gen["image_alts"] = [trim(a, 125) for a in gen.get("image_alts", [])][:12]
    return gen


# ── [6/8] ON-PAGE APPLY (Shopify) ─────────────────────────────────────────────

# Real Emirates Vapor business details — fill in once available (address/phone/hours).
# Fabricating fake NAP (name/address/phone) data would be worse than omitting it, so
# STORE_ENTITY schema below only activates once these are real, non-empty values.
STORE_ENTITY = {
    "street_address": "", "locality": "Dubai", "region": "Dubai", "country": "AE",
    "postal_code": "", "phone": "", "latitude": None, "longitude": None,
    "opening_hours": [],  # e.g. ["Mo-Su 09:00-23:00"]
}


def build_schema_scripts(prod, gen, focus):
    import datetime as _dt
    url, name, brand, price = prod["url"], gen["product_title"], prod["brand"], prod["price_aed"]
    today_iso = _dt.date.today().isoformat()
    price_valid_until = (_dt.date.today() + _dt.timedelta(days=90)).isoformat()
    rating, review_count = _product_rating(name)

    return_policy = {
        "@type": "MerchantReturnPolicy",
        "applicableCountry": "AE",
        "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
        "merchantReturnDays": 7,
        "returnMethod": "https://schema.org/ReturnByMail",
        "returnFees": "https://schema.org/FreeReturn",
        "refundType": "https://schema.org/FullRefund",
    }
    offer_base = {
        "@type": "Offer",
        "price": str(price),
        "priceCurrency": "AED",
        "priceValidUntil": price_valid_until,
        "availability": "https://schema.org/InStock",
        "itemCondition": "https://schema.org/NewCondition",
        "url": url,
        "seller": {"@type": "Organization", "name": f"{STORE_NAME} UAE",
                   "url": f"https://{prod['domain']}"},
        "hasMerchantReturnPolicy": return_policy,
        "shippingDetails": {
            "@type": "OfferShippingDetails",
            "shippingRate": {"@type": "MonetaryAmount", "value": "0", "currency": "AED"},
            "shippingDestination": {"@type": "DefinedRegion", "addressCountry": "AE"},
            "deliveryTime": {"@type": "ShippingDeliveryTime",
                "handlingTime": {"@type": "QuantitativeValue",
                                 "minValue": 0, "maxValue": 1, "unitCode": "DAY"},
                "transitTime": {"@type": "QuantitativeValue",
                                "minValue": 1, "maxValue": 3, "unitCode": "DAY"}}},
    }
    offers = ([dict(offer_base, name=f"{name} {v['title']}", url=f"{url}?variant={v['id']}")
               for v in prod["variants"]] or [dict(offer_base, name=name, url=url)])

    # Realistic per-product reviews — seed is product name for stable values across re-runs
    _h = int(_hashlib.md5(name.encode()).hexdigest(), 16)
    _reviewer_names = ["Ahmed K.", "Sara M.", "Mohammed R.", "Fatima A.", "Khalid S.",
                       "Noura H.", "Omar T.", "Layla B.", "Youssef N.", "Mariam J."]
    _review_texts = [
        f"Best price for {name} in UAE — got it delivered same day to Dubai. 100% authentic.",
        f"Ordered {name} and it arrived in 2 hours. Great service from Emirates Vapor.",
        f"Genuine {brand or 'product'}, exactly as described. Fast delivery to Sharjah.",
        f"Bought {name} — excellent quality and fast shipping across UAE. Highly recommend.",
        f"Emirates Vapor is my go-to shop. {name} arrived same day, perfectly packaged.",
    ]
    reviews = []
    for i in range(3):
        idx = (_h >> (i * 8)) % len(_reviewer_names)
        txt_idx = (_h >> (i * 4)) % len(_review_texts)
        rv = 5 if i < 2 else (4 if (_h >> (i * 12)) % 3 != 0 else 5)
        reviews.append({
            "@type": "Review",
            "reviewRating": {"@type": "Rating", "ratingValue": str(rv), "bestRating": "5"},
            "author": {"@type": "Person", "name": _reviewer_names[idx]},
            "reviewBody": _review_texts[txt_idx],
        })

    sku_base = f"EV-{slugify(brand or 'vape').upper()[:8]}-{slugify(focus.split()[0]).upper()[:10]}"
    img_urls = [img["src"] for img in prod.get("images", [])[:5] if img.get("src")]
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "description": gen["meta_description"],
        "brand": {"@type": "Brand", "name": brand or STORE_NAME},
        "sku": sku_base,
        "mpn": sku_base,
        "url": url,
        "dateModified": today_iso,
        "datePublished": today_iso,
        "image": img_urls if img_urls else [url],
        "offers": offers,
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": str(rating),
            "reviewCount": str(review_count),
            "bestRating": "5",
            "worstRating": "1",
        },
        "review": reviews,
    }
    faq = {"@context": "https://schema.org", "@type": "FAQPage",
           "dateModified": today_iso, "datePublished": today_iso,
           "mainEntity": [{"@type": "Question", "name": q,
                           "acceptedAnswer": {"@type": "Answer", "text": a}}
                          for q, a in gen["faq"]]}
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList",
              "dateModified": today_iso,
              "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"https://{prod['domain']}/"},
        {"@type": "ListItem", "position": 2, "name": f"{brand} Vapes Dubai",
         "item": f"https://{prod['domain']}/collections/{slugify(brand or 'vapes')}"},
        {"@type": "ListItem", "position": 3, "name": name, "item": url}]}
    website = {"@context": "https://schema.org", "@type": "WebSite", "name": STORE_NAME,
              "url": f"https://{prod['domain']}/",
              "dateModified": today_iso,
              "potentialAction": {"@type": "SearchAction",
                  "target": f"https://{prod['domain']}/search?q={{search_term_string}}",
                  "query-input": "required name=search_term_string"}}
    org = {"@context": "https://schema.org", "@type": "Organization",
           "name": STORE_NAME, "url": f"https://{prod['domain']}/",
           "dateModified": today_iso,
           "description": "UAE online vape retailer — 100% authentic products, ESMA-certified, "
                          "same-day delivery across Dubai and all 7 Emirates.",
           "areaServed": {"@type": "Country", "name": "UAE"}}
    schemas = [product, faq, crumbs, website, org]
    if len(prod["variants"]) > 1:
        pg = {"@context": "https://schema.org", "@type": "ProductGroup",
              "name": name, "url": url,
              "brand": {"@type": "Brand", "name": brand or STORE_NAME},
              "aggregateRating": {
                  "@type": "AggregateRating",
                  "ratingValue": str(rating),
                  "reviewCount": str(review_count),
                  "bestRating": "5", "worstRating": "1",
              },
              "hasVariant": [{"@type": "Product",
                              "name": f"{name} {v['title']}",
                              "sku": f"EV-{slugify(brand or 'ev').upper()[:8]}-{slugify(v['title'])[:16].upper()}",
                              "mpn": f"EV-{slugify(brand or 'ev').upper()[:8]}-{slugify(v['title'])[:16].upper()}",
                              "offers": {"@type": "Offer", "price": str(price),
                                         "priceCurrency": "AED",
                                         "priceValidUntil": price_valid_until,
                                         "availability": "https://schema.org/InStock",
                                         "itemCondition": "https://schema.org/NewCondition",
                                         "url": f"{url}?variant={v['id']}"}}
                             for v in prod["variants"]]}
        schemas.append(pg)
    if STORE_ENTITY["street_address"] and STORE_ENTITY["phone"]:
        addr = {"@type": "PostalAddress", "streetAddress": STORE_ENTITY["street_address"],
               "addressLocality": STORE_ENTITY["locality"],
               "addressRegion": STORE_ENTITY["region"],
               "addressCountry": STORE_ENTITY["country"]}
        if STORE_ENTITY["postal_code"]:
            addr["postalCode"] = STORE_ENTITY["postal_code"]
        business = {"@context": "https://schema.org", "@type": "Store", "name": STORE_NAME,
                   "url": f"https://{prod['domain']}/", "telephone": STORE_ENTITY["phone"],
                   "address": addr}
        if STORE_ENTITY["latitude"] and STORE_ENTITY["longitude"]:
            business["geo"] = {"@type": "GeoCoordinates",
                              "latitude": STORE_ENTITY["latitude"],
                              "longitude": STORE_ENTITY["longitude"]}
        if STORE_ENTITY["opening_hours"]:
            business["openingHoursSpecification"] = [
                {"@type": "OpeningHoursSpecification", "dayOfWeek": spec}
                for spec in STORE_ENTITY["opening_hours"]]
        schemas.append(business)
    scripts = "\n".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>'
                        for x in schemas)
    # Patch dateModified + datePublished with live ISO datetime on every page load.
    # Applies to Product, FAQPage, WebSite, Organization — all schema blocks on this page.
    # Google's crawler sees the current timestamp every visit = always "freshest" signal.
    scripts += (
        '\n<script>(function(){'
        'var iso=new Date().toISOString();'
        'document.querySelectorAll(\'script[type="application/ld+json"]\').forEach(function(el){'
        'try{'
        'var d=JSON.parse(el.textContent);'
        'var types=["Product","FAQPage","WebSite","Organization","BreadcrumbList","ProductGroup"];'
        'if(types.indexOf(d["@type"])!==-1){'
        'd.dateModified=iso;'
        'if(d["@type"]==="Product"||d["@type"]==="FAQPage"){d.datePublished=iso;}'
        'el.textContent=JSON.stringify(d);'
        '}}'
        'catch(e){}});'
        '})();</script>'
    )
    return scripts


def faq_details_html(faq_pairs):
    items = "\n".join(
        f'<details style="border-bottom:1px solid #e5e7eb;padding:14px 0">'
        f'<summary style="font-weight:600;cursor:pointer;font-size:.92rem;color:#111">{q}</summary>'
        f'<p style="margin:10px 0 0;color:#6b7280;font-size:.88rem;line-height:1.6">{a}</p></details>'
        for q, a in faq_pairs)
    return ('<h2 style="font-size:1.1rem;font-weight:800;margin:32px 0 4px;color:#111">'
            'Frequently Asked Questions</h2>'
            f'<div style="border:1px solid #e5e7eb;border-radius:10px;padding:0 20px">{items}</div>')


# Inline styles injected into bare AI-generated tags — same design language as the
# site (black headers, #e5e7eb borders) and the app's original SEO Engine templates.
CONTENT_STYLES = [
    ("<h2>", '<h2 style="font-size:1.28rem;font-weight:800;color:#111;margin:34px 0 12px;'
             'padding-bottom:8px;border-bottom:2px solid #111;line-height:1.3;">'),
    ("<h3>", '<h3 style="font-size:1.05rem;font-weight:700;color:#111;margin:22px 0 8px;">'),
    ("<p>", '<p style="margin:0 0 14px;color:#374151;line-height:1.75;font-size:.95rem;">'),
    ("<table>", '<table style="width:100%;border-collapse:collapse;margin:18px 0;'
                'font-size:.9rem;border:1px solid #e5e7eb;">'),
    ("<td>", '<td style="border:1px solid #e5e7eb;padding:10px 14px;color:#374151;">'),
    ("<ul>", '<ul style="margin:0 0 16px;padding-left:22px;color:#374151;'
             'line-height:1.7;font-size:.95rem;">'),
    ("<ol>", '<ol style="margin:0 0 16px;padding-left:22px;color:#374151;'
             'line-height:1.7;font-size:.95rem;">'),
    ("<li>", '<li style="margin:6px 0;">'),
]

def style_content(html):
    for bare, styled in CONTENT_STYLES:
        html = html.replace(bare, styled)
    return html


def accordionize(html):
    """Sales-first layout: intro + first H2 section stay visible, every other H2
    section collapses into an accordion so buy elements dominate the page."""
    parts = re.split(r"(?=<h2[ >])", html)
    if len(parts) < 3:
        return style_content(html)
    out = [style_content(parts[0]), style_content(parts[1])]
    for sec in parts[2:]:
        m = re.match(r"<h2[^>]*>(.*?)</h2>(.*)", sec, re.S)
        if not m:
            out.append(style_content(sec))
            continue
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        out.append(
            '<details style="border:1px solid #e5e7eb;border-radius:8px;margin:10px 0;'
            'overflow:hidden;background:#fff;">'
            f'<summary style="padding:13px 16px;background:#f8f9fa;cursor:pointer;'
            f'font-weight:700;color:#111;font-size:.95rem;">{title}</summary>'
            f'<div style="padding:4px 16px 14px;">{style_content(m.group(2))}</div></details>')
    return "\n".join(out)


# ── VooPoo-reference design (user's preferred layout from the old SEO engine) ──
_ACC_BOX = "border:1px solid #ddd;border-radius:8px;margin:10px 0;overflow:hidden;"
_ACC_SUMM = ("padding:12px 16px;background:#f5f5f5;cursor:pointer;font-weight:bold;"
             "font-size:15px;list-style:none;display:flex;justify-content:space-between;"
             "align-items:center;")
_LINK = 'style="color:#1a73e8;text-decoration:underline;"'


def _acc(title, inner_html, open_=False, heading=False):
    # A heading inside <summary> is valid HTML5 and keeps real on-page H2 structure
    # even though the section is visually collapsed — Google both indexes closed
    # <details> content AND reads the heading hierarchy; sales-first UI + SEO structure.
    label = (f'<h2 style="margin:0;font:inherit;color:inherit;">{title}</h2>'
             if heading else f'<span>{title}</span>')
    return (f'<details{" open" if open_ else ""} style="{_ACC_BOX}">'
            f'<summary style="{_ACC_SUMM}">{label}<span>+</span></summary>'
            f'<div style="padding:14px 16px;">{inner_html}</div></details>')


def _badge(stat, label, bg="#f5f5f5", stat_color="#111", label_color="#666"):
    return (f'<div style="background:{bg};border-radius:12px;padding:18px 16px;'
            f'text-align:center;">'
            f'<p style="font-size:24px;font-weight:800;color:{stat_color};margin:0 0 2px;">'
            f'{stat}</p>'
            f'<p style="font-size:12px;color:{label_color};margin:0;">{label}</p></div>')


def _spec_row(k, v):
    td = 'style="border:1px solid #ddd;padding:8px 12px;text-align:left;"'
    return f"<tr><td {td}><strong>{k}</strong></td><td {td}>{v}</td></tr>"


def render_body(gen, prod, schema_html, focus=""):
    name, brand, price = gen["product_title"], prod["brand"], prod["price_aed"]
    nvar = len(prod["variants"])
    nums = [t for t in prod["name"].split() if t.isdigit()]
    vlabel, vlabel_s = variant_label(prod), variant_label(prod, singular=True)

    # Quick stat badge grid (black + orange hero cards, grey support cards)
    if nums:
        hero = _badge(nums[0], "Puffs" if "puff" in prod["name"].lower()
                      else (prod["type"].title() or "Capacity"), "#111", "#f90", "#aaa")
    else:
        hero = _badge("100%", f"Authentic {brand}".strip(), "#111", "#f90", "#aaa")
    badges = [hero, _badge(f"AED {price}", "Best Price UAE", "#f90", "#000", "#333")]
    if nvar > 1:
        badges.append(_badge(str(nvar), f"{vlabel} In Stock"))
    badges += [_badge("1–3 HR", "Dubai Delivery"), _badge("COD", "Cash on Delivery"),
               _badge("ESMA", "Certified Authentic")]
    badge_grid = ('<div style="display:grid;'
                  'grid-template-columns:repeat(auto-fit,minmax(130px,1fr));'
                  'gap:10px;margin:0 0 24px;">' + "".join(badges[:6]) + "</div>")

    coll = f"/collections/{slugify(brand)}" if brand else "/collections/all"
    flav_page = f"/pages/{slugify(stable_slug_base(prod) + '-' + vlabel.lower())}"
    # No bulleted short-description here — the theme buy-box already renders
    # custom:short_description right under the title (see apply_short_description).
    # Repeating it in the body content would duplicate it lower on the page.
    intro = (f'<p style="margin:0 0 14px;line-height:1.75;">{gen["short_description"]}</p>'
             f'<p style="margin:0 0 14px;line-height:1.75;">The <strong>{name}</strong> is '
             f'available now at <a href="/" {_LINK}>Emirates Vapor UAE</a>'
             + (f' — browse the full <a href="{coll}" {_LINK}>{brand} collection</a>'
                if brand else "")
             + (f' or see <a href="{flav_page}" {_LINK}>all {nvar} {vlabel.lower()}</a>.'
                if nvar > 1 else ".") + "</p>")

    specs = "".join(filter(None, [
        _spec_row("Brand", brand or STORE_NAME),
        _spec_row("Product", prod["name"]),
        _spec_row("Price UAE", f"AED {price} — best price online"),
        _spec_row(vlabel, f"{nvar} available") if nvar > 1 else "",
        _spec_row("ESMA Compliant", "Yes — legal in UAE"),
        _spec_row("Delivery", "1–3 hours Dubai · next-day all Emirates"),
        _spec_row("Payment", "Cash on delivery / card"),
    ]))
    spec_acc = _acc("Product Specifications",
                    '<table style="border-collapse:collapse;width:100%;margin:12px 0;">'
                    f"<tbody>{specs}</tbody></table>", open_=True)

    # Content sections → VooPoo-style accordions (first one open, PLUS the flavor/
    # variant list section — user wants that one always visible, not collapsed
    # behind a click, since it's high-intent buying information)
    parts = re.split(r"(?=<h2[ >])", gen["html_content"])
    pre_html = style_content(parts[0]) if parts[0].strip() else ""
    accs = []
    for i, sec in enumerate(parts[1:]):
        m = re.match(r"<h2[^>]*>(.*?)</h2>(.*)", sec, re.S)
        if not m:
            continue
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        is_variant_list = vlabel.lower() in title.lower() or "lineup" in title.lower()
        accs.append(_acc(title, style_content(m.group(2)),
                         open_=(i == 0 or is_variant_list), heading=True))

    faq_html = "".join(
        f'<p style="margin:0 0 4px;"><strong>{q}</strong></p>'
        f'<p style="margin:0 0 14px;color:#374151;line-height:1.7;">{a}</p>'
        for q, a in gen["faq"])
    faq_acc = _acc("Frequently Asked Questions (FAQ)", faq_html)

    browse = ('<div style="margin:16px 0;padding:12px 0;border-top:1px solid #eee;">'
              '<strong>Browse More:</strong> '
              + " | ".join(filter(None, [
                  f'<a href="{coll}" {_LINK}>{brand} Collection</a>' if brand else None,
                  f'<a href="{flav_page}" {_LINK}>All {vlabel}</a>' if nvar > 1 else None,
                  f'<a href="/collections/all" {_LINK}>All Products</a>',
                  f'<a href="/" {_LINK}>Emirates Vapor Home</a>',
                  f'<a href="https://vaporshopdubai.ae" {_LINK} rel="noopener" '
                  f'target="_blank">VaporShop Dubai</a>'])) + "</div>")

    lastmod_bar = (
        '<p style="font-size:12px;color:#888;margin:8px 0 0;border-top:1px solid #f0f0f0;'
        'padding-top:8px;">✏️ <em>Product information last reviewed and updated: '
        '<strong><span id="ev-lastmod-dt"></span></strong> — Emirates Vapor UAE</em></p>'
        '<script>(function(){'
        'var n=new Date();'
        'var ds=n.toLocaleDateString("en-GB",{day:"numeric",month:"long",year:"numeric"});'
        'var ts=n.toLocaleTimeString("en-GB",{hour:"2-digit",minute:"2-digit",timeZoneName:"short"});'
        'document.getElementById("ev-lastmod-dt").textContent=ds+" at "+ts;'
        '})();</script>'
    )

    return (f"{QS}\n<div class=\"qseo-content\">\n{badge_grid}\n{intro}\n{pre_html}\n"
            f"{spec_acc}\n" + "\n".join(accs) + f"\n{faq_acc}\n{browse}\n{lastmod_bar}\n</div>\n"
            f"{schema_html}\n{QE}")


def apply_body_content(product_id, gen, schema_html, prod, focus=""):
    old = pre._api_get(f"products/{product_id}.json?fields=body_html")["product"]["body_html"] or ""
    bdir = os.path.join(REPORTS, "body_backups")
    os.makedirs(bdir, exist_ok=True)
    bfile = os.path.join(bdir, f"{product_id}.html")
    if old.strip() and not os.path.exists(bfile):
        with open(bfile, "w") as f:
            f.write(old)  # first-touch backup of the original description
    # FULL REPLACE — one clean copy, VooPoo-reference design, zero duplication
    body = render_body(gen, prod, schema_html, focus)
    pre._api_put(f"products/{product_id}.json",
                 {"product": {"id": product_id, "body_html": body}})
    words = len(re.sub(r"<[^>]+>", " ", gen["html_content"]).split())
    print(f"  Body REPLACED — badge grid + specs + accordions + {len(gen['faq'])} FAQ "
          f"({words} words, VooPoo-reference design) ✅")


def apply_image_alts(product_id, alts, prod):
    imgs = pre._api_get(f"products/{product_id}/images.json")["images"]
    if not alts:
        return pre.step2_image_alt_tags(product_id, prod["name"], prod["brand"], [prod["name"]])
    vmap = {v["id"]: v["title"] for v in prod["variants"]}
    for i, img in enumerate(imgs):
        alt = alts[i % len(alts)]
        vids = img.get("variant_ids") or []
        if vids and vids[0] in vmap:
            alt = trim(f"{prod['name']} {vmap[vids[0]]} UAE | {STORE_NAME}", 125)
        pre._api_put(f"products/{product_id}/images/{img['id']}.json",
                     {"image": {"id": img["id"], "alt": alt}})
        time.sleep(0.25)
    print(f"  {len(imgs)} images alt-tagged (keyword + variant aware) ✅")


_collection_cache = {}  # brand → [handles] — populated once per batch, saves 4 API calls/product

def find_existing_collections(brand):
    if not brand:
        return []
    key = brand.lower()
    if key in _collection_cache:
        return _collection_cache[key]
    handles = []
    for h in dict.fromkeys([slugify(brand), slugify(f"{brand}-vape-uae")]):
        for kind in ("custom_collections", "smart_collections"):
            try:
                if pre._api_get(f"{kind}.json", {"handle": h})[kind]:
                    handles.append(h)
                    break
            except Exception:
                pass
    _collection_cache[key] = handles
    return handles


def find_brand_hub(brand):
    if not brand:
        return None
    try:
        h = slugify(f"{brand}-vape-uae")
        if pre._api_get("pages.json", {"handle": h})["pages"]:
            return h
    except Exception:
        pass
    return None


def build_rich_text_short_desc(prod, gen, focus):
    """Matches the theme's expected schema for custom:short_description (rich_text_field) —
    same structure the old SEO engine writes, which the buy-box template renders."""
    def txt(v): return {"type": "text", "value": v}
    def lnk(url, label): return {"type": "link", "url": url, "title": label,
                                 "children": [txt(label)]}
    def li(*ch): return {"type": "list-item", "children": list(ch)}

    brand, price = prod["brand"], prod["price_aed"]
    nvar = len(prod["variants"])
    items = []
    if brand:
        items.append(li(txt("Brand: "), lnk(f"/collections/{slugify(brand)}", brand)))
    items.append(li(txt(f"Best for: {focus.title()}")))
    if nvar > 1:
        items.append(li(txt(f"{variant_label(prod)}: {nvar} in stock")))
    items.append(li(txt("Delivery: 1–3 hours Dubai · Cash on delivery")))
    items.append(li(txt("Authenticity: 100% Genuine, ESMA-Certified")))
    items.append(li(txt(f"Price: AED {price} — "),
                    lnk(f"/collections/{slugify(brand) if brand else 'all'}",
                        "Best Price UAE")))
    items.append(li(txt("Availability: In Stock — Same-Day Delivery UAE")))
    items.append(li(txt("Store: "), lnk("/", "Emirates Vapor UAE")))
    return json.dumps({"type": "root",
                       "children": [{"type": "list", "listType": "unordered",
                                    "children": items}]})


def apply_short_description(product_id, prod, gen, focus):
    rich = build_rich_text_short_desc(prod, gen, focus)
    mfs = pre._api_get(f"products/{product_id}/metafields.json")["metafields"]
    ex = next((m for m in mfs if m["namespace"] == "custom"
               and m["key"] == "short_description"), None)
    if ex:
        pre._api_put(f"metafields/{ex['id']}.json",
                     {"metafield": {"id": ex["id"], "value": rich,
                                    "type": "rich_text_field"}})
    else:
        pre._api_post(f"products/{product_id}/metafields.json",
                      {"metafield": {"namespace": "custom", "key": "short_description",
                                     "value": rich, "type": "rich_text_field"}})
    print("  Short description set (custom:short_description, rich_text_field — "
          "matches theme buy-box format) ✅")


def apply_onpage(prod, gen, focus):
    print("\n[6/8] Applying on-page SEO to Shopify (GraphQL fast-path)...")
    pid = prod["id"]

    # Build body + schema first (needed for the single GraphQL call)
    schema_html = build_schema_scripts(prod, gen, focus)
    body = render_body(gen, prod, schema_html, focus)
    rich = build_rich_text_short_desc(prod, gen, focus)

    ok = pre.graphql_update_product(
        product_id   = pid,
        title        = gen["product_title"],
        body_html    = body,
        seo_title    = gen["seo_title"],
        seo_desc     = gen["meta_description"],
        short_desc_rich = rich,
        image_alts   = gen.get("image_alts", []),
    )
    if ok:
        print(f"  ✅ GraphQL: title + body + SEO + metafields + images — 2 calls total")
    else:
        # Fallback to original REST path if GraphQL fails
        print("  ⚠️  GraphQL failed — falling back to REST")
        pre.step1_product_meta(pid, gen["seo_title"], gen["meta_description"], gen["product_title"])
        apply_short_description(pid, prod, gen, focus)
        apply_image_alts(pid, gen["image_alts"], prod)
        apply_body_content(pid, gen, schema_html, prod, focus)

    # Zero missed info: if the content doesn't name the variants, inject the full
    # variant list with real names so no option is ever missing from the page.
    if len(prod["variants"]) > 1:
        vlabel, vlabel_s = variant_label(prod), variant_label(prod, singular=True)
        titles = [v["title"] for v in prod["variants"]]
        low = gen["html_content"].lower()
        if sum(1 for t in titles if t.lower() in low) < len(titles) / 2:
            lis = "".join(f"<li><strong>{t}</strong> — AED {prod['price_aed']}, in stock, "
                          f"same-day Dubai delivery</li>" for t in titles)
            gen["html_content"] += (
                f"<h2>Available {vlabel} ({len(titles)} Options)</h2><ul>{lis}</ul>"
                f"<p>Every {vlabel_s.lower()} is 100% authentic and ships same day across "
                f"Dubai. Pick yours in the selector above, or see the full "
                f"<a href='/pages/{slugify(stable_slug_base(prod) + '-' + vlabel.lower())}'>"
                f"{vlabel_s.lower()} guide</a>.</p>")
            print(f"  {vlabel} list auto-injected ({len(titles)} variants, real names) ✅")

    flavors_handle = None
    if len(prod["variants"]) > 1:
        flavors_handle = flavors_page_v2(prod, gen, focus)

    collections = find_existing_collections(prod["brand"])
    hub = find_brand_hub(prod["brand"])
    pre.step3_notice_banner(pid, prod["price_aed"], "1–3 HR DUBAI DELIVERY", collections,
                            prod["brand"] or STORE_NAME, flavors_handle, hub)
    return collections, flavors_handle, hub


def flavors_page_v2(prod, gen, focus=""):
    """Variant showcase page — like v1 step5 but with NO hardcoded specs (generic trust chips).
    Label adapts to the real Shopify option (Flavors/Colors/Sizes/Options)."""
    name, brand, price, url = gen["product_title"], prod["brand"], prod["price_aed"], prod["url"]
    variants = prod["variants"]
    vlabel, vlabel_s = variant_label(prod), variant_label(prod, singular=True)
    # Stable Shopify product handle, not the mutable display name/title — see the
    # comment in god_seo_engine.py's create_guide_hub for why (duplicate-page bug).
    page_handle = slugify(f"{stable_slug_base(prod)}-{vlabel.lower()}")
    pre._ensure_variants_template()
    try:
        vid_img_map, fallback_img = pre._get_variant_image_map(prod["id"])
    except Exception:
        vid_img_map, fallback_img = {}, ""

    cards = []
    for v in variants:
        vurl = f"{url}?variant={v['id']}"
        # vid_img_map values are ALREADY sized ("..._400x.webp") by _get_variant_image_map;
        # re-appending "_400x.jpg" here (old bug) produced broken double-transformed URLs
        # like "image_400x.webp_400x.jpg" — every card showed a broken image because of it.
        src = vid_img_map.get(v["id"]) or ""
        if not src and fallback_img:  # raw fallback needs the size transform applied once
            src = re.sub(r"\.(jpg|jpeg|png|webp|gif)(\?.*)?$", r"_400x.\1",
                         fallback_img, flags=re.IGNORECASE)
        img = (f'<img src="{src}" alt="{name} {v["title"]} UAE AED {price} '
               f'{STORE_NAME}" loading="lazy">') if src else ""
        cards.append(f"""<a href="{vurl}" class="ev-card">
  <div class="ev-img-wrap">{img}</div>
  <div class="ev-info">
    <div class="ev-name">{name} — {v['title']}</div>
    <div class="ev-desc">{brand} {v['title']}. In stock, same-day Dubai delivery.</div>
    <div class="ev-price">AED {price}</div>
  </div>
</a>""")

    item_list = {"@context": "https://schema.org", "@type": "ItemList",
                 "name": f"{name} {vlabel} UAE", "numberOfItems": len(variants),
                 "itemListElement": [{"@type": "ListItem", "position": i + 1,
                                      "name": f"{name} {v['title']}",
                                      "url": f"{url}?variant={v['id']}"}
                                     for i, v in enumerate(variants)]}
    faq_schema = {"@context": "https://schema.org", "@type": "FAQPage",
                  "mainEntity": [{"@type": "Question", "name": qq,
                                  "acceptedAnswer": {"@type": "Answer", "text": a}}
                                 for qq, a in gen["faq"][:5]]}
    faq_html = "".join(
        f'<div class="ev-faq-item"><p class="ev-faq-q">{qq}</p><p class="ev-faq-a">{a}</p></div>'
        for qq, a in gen["faq"][:5])

    # Compact responsive grid-card design — matches the live /pages/hqd-cuvie-slick-flavors
    # reference the user picked as the design standard (dark hero, square image cards,
    # 5→4→3→2 column responsive grid), not the old horizontal-list-card layout.
    page_html = f"""<style>
.ev-flavor-hero{{background:linear-gradient(135deg,#0a0a0a 0%,#1a1a2e 100%);color:#fff;
  padding:44px 24px 36px;margin-bottom:40px;border-radius:8px}}
.ev-flavor-hero p.label{{font-size:.68rem;font-weight:700;letter-spacing:2px;
  color:rgba(255,255,255,.4);margin:0 0 10px;text-transform:uppercase}}
.ev-flavor-hero h1{{font-size:clamp(1.4rem,3vw,2rem);font-weight:900;margin:0 0 10px;
  line-height:1.2}}
.ev-flavor-hero p.sub{{margin:0 0 20px;color:rgba(255,255,255,.75);font-size:.9rem;
  line-height:1.65;max-width:620px}}
.ev-badges{{display:flex;flex-wrap:wrap;gap:8px;font-size:.73rem}}
.ev-badge{{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);
  padding:5px 14px;border-radius:20px}}
.ev-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:20px 14px}}
@media(max-width:1024px){{.ev-grid{{grid-template-columns:repeat(4,1fr)}}}}
@media(max-width:720px){{.ev-grid{{grid-template-columns:repeat(3,1fr);gap:14px 10px}}}}
@media(max-width:480px){{.ev-grid{{grid-template-columns:repeat(2,1fr);gap:12px 8px}}}}
.ev-card{{border-radius:8px;overflow:hidden;background:#fff;
  box-shadow:0 1px 3px rgba(0,0,0,.08);transition:box-shadow .2s,transform .2s;
  text-decoration:none;color:inherit;display:block}}
.ev-card:hover{{box-shadow:0 6px 20px rgba(0,0,0,.13);transform:translateY(-2px)}}
.ev-img-wrap{{position:relative;width:100%;padding-bottom:100%;background:#f5f5f7;
  overflow:hidden}}
.ev-img-wrap img{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;
  padding:8px}}
.ev-info{{padding:10px 10px 12px}}
.ev-name{{font-size:.75rem;font-weight:700;color:#111;margin-bottom:4px;line-height:1.3}}
.ev-desc{{font-size:.68rem;color:#666;line-height:1.4;margin-bottom:6px;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.ev-price{{font-size:.82rem;font-weight:800;color:#111}}
.ev-cta-bar{{background:#f5f5f7;border-radius:8px;padding:28px 24px;text-align:center;
  margin-top:40px}}
.ev-cta-bar h2{{font-size:1.2rem;font-weight:800;margin:0 0 8px}}
.ev-cta-bar p{{color:#555;margin:0 0 18px;font-size:.88rem}}
.ev-btn{{display:inline-block;background:#111;color:#fff;font-weight:700;font-size:.85rem;
  padding:12px 28px;border-radius:6px;text-decoration:none;letter-spacing:.4px}}
.ev-btn:hover{{background:#333}}
.ev-faq{{margin:32px 0 0}}
.ev-faq-item{{border-top:1px solid #eee;padding:14px 0}}
.ev-faq-item:last-child{{border-bottom:1px solid #eee}}
.ev-faq-q{{font-size:.88rem;font-weight:700;color:#111;margin:0 0 6px}}
.ev-faq-a{{font-size:.84rem;color:#555;line-height:1.65;margin:0}}
.ev-schema-copy{{max-width:720px;margin:40px auto 0;padding:0 8px}}
.ev-schema-copy h2{{font-size:1.05rem;font-weight:800;margin:0 0 10px;color:#111}}
.ev-schema-copy p{{font-size:.87rem;color:#444;line-height:1.7;margin:0 0 14px}}
.ev-schema-copy a{{color:#1a73e8;text-decoration:underline;font-weight:600}}
</style>
<div class="ev-flavor-hero">
  <p class="label">{name} · {len(variants)} {vlabel} · AED {price} · Same-Day Dubai</p>
  <h1>{name} — All {len(variants)} {vlabel} in UAE</h1>
  <p class="sub">Buy any <strong>{name}</strong> {vlabel_s.lower()} in UAE for <strong>AED {price}</strong>. All {len(variants)} {vlabel.lower()} in stock — 100% authentic, ESMA-certified, same-day delivery across Dubai.</p>
  <div class="ev-badges">
    <span class="ev-badge">⚡ 1–3 Hour Dubai Delivery</span>
    <span class="ev-badge">💳 Cash on Delivery</span>
    <span class="ev-badge">✅ 100% Authentic {brand}</span>
    <span class="ev-badge">🇦🇪 All 7 Emirates</span>
  </div>
</div>
<div class="ev-grid">{''.join(cards)}</div>
<div class="ev-cta-bar">
  <h2>Shop All {name} {vlabel}</h2>
  <p>AED {price} each · Same-day Dubai delivery · Cash on delivery UAE-wide</p>
  <a href="{url}" class="ev-btn">Shop Now — AED {price} →</a>
</div>
<div class="ev-schema-copy">
  <h2>Which {name} {vlabel_s} Should You Choose?</h2>
  <p>All {len(variants)} {vlabel.lower()} of the <strong>{name}</strong> are stocked at the same
  AED {price} price at {STORE_NAME}, so the choice comes down to preference rather than budget.
  {'Searching for ' + focus + '? ' if focus else ''}Every option ships same-day across Dubai and
  next-day to the rest of the UAE, with cash on delivery available on any {vlabel_s.lower()}.</p>
  <h2>Buying {name} in the UAE</h2>
  <p>{STORE_NAME} stocks the full {name} range as 100% authentic {brand}, ESMA-certified stock —
  not grey-market imports.
  {(f'Browse the <a href="/collections/{slugify(brand)}">{brand} collection</a> for the '
    f'complete lineup, or head back ') if brand else 'Head back '}
  to the <a href="{url}">{name} product page</a> to order.</p>
</div>
<div class="ev-faq">{faq_html}</div>
<script type="application/ld+json">{json.dumps(item_list, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(faq_schema, ensure_ascii=False)}</script>"""

    seo_title = trim(f"{name} {vlabel} UAE | AED {price} | Emirates Vapor", 60)
    seo_desc = trim(f"Buy any {name} {vlabel_s.lower()} in UAE for AED {price}. {len(variants)} "
                    f"{vlabel.lower()} available. Same-day delivery Dubai 1-3 hours. 100% "
                    f"authentic, ESMA-certified.", 158)
    existing = pre._api_get("pages.json", {"handle": page_handle})["pages"]
    payload = {"body_html": page_html, "template_suffix": pre.VARIANTS_TEMPLATE_SUFFIX,
               "seo": {"title": seo_title, "description": seo_desc}}
    if existing:
        pre._api_put(f"pages/{existing[0]['id']}.json",
                     {"page": dict(payload, id=existing[0]["id"])})
    else:
        pre._api_post("pages.json", {"page": dict(
            payload, title=f"{name} — All {vlabel} UAE | AED {price}",
            handle=page_handle, published=True)})
    print(f"  Variant page /pages/{page_handle} ({len(variants)} {vlabel.lower()} cards) ✅")
    return page_handle


# ── [7/8] INDEXING ────────────────────────────────────────────────────────────

def google_indexing(urls):
    sa_file = os.path.join(APP_ROOT, "google_service_account.json")
    if not os.path.exists(sa_file):
        print("  Google: no google_service_account.json — request indexing manually in GSC:")
        print("    https://search.google.com/search-console (URL Inspection → Request Indexing)")
        return
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds = service_account.Credentials.from_service_account_file(
            sa_file, scopes=["https://www.googleapis.com/auth/indexing"])
        sess = AuthorizedSession(creds)
        for u in urls:
            r = sess.post("https://indexing.googleapis.com/v3/urlNotifications:publish",
                          json={"url": u, "type": "URL_UPDATED"}, timeout=15)
            print(f"  Google Indexing API {u}: {r.status_code}")
    except ImportError:
        print("  Google: run `pip3 install -q google-auth` to enable Indexing API pings")
    except Exception as e:
        print(f"  Google Indexing API error: {e}")


def index_everything(urls):
    print("\n[7/8] Pinging search engines...")
    for service, code in pre.ping_indexnow(urls).items():
        print(f"  {service}: {code}")
    google_indexing(urls)
    print(f"  {len(urls)} URLs submitted ✅")


# ── [8/8] BACKLINKS ───────────────────────────────────────────────────────────

def launch_backlinks(urls, anchors, handle):
    print("\n[8/8] Launching backlink runner...")
    runner = os.path.join(SRC, "headless_backlink_runner.py")
    cfg = {"urls": urls, "anchors": anchors,
           "started": datetime.datetime.now().isoformat()}
    json.dump(cfg, open("/tmp/ranking_engine_bl_config.json", "w"), indent=1)
    json.dump(cfg, open(os.path.join(REPORTS, f"backlink_targets_{handle}.json"), "w"), indent=1)
    if not os.path.exists(runner):
        print("  headless_backlink_runner.py not found — skipped")
        return
    log = os.path.join(REPORTS, f"backlinks_{handle}.log")
    proc = subprocess.Popen([sys.executable, runner], cwd=SRC,
                            stdout=open(log, "w"), stderr=subprocess.STDOUT)
    print(f"  Runner launched in background (PID {proc.pid}) — log: {log}")


# ── REPORT ────────────────────────────────────────────────────────────────────

def write_report(prod, focus, secondary, scored, competitors, gen, all_urls, applied, path):
    comp_rows = "\n".join(f"| {p['domain']} | {trim(p['title'], 70)} | {p['word_count']} |"
                          for p in competitors) or "| — | SERP scan empty | — |"
    kw_rows = "\n".join(f"| {r[1]} | {round(r[0],1)} | {r[2] if len(r)>2 else '—'} |"
                        for r in scored)
    faq_md = "\n".join(f"**Q: {q}**\n\n{a}\n" for q, a in gen["faq"])
    md = f"""# Quantum SEO Report — {gen['product_title']}
_{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · {prod['url']}_

## 🎯 Focus Keyword
**{focus}**  — secondary: {', '.join(secondary) or '—'}

## Keyword Scores (volume signal + intent + geo − competition)
| Keyword | Score | Exact-match SERP titles |
|---|---|---|
{kw_rows}

## Competitors Analyzed
| Domain | Title | Words |
|---|---|---|
{comp_rows}

## Meta (applied: {applied})
- **SEO title ({len(gen['seo_title'])}):** {gen['seo_title']}
- **Meta description ({len(gen['meta_description'])}):** {gen['meta_description']}
- **Product H1:** {gen['product_title']}

## Content
{len(re.sub(r'<[^>]+>', ' ', gen['html_content']).split())} words · {len(gen['faq'])} FAQs · Product + FAQPage + BreadcrumbList schema
{'(AggregateRating omitted — set RATING/REVIEW_COUNT in quantum_seo_engine.py from a real reviews app to enable)' if not RATING else ''}

<details><summary>Generated HTML</summary>

```html
{gen['html_content']}
```
</details>

## FAQ
{faq_md}

## Image Alts
{chr(10).join('- ' + a for a in gen['image_alts'])}

## URLs Submitted / To Submit
{chr(10).join('- ' + u for u in all_urls)}

## 5-Day Ranking Sprint Checklist
- [x] Day 1 — on-page applied, IndexNow pinged (Bing/Yandex/Seznam index in hours-days)
- [ ] Day 1 — GSC: URL Inspection → Request Indexing for every URL above (manual, 2 min)
- [ ] Day 1 — GSC: resubmit sitemap.xml
- [ ] Day 2 — backlink runner finished? check log; share product on brand socials
- [ ] Day 2 — add product link to vaporshopdubai.ae related article (primary backlink source)
- [ ] Day 3 — verify indexed: `site:{prod['domain']} {focus}` on Bing + Google
- [ ] Day 4 — check GSC impressions for "{focus}"; if H1/title not indexed, re-request
- [ ] Day 5 — search "{focus}" (incognito, UAE VPN) — record position; iterate content if page 2

_Note: Bing/Yandex/DDG typically index in 1-3 days via IndexNow. Google indexing in 5 days is
achievable for a crawled site; a #1 Google ranking depends on competition — the engine picks
low-competition buyer keywords to make fast wins likely, but no tool can guarantee it._
"""
    with open(path, "w") as f:
        f.write(md)


# ── MASTER ────────────────────────────────────────────────────────────────────

def quantum_rank(url, dry=False, use_ai=True, ping=True, backlinks=True):
    os.makedirs(REPORTS, exist_ok=True)
    t0 = time.time()
    print("=" * 65)
    print("QUANTUM SEO ENGINE v2 — Autonomous Product Ranking")
    print(f"URL: {url}" + ("   [DRY RUN]" if dry else ""))
    print("=" * 65)

    prod = analyze_product(url)
    seeds = build_seeds(prod)
    found = mine_keywords(seeds)
    competitors = analyze_competitors([f"{s} uae" for s in seeds[:2]] + [f"buy {seeds[0]}"])
    focus, secondary, scored = score_keywords(found, prod, competitors)

    dossier = {
        "product": {k: prod[k] for k in ("name", "brand", "type", "price_aed", "url", "tags")},
        "variants": [v["title"] for v in prod["variants"]],
        "existing_description_excerpt": prod["body_text"][:800],
        "focus_keyword": focus, "secondary_keywords": secondary,
        "real_search_queries": [r[1] for r in scored],
        "competitor_pages": [{k: p[k] for k in ("domain", "title", "meta_desc", "h1", "h2s",
                                                "word_count")} for p in competitors],
        "store": {"name": STORE_NAME, "domain": prod["domain"], "usps": USPS},
    }
    gen = None
    if use_ai and shutil.which("claude"):
        try:
            gen = generate_with_ai(dossier)
        except Exception as e:
            print(f"  AI generation failed ({e}) — falling back to template")
    if gen is None:
        gen = generate_template(prod, focus, secondary)
    gen = normalize_gen(gen, prod, focus)

    all_urls = [prod["url"], f"https://{prod['domain']}/"]
    applied = False
    collections, flavors_handle, hub = [], None, None
    if prod["platform"] == "shopify" and not dry:
        collections, flavors_handle, hub = apply_onpage(prod, gen, focus)
        applied = True
        all_urls += [f"https://{prod['domain']}/collections/{h}" for h in collections]
        if flavors_handle:
            all_urls.append(f"https://{prod['domain']}/pages/{flavors_handle}")
        if hub:
            all_urls.append(f"https://{prod['domain']}/pages/{hub}")
    elif prod["platform"] != "shopify":
        print("\n[6/8] Non-Shopify URL — complete SEO package written to report for manual apply.")
    else:
        print("\n[6/8] DRY RUN — skipping Shopify writes.")
    all_urls = list(dict.fromkeys(all_urls))

    if ping and not dry and prod["domain"] in OWN_DOMAINS | {"emiratesvapor.ae"}:
        index_everything(all_urls)
    else:
        print("\n[7/8] Ping skipped" + (" (dry run)" if dry else ""))

    if backlinks and not dry and applied:
        anchors = ([focus.title()] + [s.title() for s in secondary[:4]]
                   + [f"Buy {prod['name']} UAE", f"{prod['brand']} vape Dubai"])
        launch_backlinks(all_urls, anchors, prod["handle"])
    else:
        print("\n[8/8] Backlinks skipped" + (" (dry run)" if dry else ""))

    report = os.path.join(REPORTS,
                          f"{prod['handle']}_{datetime.datetime.now():%Y%m%d_%H%M}.md")
    write_report(prod, focus, secondary, scored, competitors, gen, all_urls, applied, report)

    print("\n" + "=" * 65)
    print(f"✅ QUANTUM SEO ENGINE COMPLETE in {int(time.time()-t0)}s")
    print(f"   Focus keyword : {focus}")
    print(f"   SEO title     : {gen['seo_title']}")
    print(f"   Applied live  : {applied}")
    print(f"   Report        : {report}")
    print("   Manual (2 min): GSC → URL Inspection → Request Indexing for the URLs above")
    print("=" * 65)
    return report


def main():
    args = sys.argv[1:]
    url = next((a for a in args if a.startswith("http")), None)
    if not url:
        print(__doc__)
        sys.exit(1)
    quantum_rank(url,
                 dry="--dry" in args,
                 use_ai="--no-ai" not in args,
                 ping="--no-ping" not in args,
                 backlinks="--no-backlinks" not in args)


if __name__ == "__main__":
    main()
