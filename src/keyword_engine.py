"""
keyword_engine.py
─────────────────
Real-time keyword research engine:
  • Google Autocomplete / People Also Ask scraper
  • SERP competitor title/meta extraction
  • Keyword gap analysis (your page vs top-10 competitors)
  • Buy-intent ontology table builder
  • Topical map generator
"""

import re
import json
import time
import asyncio
import logging
import requests
import urllib.parse
from typing     import List, Dict, Tuple, Optional
from bs4        import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1.  GOOGLE AUTOCOMPLETE  (suggest API – no auth needed)
# ─────────────────────────────────────────────────────────────────────────────

def google_autocomplete(seed: str, lang: str = "en") -> List[str]:
    """Fetch Google autocomplete suggestions for a seed keyword."""
    url = "http://suggestqueries.google.com/complete/search"
    params = {"client": "firefox", "q": seed, "hl": lang}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = r.json()
        suggestions = data[1] if len(data) > 1 else []
        return [s.strip() for s in suggestions if s.strip()]
    except Exception as e:
        logger.warning(f"Autocomplete error for '{seed}': {e}")
        return []


def expand_keyword_matrix(seed: str) -> List[str]:
    """
    Build a rich keyword matrix using:
      – Base seed
      – Alphabet permutations (seed + a, b, c …)
      – Question permutations (who/what/where/why/how/best/buy/cheap)
      – Long-tail suffixes
    """
    all_kws: List[str] = []
    modifiers = [
        "buy", "price", "review", "best", "cheap", "online",
        "discount", "flavors", "where to buy", "vs", "alternative",
        "how to use", "benefits",
        # UAE-specific modifiers
        "dubai", "abu dhabi", "uae", "buy in uae", "price uae",
        "delivery uae", "dubai price", "sharjah", "free delivery uae",
        "online uae", "buy online dubai",
    ]
    questions = ["what is", "how to", "where to buy", "best", "why"]
    alphabet   = list("abcdefghijklmnopqrstuvwxyz")

    # Base seed
    all_kws += google_autocomplete(seed)

    # Modifier-enriched
    for mod in modifiers:
        all_kws += google_autocomplete(f"{seed} {mod}")
        time.sleep(0.15)

    # Alphabet soup
    for letter in alphabet[:10]:          # first 10 letters only to stay fast
        all_kws += google_autocomplete(f"{seed} {letter}")
        time.sleep(0.1)

    # Question starters
    for q in questions:
        all_kws += google_autocomplete(f"{q} {seed}")
        time.sleep(0.1)

    # Deduplicate and clean
    unique = list(dict.fromkeys([k.lower().strip() for k in all_kws if k]))
    return unique


# ─────────────────────────────────────────────────────────────────────────────
# 2.  GOOGLE SERP SCRAPER  (top-10 organic results)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_serp(query: str, num: int = 10) -> List[Dict]:
    """
    Scrape Google SERP for a query.
    Returns list of {rank, url, title, description, h1s, h2s, body_text}
    """
    url = "https://www.google.com/search"
    params = {"q": query, "num": num, "hl": "en", "gl": "ae", "cr": "countryAE"}
    results = []
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")

        for rank, g in enumerate(soup.select("div.g"), start=1):
            title_el = g.select_one("h3")
            link_el  = g.select_one("a[href]")
            desc_el  = g.select_one("div[data-sncf], div.VwiC3b, span.st")
            if not title_el or not link_el:
                continue
            href = link_el["href"]
            if not href.startswith("http"):
                continue
            results.append({
                "rank":        rank,
                "url":         href,
                "title":       title_el.get_text(strip=True),
                "description": desc_el.get_text(strip=True) if desc_el else "",
            })
            if len(results) >= num:
                break
    except Exception as e:
        logger.warning(f"SERP scrape error for '{query}': {e}")

    return results


def fetch_page_content(url: str) -> Dict:
    """Fetch a competitor page and extract SEO signals."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        title    = soup.find("title")
        meta_desc = soup.find("meta", {"name": re.compile("description", re.I)})
        h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
        h2s = [h.get_text(strip=True) for h in soup.find_all("h2")]
        h3s = [h.get_text(strip=True) for h in soup.find_all("h3")]

        # Body text (strip nav/footer/script)
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body = soup.get_text(separator=" ", strip=True)
        body = re.sub(r'\s+', ' ', body)[:5000]    # cap at 5k chars

        return {
            "url":        url,
            "title":      title.get_text(strip=True)  if title     else "",
            "meta_desc":  meta_desc["content"]         if meta_desc else "",
            "h1":         h1s,
            "h2":         h2s,
            "h3":         h3s,
            "body":       body,
            "word_count": len(body.split()),
        }
    except Exception as e:
        logger.warning(f"Fetch error {url}: {e}")
        return {"url": url, "title": "", "meta_desc": "", "h1": [], "h2": [], "h3": [], "body": "", "word_count": 0}


def scrape_competitors_parallel(urls: List[str], max_workers: int = 5) -> List[Dict]:
    """Fetch multiple competitor pages in parallel."""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_page_content, url): url for url in urls}
        for fut in as_completed(futures):
            results.append(fut.result())
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 3.  KEYWORD GAP ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def extract_ngrams(text: str, n_range=(1, 3)) -> List[str]:
    """Extract word n-grams from text."""
    words = re.findall(r'\b[a-z]{3,}\b', text.lower())
    ngrams = []
    for n in range(n_range[0], n_range[1] + 1):
        for i in range(len(words) - n + 1):
            ngrams.append(" ".join(words[i:i+n]))
    return ngrams


def keyword_gap_analysis(
    your_page:    Dict,
    competitors:  List[Dict],
    seed_keyword: str,
) -> Dict:
    """
    Compare your product page vs top competitors.
    Returns:
      - missing_keywords: keywords competitors use but you don't
      - your_keywords:    keywords you're already targeting
      - opportunity_score: how many competitor kws you're missing (%)
      - top_missing:      ranked list of highest-value missing keywords
    """
    your_text  = (
        f"{your_page.get('title','')} "
        f"{your_page.get('meta_desc','')} "
        f"{' '.join(your_page.get('h1',[]))} "
        f"{' '.join(your_page.get('h2',[]))} "
        f"{your_page.get('body','')}"
    ).lower()

    your_ngrams = set(extract_ngrams(your_text))

    comp_kw_freq: Dict[str, int] = {}
    for comp in competitors:
        comp_text = (
            f"{comp.get('title','')} "
            f"{comp.get('meta_desc','')} "
            f"{' '.join(comp.get('h1',[]))} "
            f"{' '.join(comp.get('h2',[]))} "
            f"{comp.get('body','')}"
        ).lower()
        for kw in set(extract_ngrams(comp_text)):
            comp_kw_freq[kw] = comp_kw_freq.get(kw, 0) + 1

    # Keywords in ≥2 competitors but missing from your page
    threshold = max(2, len(competitors) // 3)
    missing = {
        kw: freq for kw, freq in comp_kw_freq.items()
        if freq >= threshold and kw not in your_ngrams
        and len(kw.split()) >= 2                   # phrases only
        and len(kw) > 6
    }

    # Sort by frequency (proxy for importance)
    top_missing = sorted(missing.items(), key=lambda x: x[1], reverse=True)[:50]

    opportunity = round(
        len(missing) / max(len(comp_kw_freq), 1) * 100, 1
    )

    return {
        "missing_keywords":   [kw for kw, _ in top_missing],
        "missing_with_scores": top_missing,
        "your_keywords":      list(your_ngrams)[:30],
        "opportunity_score":  opportunity,
        "competitor_count":   len(competitors),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4.  PEOPLE ALSO ASK  scraper
# ─────────────────────────────────────────────────────────────────────────────

def get_people_also_ask(query: str) -> List[str]:
    """Extract 'People Also Ask' questions from Google SERP."""
    url    = "https://www.google.com/search"
    params = {"q": query + " UAE", "hl": "en", "gl": "ae"}
    paa    = []
    try:
        r    = requests.get(url, params=params, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        for div in soup.select("div[data-q], div.related-question-pair"):
            q = div.get_text(strip=True)
            if "?" in q and len(q) < 200:
                paa.append(q)
        # Also try aria-expanded buttons
        for btn in soup.select("div[role='button']"):
            t = btn.get_text(strip=True)
            if "?" in t and 10 < len(t) < 150:
                paa.append(t)
    except Exception as e:
        logger.warning(f"PAA error for '{query}': {e}")

    return list(dict.fromkeys(paa))[:15]


# ─────────────────────────────────────────────────────────────────────────────
# 5.  RELATED SEARCHES  scraper
# ─────────────────────────────────────────────────────────────────────────────

def get_related_searches(query: str) -> List[str]:
    """Extract related searches from Google SERP bottom."""
    url    = "https://www.google.com/search"
    params = {"q": query + " UAE", "hl": "en", "gl": "ae"}
    related = []
    try:
        r    = requests.get(url, params=params, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href*='search?q='], p.nVcaUb a, div.dg6jd a"):
            t = a.get_text(strip=True)
            if t and len(t) > 4 and query.split()[0].lower() in t.lower():
                related.append(t)
    except Exception as e:
        logger.warning(f"Related searches error for '{query}': {e}")
    return list(dict.fromkeys(related))[:15]


# ─────────────────────────────────────────────────────────────────────────────
# 6.  KEYWORD CLASSIFIER  (buy-intent categorizer)
# ─────────────────────────────────────────────────────────────────────────────

INTENT_PATTERNS = {
    "Transactional": [
        r'\bbuy\b', r'\bpurchase\b', r'\border\b', r'\bshop\b',
        r'\badd to cart\b', r'\bcheckout\b', r'\bget\b',
    ],
    "Price / Deal": [
        r'\bprice\b', r'\bcheap\b', r'\bdiscount\b', r'\bdeal\b',
        r'\bsale\b', r'\baffordable\b', r'\bcost\b', r'\bpromo\b',
    ],
    "Local / Availability": [
        r'\bnear me\b', r'\blocal\b', r'\bstore\b', r'\bnearby\b',
        r'\bdelivery\b', r'\bshipping\b', r'\bin stock\b',
    ],
    "Product Variant": [
        r'\bflavors?\b', r'\btypes?\b', r'\bvariant\b', r'\bsizes?\b',
        r'\bcolors?\b', r'\bversion\b', r'\bpack\b', r'\bmg\b', r'\bml\b',
    ],
    "Brand Comparison": [
        r'\bvs\b', r'\bversus\b', r'\bcompare\b', r'\bbetter than\b',
        r'\bdifference\b', r'\balternative\b', r'\binstead of\b',
    ],
    "Review / Social Proof": [
        r'\breview\b', r'\brating\b', r'\blegit\b', r'\bworth it\b',
        r'\bhonest\b', r'\bopinion\b', r'\btestimonial\b',
    ],
    "Commercial Investigation": [
        r'\bbest\b', r'\btop\b', r'\brecommend\b', r'\bworth\b',
        r'\bshould i\b', r'\bwhich\b',
    ],
    "Informational (Top-of-Funnel)": [
        r'\bwhat is\b', r'\bhow to\b', r'\bwhy\b', r'\bwhen\b',
        r'\bbenefits?\b', r'\beffects?\b', r'\bingredients?\b',
    ],
}

def classify_keyword_intent(keyword: str) -> str:
    """Return the primary buy-intent category for a keyword."""
    kw_lower = keyword.lower()
    for category, patterns in INTENT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, kw_lower):
                return category
    return "Informational (Top-of-Funnel)"


def build_ontology_table(keywords: List[str], seed: str) -> List[Dict]:
    """
    Build a buy-intent ontology table.
    Each row: keyword, intent, search_volume_proxy, priority, content_action
    """
    table   = []
    content_actions = {
        "Transactional":                  "Product Page CTA – Add direct buy link + urgency text",
        "Price / Deal":                   "Product Page – Add price section, discount badge, comparison",
        "Local / Availability":           "Product Page – Add stock indicator, shipping info widget",
        "Product Variant":                "Product Page – Showcase variant selector with descriptions",
        "Brand Comparison":               "Blog/Category – Create comparison table or FAQ",
        "Review / Social Proof":          "Product Page – Add review schema, star rating block",
        "Commercial Investigation":       "Category/Blog – 'Best of' roundup or buying guide",
        "Informational (Top-of-Funnel)":  "Blog Post – Educational content with CTA at bottom",
    }
    priority_map = {
        "Transactional":                  "🔴 HIGH",
        "Price / Deal":                   "🔴 HIGH",
        "Product Variant":                "🟠 MEDIUM-HIGH",
        "Brand Comparison":               "🟠 MEDIUM-HIGH",
        "Review / Social Proof":          "🟠 MEDIUM-HIGH",
        "Commercial Investigation":       "🟡 MEDIUM",
        "Local / Availability":           "🟡 MEDIUM",
        "Informational (Top-of-Funnel)":  "🟢 LOW (build authority)",
    }

    for kw in keywords:
        intent = classify_keyword_intent(kw)
        # Volume proxy: shorter = more general = higher volume estimate
        word_c = len(kw.split())
        vol_proxy = "High" if word_c <= 2 else "Medium" if word_c <= 4 else "Low (Long-tail)"

        table.append({
            "keyword":        kw,
            "intent":         intent,
            "volume_proxy":   vol_proxy,
            "priority":       priority_map.get(intent, "🟢 LOW"),
            "content_action": content_actions.get(intent, "General content"),
            "seed":           seed,
        })

    # Sort: Transactional first, then priority
    priority_order = list(priority_map.keys())
    table.sort(key=lambda x: priority_order.index(x["intent"]) if x["intent"] in priority_order else 99)
    return table


# ─────────────────────────────────────────────────────────────────────────────
# 7.  TOPICAL MAP GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def build_topical_map(seed: str, keywords: List[str], paa: List[str]) -> Dict:
    """
    Create a topical map with cluster → pillar → supporting pages.
    Returns structured dict ready for display and export.
    """
    clusters = {
        "🏠 Pillar: Core Product":            [],
        "📦 Product Variants & Flavors":      [],
        "💰 Price & Deals":                   [],
        "⭐ Reviews & Comparisons":           [],
        "📖 Informational / Educational":     [],
        "❓ FAQ / People Also Ask":           [],
        "🔗 Category & Collection":           [],
        "📍 Local & Availability":            [],
    }

    for kw in keywords:
        intent = classify_keyword_intent(kw)
        if intent == "Transactional":
            clusters["🏠 Pillar: Core Product"].append(kw)
        elif intent == "Product Variant":
            clusters["📦 Product Variants & Flavors"].append(kw)
        elif intent == "Price / Deal":
            clusters["💰 Price & Deals"].append(kw)
        elif intent in ("Review / Social Proof", "Brand Comparison"):
            clusters["⭐ Reviews & Comparisons"].append(kw)
        elif intent == "Informational (Top-of-Funnel)":
            clusters["📖 Informational / Educational"].append(kw)
        elif intent == "Local / Availability":
            clusters["📍 Local & Availability"].append(kw)
        elif intent == "Commercial Investigation":
            clusters["🔗 Category & Collection"].append(kw)
        else:
            clusters["📖 Informational / Educational"].append(kw)

    # Add PAA to FAQ cluster
    clusters["❓ FAQ / People Also Ask"] = paa[:15]

    # Trim each cluster to top 10, deduplicate
    for k in clusters:
        clusters[k] = list(dict.fromkeys(clusters[k]))[:10]

    return {
        "seed":     seed,
        "clusters": clusters,
        "total_topics": sum(len(v) for v in clusters.values()),
        "pillar_page": f"Best {seed.title()} – Complete Guide & Where to Buy",
        "url_structure": {
            "pillar":   f"/collections/{seed.lower().replace(' ','-')}",
            "products": f"/products/{seed.lower().replace(' ','-')}-[variant]",
            "blog":     f"/blogs/vape-guide/{seed.lower().replace(' ','-')}-uae-review",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8.  MASTER KEYWORD RESEARCH RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_full_keyword_research(
    seed:           str,
    your_page_data: Optional[Dict] = None,
    progress_cb:    Optional[callable] = None,
) -> Dict:
    """
    Master pipeline:
      1. Autocomplete expansion
      2. SERP top-10 scrape
      3. Competitor page crawl
      4. Keyword gap analysis
      5. PAA extraction
      6. Related searches
      7. Ontology table
      8. Topical map
    """

    def _progress(msg, pct):
        logger.info(f"[{pct}%] {msg}")
        if progress_cb:
            progress_cb(msg, pct)

    _progress(f"Expanding keyword matrix for: {seed}", 5)
    all_keywords = expand_keyword_matrix(seed)
    _progress(f"Found {len(all_keywords)} keyword variations", 20)

    _progress("Scraping Google SERP top-10 results…", 25)
    serp_results = scrape_serp(seed, num=10)
    competitor_urls = [r["url"] for r in serp_results if "google" not in r["url"]][:8]

    _progress(f"Crawling {len(competitor_urls)} competitor pages…", 35)
    competitors_data = scrape_competitors_parallel(competitor_urls[:6])

    _progress("Running keyword gap analysis…", 55)
    gap_data = {}
    if your_page_data:
        gap_data = keyword_gap_analysis(your_page_data, competitors_data, seed)
    else:
        # If no product page provided yet, analyse vs empty baseline
        empty_page = {"title": "", "meta_desc": "", "h1": [], "h2": [], "body": ""}
        gap_data   = keyword_gap_analysis(empty_page, competitors_data, seed)

    _progress("Extracting People Also Ask…", 65)
    paa      = get_people_also_ask(seed)

    _progress("Fetching related searches…", 70)
    related  = get_related_searches(seed)

    # Merge all keywords
    combined_kws = list(dict.fromkeys(
        all_keywords
        + gap_data.get("missing_keywords", [])
        + related
    ))[:200]

    _progress("Building buy-intent ontology table…", 80)
    ontology = build_ontology_table(combined_kws, seed)

    _progress("Generating topical map…", 90)
    topical  = build_topical_map(seed, combined_kws, paa)

    _progress("Keyword research complete!", 100)

    return {
        "seed":            seed,
        "all_keywords":    combined_kws,
        "serp_results":    serp_results,
        "competitors":     competitors_data,
        "gap_analysis":    gap_data,
        "paa":             paa,
        "related":         related,
        "ontology_table":  ontology,
        "topical_map":     topical,
        "total_keywords":  len(combined_kws),
    }
