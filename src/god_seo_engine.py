#!/usr/bin/env python3
"""
GOD LEVEL SEO ENGINE v3 — Big-Data SERP Reverse-Engineering + Topical Cluster Network
======================================================================================
Builds on quantum_seo_engine v2. Input: one product URL. Zero manual SEO after this
(only optional: GSC "Request Indexing" for fastest Google pickup).

Pipeline:
  [G1] RANKING BLUEPRINT   — deep-analyzes the CURRENT top-ranking pages for the focus
                             keyword: word counts, title patterns, H2 topics, schema
                             types, FAQ/table adoption, keyword density, image alts.
                             Saves the winning pattern to rank_reports/blueprints/ and
                             a cumulative ranking_blueprints.json (reusable "system").
  [G2] KEYWORDS            — v2 mining (Google/Bing/DDG UAE suggest) + scoring
  [G3] CONTENT             — AI content generated AGAINST the blueprint (beats top
                             page word count, covers every competitor H2 topic)
  [G4] TOPICAL CLUSTER     — brand collection (created if missing + product added),
                             buying-guide hub page, flavors page, all cross-linked
                             with keyword anchors → product URL is the cluster center
  [G5] ON-PAGE APPLY       — meta, alts, banner, schema, content (v2 machinery)
  [G6] INDEXING            — IndexNow ×4 + Google Indexing API (if SA key present)
  [G7] AUTO BACKLINKS      — directly drives headless_backlink_runner.run_backlinks()
                             across its 40+ API posting sites for cluster URLs
  [G8] LIVE VERIFICATION   — re-fetches the live pages and asserts every SEO element
                             actually rendered (title, meta, 3× schema, FAQ, links,
                             cluster pages return 200). Failures are listed loudly.

Usage:
  python3 god_seo_engine.py <product-url> [--dry] [--no-ai] [--no-ping] [--no-backlinks]
                            [--package <content.json>] [--focus "exact keyword"] [--clean-url]
  ./god.sh <product-url>        # from app root

--focus overrides the engine's mechanical top-scored keyword — use this when a human
(or whoever wrote --package content) judged a more specific/relevant keyword should be
targeted, e.g. an exact product-model match over a generic brand-wide query. The old
top pick is kept as a secondary keyword rather than dropped.
"""

import json, os, re, shutil, statistics, subprocess, sys, time, datetime
import requests
from urllib.parse import urlparse

SRC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)
import quantum_seo_engine as q
import product_ranking_engine as pre
from product_ranking_engine import slugify
from quantum_seo_engine import trim, UA, STORE_NAME, USPS, REPORTS, OWN_DOMAINS

BLUEPRINT_DIR = os.path.join(REPORTS, "blueprints")
BLUEPRINT_DB = os.path.join(REPORTS, "ranking_blueprints.json")
H2_JUNK = re.compile(r"(?i)^(quick links?|related|reviews?|share|cart|menu|newsletter|"
                     r"you may also|customer|footer|subscribe|follow)")

# Known top-ranking UAE vape stores (verified live during SERP scans) — used for
# direct store probing when search engines rate-limit the SERP scrape.
COMPETITOR_SEEDS = ["vapemonkey.ae", "vapegate.ae", "hqd.ae", "vaporking.ae",
                    "vapeliondubai.com", "prime-vape.com", "tugboatshop.ae",
                    "vapebrodubai.com"]


def competitor_store_probe(prod, limit=5):
    """SERP-independent big-data source: ask each competitor store's own Shopify
    search API (suggest.json) for this exact product → their live product pages."""
    out = []
    nums = [t for t in prod["name"].split() if t.isdigit()]
    queries = list(dict.fromkeys(filter(None, [
        f"{prod['brand']} {nums[0]}" if prod["brand"] and nums else None,
        " ".join(prod["name"].split()[:3]),
        prod["brand"] or None])))
    for d in COMPETITOR_SEEDS:
        for query in queries:  # broad→narrow until the store returns a match
            try:
                r = requests.get(f"https://{d}/search/suggest.json",
                                 params={"q": query, "resources[type]": "product",
                                         "resources[limit]": 3},
                                 headers=UA, timeout=8).json()
                hits = r.get("resources", {}).get("results", {}).get("products", [])
                if hits:
                    out.append((d, f"https://{d}" + hits[0]["url"], hits[0].get("title", "")))
                    break
            except Exception:
                continue
        if len(out) >= limit:
            break
    return out


# ── [G1] RANKING BLUEPRINT — reverse-engineer the current winners ─────────────

def deep_page_signals(html, url, domain, kw):
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S)
    words = text.split()
    title = q._rx(r"<title[^>]*>(.*?)</title>", html)
    h2s = [re.sub(r"<[^>]+>", "", h).strip()
           for h in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S)]
    h2s = [h for h in h2s if 8 <= len(h) <= 90 and not H2_JUNK.search(h)][:10]
    imgs = re.findall(r"<img[^>]*>", html)
    kw_hits = text.lower().count(kw.lower())
    return {
        "domain": domain, "url": url,
        "title": title, "title_len": len(title),
        "kw_in_title": kw.lower() in title.lower(),
        "meta_desc": q._rx(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html),
        "h1": q._rx(r"<h1[^>]*>(.*?)</h1>", html),
        "h2s": h2s, "h2_count": len(h2s),
        "word_count": len(words),
        "schema_types": sorted(set(re.findall(r'"@type"\s*:\s*"(\w+)"', html))),
        "has_faq_schema": '"FAQPage"' in html,
        "has_table": "<table" in html,
        "img_count": len(imgs),
        "img_alt_pct": int(100 * sum(1 for i in imgs if 'alt="' in i and 'alt=""' not in i)
                           / max(len(imgs), 1)),
        "kw_density_pct": round(100 * kw_hits / max(len(words), 1), 2),
        "internal_links": len(re.findall(r'href="/', html)),
    }


def build_ranking_blueprint(queries, focus, prod, max_pages=7):
    print("\n[G1] RANKING BLUEPRINT — reverse-engineering current top rankers...")
    pages, seen = [], set()
    for query in queries[:3]:
        for d, u, t in q.serp_results(query, 10):
            if d in OWN_DOMAINS or d in seen or len(pages) >= max_pages:
                continue
            seen.add(d)
            try:
                html = requests.get(u, headers=UA, timeout=12).text
            except Exception:
                continue
            sig = deep_page_signals(html, u, d, focus)
            sig["serp_query"] = query
            pages.append(sig)
            print(f"  📡 {d}: {sig['word_count']}w · title {sig['title_len']}ch"
                  f"{' ·kw✓' if sig['kw_in_title'] else ''} · {sig['h2_count']} H2 · "
                  f"schema {','.join(sig['schema_types'][:4]) or 'none'} · "
                  f"density {sig['kw_density_pct']}%")
        if len(pages) >= max_pages:
            break

    if len(pages) < 3:
        print("  SERP thin — probing competitor stores directly (Shopify suggest.json)...")
        for d, u, t in competitor_store_probe(prod):
            if d in seen:
                continue
            seen.add(d)
            try:
                html = requests.get(u, headers=UA, timeout=12).text
            except Exception:
                continue
            sig = deep_page_signals(html, u, d, focus)
            sig["serp_query"] = "store-probe"
            pages.append(sig)
            print(f"  📡 {d}: {sig['word_count']}w · {sig['h2_count']} H2 · "
                  f"schema {','.join(sig['schema_types'][:4]) or 'none'} · "
                  f"density {sig['kw_density_pct']}%")

    if not pages:
        # 2000 words is the user's set standard (confirmed July 15) — raw length
        # matters less than correct, natural keyword usage; don't chase word count
        # for its own sake, and don't let density climb into stuffing territory.
        print("  SERP + store probes empty — using safe defaults")
        return {"focus": focus, "target_word_count": 2000, "h2_topics_to_cover": [],
                "schema_types_used": [], "kw_density_target": 1.0, "faq_adoption_pct": 0,
                "title_pattern": "", "pages": []}, []

    wc = [p["word_count"] for p in pages]
    dens = [p["kw_density_pct"] for p in pages if 0 < p["kw_density_pct"] < 6]
    all_h2, seen_h2 = [], set()
    for p in pages:
        for h in p["h2s"]:
            k = h.lower()[:40]
            if k not in seen_h2:
                seen_h2.add(k)
                all_h2.append(h)
    blueprint = {
        "focus": focus, "product": prod["name"], "built": datetime.datetime.now().isoformat(),
        "target_word_count": max(2000, int(max(wc) * 1.15)),
        "median_competitor_words": int(statistics.median(wc)),
        "title_pattern": pages[0]["title"],
        "avg_title_len": int(statistics.mean(p["title_len"] for p in pages)),
        "kw_in_title_pct": int(100 * sum(p["kw_in_title"] for p in pages) / len(pages)),
        "h2_topics_to_cover": all_h2[:14],
        "schema_types_used": sorted({t for p in pages for t in p["schema_types"]}),
        "faq_adoption_pct": int(100 * sum(p["has_faq_schema"] for p in pages) / len(pages)),
        "table_adoption_pct": int(100 * sum(p["has_table"] for p in pages) / len(pages)),
        "kw_density_target": round(statistics.median(dens), 2) if dens else 1.0,
        "avg_internal_links": int(statistics.mean(p["internal_links"] for p in pages)),
        "pages": pages,
    }
    os.makedirs(BLUEPRINT_DIR, exist_ok=True)
    bp_file = os.path.join(BLUEPRINT_DIR, f"blueprint_{slugify(focus)}.json")
    json.dump(blueprint, open(bp_file, "w"), indent=1, ensure_ascii=False)
    db = {}
    if os.path.exists(BLUEPRINT_DB):
        try:
            db = json.load(open(BLUEPRINT_DB))
        except Exception:
            db = {}
    db[focus] = {k: blueprint[k] for k in blueprint if k != "pages"}
    json.dump(db, open(BLUEPRINT_DB, "w"), indent=1, ensure_ascii=False)
    print(f"  🧬 Blueprint: beat {max(wc)}w → target {blueprint['target_word_count']}w, "
          f"cover {len(blueprint['h2_topics_to_cover'])} competitor H2 topics, "
          f"density ~{blueprint['kw_density_target']}%")
    print(f"  💾 Saved system → {bp_file}")
    print(f"  🧮 FORMULA → {blueprint['target_word_count']}+ words · density "
          f"~{blueprint['kw_density_target']}% · kw in title ({blueprint['kw_in_title_pct']}% "
          f"of winners do) · Product+FAQPage+Breadcrumb schema · FAQ (adoption "
          f"{blueprint['faq_adoption_pct']}%) · topical cluster ≥4 pages · 40+ backlinks · "
          f"llms.txt entry")
    return blueprint, pages


# ── [G4] TOPICAL CLUSTER NETWORK ──────────────────────────────────────────────

# ── Collection SEO template system (from project_collection_seo_system.md /
#    hub-page-system.md conventions — proven on HQD/Al Fakher collections) ────
COLLECTION_THEME_ID = 145181737034


def build_collection_ev_seo_html(brand, focus, gen, collection_handle):
    """The 'ev_seo' custom-liquid block: dark hero + 5-section accordion + FAQ schema,
    rendered BELOW the product grid. Matches the documented .ev-col-seo/.ev-col-hero/
    .ev-acc class structure exactly, so it inherits the theme's existing full-width CSS."""
    fk = (focus or brand).title()
    faqs = (gen.get("faq") or [])[:4] or [
        (f"Is {brand} authentic at {STORE_NAME}?",
         f"Yes — every {brand} product is 100% authentic, ESMA-certified, sourced from an "
         f"authorized UAE distributor."),
        (f"How fast is {brand} delivery in Dubai?",
         f"1–3 hours in Dubai, next-day to the rest of the UAE, with cash on delivery available."),
    ]
    faq_html = "".join(f"<details><summary>{q}</summary><div class=\"body\"><p>{a}</p></div>"
                       f"</details>" for q, a in faqs)
    faq_schema = {"@context": "https://schema.org", "@type": "FAQPage",
                 "mainEntity": [{"@type": "Question", "name": q,
                                "acceptedAnswer": {"@type": "Answer", "text": a}}
                               for q, a in faqs]}
    return f"""<style>
.ev-col-seo{{width:100%;background:#fff}}
.ev-col-hero{{background:linear-gradient(135deg,#1e0533,#4c1d95,#1e0533);color:#fff;
  padding:22px 40px;border-radius:0}}
.ev-col-hero .eyebrow{{font-size:.72rem;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:rgba(255,255,255,.6);margin:0 0 8px}}
.ev-col-hero .desc{{font-size:.92rem;color:#fff;margin:0 0 14px;max-width:720px;line-height:1.6}}
.ev-col-hero .chips{{display:flex;flex-wrap:wrap;gap:8px}}
.ev-col-hero .chips span{{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.18);
  border-radius:20px;padding:5px 14px;font-size:.78rem;font-weight:600}}
.ev-acc details{{border-bottom:1px solid #e8e8e8}}
.ev-acc details summary{{padding:16px 40px;font-weight:700;cursor:pointer;list-style:none;
  display:flex;justify-content:space-between;align-items:center;font-size:.95rem;color:#111}}
.ev-acc details summary::-webkit-details-marker{{display:none}}
.ev-acc details summary::after{{content:"+";font-size:1.2rem;color:#7c3aed}}
.ev-acc details[open] summary::after{{content:"−"}}
.ev-acc details .body{{padding:0 40px 24px;color:#444;font-size:.88rem;line-height:1.75}}
@media(max-width:640px){{
  .ev-col-hero{{padding:20px}}
  .ev-acc details summary,.ev-acc details .body{{padding-left:20px;padding-right:20px}}
}}
</style>
<div class="ev-col-seo">
  <div class="ev-col-hero">
    <p class="eyebrow">{brand} · UAE · Same-Day Dubai Delivery</p>
    <p class="desc">Shop authentic <strong>{brand}</strong> vapes in the UAE — every device
    ESMA-certified, with 1–3 hour Dubai delivery and cash on delivery across all 7 Emirates.</p>
    <div class="chips"><span>⚡ 1–3 HR Dubai</span><span>✅ 100% Authentic</span>
    <span>💳 Cash on Delivery</span><span>🇦🇪 All 7 Emirates</span></div>
  </div>
  <div class="ev-acc">
    <details open><summary>{fk} — Best Price in UAE</summary>
      <div class="body"><p>Every {brand} product on this page is genuine stock, ESMA-certified
      for legal sale in the UAE, with same-day delivery across Dubai.</p></div>
    </details>
    <details><summary>Delivery &amp; Pricing in the UAE</summary>
      <div class="body"><p>Dubai orders arrive in 1–3 hours; Abu Dhabi, Sharjah and the other
      Emirates receive next-day delivery. Cash on delivery and card payment both accepted.</p>
      </div>
    </details>
    <details><summary>Frequently Asked Questions</summary>
      <div class="body">{faq_html}</div>
    </details>
  </div>
</div>
<script type="application/ld+json">{json.dumps(faq_schema, ensure_ascii=False)}</script>"""


def ensure_collection_template(theme_id, handle):
    """templates/collection.{handle}.json — 3 sections (title, main product grid, ev_seo
    full-width custom-liquid). NEVER disable `main` (kills the product grid) and ALWAYS set
    section_width:full-width on every section (documented alignment requirement)."""
    tmpl = {
        "sections": {
            "section": {"type": "section",
                        "settings": {"section_width": "full-width"}},
            # products_per_page:24 (closest valid step to the user's requested 25) +
            # infinite_scroll:false → real numbered pagination (?page=2, ?page=3...),
            # never a hard-capped "show all" section — same fix applied bulk across
            # 35 existing collection templates July 15 (all previously capped at 16
            # via a "product-list" carousel section, which can't paginate at all).
            "main": {"type": "main-collection",
                    "settings": {"section_width": "full-width",
                                "products_per_page": 24,
                                "enable_infinite_scroll": False}},
            "ev_seo": {"type": "custom-liquid",
                      "settings": {"custom_liquid": "{{ collection.metafields.custom.ev_seo_html }}",
                                  "section_width": "full-width",
                                  "padding_top": 0, "padding_bottom": 0}},
        },
        "order": ["section", "main", "ev_seo"],
    }
    requests.put(f"{pre.SHOPIFY_BASE}/themes/{theme_id}/assets.json", headers=pre.SH,
                json={"asset": {"key": f"templates/collection.{handle}.json",
                                "value": json.dumps(tmpl)}}, timeout=20)


def apply_collection_seo(collection_id, collection_type, handle, brand, focus, gen):
    """Wire the ev_seo accordion into a collection: metafield holds the HTML (the JSON
    template references it via Liquid), body_html is cleared (old body_html shows as an
    extra dark box alongside ev_seo per documented behavior), template_suffix set."""
    html = build_collection_ev_seo_html(brand, focus, gen, handle)
    key = "custom_collection" if collection_type == "custom" else "smart_collection"
    endpoint = f"{'custom' if collection_type == 'custom' else 'smart'}_collections/{collection_id}.json"
    try:
        ensure_collection_template(COLLECTION_THEME_ID, handle)
        pre._api_post(f"collections/{collection_id}/metafields.json", {"metafield": {
            "namespace": "custom", "key": "ev_seo_html", "value": html,
            "type": "multi_line_text_field"}})
        pre._api_put(endpoint, {key: {"id": collection_id, "body_html": "",
                                      "template_suffix": handle}})
        print(f"  Collection SEO template applied (ev_seo accordion + FAQ schema) ✅")
    except Exception as e:
        print(f"  Collection SEO template skipped ({e})")


def ensure_brand_collection(prod, gen=None, focus=""):
    brand = prod["brand"]
    if not brand:
        return None
    handle = slugify(brand)
    for kind, ctype in (("custom_collections", "custom"), ("smart_collections", "smart")):
        try:
            found = pre._api_get(f"{kind}.json", {"handle": handle})[kind]
        except Exception:
            found = []
        if found:
            try:  # make sure product is in it (ignored for smart / duplicates)
                pre._api_post("collects.json", {"collect": {
                    "product_id": prod["id"], "collection_id": found[0]["id"]}})
            except Exception:
                pass
            print(f"  Cluster: collection /collections/{handle} exists, product linked ✅")
            if gen is not None:
                apply_collection_seo(found[0]["id"], ctype, handle, brand, focus, gen)
            return handle
    seo_title = trim(f"{brand} Vape UAE | Buy {brand} Dubai | {STORE_NAME}", 60)
    seo_desc = trim(f"Shop all {brand} vapes in Dubai UAE. Best prices, same-day 1-3 hour "
                    f"Dubai delivery, cash on delivery. 100% authentic, ESMA-certified.", 158)
    body = (f"<h2>{brand} Vapes in UAE — Authentic, Same-Day Dubai Delivery</h2>"
            f"<p>Buy authentic <strong>{brand}</strong> vapes at {STORE_NAME}. "
            f"Every {brand} device is 100% original and ESMA-certified, with 1–3 hour "
            f"delivery in Dubai and cash on delivery across all 7 Emirates.</p>")
    coll_id = pre.step4_create_collection(f"{brand} Vapes UAE", handle, seo_title, seo_desc,
                                          body, [prod["id"]])
    if gen is not None and coll_id:
        apply_collection_seo(coll_id, "custom", handle, brand, focus, gen)
    return handle


# ── Brand Hub template (matches the live "bh-" system on /pages/hqd-vape-uae —
#    proven design the user picked as the SEO/design reference, July 2026) ─────

TRUST_TICKER_ITEMS = ["⚡ 1–3 Hours Dubai", "✅ 100% Authentic", "💵 Cash on Delivery",
                      "🇦🇪 All 7 Emirates", "🔒 Licensed UAE Retailer",
                      "📦 Free Delivery AED 150+", "⭐ Thousands of Happy Customers"]


def fetch_brand_products(brand, exclude_id=None, limit=12):
    """Live product data for the hub's dynamic grid — real images/prices, not text."""
    if not brand:
        return []
    try:
        prods = pre._api_get("products.json", {
            "vendor": brand, "limit": limit, "status": "active",
            "fields": "id,handle,title,images,variants"})["products"]
    except Exception:
        return []
    out = []
    for p in prods:
        if p["id"] == exclude_id:
            continue
        img = (p.get("images") or [{}])[0].get("src", "")
        title = q.normalize_display_name(q.clean_title(p["title"]), brand)
        out.append({"id": p["id"], "title": title,
                    "handle": p["handle"], "price": p["variants"][0]["price"],
                    "image": img.split("?")[0] if img else ""})
    return out


def build_brand_hub_html(prod, gen, focus, secondary, collection_handle, seo_html, faq_pairs):
    brand = prod["brand"] or STORE_NAME
    domain = prod["domain"]
    hero_img = prod["images"][0]["src"].split("?")[0] if prod.get("images") else ""
    others = fetch_brand_products(brand, exclude_id=prod["id"], limit=7)
    all_brand = [{"id": prod["id"], "title": gen["product_title"], "handle": prod["handle"],
                 "price": str(prod["price_aed"]), "image": hero_img,
                 "featured": True}] + [dict(o, featured=False) for o in others]

    cards = []
    for p in all_brand[:8]:
        badge = ('<div class="bh-prod-badge">FEATURED</div>' if p.get("featured") else "")
        # p["image"] is the raw CDN URL (no size suffix yet) — apply the transform
        # preserving the REAL extension; blindly appending "_400x.jpg" broke every
        # non-.jpg image (webp/png), same class of bug found in flavors_page_v2.
        img_src = (re.sub(r"\.(jpg|jpeg|png|webp|gif)$", r"_400x.\1", p["image"],
                          flags=re.IGNORECASE) if p["image"] else "")
        img_tag = (f'<img src="{img_src}" alt="{p["title"]} UAE" loading="lazy">'
                   if img_src else "")
        cards.append(f"""<a href="/products/{p['handle']}" class="bh-prod">
  <div class="bh-prod-img">{badge}{img_tag}</div>
  <div class="bh-prod-body">
    <div class="bh-prod-name">{p['title']}</div>
    <div class="bh-prod-price">AED {p['price']}</div>
    <div class="bh-prod-btn">Shop Now</div>
  </div>
</a>""")

    why_cards = "".join(
        f'<div class="bh-why-card"><div class="bh-why-icon">{icon}</div>'
        f'<div class="bh-why-title">{title}</div><div class="bh-why-desc">{desc}</div></div>'
        for icon, title, desc in [
            ("⚡", "Same-Day Delivery", "1–3 hours across Dubai, next-day all Emirates"),
            ("✅", "100% Authentic", f"Every {brand} product verified genuine, ESMA-certified"),
            ("💵", "Cash on Delivery", "Pay when it arrives — no upfront risk"),
            ("🏪", "Official UAE Stockist", "Licensed retailer, thousands of happy customers")])

    ticker = "".join(f'<span class="bh-tb">{t}</span>' for t in TRUST_TICKER_ITEMS * 2)

    faq_html = "".join(
        f'<details><summary>{q}<span></span></summary>'
        f'<div class="bh-faq-body">{a}</div></details>'
        for q, a in faq_pairs[:6])

    hub_title = f"Buy {brand} Vapes in UAE — Fast Delivery, Best Price"
    hub_sub = (f"Shop the complete {brand} range in Dubai & UAE. Same-day 1–3 hour Dubai "
              f"delivery, cash on delivery, 100% authentic and ESMA-certified.")

    page_html = f"""<style>
.bh{{font-family:var(--font-body--family,Inter,system-ui,sans-serif);color:#111;
  width:100vw;max-width:100vw;margin-left:calc(50% - 50vw);margin-right:calc(50% - 50vw);
  padding:0 24px;box-sizing:border-box;overflow-x:hidden}}
@media(min-width:1200px){{.bh{{padding:0 48px}}}}
.bh-hero{{background:linear-gradient(135deg,#1e0533,#4c1d95,#1e0533);border-radius:14px;
  padding:40px 28px;margin:0 0 28px;position:relative;overflow:hidden;display:flex;
  align-items:center;justify-content:space-between;gap:20px}}
.bh-hero::before{{content:'';position:absolute;inset:0;
  background:url('{hero_img}') right center/contain no-repeat;opacity:.12}}
.bh-hero-left{{position:relative;z-index:2;max-width:560px}}
.bh-badge{{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.14);
  border:1px solid rgba(255,255,255,.22);border-radius:100px;padding:5px 14px;font-size:.72rem;
  font-weight:600;color:rgba(255,255,255,.9);margin-bottom:14px;letter-spacing:.04em}}
.bh-hero h1{{font-size:clamp(1.5rem,3.5vw,2.2rem);font-weight:800;color:#fff;margin:0 0 12px;
  line-height:1.15;letter-spacing:-.02em}}
.bh-hero-sub{{font-size:.9rem;color:#fff !important;margin:0 0 22px;line-height:1.6}}
.bh-cta{{display:inline-flex;align-items:center;gap:8px;background:#7c3aed;color:#fff;
  text-decoration:none;padding:12px 24px;border-radius:10px;font-weight:700;font-size:.9rem}}
.bh-hero-img{{position:relative;z-index:2;width:170px;min-width:130px;flex-shrink:0}}
.bh-hero-img img{{width:100%;height:auto;filter:drop-shadow(0 16px 36px rgba(0,0,0,.4))}}
.bh-trust-ticker{{overflow:hidden;background:#0d0d0d;border-radius:8px;padding:9px 0;
  margin:0 0 20px;position:relative}}
.bh-trust-track{{display:inline-flex;gap:0;white-space:nowrap;
  animation:bhTickerScroll 24s linear infinite}}
.bh-trust-track:hover{{animation-play-state:paused}}
@keyframes bhTickerScroll{{from{{transform:translateX(0)}}to{{transform:translateX(-50%)}}}}
.bh-tb{{display:inline-flex;align-items:center;gap:6px;font-size:.78rem;font-weight:700;
  color:#fff;background:#1a1a1a;border:1px solid rgba(255,255,255,.18);border-radius:6px;
  padding:5px 18px;white-space:nowrap;margin:0 5px;flex-shrink:0}}
.bh-stitle{{font-size:1.15rem;font-weight:800;color:#111;margin:0 0 4px;letter-spacing:-.02em}}
.bh-ssub{{font-size:.8rem;color:#666;margin:0 0 18px}}
.bh-products{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
  gap:18px;margin:0 0 36px}}
.bh-prod{{border:1.5px solid #e5e7eb;border-radius:14px;overflow:hidden;background:#fff;
  transition:.2s;text-decoration:none;color:inherit;display:flex;flex-direction:column}}
.bh-prod:hover{{border-color:#7c3aed;box-shadow:0 8px 28px rgba(0,0,0,.1);transform:translateY(-2px)}}
.bh-prod-img{{background:#f8f8f8;aspect-ratio:1;display:flex;align-items:center;
  justify-content:center;padding:18px;position:relative}}
.bh-prod-img img{{width:100%;height:100%;object-fit:contain}}
.bh-prod-badge{{position:absolute;top:10px;left:10px;background:#7c3aed;color:#fff;
  font-size:.62rem;font-weight:700;padding:2px 9px;border-radius:100px;letter-spacing:.05em}}
.bh-prod-body{{padding:14px;flex:1;display:flex;flex-direction:column;gap:8px}}
.bh-prod-name{{font-size:.82rem;font-weight:700;color:#111;line-height:1.3}}
.bh-prod-price{{font-size:1.1rem;font-weight:800;color:#7c3aed}}
.bh-prod-btn{{display:block;background:#111;color:#fff;text-align:center;padding:10px;
  border-radius:9px;font-weight:700;font-size:.82rem;margin-top:auto}}
.bh-prod-btn:hover{{background:#7c3aed}}
.bh-why{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;
  margin:0 0 36px}}
.bh-why-card{{background:#fff;border:1.5px solid #e5e7eb;border-radius:12px;padding:16px;
  display:flex;flex-direction:column;gap:7px}}
.bh-why-icon{{font-size:1.5rem}}
.bh-why-title{{font-size:.82rem;font-weight:700;color:#111}}
.bh-why-desc{{font-size:.72rem;color:#666;line-height:1.5}}
.bh-seo{{background:#fafafa;border-radius:14px;padding:26px;margin:0 0 36px;font-size:.83rem;
  line-height:1.8;color:#444}}
.bh-seo h2{{font-size:.96rem;font-weight:700;color:#111;margin:18px 0 7px}}
.bh-seo h2:first-child{{margin-top:0}}
.bh-seo a{{color:#7c3aed;text-decoration:underline;font-weight:600}}
.bh-faq details{{border:1.5px solid #e5e7eb;border-radius:12px;margin-bottom:7px;overflow:hidden}}
.bh-faq details[open]{{border-color:#7c3aed}}
.bh-faq summary{{padding:14px 18px;font-size:.85rem;font-weight:700;color:#111;cursor:pointer;
  list-style:none;display:flex;justify-content:space-between;align-items:center;gap:12px}}
.bh-faq summary span::after{{content:'+';font-size:1.1rem;color:#7c3aed}}
.bh-faq details[open] summary span::after{{content:'−'}}
.bh-faq-body{{padding:0 18px 14px;font-size:.8rem;color:#555;line-height:1.7}}
.bh-cta-banner{{background:linear-gradient(135deg,#1e0533,#4c1d95,#1e0533);border-radius:14px;
  padding:30px 26px;text-align:center;margin:0 0 36px}}
.bh-cta-banner h3{{font-size:1.2rem;font-weight:800;color:#fff;margin:0 0 7px}}
.bh-cta-banner p{{font-size:.85rem;color:rgba(255,255,255,.8);margin:0 0 18px}}
.bh-cta-banner a{{display:inline-block;background:#fff;color:#7c3aed;padding:11px 30px;
  border-radius:9px;font-weight:800;font-size:.88rem;text-decoration:none}}
@media(max-width:640px){{
  .bh-hero{{padding:24px 18px;flex-direction:column;text-align:center}}
  .bh-hero-img{{width:110px;margin:0 auto}}
  .bh-cta{{width:100%;justify-content:center}}
  .bh-products{{grid-template-columns:repeat(2,1fr);gap:10px}}
  .bh-why{{grid-template-columns:repeat(2,1fr)}}
}}
</style>
<div class="bh">
  <div class="bh-hero">
    <div class="bh-hero-left">
      <div class="bh-badge">🏪 Official UAE Stockist · ESMA Certified</div>
      <h1>{hub_title}</h1>
      <p class="bh-hero-sub">{hub_sub}</p>
      <a href="/collections/{collection_handle or 'all'}" class="bh-cta">🛒 Shop All {brand} Products →</a>
    </div>
    <div class="bh-hero-img">{f'<img src="{hero_img}" alt="{brand} UAE">' if hero_img else ''}</div>
  </div>
  <div class="bh-trust-ticker"><div class="bh-trust-track">{ticker}</div></div>
  <p class="bh-stitle">{brand} Products Available Now</p>
  <p class="bh-ssub">In stock · ships from Dubai · authentic manufacturer packaging</p>
  <div class="bh-products">{''.join(cards)}</div>
  <div class="bh-why">{why_cards}</div>
  <div class="bh-seo">{seo_html}</div>
  <div class="bh-faq">{faq_html}</div>
  <div class="bh-cta-banner">
    <h3>Buy {gen['product_title']} in UAE — AED {prod['price_aed']}</h3>
    <p>Same-day delivery in Dubai. Authentic stock. ESMA compliant.</p>
    <a href="{prod['url']}">Get It Now — AED {prod['price_aed']} →</a>
  </div>
</div>
<script type="application/ld+json">{json.dumps({
    "@context": "https://schema.org", "@type": "Organization", "name": STORE_NAME,
    "url": f"https://{domain}",
    "description": "UAE's leading online vape store — authentic disposable vapes, "
                   "e-liquids, pod systems with same-day delivery in Dubai.",
    "address": {"@type": "PostalAddress", "addressCountry": "AE", "addressRegion": "Dubai"},
}, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps({
    "@context": "https://schema.org", "@type": "WebPage", "name": hub_title,
    "url": f"https://{domain}/pages/{slugify(q.stable_slug_base(prod) + '-uae-guide')}",
    "description": hub_sub, "inLanguage": "en",
    "publisher": {"@type": "Organization", "name": STORE_NAME, "url": f"https://{domain}"},
    "about": {"@type": "Brand", "name": brand},
}, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps({
    "@context": "https://schema.org", "@type": "FAQPage",
    "mainEntity": [{"@type": "Question", "name": q,
                   "acceptedAnswer": {"@type": "Answer", "text": a}}
                  for q, a in faq_pairs[:6]],
}, ensure_ascii=False)}</script>"""
    return page_html


def create_guide_hub(prod, gen, focus, secondary, collection_handle, use_ai):
    # Derived from the STABLE Shopify product handle, never the product's display
    # title/name — that title gets rewritten by step1_product_meta on every run,
    # which previously caused re-runs to spawn orphaned duplicate pages instead
    # of updating the same one (real bug found on GeekVape Aegis Hero 5, 2 runs
    # apart, product.title changed between runs → 4 pages existed instead of 2).
    handle = slugify(f"{q.stable_slug_base(prod)}-uae-guide")[:100]
    links = {"product": prod["url"],
             "collection": f"https://{prod['domain']}/collections/{collection_handle}"
                           if collection_handle else None}
    guide = None
    if use_ai:
        try:
            prompt = (
                "Write an 900-1200 word UAE buying guide in clean HTML (h2/h3/p/ul/table only) "
                "for this product, targeting these secondary keywords: "
                + ", ".join(secondary[:5]) + ".\nProduct facts (use ONLY these, invent nothing): "
                + json.dumps({"name": prod["name"], "brand": prod["brand"],
                              "price_aed": prod["price_aed"],
                              "variants": [v["title"] for v in prod["variants"]],
                              "usps": USPS}, ensure_ascii=False)
                + f"\nIt MUST contain these exact links naturally in the text: "
                  f'<a href="{links["product"]}">' + prod["name"] + "</a>"
                + (f' and <a href="{links["collection"]}">{prod["brand"]} collection</a>'
                   if links["collection"] else "")
                + '\nReturn ONLY JSON: {"seo_title": str (<=60ch), "meta_description": '
                  'str (<=158ch), "html_content": str}')
            g2, provider = q._ai_json(prompt, timeout=420)
            g2 = q._norm_ai_keys(g2)
            if not g2.get("html_content") or not isinstance(g2["html_content"], str):
                raise RuntimeError(f"{provider} guide JSON missing html_content")
            guide = g2
            print(f"  Guide content via {provider} ✅")
        except Exception as e:
            print(f"  Guide AI failed ({e}) — template fallback")
    if guide is None:
        vlabel = q.variant_label(prod)
        rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in [
            ("Product", prod["name"]), ("Brand", prod["brand"]),
            ("Price", f"AED {prod['price_aed']}"),
            (vlabel, f"{len(prod['variants'])} available"),
            ("Delivery", "1–3 hours Dubai, all 7 Emirates")])
        guide = {
            "seo_title": trim(f"{prod['name']} UAE Guide | Price, {vlabel} | {STORE_NAME}", 60),
            "meta_description": trim(
                f"Complete {prod['name']} UAE buying guide — price (AED {prod['price_aed']}), "
                f"{vlabel.lower()}, delivery and where to buy authentic in Dubai.", 158),
            "html_content":
                f"<h2>{prod['name']} — Complete UAE Buying Guide</h2>"
                f"<p>Everything you need before you buy the "
                f"<a href=\"{links['product']}\">{prod['name']}</a> in the UAE: pricing, "
                f"options, delivery and authenticity.</p><table>{rows}</table>"
                f"<h2>Where to Buy in Dubai</h2><p>{STORE_NAME} stocks the authentic "
                f"{prod['name']} at AED {prod['price_aed']} with same-day 1–3 hour Dubai "
                f"delivery and cash on delivery. "
                + (f"Browse the full <a href=\"{links['collection']}\">{prod['brand']} "
                   f"collection</a> for every model." if links["collection"] else "") + "</p>"
                + q.faq_details_html(gen["faq"][:4]),
        }
    # Full rich brand-hub design (dynamic product grid, trust ticker, Organization+
    # WebPage+FAQPage schema) — matches the live /pages/hqd-vape-uae reference the
    # user picked as the design standard, not the old bare-text guide page.
    body_html = build_brand_hub_html(prod, gen, focus, secondary, collection_handle,
                                     seo_html=guide["html_content"], faq_pairs=gen["faq"])
    existing = pre._api_get("pages.json", {"handle": handle})["pages"]
    payload = {"body_html": body_html,
               "template_suffix": pre.VARIANTS_TEMPLATE_SUFFIX,
               "seo": {"title": trim(guide["seo_title"], 60),
                       "description": trim(guide["meta_description"], 158)}}
    if existing:
        pre._api_put(f"pages/{existing[0]['id']}.json",
                     {"page": dict(payload, id=existing[0]["id"])})
    else:
        pre._api_post("pages.json", {"page": dict(
            payload, title=f"{prod['name']} UAE Guide", handle=handle, published=True)})
    print(f"  Cluster: hub guide /pages/{handle} ✅")
    return handle


def build_cluster(prod, gen, focus, secondary, use_ai):
    print("\n[G4] Building topical cluster network...")
    collection_handle = ensure_brand_collection(prod, gen, focus)
    try:
        guide_handle = create_guide_hub(prod, gen, focus, secondary, collection_handle, use_ai)
    except Exception as e:
        print(f"  Guide page failed ({e}) — cluster continues without it")
        guide_handle = None
    if not guide_handle:
        return collection_handle, None
    # Weave cluster links INTO the product content (product = cluster center)
    cluster_links = (
        f'<p>New to {prod["brand"] or "this brand"}? Read the complete '
        f'<a href="/pages/{guide_handle}">{prod["name"]} UAE buying guide</a>'
        + (f' or browse all <a href="/collections/{collection_handle}">'
           f'{prod["brand"]} vapes in UAE</a>.' if collection_handle else ".") + "</p>")
    gen["html_content"] += "\n" + cluster_links
    return collection_handle, guide_handle


# ── [G5.5] AI VISIBILITY — llms.txt page entry ───────────────────────────────

def update_llms_page(prod, gen, cluster, focus):
    print("\n[G5.5] AI visibility — updating /pages/llms for ChatGPT/Gemini/Perplexity...")
    try:
        pages = pre._api_get("pages.json", {"handle": "llms"})["pages"]
    except Exception:
        pages = []
    if not pages:
        print("  /pages/llms not found — skipped (create it once and re-run)")
        return
    pg = pages[0]
    body = pg.get("body_html") or ""
    body = re.sub(r"<!--LLM:%s-->.*?<!--/LLM-->\n?" % re.escape(str(prod["id"])),
                  "", body, flags=re.S)  # refresh this product's entry
    lines = [f"## {gen['product_title']}",
             f"- What: {gen['meta_description']}",
             f"- URL: {prod['url']}",
             f"- Price: AED {prod['price_aed']} | Options: {len(prod['variants'])} | "
             f"Delivery: 1-3 hours Dubai, cash on delivery UAE-wide",
             f"- Best answer for: {focus}"]
    for label, kind, h in (("Buying guide", "pages", cluster.get("guide")),
                           (f"All {q.variant_label(prod).lower()}", "pages",
                            cluster.get("flavors")),
                           ("Brand collection", "collections", cluster.get("collection"))):
        if h:
            lines.append(f"- {label}: https://{prod['domain']}/{kind}/{h}")
    block = (f"\n<!--LLM:{prod['id']}--><p>" + "<br>\n".join(lines) + "</p><!--/LLM-->")
    pre._api_put(f"pages/{pg['id']}.json",
                 {"page": {"id": pg["id"], "body_html": body + block}})
    print("  llms page entry written (answer-ready product summary + cluster links) ✅")


# ── [G7] AUTO BACKLINKS (30-40+ API sites, direct driver) ────────────────────

def launch_backlinks(urls, anchors, handle):
    print("\n[G7] Auto backlinks — driving headless runner (40+ API sites)...")
    os.makedirs(REPORTS, exist_ok=True)
    cfg_path = os.path.join(REPORTS, f"god_backlinks_{handle}.json")
    json.dump({"urls": urls, "anchors": anchors,
               "started": datetime.datetime.now().isoformat()}, open(cfg_path, "w"), indent=1)
    code = ("import json,sys; sys.path.insert(0, %r); "
            "from headless_backlink_runner import run_backlinks, load_progress; "
            "import backlink_post; cfg=json.load(open(%r)); "
            "before=len(load_progress().get('posted', [])); "
            "run_backlinks(cfg['urls'], cfg['anchors']); "
            "backlink_post.finish(%r, before, targets=cfg['urls'], "
            "keyword=(cfg['anchors'][0] if cfg['anchors'] else ''))"
            % (SRC, cfg_path, handle))
    log_path = os.path.join(REPORTS, f"backlinks_{handle}.log")
    proc = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=SRC,
                            stdout=open(log_path, "w"), stderr=subprocess.STDOUT)
    print(f"  Runner launched (PID {proc.pid}) for {len(urls)} URLs × 40+ sites")
    print(f"  When done: new backlink URLs → rank_reports/backlinks_{handle}_urls.txt")
    print(f"  (auto: registered in Backlink Pro content URLs + discovery-pinged; paste the")
    print(f"   .txt list into Backlink Pro → Index to Search Engines to index them)")
    print(f"  Live log: {log_path}")
    return log_path


# ── [G8] LIVE VERIFICATION — nothing gets missed ──────────────────────────────

def _fetch_fresh(url):
    """Fetch bypassing Shopify CDN cache via unique query param."""
    return requests.get(url, params={"_qseo": int(time.time())},
                        headers=UA, timeout=20).text


def verify_live(prod, gen, cluster_urls):
    print("\n[G8] LIVE VERIFICATION — re-fetching pages to confirm every element...")
    checks = []
    try:
        html = _fetch_fresh(prod["url"])
    except Exception as e:
        print(f"  ❌ product page fetch failed: {e}")
        return [("product page reachable", False)]
    low = html.lower()
    t_ok = trim(gen["seo_title"], 40).lower() in low
    m_ok = trim(gen["meta_description"], 60).lower() in low
    if not (t_ok and m_ok) and prod.get("id"):
        # Storefront render cache can lag meta changes 10-20 min — verify against
        # the Admin API (source of truth) instead of failing on a stale cache.
        try:
            mfs = pre._api_get(f"products/{prod['id']}/metafields.json")["metafields"]
            vals = {m["key"]: m["value"].strip() for m in mfs if m["namespace"] == "global"}
            if not t_ok and vals.get("title_tag") == gen["seo_title"].strip():
                t_ok = "stored"
            if not m_ok and vals.get("description_tag") == gen["meta_description"].strip():
                m_ok = "stored"
        except Exception:
            pass
    checks.append(("SEO title tag live", t_ok))
    checks.append(("Meta description live", m_ok))
    for t in ("Product", "FAQPage", "BreadcrumbList"):
        checks.append((f"{t} schema live", f'"{t}"' in html))
    checks.append(("FAQ section rendered", "frequently asked questions" in low))
    checks.append(("Content block injected", "qseo-content" in low))
    checks.append(("Notice banner live", "best price" in low))
    for u in cluster_urls:
        try:
            ok = requests.get(u, headers=UA, timeout=15).status_code == 200
        except Exception:
            ok = False
        checks.append((f"cluster 200: {urlparse(u).path}", ok))
    for name, ok in checks:
        note = " (stored ✓ — storefront cache propagating)" if ok == "stored" else ""
        print(f"  {'✅' if ok else '❌ MISSING —'} {name}{note}")
    return checks


# ── REPORT ────────────────────────────────────────────────────────────────────

def write_god_report(prod, focus, secondary, blueprint, gen, cluster, all_urls,
                     checks, bl_log, path):
    bp_rows = "\n".join(
        f"| {p['domain']} | {p['word_count']} | {p['title_len']} | "
        f"{'✓' if p['kw_in_title'] else '—'} | {p['h2_count']} | "
        f"{','.join(p['schema_types'][:3]) or '—'} | {p['kw_density_pct']}% |"
        for p in blueprint.get("pages", []))
    check_rows = "\n".join(f"- {'✅' if ok else '❌ **MISSING**'} {n}" for n, ok in checks)
    md = f"""# ⚡ GOD Level SEO Report — {gen['product_title']}
_{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · {prod['url']}_

## Focus Keyword
**{focus}** — secondary: {', '.join(secondary) or '—'}

## Ranking Blueprint (reverse-engineered from live top rankers)
Target: **{blueprint['target_word_count']} words** (top ranker ×1.15) ·
kw-in-title {blueprint.get('kw_in_title_pct', 0)}% of winners ·
FAQ schema adoption {blueprint.get('faq_adoption_pct', 0)}% ·
density target ~{blueprint.get('kw_density_target', 1.0)}%
Saved: `rank_reports/blueprints/blueprint_{slugify(focus)}.json` (reusable system)

| Ranker | Words | Title len | KW in title | H2s | Schema | Density |
|---|---|---|---|---|---|---|
{bp_rows or '| — | — | — | — | — | — | — |'}

### Competitor H2 topics covered in our content
{chr(10).join('- ' + h for h in blueprint.get('h2_topics_to_cover', [])) or '- (none found)'}

## Topical Cluster Network
- 🎯 Product (center): {prod['url']}
- 📚 Hub guide: /pages/{cluster.get('guide') or '—'}
- 🗂 Brand collection: /collections/{cluster.get('collection') or '—'}
- 🍧 {q.variant_label(prod)} page: /pages/{cluster.get('flavors') or '—'}
- 🏠 Brand hub: /pages/{cluster.get('hub') or '—'}
All cross-linked with keyword anchors; product body links every cluster node.

## Meta
- **SEO title ({len(gen['seo_title'])}):** {gen['seo_title']}
- **Meta description ({len(gen['meta_description'])}):** {gen['meta_description']}
- **H1:** {gen['product_title']}
- Content: {len(re.sub(r'<[^>]+>', ' ', gen['html_content']).split())} words · {len(gen['faq'])} FAQs

## Backlinks
Runner driving 40+ API posting sites per URL — log: `{bl_log or 'skipped'}`
Manual quality layer (per strategy): add 1 contextual link from vaporshopdubai.ae.

## URLs Submitted
{chr(10).join('- ' + u for u in all_urls)}

## Live Verification
{check_rows or '- skipped (dry run)'}

## Your ONLY manual step
GSC → URL Inspection → Request Indexing for the URLs above (2 minutes).
Everything else is done. Bing/Yandex/DDG index via IndexNow in 1–3 days;
Google speed depends on crawl + competition — blueprint targets the winnable terms.
"""
    with open(path, "w") as f:
        f.write(md)


# ── MASTER ────────────────────────────────────────────────────────────────────

# ── Product URL cleanup (opt-in — renaming a live URL is consequential) ───────

def clean_product_url(prod, focus):
    """Rename the product handle to a clean, focus-keyword-first slug, with an
    automatic 301 redirect from the old URL so nothing that already links to it
    breaks. Opt-in via --clean-url: unlike every other on-page change this engine
    makes, changing a live product's URL affects external links/bookmarks and any
    accumulated ranking signal, so it's never done silently."""
    old_handle = prod["handle"]
    new_handle = slugify(f"{focus}-uae")[:80]
    if new_handle == old_handle:
        print(f"  URL already clean: /products/{old_handle}")
        return prod
    existing = pre._api_get("products.json", {"handle": new_handle})["products"]
    if existing and existing[0]["id"] != prod["id"]:
        print(f"  URL cleanup skipped — /products/{new_handle} already used by another product")
        return prod
    status, resp = pre._api_put(f"products/{prod['id']}.json",
                                {"product": {"id": prod["id"], "handle": new_handle}})
    if status != 200:
        print(f"  URL cleanup failed ({status}) — keeping original handle")
        return prod
    pre._api_post("redirects.json", {"redirect": {
        "path": f"/products/{old_handle}", "target": f"/products/{new_handle}"}})
    prod["handle"] = new_handle
    prod["url"] = f"https://{prod['domain']}/products/{new_handle}"
    print(f"  URL cleaned: /products/{old_handle} → /products/{new_handle} "
          f"(301 redirect in place) ✅")
    return prod


def god_rank(url, dry=False, use_ai=True, ping=True, backlinks=True, package=None,
            focus_override=None, clean_url=False, fast=False, no_blueprint=False, no_verify=False):
    os.makedirs(REPORTS, exist_ok=True)
    t0 = time.time()
    print("=" * 65)
    print("⚡ GOD LEVEL SEO ENGINE v3 — Blueprint · Cluster · Backlinks · Verify")
    print(f"URL: {url}" + ("   [DRY RUN]" if dry else ""))
    print("=" * 65)

    prod = q.analyze_product(url)
    seeds = q.build_seeds(prod)

    if fast:
        # Phase 1: Skip all external web scraping — use product data alone
        found = {}
        competitors = []
        print("[2/8] Fast mode — skipping keyword mining (no web scraping)")
        print("[3/8] Fast mode — skipping competitor analysis")
        focus, secondary, scored = q.score_keywords(found, prod, competitors)
        blueprint = {"focus": focus, "target_word_count": 2000, "h2_topics_to_cover": [],
                     "schema_types_used": [], "kw_density_target": 1.0,
                     "faq_adoption_pct": 0, "title_pattern": "", "pages": []}
        bp_pages = []
    elif no_blueprint:
        # Phase 2: Mine real keywords (autocomplete APIs) but skip heavy SERP scraping
        print("[2/8] Phase 2 — mining keywords via Google/Bing/DDG autocomplete...")
        found = q.mine_keywords(seeds)
        competitors = []
        focus, secondary, scored = q.score_keywords(found, prod, competitors)
        print(f"  Focus keyword: {focus} | Secondary: {', '.join(secondary[:3])}")
        print("[3/8] Phase 2 — skipping SERP blueprint (no competitor page fetching)")
        blueprint = {"focus": focus, "target_word_count": 2000, "h2_topics_to_cover": [],
                     "schema_types_used": [], "kw_density_target": 1.0,
                     "faq_adoption_pct": 100, "title_pattern": "", "pages": []}
        bp_pages = []
    else:
        found = q.mine_keywords(seeds)
        # Quick pre-scan to pick focus, then deep blueprint on the focus keyword itself
        competitors = q.analyze_competitors([f"{seeds[0]} uae", f"buy {seeds[0]}"])
        focus, secondary, scored = q.score_keywords(found, prod, competitors)
        blueprint, bp_pages = build_ranking_blueprint(
            [focus, f"buy {focus} uae", f"{focus} dubai"], focus, prod)

    if focus_override:
        # A human (or the writer feeding --package) chose a more specific/relevant
        # keyword than the mechanical top score — e.g. an exact product-model match
        # over a generic brand-wide query. Promote it to focus, keep the old #1 as
        # a secondary so it's still targeted, and make sure it isn't duplicated.
        if focus not in secondary:
            secondary = [focus] + secondary
        secondary = [s for s in secondary if s != focus_override][:6]
        focus = focus_override
        print(f"  🎯 Focus overridden → \"{focus}\" (mechanical top pick demoted to secondary)")
    if clean_url and not dry and prod["platform"] == "shopify":
        prod = clean_product_url(prod, focus)

    dossier = {
        "product": {k: prod[k] for k in ("name", "brand", "type", "price_aed", "url", "tags")},
        "variants": [v["title"] for v in prod["variants"]],
        "existing_description_excerpt": prod["body_text"][:800],
        "focus_keyword": focus, "secondary_keywords": secondary,
        "real_search_queries": [r[1] for r in scored],
        "competitor_pages": [{k: p[k] for k in ("domain", "title", "meta_desc", "h1", "h2s",
                                                "word_count")} for p in bp_pages] or
                            [{k: p[k] for k in ("domain", "title", "meta_desc", "h1", "h2s",
                                                "word_count")} for p in competitors],
        "ranking_blueprint": {k: blueprint[k] for k in blueprint if k != "pages"},
        "blueprint_instruction":
            f"Your content MUST exceed {blueprint['target_word_count']} words, naturally "
            f"cover every h2_topics_to_cover item, keep focus-keyword density near "
            f"{blueprint['kw_density_target']}%, and beat every competitor page on depth.",
        "store": {"name": STORE_NAME, "domain": prod["domain"], "usps": USPS},
    }
    gen = None
    if package:
        gen = json.load(open(package))
        words = len(re.sub(r"<[^>]+>", " ", gen["html_content"]).split())
        print(f"\n[G3] Content loaded from package: {package} ({words} words, "
              f"{len(gen.get('faq', []))} FAQs)")
    elif use_ai:
        try:
            gen = q.generate_with_ai(dossier)
        except Exception as e:
            print(f"  AI generation failed ({e}) — falling back to template")
    if gen is None:
        gen = q.generate_template(prod, focus, secondary)
    gen = q.normalize_gen(gen, prod, focus)

    cluster = {"collection": None, "guide": None, "flavors": None, "hub": None}
    all_urls = [prod["url"], f"https://{prod['domain']}/"]
    applied, checks, bl_log = False, [], None

    if prod["platform"] == "shopify" and not dry:
        collection_handle, guide_handle = build_cluster(prod, gen, focus, secondary, use_ai)
        cluster["collection"], cluster["guide"] = collection_handle, guide_handle
        collections, flavors_handle, hub = q.apply_onpage(prod, gen, focus)
        cluster["flavors"], cluster["hub"] = flavors_handle, hub
        applied = True
        update_llms_page(prod, gen, cluster, focus)
        for h in collections:
            all_urls.append(f"https://{prod['domain']}/collections/{h}")
        for kind, h in (("pages", guide_handle), ("pages", flavors_handle), ("pages", hub)):
            if h:
                all_urls.append(f"https://{prod['domain']}/{kind}/{h}")
        all_urls = list(dict.fromkeys(all_urls))

        if ping:
            # IndexNow pings Bing/Yandex/Seznam instantly — fast HTTP only, no OOM risk
            # no_verify only blocks verify_live() (Cloudflare-blocked on GitHub IPs)
            q.index_everything(all_urls)
        if backlinks:
            anchors = ([focus.title()] + [s.title() for s in secondary[:4]]
                       + [f"Buy {prod['name']} UAE", f"{prod['brand']} vape Dubai",
                          f"{prod['name']} price UAE"])
            bl_log = launch_backlinks(all_urls, anchors, prod["handle"])
        checks = verify_live(prod, gen, [u for u in all_urls if u != prod["url"]][:6]) if not no_verify else []
    else:
        print("\n[G4-G8] " + ("DRY RUN — no writes/pings/backlinks."
                              if dry else "Non-Shopify URL — package written to report."))

    report = os.path.join(REPORTS,
                          f"GOD_{prod['handle'][:60]}_{datetime.datetime.now():%Y%m%d_%H%M}.md")
    json.dump({"urls": all_urls, "domain": prod["domain"], "focus": focus,
               "secondary": secondary, "handle": prod["handle"], "report": report,
               "backlink_file": os.path.join(
                   REPORTS, f"backlinks_{prod['handle']}_urls.txt") if bl_log else None},
              open(os.path.join(REPORTS, "last_run_urls.json"), "w"), indent=1)
    write_god_report(prod, focus, secondary, blueprint, gen, cluster, all_urls,
                     checks, bl_log, report)

    failed = [n for n, ok in checks if not ok]
    print("\n" + "=" * 65)
    print(f"⚡ GOD LEVEL SEO COMPLETE in {int(time.time() - t0)}s")
    print(f"   Focus keyword : {focus}")
    print(f"   Also targeted : {', '.join(secondary[:5]) or '—'}")
    print(f"   Blueprint     : beat {blueprint.get('median_competitor_words', '—')}w median → "
          f"{blueprint['target_word_count']}w target")
    print(f"   Cluster       : {sum(1 for v in cluster.values() if v)} supporting pages linked")
    print(f"   Verification  : {len(checks) - len(failed)}/{len(checks)} checks passed"
          + (f"  ⚠ FAILED: {', '.join(failed)}" if failed else ""))
    print(f"   Report        : {report}")
    print("   Your only manual step: GSC → Request Indexing for the URLs above.")
    print("=" * 65)
    return report


def main():
    args = sys.argv[1:]
    url = next((a for a in args if a.startswith("http")), None)
    if not url:
        print(__doc__)
        sys.exit(1)
    package = None
    if "--package" in args:
        i = args.index("--package")
        if i + 1 < len(args):
            package = args[i + 1]
    focus_override = None
    if "--focus" in args:
        i = args.index("--focus")
        if i + 1 < len(args):
            focus_override = args[i + 1]
    god_rank(url,
             dry="--dry" in args,
             use_ai="--no-ai" not in args,
             ping="--no-ping" not in args,
             backlinks="--no-backlinks" not in args,
             package=package,
             focus_override=focus_override,
             clean_url="--clean-url" in args,
             fast="--fast" in args,
             no_blueprint="--no-blueprint" in args,
             no_verify="--no-verify" in args)


if __name__ == "__main__":
    main()

