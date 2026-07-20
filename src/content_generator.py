"""
content_generator.py
--------------------
Gemini 2.5 Flash powered SEO content engine:
  * Generates SGE-optimised product titles, meta descriptions, body content
  * Writes keyword-rich product descriptions using ontology data
  * Scores existing SEO content
  * Rewrites content with topical authority structure
"""

import re
import json
import logging
from typing import Dict, List, Optional

from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    SGE_CONTENT_GUIDELINES, SEO_WEIGHTS,
)

logger = logging.getLogger(__name__)

# --- Gemini client -----------------------------------------------------------
_gemini_client: Optional[genai.Client] = None

def get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _safe_text(response) -> str:
    """Extract text from Gemini response - handles thinking models that set .text=None."""
    if response.text is not None:
        return response.text.strip()
    try:
        for candidate in response.candidates:
            if candidate.content and candidate.content.parts:
                texts = [p.text for p in candidate.content.parts if p.text]
                if texts:
                    return "".join(texts).strip()
    except Exception:
        pass
    return ""


def _chat(system: str, user: str, temperature: float = 0.4) -> str:
    """Call Gemini 2.5 Flash with system + user prompt.
    Uses thinking_budget=0 to prevent truncation on -flash models.
    """
    client = get_gemini_client()
    full_prompt = f"{system}\n\n{user}"
    cfg = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=4096,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=full_prompt,
        config=cfg,
    )
    return _safe_text(response)


# =============================================================================
# 1.  SEO SCORE CALCULATOR
# =============================================================================

def calculate_seo_score(page: Dict, primary_keyword: str) -> Dict:
    """Score existing page content 0-100."""
    scores   = {}
    feedback = []
    kw       = primary_keyword.lower().strip()

    title     = page.get("title",     "")
    meta_desc = page.get("meta_desc", "")
    body      = page.get("body",      "")
    h1        = " ".join(page.get("h1", []))
    h2        = " ".join(page.get("h2", []))
    full_text = f"{title} {meta_desc} {h1} {h2} {body}".lower()

    # Title length (50-60 chars ideal)
    tlen = len(title)
    if 50 <= tlen <= 60:
        scores["title_length"] = SEO_WEIGHTS["title_length"]
    elif 40 <= tlen <= 70:
        scores["title_length"] = int(SEO_WEIGHTS["title_length"] * 0.6)
        feedback.append(f"Title is {tlen} chars - ideal is 50-60.")
    else:
        scores["title_length"] = 0
        feedback.append(f"Title length ({tlen}) is off. Target 50-60 characters.")

    # Meta description (140-160 chars)
    mlen = len(meta_desc)
    if 140 <= mlen <= 160:
        scores["meta_desc_length"] = SEO_WEIGHTS["meta_desc_length"]
    elif 100 <= mlen <= 180:
        scores["meta_desc_length"] = int(SEO_WEIGHTS["meta_desc_length"] * 0.6)
        feedback.append(f"Meta desc is {mlen} chars - ideal is 140-160.")
    else:
        scores["meta_desc_length"] = 0
        feedback.append(f"Meta description ({mlen} chars) needs to be 140-160 characters.")

    # Primary keyword in title, H1, first 100 words
    in_title  = kw in title.lower()
    in_h1     = kw in h1.lower()
    first_100 = " ".join(body.split()[:100]).lower()
    in_intro  = kw in first_100

    pk_score = 0
    if in_title:  pk_score += 8
    if in_h1:     pk_score += 7
    if in_intro:  pk_score += 5
    scores["primary_keyword"] = min(pk_score, SEO_WEIGHTS["primary_keyword"])
    if not in_title: feedback.append(f"Add primary keyword \"{primary_keyword}\" to title.")
    if not in_h1:    feedback.append(f"Add primary keyword \"{primary_keyword}\" to H1.")
    if not in_intro: feedback.append("Use keyword in the opening paragraph.")

    # Keyword density (1-2% in body)
    words    = body.lower().split()
    kw_words = kw.split()
    kw_count = sum(
        1 for i in range(len(words) - len(kw_words) + 1)
        if words[i:i+len(kw_words)] == kw_words
    )
    density = (kw_count / max(len(words), 1)) * 100
    if 1.0 <= density <= 2.5:
        scores["keyword_density"] = SEO_WEIGHTS["keyword_density"]
    elif density > 0:
        scores["keyword_density"] = int(SEO_WEIGHTS["keyword_density"] * 0.5)
        feedback.append(f"Keyword density {density:.1f}% - aim for 1-2%.")
    else:
        scores["keyword_density"] = 0
        feedback.append("Primary keyword missing from product description body.")

    # Content length
    wc = len(words)
    if wc >= 300:
        scores["content_length"] = SEO_WEIGHTS["content_length"]
    elif wc >= 150:
        scores["content_length"] = int(SEO_WEIGHTS["content_length"] * 0.5)
        feedback.append(f"Description is only {wc} words - aim for 300+.")
    else:
        scores["content_length"] = 0
        feedback.append(f"Very short description ({wc} words). Expand to 300+ words.")

    # LSI coverage
    lsi_terms = _get_lsi_terms(kw)
    found_lsi = sum(1 for term in lsi_terms if term in full_text)
    lsi_pct   = found_lsi / max(len(lsi_terms), 1)
    scores["lsi_coverage"] = int(SEO_WEIGHTS["lsi_coverage"] * lsi_pct)
    if lsi_pct < 0.4:
        feedback.append("Low semantic keyword coverage. Add words like: " + ", ".join(lsi_terms[:5]))

    # Readability
    sentences    = re.split(r'[.!?]+', body)
    avg_sent_len = len(words) / max(len([s for s in sentences if s.strip()]), 1)
    if avg_sent_len <= 20:
        scores["readability"] = SEO_WEIGHTS["readability"]
    elif avg_sent_len <= 30:
        scores["readability"] = int(SEO_WEIGHTS["readability"] * 0.6)
        feedback.append("Sentences are a bit long. Aim for avg <=20 words/sentence.")
    else:
        scores["readability"] = int(SEO_WEIGHTS["readability"] * 0.3)
        feedback.append("Sentences too long - split them for better readability/SGE.")

    # Schema signals
    schema_hints = ["price", "in stock", "available", "buy", "rating", "review"]
    found_schema = sum(1 for h in schema_hints if h in full_text)
    scores["schema_signals"] = int(SEO_WEIGHTS["schema_signals"] * (found_schema / len(schema_hints)))

    total = sum(scores.values())
    grade = "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D"

    return {
        "total_score": total,
        "max_score":   100,
        "grade":       grade,
        "breakdown":   scores,
        "feedback":    feedback,
        "keyword_density_pct": round(density, 2),
        "word_count":  wc,
    }


def _get_lsi_terms(keyword: str) -> List[str]:
    lsi_db = {
        "vape":      ["nicotine", "device", "e-liquid", "coil", "pod", "puff", "cloud", "flavor", "mod", "battery"],
        "al fakher": ["shisha", "hookah", "flavour", "tobacco", "mint", "double apple", "grape", "watermelon"],
        "hookah":    ["shisha", "charcoal", "foil", "bowl", "hose", "stem", "base", "smoke", "flavour"],
        "default":   ["quality", "premium", "best", "buy", "price", "review", "online", "shop", "available"],
    }
    kw_lower = keyword.lower()
    for key, terms in lsi_db.items():
        if key in kw_lower:
            return terms
    return lsi_db["default"]


# =============================================================================
# 2.  META TITLE GENERATOR
# =============================================================================

def generate_seo_title(
    product_name:       str,
    primary_keyword:    str,
    brand:              str,
    top_keywords:       List[str],
    competitor_titles:  List[str],
) -> Dict:
    system = (
        "You are a senior SEO content strategist specialising in e-commerce and "
        "Google SGE optimisation. You write conversion-focused meta titles."
    )
    user = f"""
Generate 3 SEO-optimised product page TITLE TAG options for:

Product: {product_name}
Brand: {brand}
Primary Keyword: {primary_keyword}
Top Supporting Keywords: {", ".join(top_keywords[:10])}

Competitor Titles for reference (do NOT copy, beat them):
{chr(10).join(f"- {t}" for t in competitor_titles[:5])}

RULES:
- Length: 50-60 characters EXACTLY
- Include primary keyword near the start
- Include brand name
- High CTR - include a power word or emotional trigger
- No keyword stuffing
- Each option must be different style

Return ONLY valid JSON (no markdown, no code fences):
{{
  "titles": [
    {{"title": "...", "char_count": 0, "style": "...", "ctr_reasoning": "..."}},
    {{"title": "...", "char_count": 0, "style": "...", "ctr_reasoning": "..."}},
    {{"title": "...", "char_count": 0, "style": "...", "ctr_reasoning": "..."}}
  ],
  "recommended_index": 0
}}
"""
    raw  = _chat(system, user)
    raw  = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    # Find JSON object
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    return data


# =============================================================================
# 3.  META DESCRIPTION GENERATOR
# =============================================================================

def generate_meta_description(
    product_name:    str,
    primary_keyword: str,
    key_benefits:    List[str],
    top_keywords:    List[str],
) -> Dict:
    system = "You are a senior SEO expert specialising in high-CTR meta descriptions for e-commerce."
    user = f"""
Write 3 SEO meta descriptions for:

Product: {product_name}
Primary Keyword: {primary_keyword}
Key Benefits/Features: {", ".join(key_benefits)}
Supporting Keywords to weave in: {", ".join(top_keywords[:8])}

RULES:
- Length: 140-160 characters EXACTLY
- Primary keyword in first 60 chars
- Include a clear CTA (Shop Now / Buy Today / Order Online etc.)
- Include a key benefit or USP
- No duplicate phrasing across options

Return ONLY valid JSON (no markdown):
{{
  "descriptions": [
    {{"text": "...", "char_count": 0, "cta": "...", "usp_highlight": "..."}},
    {{"text": "...", "char_count": 0, "cta": "...", "usp_highlight": "..."}},
    {{"text": "...", "char_count": 0, "cta": "...", "usp_highlight": "..."}}
  ],
  "recommended_index": 0
}}
"""
    raw  = _chat(system, user)
    raw  = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    return data


# =============================================================================
# 4.  PRODUCT DESCRIPTION GENERATOR  (SGE / Helpful Content)
# =============================================================================

def generate_product_description(
    product_name:    str,
    primary_keyword: str,
    top_keywords:    List[str],
    gap_keywords:    List[str],
    paa_questions:   List[str],
    ontology_table:  List[Dict],
    competitor_data: List[Dict],
    existing_desc:   str = "",
) -> Dict:
    comp_h2s = []
    for cd in competitor_data[:3]:
        comp_h2s.extend(cd.get("h2", [])[:3])

    transactional_kws = [
        row["keyword"] for row in ontology_table
        if row["intent"] in ("Transactional", "Price / Deal", "Product Variant")
    ][:15]

    system = f"""
You are an elite SEO content strategist and copywriter. You write product page content
that ranks on Google's first page and captures Google SGE (AI Overview) snippets.

{SGE_CONTENT_GUIDELINES}
"""
    user = f"""
Write a complete SEO product page content package for:

Product: {product_name}
Primary Keyword: {primary_keyword}

TOP KEYWORDS TO NATURALLY INCLUDE:
{chr(10).join(f"- {k}" for k in top_keywords[:20])}

KEYWORD GAPS FROM COMPETITORS (include naturally):
{chr(10).join(f"- {k}" for k in gap_keywords[:15])}

BUY-INTENT KEYWORDS:
{chr(10).join(f"- {k}" for k in transactional_kws[:10])}

COMPETITOR H2 HEADINGS (do better):
{chr(10).join(f"- {h}" for h in comp_h2s[:8])}

PEOPLE ALSO ASK (answer in FAQ):
{chr(10).join(f"- {q}" for q in paa_questions[:6])}

EXISTING DESCRIPTION (improve/rewrite):
{existing_desc[:800] if existing_desc else "None - write from scratch."}

Return ONLY valid JSON (no markdown):
{{
  "h1":               "...",
  "intro_paragraph":  "...",
  "key_features":     ["...", "...", "...", "...", "..."],
  "body_paragraphs":  ["para1...", "para2...", "para3..."],
  "faq": [
    {{"question": "...", "answer": "..."}},
    {{"question": "...", "answer": "..."}},
    {{"question": "...", "answer": "..."}}
  ],
  "alt_text_suggestions": ["img1 alt: ...", "img2 alt: ...", "img3 alt: ..."],
  "internal_link_suggestions": [
    {{"anchor": "...", "suggested_page": "...", "reason": "..."}}
  ],
  "schema_product_desc": "...",
  "word_count_estimate": 0,
  "primary_keyword_placements": ["H1", "Intro", "Paragraph 2", "FAQ"],
  "sge_snippet_candidate": "..."
}}
"""
    raw  = _chat(system, user, temperature=0.3)
    raw  = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    return data


# =============================================================================
# 5.  SHOPIFY SEO PACKAGE GENERATOR
# =============================================================================

def generate_shopify_seo_package(
    product_name:     str,
    primary_keyword:  str,
    research_data:    Dict,
    existing_product: Dict = None,
) -> Dict:
    top_keywords       = research_data.get("all_keywords", [])[:25]
    gap_keywords       = research_data.get("gap_analysis", {}).get("missing_keywords", [])[:20]
    paa                = research_data.get("paa", [])
    ontology           = research_data.get("ontology_table", [])
    competitors        = research_data.get("competitors", [])
    serp_results       = research_data.get("serp_results", [])
    competitor_titles  = [r["title"] for r in serp_results if r.get("title")]

    existing_desc = ""
    brand         = ""
    key_benefits  = []

    if existing_product:
        import re as _re
        existing_desc = _re.sub(r'<[^>]+>', '', existing_product.get("body_html", ""))
        brand         = existing_product.get("vendor", "")
        tags          = existing_product.get("tags", [])
        key_benefits  = tags[:5] if tags else []

    if not brand:
        brand = primary_keyword.split()[0].title()
    if not key_benefits:
        key_benefits = [primary_keyword, "premium quality", "fast delivery", "best price"]

    titles     = generate_seo_title(product_name, primary_keyword, brand, top_keywords, competitor_titles)
    meta_descs = generate_meta_description(product_name, primary_keyword, key_benefits, top_keywords)
    content    = generate_product_description(
        product_name, primary_keyword, top_keywords,
        gap_keywords, paa, ontology, competitors, existing_desc
    )

    best_title = titles["titles"][titles.get("recommended_index", 0)]["title"]
    best_desc  = meta_descs["descriptions"][meta_descs.get("recommended_index", 0)]["text"]
    body_html  = _build_html_description(content, primary_keyword)
    handle     = re.sub(r'[^a-z0-9]+', '-', primary_keyword.lower()).strip('-')

    tags = list(dict.fromkeys(
        [primary_keyword]
        + [k for k in top_keywords[:15] if len(k.split()) <= 3]
    ))[:20]

    return {
        "seo_title":       best_title,
        "seo_description": best_desc,
        "body_html":       body_html,
        "handle":          handle,
        "tags":            tags,
        "all_titles":      titles["titles"],
        "all_meta_descs":  meta_descs["descriptions"],
        "content_data":    content,
        "schema_markup":   _generate_schema(product_name, brand, best_desc, primary_keyword),
    }


def _build_html_description(content: Dict, keyword: str) -> str:
    html_parts = []

    if content.get("h1"):
        html_parts.append(f'<h1>{content["h1"]}</h1>')

    if content.get("intro_paragraph"):
        html_parts.append(f'<p>{content["intro_paragraph"]}</p>')

    if content.get("key_features"):
        html_parts.append('<ul class="product-features">')
        for feat in content["key_features"]:
            html_parts.append(f'  <li>{feat}</li>')
        html_parts.append('</ul>')

    for para in content.get("body_paragraphs", []):
        if para.strip():
            html_parts.append(f'<p>{para}</p>')

    if content.get("faq"):
        html_parts.append('<div class="product-faq" itemscope itemtype="https://schema.org/FAQPage">')
        html_parts.append(f'  <h2>Frequently Asked Questions About {keyword.title()}</h2>')
        for item in content["faq"]:
            q = item.get("question", "")
            a = item.get("answer", "")
            html_parts.append(
                f'  <div itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">' +
                f'    <h3 itemprop="name">{q}</h3>' +
                f'    <div itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">' +
                f'      <p itemprop="text">{a}</p>' +
                f'    </div>' +
                f'  </div>'
            )
        html_parts.append('</div>')

    if content.get("sge_snippet_candidate"):
        html_parts.append(
            f'<div class="sge-featured-snippet" style="background:#f8f9fa;padding:16px;border-left:4px solid #4CAF50;margin:16px 0;">' +
            f'<p><strong>{content["sge_snippet_candidate"]}</strong></p>' +
            f'</div>'
        )

    return "\n".join(html_parts)


def _generate_schema(product_name: str, brand: str, description: str, keyword: str) -> str:
    import json as _json
    schema = {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": product_name,
        "brand": {"@type": "Brand", "name": brand},
        "description": description,
        "keywords": keyword,
        "offers": {
            "@type": "Offer",
            "availability": "https://schema.org/InStock",
            "priceCurrency": "USD",
        }
    }
    return _json.dumps(schema, indent=2)
