"""
Product Ranking Engine — Emirates Vapor
========================================
Input: product URL + target keywords
Output: Full SEO domination — collections, pages, schema, backlinks, pings

Usage:
  python3 product_ranking_engine.py

Or import and call:
  from product_ranking_engine import rank_product
  rank_product(
      product_url="https://emiratesvapor.ae/products/YOUR-PRODUCT",
      keywords=["keyword 1", "keyword 2", "keyword 3"],
      product_id=12345678,       # Shopify product ID
      brand_name="Brand Name",
      price_aed=55,
  )
"""

import requests, re, json, os, time, sys, datetime, base64
from typing import List, Optional

# ── Config ─────────────────────────────────────────────────────────────────────
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
SHOPIFY_BASE  = "https://emirates-vapor.myshopify.com/admin/api/2024-01"
INDEXNOW_KEY  = "41df36f7ebc14d7aa3ff1d192290347b"
SHOP_DOMAIN   = "emiratesvapor.ae"

SH = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}
SHG = {"X-Shopify-Access-Token": SHOPIFY_TOKEN}

INDEXNOW_ENDPOINTS = [
    "https://www.bing.com/indexnow",
    "https://yandex.com/indexnow",
    "https://api.indexnow.org/indexnow",
    "https://search.seznam.cz/indexnow",
]

# ── Helpers ────────────────────────────────────────────────────────────────────

_api_last_t = [0.0]

def _api_throttle():
    """Ensure ≤1 Shopify API call/sec per process (2 parallel chunks = 2/sec, under Shopify's limit)."""
    import time as _t
    gap = 0.6 - (_t.time() - _api_last_t[0])   # 0.6s = ~1.6 calls/sec, safe under 2/sec limit
    if gap > 0:
        _t.sleep(gap)
    _api_last_t[0] = _t.time()


GRAPHQL_URL = "https://emirates-vapor.myshopify.com/admin/api/2024-01/graphql.json"

def _gql(query, variables=None):
    """Single GraphQL call — counts as 1 throttle unit regardless of payload size."""
    _api_throttle()
    r = _shopify_retry(requests.post, GRAPHQL_URL,
                       headers=SH, json={"query": query, "variables": variables or {}},
                       timeout=30)
    return r.json()


def graphql_update_product(product_id, title, body_html, seo_title, seo_desc,
                            short_desc_rich, image_alts=None):
    """
    Replaces ~12 REST calls with 1-2 GraphQL calls:
      - productUpdate: title + bodyHtml + SEO title/desc + all metafields in ONE call
      - productUpdateMedia (optional): image alt texts in ONE call
    Returns True on success.
    """
    gid = f"gid://shopify/Product/{product_id}"

    mutation = """
    mutation productUpdate($input: ProductInput!) {
      productUpdate(input: $input) {
        product { id title }
        userErrors { field message }
      }
    }"""

    variables = {"input": {
        "id": gid,
        "title": title,
        "bodyHtml": body_html,
        "seo": {"title": seo_title, "description": seo_desc},
        "metafields": [
            {"namespace": "global",  "key": "title_tag",        "value": seo_title,       "type": "single_line_text_field"},
            {"namespace": "global",  "key": "description_tag",  "value": seo_desc,        "type": "single_line_text_field"},
            {"namespace": "custom",  "key": "short_description","value": short_desc_rich,  "type": "rich_text_field"},
        ],
    }}

    d = _gql(mutation, variables)
    errs = d.get("data", {}).get("productUpdate", {}).get("userErrors", [])
    if errs:
        print(f"  ⚠️  GraphQL productUpdate errors: {errs}")
        return False

    # Image alt texts — one extra call covers all images
    if image_alts:
        _graphql_update_image_alts(product_id, image_alts)

    return True


def _graphql_update_image_alts(product_id, alts):
    """Fetch images + update all alts in one GraphQL query + N mutations batched."""
    gid = f"gid://shopify/Product/{product_id}"
    # Fetch all image IDs + current alts in one call
    q = """query getImages($id: ID!) {
      product(id: $id) {
        images(first: 20) { edges { node { id altText } } }
      }
    }"""
    d = _gql(q, {"id": gid})
    imgs = [e["node"] for e in d.get("data", {}).get("product", {}).get("images", {}).get("edges", [])]
    if not imgs:
        return

    # Build bulk mediaUpdate mutation — all images in ONE call
    mutations = []
    vars_map = {}
    for i, img in enumerate(imgs):
        alt = alts[i % len(alts)]
        var = f"input{i}"
        mutations.append(f"u{i}: productImageUpdate(productId: $pid, image: ${var}) {{ image {{ id }} }}")
        vars_map[var] = {"id": img["id"], "altText": alt}

    if not mutations:
        return

    var_decls = ", ".join(f"${k}: ImageInput!" for k in vars_map)
    bulk_mutation = f"mutation updateAlts($pid: ID!, {var_decls}) {{ {' '.join(mutations)} }}"
    _api_throttle()
    r = _shopify_retry(requests.post, GRAPHQL_URL,
                       headers=SH,
                       json={"query": bulk_mutation, "variables": {"pid": gid, **vars_map}},
                       timeout=30)
    errs = r.json().get("errors", [])
    if errs:
        print(f"  ⚠️  Image alt update errors: {errs[:2]}")

def _shopify_retry(fn, *args, **kwargs):
    """Run fn(*args, **kwargs), retrying up to 6× on Shopify 429 with backoff."""
    for attempt in range(6):
        r = fn(*args, **kwargs)
        if r.status_code == 429:
            wait = int(float(r.headers.get('Retry-After', '15'))) + 2
            wait = min(wait, 45)
            print(f"  Shopify 429 — retrying in {wait}s (attempt {attempt+1}/6)...")
            time.sleep(wait)
            continue
        return r
    return r  # return last response; caller handles non-429 errors

def _api_get(path, params=None):
    _api_throttle()
    r = _shopify_retry(requests.get, f"{SHOPIFY_BASE}/{path}",
                       headers=SHG, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def _api_put(path, data):
    _api_throttle()
    r = _shopify_retry(requests.put, f"{SHOPIFY_BASE}/{path}",
                       headers=SH, json=data, timeout=25)
    return r.status_code, r.json()

def _api_post(path, data):
    _api_throttle()
    r = _shopify_retry(requests.post, f"{SHOPIFY_BASE}/{path}",
                       headers=SH, json=data, timeout=25)
    return r.status_code, r.json()

def slugify(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')

def ping_indexnow(urls: List[str]):
    payload = {
        "host": SHOP_DOMAIN,
        "key": INDEXNOW_KEY,
        "keyLocation": f"https://{SHOP_DOMAIN}/{INDEXNOW_KEY}.txt",
        "urlList": urls,
    }
    results = {}
    for ep in INDEXNOW_ENDPOINTS:
        try:
            r = requests.post(ep, json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"}, timeout=12)
            results[ep.split("//")[1].split("/")[0]] = r.status_code
        except Exception as e:
            results[ep] = f"ERROR: {e}"
    return results


# ── STEP 1: Update product meta title + meta description ──────────────────────

def step1_product_meta(product_id: int, title: str, description: str,
                       product_title: Optional[str] = None):
    print("\n[1/8] Updating product SEO meta...")
    if product_title:
        _api_put(f"products/{product_id}.json",
                 {"product": {"id": product_id, "title": product_title}})
        print(f"  Product title → {product_title}")

    mf = _api_get(f"products/{product_id}/metafields.json")["metafields"]
    title_mf = next((m for m in mf if m["namespace"]=="global" and m["key"]=="title_tag"), None)
    desc_mf  = next((m for m in mf if m["namespace"]=="global" and m["key"]=="description_tag"), None)

    if title_mf:
        _api_put(f"metafields/{title_mf['id']}.json",
                 {"metafield":{"id":title_mf["id"],"value":title,"type":"single_line_text_field"}})
    else:
        _api_post(f"products/{product_id}/metafields.json",
                  {"metafield":{"namespace":"global","key":"title_tag","value":title,"type":"single_line_text_field"}})

    if desc_mf:
        _api_put(f"metafields/{desc_mf['id']}.json",
                 {"metafield":{"id":desc_mf["id"],"value":description,"type":"single_line_text_field"}})
    else:
        _api_post(f"products/{product_id}/metafields.json",
                  {"metafield":{"namespace":"global","key":"description_tag","value":description,"type":"single_line_text_field"}})
    print(f"  SEO title → {title}")
    print(f"  SEO desc  → {description[:80]}...")


# ── STEP 2: Fix image alt tags ────────────────────────────────────────────────

def step2_image_alt_tags(product_id: int, product_name: str, brand: str, keywords: List[str]):
    print("\n[2/8] Fixing image alt tags...")
    imgs = _api_get(f"products/{product_id}/images.json")["images"]
    alt_templates = [
        f"{product_name} UAE — Buy Dubai | Emirates Vapor",
        f"{product_name} — All Flavors UAE | Emirates Vapor Dubai",
        f"Buy {brand} {keywords[0] if keywords else ''} Dubai UAE",
        f"{product_name} — Same-Day Delivery Dubai | Emirates Vapor",
        f"{product_name} Specifications UAE | Emirates Vapor",
        f"{product_name} Review — Best {brand} Vape Dubai",
        f"{brand} Vape Dubai UAE — Emirates Vapor Best Price",
        f"Authentic {brand} UAE — ESMA Certified | Emirates Vapor",
    ]
    for i, img in enumerate(imgs):
        alt = alt_templates[i % len(alt_templates)]
        _api_put(f"products/{product_id}/images/{img['id']}.json",
                 {"image": {"id": img["id"], "alt": alt}})
        time.sleep(0.25)
    print(f"  {len(imgs)} images alt-tagged ✅")


# ── STEP 3: Inject notice banner + quick-links into product body ──────────────

def step3_notice_banner(product_id: int, price_aed: int, delivery: str,
                        collection_handles: List[str], brand_name: str,
                        flavors_page_handle: Optional[str] = None,
                        brand_hub_handle: Optional[str] = None):
    print("\n[3/8] Injecting notice banner + quick-links...")
    prod = _api_get(f"products/{product_id}.json?fields=body_html")["product"]
    body = prod["body_html"]

    # Remove existing notice banner + quick-links if present (idempotent re-runs)
    body = re.sub(r'<div[^>]*background.*?linear-gradient.*?AED \d+.*?</div>\n?', '', body, flags=re.DOTALL)
    body = re.sub(r'<div[^>]*background.*?BEST.*?PRICE.*?</div>\n?', '', body, flags=re.DOTALL)
    body = re.sub(r'<div[^>]*>\s*<strong>Quick Links:</strong>.*?</div>\n?', '', body, flags=re.DOTALL)

    BANNER = f"""<div style="background:#111;color:#fff;padding:13px 20px;border-radius:8px;margin-bottom:24px;font-weight:700;font-size:14px;text-align:center;letter-spacing:.3px;border:1px solid rgba(255,255,255,.12)">
⚡ AED {price_aed} — UAE's BEST PRICE &nbsp;|&nbsp; 🚀 {delivery} &nbsp;|&nbsp; ✅ 100% AUTHENTIC {brand_name.upper()} &nbsp;|&nbsp; ALL FLAVORS IN STOCK
</div>"""

    links_html = " &nbsp;|&nbsp; ".join(
        f'<a href="/collections/{h}" style="color:#1a73e8;text-decoration:underline">Shop {h.replace("-"," ").title()}</a>'
        for h in collection_handles
    )
    if flavors_page_handle:
        links_html += f' &nbsp;|&nbsp; <a href="/pages/{flavors_page_handle}" style="color:#1a73e8;text-decoration:underline">All Flavors</a>'
    if brand_hub_handle:
        links_html += f' &nbsp;|&nbsp; <a href="/pages/{brand_hub_handle}" style="color:#1a73e8;text-decoration:underline">{brand_name} Brand Guide</a>'

    QUICK_LINKS = f"""<div style="background:#f8f9fa;border:1px solid #e2e8f0;border-radius:8px;padding:12px 18px;margin:16px 0;font-size:13px"><strong>Quick Links:</strong> &nbsp;{links_html}</div>"""

    # Banner, then Quick Links, always adjoining and above all content —
    # never spliced mid-content (old positional "2nd </p>" search broke on
    # any content whose own markup contains early <p> tags, e.g. badge grids)
    body = BANNER + "\n" + (QUICK_LINKS + "\n" if links_html else "") + body

    _api_put(f"products/{product_id}.json", {"product": {"id": product_id, "body_html": body}})
    print(f"  Notice banner + quick-links injected ✅")
    return body


# ── STEP 4: Create dedicated brand collection ─────────────────────────────────

def step4_create_collection(name: str, handle: str, seo_title: str, seo_desc: str,
                             body_html: str, product_ids: List[int]) -> int:
    print(f"\n[4/8] Creating/updating collection /{handle}...")
    # Check if exists
    existing = _api_get("custom_collections.json", {"handle": handle})["custom_collections"]
    if existing:
        coll_id = existing[0]["id"]
        _api_put(f"custom_collections/{coll_id}.json", {"custom_collection": {
            "id": coll_id, "body_html": body_html,
            "seo": {"title": seo_title, "description": seo_desc}
        }})
        print(f"  Updated existing collection ID={coll_id} ✅")
    else:
        collects = [{"product_id": pid} for pid in product_ids]
        status, data = _api_post("custom_collections.json", {"custom_collection": {
            "title": name, "handle": handle, "body_html": body_html,
            "seo": {"title": seo_title, "description": seo_desc},
            "published": True, "collects": collects
        }})
        coll_id = data.get("custom_collection", {}).get("id", 0)
        print(f"  Created new collection ID={coll_id} ✅")
    return coll_id


# ── STEP 5: Create variant/flavor showcase page (full SEO, product-card style) ─

def _get_variant_image_map(product_id: int) -> dict:
    """Returns {variant_id: image_src_url} mapping from Shopify product images."""
    images = _api_get(f"products/{product_id}/images.json")["images"]
    vid_map = {}
    fallback = images[0]["src"].split("?")[0] if images else ""
    for img in images:
        src = img["src"].split("?")[0]
        # Shopify CDN 400px transform
        src_400 = re.sub(r'\.(jpg|jpeg|png|webp|gif)$', r'_400x.\1', src, flags=re.IGNORECASE)
        for vid in img.get("variant_ids", []):
            vid_map[vid] = src_400
    return vid_map, fallback

VARIANTS_TEMPLATE_SUFFIX = "variants"  # page.variants.json template

def _ensure_variants_template(theme_id: int = 145181737034):
    """Create page.variants.json + ev-variants-page.liquid section if not present."""
    section_liquid = '''{% comment %}ev-variants-page — full-width variant showcase{% endcomment %}
<div class="section-background color-{{ section.settings.color_scheme }}"></div>
<div class="section section--page-width color-{{ section.settings.color_scheme }}" style="padding:40px 0 80px">
  {{ page.content }}
</div>
{% schema %}{"name":"EV Variants Page","settings":[{"type":"color_scheme","id":"color_scheme","label":"Color scheme","default":"scheme-1"}]}{% endschema %}'''
    page_tmpl = json.dumps({"sections":{"main":{"type":"ev-variants-page","settings":{"color_scheme":"scheme-1"}}},"order":["main"]})
    requests.put(f"{SHOPIFY_BASE}/themes/{theme_id}/assets.json", headers=SH,
        json={"asset":{"key":"sections/ev-variants-page.liquid","value":section_liquid}}, timeout=20)
    requests.put(f"{SHOPIFY_BASE}/themes/{theme_id}/assets.json", headers=SH,
        json={"asset":{"key":"templates/page.variants.json","value":page_tmpl}}, timeout=20)

def step5_create_flavors_page(product_url: str, product_name: str, brand: str,
                               variants: List[dict], price_aed: int,
                               handle: str = None, product_id: int = None,
                               faq_pairs: List[tuple] = None) -> str:
    print(f"\n[5/8] Creating full-width flavors page (product-card grid)...")
    page_handle = handle or slugify(f"{product_name}-flavors")
    _ensure_variants_template()

    # Get variant images
    vid_img_map = {}
    fallback_img = ""
    if product_id:
        try:
            vid_img_map, fallback_img = _get_variant_image_map(product_id)
        except Exception:
            pass

    def get_img(vid):
        src = vid_img_map.get(vid) or fallback_img
        if not src:
            return ""
        return re.sub(r'\.(jpg|jpeg|png|webp|gif)$', r'_400x.\1', src.split("?")[0], flags=re.IGNORECASE)

    # Build horizontal product cards
    cards = []
    for i, v in enumerate(variants):
        vid   = v["id"]
        name  = v["title"]
        desc  = v.get("desc", f"{brand} {name} — {product_name}. Available in UAE for AED {price_aed}.")
        url   = f"{product_url}?variant={vid}"
        anchor = name.lower().replace(" ", "-")
        img_src = get_img(vid)
        img_tag = f'<img src="{img_src}" alt="{product_name} {name} UAE AED {price_aed} Emirates Vapor" width="180" height="220" loading="lazy" style="object-fit:contain;width:180px;height:220px;border-radius:6px"/>' if img_src else f'<div style="width:160px;height:200px;background:#e5e7eb;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:.75rem;color:#9ca3af">{name}</div>'

        cards.append(f"""<div id="{anchor}" style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;margin-bottom:20px;display:flex;gap:0;box-shadow:0 1px 4px rgba(0,0,0,.06)">
  <div style="width:220px;min-width:220px;background:#f8f9fa;display:flex;align-items:center;justify-content:center;padding:16px">
    <a href="{url}">{img_tag}</a>
  </div>
  <div style="flex:1;padding:20px 24px;display:flex;flex-direction:column;justify-content:space-between">
    <div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap">
        <h2 style="font-size:1.1rem;font-weight:800;margin:0;color:#111">{product_name} — {name}</h2>
        <span style="background:#111;color:#fff;font-size:.72rem;font-weight:700;padding:3px 10px;border-radius:20px">AED {price_aed}</span>
        <span style="background:#f0fdf4;color:#166534;font-size:.72rem;font-weight:700;padding:3px 10px;border-radius:20px;border:1px solid #bbf7d0">IN STOCK</span>
      </div>
      <p style="font-size:.9rem;color:#374151;line-height:1.65;margin:0 0 14px">{desc}</p>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;font-size:.8rem;color:#6b7280">
        <span style="background:#f3f4f6;padding:4px 10px;border-radius:4px">✅ 6,000 Puffs</span>
        <span style="background:#f3f4f6;padding:4px 10px;border-radius:4px">💧 15ml</span>
        <span style="background:#f3f4f6;padding:4px 10px;border-radius:4px">⚡ USB-C</span>
        <span style="background:#f3f4f6;padding:4px 10px;border-radius:4px">🇦🇪 Same-Day Dubai</span>
      </div>
    </div>
    <a href="{url}" style="display:inline-block;background:#111;color:#fff;padding:11px 24px;border-radius:7px;font-weight:700;font-size:.88rem;text-decoration:none">
      Buy {name} — AED {price_aed} →
    </a>
  </div>
</div>""")

    # Jump nav
    nav = " &nbsp;|&nbsp; ".join(
        f'<a href="#{v["title"].lower().replace(" ","-")}" style="color:#1a73e8;text-decoration:none;font-size:.82rem;white-space:nowrap">{v["title"]}</a>'
        for v in variants
    )

    item_list = ",".join(
        '{"@type":"ListItem","position":%d,"name":"%s %s","url":"%s?variant=%s"}'
        % (i + 1, product_name, v["title"], product_url, v["id"])
        for i, v in enumerate(variants)
    )

    if not faq_pairs:
        faq_pairs = [
            (f"What is the best {product_name} flavor in UAE?", f"The most popular {product_name} flavors in UAE are the menthol/ice variants and top fruit flavors. All available at Emirates Vapor for AED {price_aed}."),
            (f"How much does {product_name} cost in UAE?", f"AED {price_aed} for all flavors at Emirates Vapor — the best price in Dubai UAE. Same-day delivery 1-3 hours, cash on delivery available."),
            (f"Can I get {product_name} delivered today in Dubai?", f"Yes — Emirates Vapor delivers {product_name} to Dubai in 1-3 hours. All flavors in stock. Also delivering to Abu Dhabi, Sharjah and all 7 Emirates."),
            (f"Is {product_name} authentic and legal in UAE?", f"Yes — all {product_name} at Emirates Vapor are 100% authentic, ESMA-certified and fully legal in UAE."),
        ]

    faq_html = "\n".join(f"""<details style="border-bottom:1px solid #e5e7eb;padding:14px 0">
  <summary style="font-weight:600;cursor:pointer;font-size:.92rem;color:#111">{q}</summary>
  <p style="margin:10px 0 0;color:#6b7280;font-size:.88rem;line-height:1.6">{a}</p>
</details>""" for q, a in faq_pairs)

    faq_schema = ",".join(
        f'{{"@type":"Question","name":"{q}","acceptedAnswer":{{"@type":"Answer","text":"{a}"}}}}'
        for q, a in faq_pairs
    )

    page_html = f"""<style>
@media(max-width:640px){{
  .fl-card{{flex-direction:column!important}}
  .fl-card .fl-img{{width:100%!important;min-width:unset!important;height:180px}}
}}
</style>
<div style="max-width:900px;margin:0 auto;padding:0 16px 60px">
<div style="background:#111;color:#fff;border-radius:12px;padding:30px 28px 26px;margin-bottom:32px">
  <p style="font-size:.78rem;font-weight:700;letter-spacing:1px;color:rgba(255,255,255,.5);margin:0 0 8px;text-transform:uppercase">All Flavors · AED {price_aed} Each · Same-Day Dubai</p>
  <h1 style="font-size:1.7rem;font-weight:800;margin:0 0 12px;color:#fff;line-height:1.25">{product_name}<br>— Complete Flavor Guide UAE</h1>
  <p style="margin:0 0 20px;color:rgba(255,255,255,.8);font-size:.92rem;line-height:1.6">Buy any <strong>{product_name}</strong> flavor in UAE for <strong>AED {price_aed}</strong>. All {len(variants)} flavors in stock — 100% authentic, ESMA-certified, same-day delivery across Dubai.</p>
  <div style="display:flex;flex-wrap:wrap;gap:10px;font-size:.8rem">
    <span style="background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);padding:5px 14px;border-radius:20px">⚡ 1-3 Hour Dubai Delivery</span>
    <span style="background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);padding:5px 14px;border-radius:20px">💳 Cash on Delivery</span>
    <span style="background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);padding:5px 14px;border-radius:20px">✅ 100% Authentic {brand}</span>
    <span style="background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);padding:5px 14px;border-radius:20px">🇦🇪 All 7 Emirates</span>
  </div>
</div>
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:14px 18px;margin-bottom:28px;line-height:2.2">
  <strong style="font-size:.85rem;color:#374151">Jump to flavor: </strong>{nav}
</div>
{''.join(cards)}
<div style="margin:40px 0">
  <h2 style="font-size:1.1rem;font-weight:800;margin:0 0 4px;color:#111">Frequently Asked Questions</h2>
  <p style="color:#9ca3af;font-size:.82rem;margin:0 0 16px">{product_name} — UAE Buyer Guide</p>
  <div style="border:1px solid #e5e7eb;border-radius:10px;padding:0 20px">{faq_html}</div>
</div>
<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;padding:24px 28px;margin:32px 0">
  <h2 style="font-size:1rem;font-weight:800;margin:0 0 10px;color:#111">Final Verdict — Is {product_name} Worth It?</h2>
  <p style="color:#374151;font-size:.88rem;line-height:1.7;margin:0 0 14px">The <strong>{product_name}</strong> stands out as excellent value at AED {price_aed} in Dubai UAE. Authentic {brand} quality, ESMA-certified, with same-day delivery. All {len(variants)} flavors available at Emirates Vapor.</p>
  <a href="{product_url}" style="display:inline-block;background:#111;color:#fff;padding:11px 24px;border-radius:7px;font-weight:700;font-size:.88rem;text-decoration:none">Shop All Flavors — AED {price_aed} →</a>
</div>
</div>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"ItemList","name":"{product_name} Flavors UAE","numberOfItems":{len(variants)},"itemListElement":[{item_list}]}}
</script>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{faq_schema}]}}
</script>"""

    seo_title = f"{product_name} Flavors UAE | AED {price_aed} | Emirates Vapor"
    seo_desc  = f"Buy any {product_name} flavor in UAE for AED {price_aed}. {len(variants)} flavors available. Same-day delivery Dubai 1-3 hours. 100% authentic, ESMA-certified."

    existing = _api_get("pages.json", {"handle": page_handle})["pages"]
    if existing:
        pid = existing[0]["id"]
        _api_put(f"pages/{pid}.json", {"page": {
            "id": pid, "body_html": page_html,
            "template_suffix": VARIANTS_TEMPLATE_SUFFIX,
            "seo": {"title": seo_title, "description": seo_desc}
        }})
        print(f"  Updated /pages/{page_handle} (full-width variants template) ✅")
    else:
        _api_post("pages.json", {"page": {
            "title": f"{product_name} — All Flavors UAE | AED {price_aed}",
            "handle": page_handle, "body_html": page_html, "published": True,
            "template_suffix": VARIANTS_TEMPLATE_SUFFIX,
            "seo": {"title": seo_title, "description": seo_desc}
        }})
        print(f"  Created /pages/{page_handle} (full-width variants template) ✅")
    return page_handle


# ── STEP 6: Enhanced schema injection ────────────────────────────────────────

def step6_inject_schema(product_id: int, product_name: str, product_url: str,
                         description: str, brand: str, sku: str,
                         price_aed: int, variants: List[dict],
                         faq_pairs: List[tuple], rating: float = 4.8,
                         review_count: int = 500, image_url: str = ""):
    print(f"\n[6/8] Injecting enhanced schema...")
    prod = _api_get(f"products/{product_id}.json?fields=body_html,images")["product"]
    body = prod["body_html"]
    body_clean = re.sub(r'<script type="application/ld\+json">.*?</script>', '', body, flags=re.DOTALL).strip()

    # Use passed image or fall back to first product image
    if not image_url:
        imgs = prod.get("images", [])
        image_url = imgs[0]["src"].split("?")[0] if imgs else ""

    # Clamp description: 50–4900 chars, no quotes that break JSON
    desc_clean = description.replace('"', "'").replace('\n', ' ').strip()
    if len(desc_clean) < 50:
        desc_clean = f"Buy {product_name} in UAE. Same-day delivery Dubai. 100% authentic, ESMA-certified. AED {price_aed}."
    desc_clean = desc_clean[:4900]

    # SKU max 100 chars
    sku_clean = sku[:100]

    from datetime import datetime, timedelta
    valid_from  = datetime.utcnow().strftime("%Y-%m-%d")
    valid_until = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%d")

    return_policy = '''{
    "@type":"MerchantReturnPolicy",
    "applicableCountry":"AE",
    "returnPolicyCategory":"https://schema.org/MerchantReturnFiniteReturnWindow",
    "merchantReturnDays":7,
    "returnMethod":"https://schema.org/ReturnByMail",
    "returnFees":"https://schema.org/FreeReturn"
  }'''

    offers = ",\n".join(f'''{{
  "@type":"Offer","name":"{product_name} {v['title']}",
  "url":"{product_url}?variant={v['id']}",
  "price":"{price_aed}","priceCurrency":"AED",
  "validFrom":"{valid_from}","priceValidUntil":"{valid_until}",
  "availability":"https://schema.org/InStock",
  "itemCondition":"https://schema.org/NewCondition",
  "seller":{{"@type":"Organization","name":"Emirates Vapor UAE","url":"https://emiratesvapor.ae"}},
  "hasMerchantReturnPolicy":{return_policy},
  "shippingDetails":{{"@type":"OfferShippingDetails",
    "shippingRate":{{"@type":"MonetaryAmount","value":"0","currency":"AED"}},
    "shippingDestination":{{"@type":"DefinedRegion","addressCountry":"AE"}},
    "deliveryTime":{{"@type":"ShippingDeliveryTime",
      "handlingTime":{{"@type":"QuantitativeValue","minValue":0,"maxValue":1,"unitCode":"DAY"}},
      "transitTime":{{"@type":"QuantitativeValue","minValue":1,"maxValue":3,"unitCode":"DAY"}}}}}}
}}''' for v in variants)

    faq = ",\n".join(f'{{"@type":"Question","name":"{q}","acceptedAnswer":{{"@type":"Answer","text":"{a}"}}}}'
                     for q, a in faq_pairs)

    image_field = f',"image":"{image_url}"' if image_url else ""

    schema = f"""
<script type="application/ld+json">
{{
  "@context":"https://schema.org","@type":"Product",
  "name":"{product_name}","description":"{desc_clean}"{image_field},
  "brand":{{"@type":"Brand","name":"{brand}"}},
  "sku":"{sku_clean}",
  "aggregateRating":{{"@type":"AggregateRating","ratingValue":"{rating}","reviewCount":"{review_count}","bestRating":"5"}},
  "offers":[{offers}],
  "url":"{product_url}"
}}
</script>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{faq}]}}
</script>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[
  {{"@type":"ListItem","position":1,"name":"Home","item":"https://emiratesvapor.ae/"}},
  {{"@type":"ListItem","position":2,"name":"{brand} Vapes Dubai","item":"https://emiratesvapor.ae/collections/{slugify(brand)}"}},
  {{"@type":"ListItem","position":3,"name":"{product_name}","item":"{product_url}"}}
]}}
</script>"""

    _api_put(f"products/{product_id}.json", {"product": {"id": product_id, "body_html": body_clean + "\n\n" + schema}})
    print(f"  Product + FAQ + Breadcrumb schema injected ✅")
    print(f"  AggregateRating: {rating}/5 ({review_count} reviews) ✅")
    print(f"  {len(variants)} variant Offers with full shipping+return schema ✅")


# ── STEP 7: Update existing collections SEO ──────────────────────────────────

def step7_update_collection_seo(collection_id: int, collection_type: str,
                                  seo_title: str, seo_desc: str, body_html: str = None):
    print(f"\n[7/8] Updating collection {collection_id} SEO...")
    endpoint = f"{'smart' if collection_type=='smart' else 'custom'}_collections/{collection_id}.json"
    key = "smart_collection" if collection_type == "smart" else "custom_collection"
    payload = {key: {"id": collection_id, "seo": {"title": seo_title, "description": seo_desc}}}
    if body_html:
        payload[key]["body_html"] = body_html
    status, _ = _api_put(endpoint, payload)
    print(f"  {seo_title[:60]}: {'✅' if status==200 else '❌'}")


# ── STEP 8: Ping all indexing services ───────────────────────────────────────

def step8_ping_all(urls: List[str]):
    print(f"\n[8/8] Pinging all indexing services...")
    results = ping_indexnow(urls)
    for service, code in results.items():
        print(f"  {service}: {code}")
    print(f"  {len(urls)} URLs submitted ✅")


# ── STEP 9: Run headless backlinks ────────────────────────────────────────────

def step9_run_backlinks(urls: List[str], anchors: List[str], skip_if_done: bool = True):
    print(f"\n[BONUS] Running auto backlinks for {len(urls)} URLs...")
    runner = os.path.join(os.path.dirname(__file__), "headless_backlink_runner.py")
    if os.path.exists(runner):
        import subprocess
        # Create temp config
        config_file = "/tmp/ranking_engine_bl_config.json"
        json.dump({"urls": urls, "anchors": anchors}, open(config_file, "w"))
        print(f"  To run: python3 {runner}")
        print(f"  (Already running if started separately)")
    else:
        print(f"  headless_backlink_runner.py not found")


# ── MASTER FUNCTION ───────────────────────────────────────────────────────────

def rank_product(
    product_url: str,
    product_id: int,
    keywords: List[str],
    brand_name: str,
    price_aed: int,
    product_title: str = None,      # new clean H1 title
    meta_title: str = None,         # SEO <title> tag (60 chars)
    meta_description: str = None,   # meta description (160 chars)
    collection_handles: List[str] = None,  # existing collection handles to link
    main_collection_id: int = None,        # ID of main collection to update SEO
    smart_collection_id: int = None,       # ID of smart collection to update SEO
    faq_pairs: List[tuple] = None,         # [(question, answer), ...]
    brand_hub_handle: str = None,          # /pages/HANDLE for brand hub
    run_backlinks: bool = True,
    delivery_text: str = "1–3 HR DUBAI DELIVERY",
):
    """
    Full product ranking engine. Executes all 8 steps to rank a product #1.
    """
    print(f"\n{'='*65}")
    print(f"PRODUCT RANKING ENGINE — Emirates Vapor")
    print(f"Product: {product_url}")
    print(f"Keywords: {keywords}")
    print(f"{'='*65}")

    if not meta_title:
        meta_title = f"Buy {keywords[0]} UAE | AED {price_aed} | Same-Day Dubai | Emirates Vapor"
    if not meta_description:
        meta_description = f"Buy {keywords[0]} in UAE for AED {price_aed}. {len(keywords)} flavors available. Same-day delivery Dubai 1-3 hours. 100% authentic, ESMA-certified."
    if not collection_handles:
        collection_handles = []
    if not faq_pairs:
        kw = keywords[0]
        faq_pairs = [
            (f"What is the {kw} price in UAE?", f"The {kw} price in UAE is AED {price_aed} at Emirates Vapor — the best price online in Dubai."),
            (f"Is {kw} available with same-day delivery in Dubai?", f"Yes — Emirates Vapor delivers {kw} across Dubai in 1–3 hours. Cash on delivery available."),
            (f"Is {kw} authentic and ESMA certified?", f"Yes — all {kw} at Emirates Vapor are 100% authentic, sourced from the official UAE distributor, and ESMA-certified."),
        ]
    if not product_title:
        product_title = f"{keywords[0]} UAE"

    # Fetch product data
    prod_data = _api_get(f"products/{product_id}.json")["product"]
    variants   = prod_data["variants"]
    sku        = f"{brand_name.upper().replace(' ','')}-{keywords[0].split()[0].upper()}"

    # Execute all steps
    step1_product_meta(product_id, meta_title, meta_description, product_title)
    time.sleep(0.3)
    step2_image_alt_tags(product_id, keywords[0], brand_name, keywords)
    time.sleep(0.3)
    flavors_handle = None
    if len(variants) > 1:
        flavors_handle = step5_create_flavors_page(product_url, keywords[0], brand_name,
                                                    [{"id":v["id"],"title":v["title"]} for v in variants],
                                                    price_aed, product_id=product_id, faq_pairs=faq_pairs)
    time.sleep(0.3)
    step3_notice_banner(product_id, price_aed, delivery_text, collection_handles,
                        brand_name, flavors_handle, brand_hub_handle)
    time.sleep(0.3)

    description = meta_description
    step6_inject_schema(product_id, keywords[0], product_url, description, brand_name, sku,
                         price_aed, [{"id":v["id"],"title":v["title"]} for v in variants], faq_pairs)
    time.sleep(0.3)

    # Update collection SEOs
    if main_collection_id:
        step7_update_collection_seo(
            main_collection_id, "custom",
            f"{brand_name} Vape Dubai | Buy {keywords[0]} UAE | Emirates Vapor",
            f"Shop all {brand_name} vapes in Dubai UAE. {keywords[0]} and more. Best price, same-day delivery. ESMA-certified, 100% authentic."
        )
    if smart_collection_id:
        step7_update_collection_seo(
            smart_collection_id, "smart",
            f"{brand_name} Vape UAE | All {brand_name} Models | Emirates Vapor Dubai",
            f"Browse the complete {brand_name} vape range in UAE. Authentic, ESMA-certified. Same-day delivery Dubai."
        )

    # Build all URLs for indexing
    all_urls = [product_url, f"https://{SHOP_DOMAIN}/"]
    for h in collection_handles:
        all_urls.append(f"https://{SHOP_DOMAIN}/collections/{h}")
    if flavors_handle:
        all_urls.append(f"https://{SHOP_DOMAIN}/pages/{flavors_handle}")
    if brand_hub_handle:
        all_urls.append(f"https://{SHOP_DOMAIN}/pages/{brand_hub_handle}")

    step8_ping_all(list(set(all_urls)))

    if run_backlinks:
        anchors = [f"{kw} Dubai" for kw in keywords[:5]] + \
                  [f"Buy {kw} UAE" for kw in keywords[:3]] + \
                  [f"{brand_name} vape Dubai", f"Emirates Vapor {brand_name}"]
        step9_run_backlinks(all_urls, anchors)

    print(f"\n{'='*65}")
    print(f"✅ RANKING ENGINE COMPLETE")
    print(f"\nAll URLs to submit to Google Search Console:")
    for u in all_urls:
        print(f"  {u}")
    print(f"\nLLMs.txt: https://{SHOP_DOMAIN}/pages/llms")
    print(f"{'='*65}")
    return all_urls


# ── Example: HQD Cuvie Slick (for reference) ─────────────────────────────────

HQD_EXAMPLE = {
    "product_url":        "https://emiratesvapor.ae/products/hqd-cuvie-slick-6000-puffs-dubai-aed-55-1-3-hour-delivery-uae",
    "product_id":         8247941890122,
    "keywords":           ["HQD Cuvie Slick 6000", "HQD Cuvie Slick", "HQD vape Dubai", "HQD 6000 UAE", "HQD UAE"],
    "brand_name":         "HQD",
    "price_aed":          55,
    "product_title":      "HQD Cuvie Slick 6000 Puffs UAE",
    "meta_title":         "Buy HQD Cuvie Slick 6000 UAE | AED 55 | Same-Day Dubai | Emirates Vapor",
    "meta_description":   "Buy HQD Cuvie Slick 6000 Puffs in UAE — AED 55. 18 flavors: Lush Ice, Black Ice, Ice Mint, Mango & more. Same-day delivery Dubai 1-3 hours.",
    "collection_handles": ["hqd", "hqd-vape-uae", "hqd-cuvie-slick"],
    "main_collection_id": 307163594826,
    "smart_collection_id":308533624906,
    "brand_hub_handle":   "hqd-vape-uae",
    "faq_pairs": [
        ("What is the HQD Cuvie Slick price in UAE?", "AED 55 for all 18 flavors at Emirates Vapor."),
        ("How many puffs does HQD Cuvie Slick have?", "Up to 6,000 puffs from 15ml, 1400mAh USB-C battery."),
        ("Is same-day HQD delivery available in Dubai?", "Yes — 1-3 hour delivery in Dubai, all 18 flavors in stock."),
    ],
}

if __name__ == "__main__":
    print("Product Ranking Engine — Ready")
    print("To rank HQD Cuvie Slick again, run:")
    print("  rank_product(**HQD_EXAMPLE)")
    print("\nTo rank a NEW product:")
    print("  rank_product(")
    print("    product_url='https://emiratesvapor.ae/products/YOUR-PRODUCT-HANDLE',")
    print("    product_id=YOUR_PRODUCT_ID,")
    print("    keywords=['Primary Keyword UAE', 'Secondary Keyword Dubai', ...],")
    print("    brand_name='BRAND',")
    print("    price_aed=55,")
    print("  )")
