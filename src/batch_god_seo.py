#!/usr/bin/env python3
"""
Batch GOD SEO Runner — applies god_seo_engine to all remaining products.
Usage: python3 batch_god_seo.py [--resume] [--vendor "Brand Name"]
"""
import sys, os, json, time, subprocess, requests, re
sys.path.insert(0, os.path.dirname(__file__))

SHOP    = 'emirates-vapor.myshopify.com'
TOKEN   = os.environ.get('SHOPIFY_TOKEN', '')
DOMAIN  = 'https://emiratesvapor.ae'
REPORTS = os.path.join(os.path.dirname(__file__), '..', 'rank_reports')
PROGRESS_FILE = os.path.join(REPORTS, 'batch_progress.json')

# Products already GOD-SEO'd (Shopify product IDs from pre-batch manual runs)
DONE_IDS = {8242398986314, 8242439422026, 8242493915210, 8247941988426, 8247942152266, 8247941890122}

# Handles confirmed GOD-SEO'd in July 20-21 batch runs (187 products)
DONE_HANDLES = {
    "18650-battery-best-vape-shop-dubai-2026",
    "acapulco-by-gold-leaf",
    "aegis-boost-replacement-coils-in-dubai-uae",
    "aegis-nano-3",
    "air-bar-best-vape-shop-dubai-2026",
    "airbar-e-shisha-15000-puffs-disposable-vape-dtl-3mg-in-the-uae",
    "airmez-mars-20000-puffs-disposable-vape-in-uae",
    "aivono-aim-magic-20000-puff-zero-nicotine-disposable-vape-in-uae",
    "al-fakher-crown-bar-15000-al-fakher-vape-best-vape-shop-dubai-2026",
    "al-fakher-crown-bar-40000",
    "al-fakher-crown-bar-8000-puffs-best-vape-shop-dubai-2026",
    "al-fakher-crown-bar-crown-bar-hypermax-15000-puff-s-best-vape-shop-dubai-2026",
    "al-fakher-crown-bar-supermax-6000-puff-s",
    "al-fakher-e-hose-x-60000-puffs-crown-bar-vape-shop-dubai",
    "al-fakher-heypermax-30000-puffs-disposable-vape-in-uae",
    "allo-1500-puffs",
    "allo-plus-5000-puffs",
    "aloe-watermelon-by-blvk-aloe-salt-series-30ml-in-uae",
    "alt-nu-8000-puffs-disposable-vape-20mg-in-dubai-uae",
    "antidote-on-ice-by-ruthless",
    "apoc-poota-best-vape-shop-dubai",
    "apple-bomb-best-vape-shop-dubai-2026",
    "apple-bomb-by-vgod-saltnic-30ml",
    "apple-fritter-by-loaded",
    "arabisk-ar-best-vape-shop-dubai-2026",
    "artery-ferocious-cl6-50000-puffs-nic-ice-edition-in-the-uae",
    "asap-grape-by-nasty-e-liquid",
    "aspire-nexi-pro-combo-kit",
    "aspire-nexi-pro-replacement-pod-1-2ohm-2pcs-in-uae",
    "aspire-pixo-aura-pod-vape-kit",
    "aspire-pixo-kit",
    "aspire-pixo-neo-vape-kit",
    "aspire-riil-x",
    "aspire-riil-x-empty-pod-cartridge-2ml-in-uae",
    "authentic-voopoo-drag-s-pnp-x-kit-60w-in-dubai-uae",
    "bad-blood-best-vape-shop-dubai-2026",
    "bad-blood-by-nasty-juice-60ml-e-liquid-3mg-in-uae",
    "bazooka-green-best-vape-shop-dubai-2026",
    "beard-e-juice-best-vape-shop-dubai-2026",
    "beco-osens-best-vape-shop-dubai-2026",
    "beco-soft-max-12000-puffs-disposable-vape-20mg",
    "beco-xl-10000",
    "becu-lux-best-vape-shop-dubai",
    "berry-blast-freeze-edition-by-ruthless-120ml",
    "berry-bomb-best-vape-shop-dubai-2026",
    "berry-bomb-by-vgod",
    "best-buy-aegis-boost-replacement-coils-0-4-0hm-in-dubai-uae",
    "best-buy-antidote-on-ice-by-ruthless-vapor-120ml-in-uae",
    "best-buy-geek-vape-g-coil-replacement-coils-5-pack-in-dubai-uae",
    "best-buy-geekvape-b-series-coil-for-aegis-boost-in-dubai-uae",
    "best-buy-geekvape-g-pod-coils",
    "best-buy-geekvape-wenax-sc-pod-system-kit-1100mah-in-uae",
    "best-buy-royalty-ii-vapetasia-salts-30ml-in-uae",
    "best-buy-uwell-caliburn-g-and-koko-prime-replacement-coils-in-uae",
    "best-buy-uwell-caliburn-g2-empty-pod-cartridge-in-uae",
    "best-ripe-vapes-vct-cafe-salt-nic-30ml-in-dubai",
    "best-vaporesso-gen-200-kit-with-itank-atomizer-8ml-in-uae",
    "black-custard-best-vape-shop-dubai-2026",
    "black-panther-saltnic-by-dr-vape",
    "blackout-disposable",
    "blue-ice-best-vape-shop-dubai-2026",
    "blue-panther-by-dr-vape",
    "blue-raspberry-i-love-salts-by-mad-hatter-juice",
    "blue-raspberry-ice-i-love-salts-by-mad-hatter-juice",
    "blvk-bar-20000-puffs-disposable-vape-in-uae",
    "blvk-bar-saltnic-30ml-e-liquid-35mg-and-50mg-in-uae",
    "blvk-bubba-ice-saltnic-30ml-e-juice-in-uae",
    "blvk-ello-best-vape-shop-dubai-2026",
    "blvk-melon-saltnic-ice-30ml-in-uae",
    "blvk-nicotine-salt-50mg-30ml-in-dubai-uae",
    "blvk-salt-best-vape-shop-dubai-2026",
    "blvk-uni-grape-60ml-e-liquid-in-uae-purple-grape",
    "blvk-unicorn-juice-vape-shop-dubai",
    "blvk-unicorn-salt-iced-berry-banana-by-blvk-pink-salt-series-30ml",
    "blvk-unicorn-salt-iced-berry-lemonade-by-blvk-pink-salt-series-30ml",
    "brazilian-tobacco-by-ruthless-in-dubai",
    "bronze-blend-by-nasty-juice-60ml-e-liquid-in-uae",
    "brown-sugar-premium-e-liquid-3mg-100ml-in-uae",
    "bubblegum-kings-original-60ml-freebase",
    "bubblegum-kings-original-ice-120ml-in-uae",
    "bubblegum-kings-original-ice-30ml-salts-by-dr-vapes-in-uae",
    "bubblegum-kings-original-ice-60ml-freebase",
    "bubblegum-kings-original-salts-by-dr-vapes-30ml-in-uae",
    "bubblegum-kings-pomegranate-60ml-freebase",
    "bubblegum-kings-pomegranate-ice-60ml-freebase",
    "bubblegum-kings-watermelon-60ml-freebase",
    "bubblegum-kings-watermelon-ice-60ml-freebase",
    "buy-authentic-citrus-strawberry-ice-blvk-fusion-salt-30ml-in-dubai-uae",
    "buy-authentic-ripe-vapes-salt-nic-vct-30ml-in-30mg-and-50mg",
    "buy-authentic-sams-vapes-enrgy-blaze-30ml-salt-nic-in-uae",
    "buy-authentic-vanilla-custard-by-blvk-unicorn-salt-30ml-in-dubai-uae",
    "buy-ez-duz-it-on-ice-by-ruthless-vapor-100ml-in-uae",
    "buy-gold-vape-juice-by-ruthless-vapor-120ml-in-uae",
    "buy-grape-apple-ice-blvk-fusion-salt-30ml-in-uae",
    "buy-new-strawberry-and-kiwi-nasty-podmate-salt-30ml-in-dubai",
    "buy-new-strawberry-kiwi-nasty-modmate-60ml-freebase",
    "buy-online-geekvape-zeus-sub-ohm-mesh-coils-5-pack",
    "buy-online-voopoo-vinci-pod-cartridge-2ml",
    "buy-orange-pineapple-freez-ripe-vapes-synthetic-60ml-in-uae",
    "buy-skir-skirrr-on-ice-by-ruthless-vapor-120ml-in-uae",
    "buy-sprk-vapor-v4-pods-compatible-with-myle-v4-pod-system-in-uae",
    "buy-vaporesso-gtx-replacement-coils-in-uae",
    "buy-voopoo-drag-nano-2-replacement-pods-3pcs-pack",
    "buy-voopoo-drag-x-pnp-x-pod-kit-80w-in-uae",
    "caliburn-gk2-best-vape-shop-dubai-2026",
    "chocolate-glazed-donuts-by-loaded",
    "cigbay-26000-puffs-disposable-vape-kit-20mg-in-the-uae",
    "classic-menthol-i-love-salts-by-mad-hatter-juice",
    "coffee-tobacco-by-ruthless-vapor-120ml-in-uae",
    "cokii-bar-best-vape-shop-dubai-2026",
    "cola-lime-salt-nic-by-pod-salt-30ml-in-uae",
    "columbus-smooth-tobacco-60ml-in-uae",
    "columbus-sweet-tobacco-60ml-in-uae",
    "cookie-butter-by-loaded-in-uae",
    "coolplay-turbo-20000-puffs-disposable-vape-in-uae",
    "cotton-bacon-prime",
    "crave-max-best-vape-shop-dubai-2026",
    "creamy-tobacco-best-vape-shop-dubai-2026",
    "creme-de-la-creme-by-phillip-rocke-grand-reserve-60ml-in-dubai-uae",
    "creme-de-la-creme-by-phillip-rocke-grand-reserve-salt-30ml",
    "crisp-menthol-60ml-by-naked100-in-uae",
    "crown-bar-al-fakher-15000",
    "crown-bar-al-fakher-8000-pro-dtl-8000puffs-dual-mode-disposable-vape-in-uae",
    "cuban-cigar-by-blvk-unicorn-60ml-in-uae-cuban-tobacco",
    "cubano-black-best-vape-shop-dubai-2026",
    "cubano-saltnic-by-vgod",
    "cubano-silver-by-vgod-60ml",
    "cush-man-best-vape-shop-dubai-2026",
    "cush-man-e-liquid-by-nasty-juice-60ml-in-uae",
    "cush-man-mango-banana-by-nasty-60ml-e-liquid-in-uae",
    "cush-man-mango-grape-by-nasty-60ml-e-liquid-in-uae",
    "cush-man-mango-strawberry-by-nasty-60ml-e-liquid-in-uae",
    "dessert-series-unicorn-strawberry-milk-120ml-by-dr-vapes-in-uae",
    "devil-teeth-best-vape-shop-dubai-2026",
    "devil-teeth-e-liquid-by-nasty-juice-60ml-in-uae",
    "digiflavor-digi-u-pod-kit-1000mah-in-uae",
    "dinner-lady-max-1500-best-vape-shop-dubai-2026",
    "dinner-lady-saltnic-30ml",
    "double-apple-best-vape-shop-dubai",
    "double-apple-by-nasty-shisha-60ml-e-liquid-in-uae",
    "double-apple-by-nasty-shisha-nicotine-salt-30ml",
    "dovpo-20000-puffs-disposable-vape-in-uae",
    "dr-vape-bubblegum-king-120ml-in-uae",
    "dr-vape-bubblegum-kings-60ml-in-uae",
    "dr-vape-bubblegum-kings-saltnic-30ml-in-uae",
    "dr-vape-e-juice-60ml",
    "dr-vape-saltnic-30ml",
    "dr-vapes-120ml-the-panther-series-vape-liquid-in-uae",
    "dr-vapes-12mg-vape-liquid-60ml-in-uae",
    "dr-vapes-black-custard-salt-nic-30ml-in-uae",
    "dr-vapes-cheesecake-salt-nic-30ml-in-uae",
    "dr-vapes-dessert-series-black-custard-120ml-in-uae",
    "dr-vapes-panther-series-salt-nic-30ml-e-juice-all-flavors-30mg-amp-50mg-in-uae",
    "dr-vapes-pink-panther",
    "dr-vapes-salt-nic-30ml-the-panther-series-vape-liquid-in-uae",
    "dr-vapes-the-frozen-series-120ml-in-uae",
    "dr-vapes-the-frozen-series-salt-30ml-vape-liquid-in-uae",
    "dr-vapes-the-tobacco-series-e-liquid-in-uae",
    "dr-vapes-unicorn-strawberry-milk-salt-30ml-in-uae",
    "drip-disposable-tank",
    "geek-ap2-best-vape-shop-dubai-2026",
    "geek-bar-best-vape-shop-dubai",
    "geek-bar-meloso-ultra-10000-best-vape-shop-dubai-2026",
    "geek-bar-pulse-15000-puffs",
    "geek-bar-pulse-x-25000",
    "geek-bar-watt-23000-puffs-disposable-vape-5-50mg-nicotine-in-uae-dubai",
    "geek-max100-best-vape-shop-dubai-2026",
    "geek-vape-aegis-boost-coil-mesh-in-uae",
    "geek-wenax-best-vape-shop-dubai-2026",
    "geek-wire-best-vape-shop-dubai-2026",
    "geek-zeus-best-vape-shop-dubai-2026",
    "geekvape-aegis-au-pod-kit-in-uae",
    "geekvape-aegis-hero",
    "geekvape-aegis-hero-q-kit-1300mah-in-uae",
    "geekvape-aegis-legend-5-vape-mod-kit-200w-in-uae",
    "geekvape-aegis-legend-iii-3-kit-200w-with-z-fli-tank-atomizer-5-5ml-in-uae",
    "geekvape-aegis-nano-2-kit-an-2-1100mah-in-uae",
    "geekvape-aegis-nano-replacement-pods",
    "geekvape-aegis-one-pod-cartridge-for-aegis-1fc-amp-one-kit-3pcs-pack",
    "geekvape-aegis-solo-3-vape-kit-external-battery-100w-in-dubai-uae",
    "geekvape-aq-pod-kit-1000mah-in-uae",
    "geekvape-b60-aegis-boost-2-replacement-pods-in-uae",
    "geekvape-g-1-0ohm-0-8ohm-coils-for-wenax-c1-5pcs-pack",
    "geekvape-g-series-coils-5pcs-pack-in-uae",
    "geekvape-h45-aegis-hero-2-empty-pod-in-the-uae",
    "geekvape-h45-pod-system-kit-1400mah-4ml-in-dubai-uae",
    "geekvape-legend-5-kit-uae",
}

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        return json.load(open(PROGRESS_FILE))
    return {"done": [], "failed": [], "skipped": []}

def save_progress(p):
    os.makedirs(REPORTS, exist_ok=True)
    json.dump(p, open(PROGRESS_FILE, 'w'), indent=2)

def get_all_products(vendor_filter=None):
    """Fetch all active products using correct Shopify cursor pagination."""
    prods, pi = [], None
    while True:
        if pi:
            # Cursor page: ONLY pass limit + page_info (Shopify ignores other params)
            r = requests.get(f'https://{SHOP}/admin/api/2024-01/products.json',
                params={'limit': 250, 'page_info': pi},
                headers={'X-Shopify-Access-Token': TOKEN}, timeout=60)
        else:
            r = requests.get(f'https://{SHOP}/admin/api/2024-01/products.json',
                params={'limit': 250, 'fields': 'id,handle,title,vendor,product_type,status',
                        'status': 'active'},
                headers={'X-Shopify-Access-Token': TOKEN}, timeout=60)
        batch = r.json().get('products', [])
        prods.extend(batch)
        m = re.search(r'page_info=([^&>]+).*?rel="next"', r.headers.get('Link', ''))
        pi = m.group(1) if m else None
        if not pi or not batch:
            break

    # Filter out already done
    remaining = [p for p in prods if p['id'] not in DONE_IDS and p['handle'] not in DONE_HANDLES]

    if vendor_filter:
        remaining = [p for p in remaining if vendor_filter.lower() in p.get('vendor','').lower()]

    return remaining

def run_god(prod, progress, log_fh):
    handle = prod['handle']
    url    = f"{DOMAIN}/products/{handle}"

    if handle in progress['done']:
        print(f"  → SKIP (already done in this batch): {handle}")
        return 'skip'

    print(f"\n{'='*65}")
    print(f"⚡ GOD SEO: {prod['title'][:60]}")
    print(f"   Vendor: {prod['vendor']}  |  {url}")
    print(f"{'='*65}")
    log_fh.write(f"\n\n--- {handle} ---\n{url}\n")
    log_fh.flush()

    god_py = os.path.join(os.path.dirname(__file__), 'god_seo_engine.py')
    cmd = [sys.executable, '-u', god_py, url, '--no-ai', '--no-backlinks', '--fast']

    try:
        result = subprocess.run(
            cmd,
            capture_output=False,
            text=True,
            timeout=300,
            cwd=os.path.join(os.path.dirname(__file__), '..'),
        )
        if result.returncode == 0:
            progress['done'].append(handle)
            save_progress(progress)
            log_fh.write(f"✓ SUCCESS\n")
            log_fh.flush()
            return 'ok'
        else:
            progress['failed'].append({'handle': handle, 'code': result.returncode})
            save_progress(progress)
            log_fh.write(f"✗ EXIT {result.returncode}\n")
            log_fh.flush()
            return 'fail'
    except subprocess.TimeoutExpired:
        progress['failed'].append({'handle': handle, 'code': 'timeout'})
        save_progress(progress)
        log_fh.write(f"✗ TIMEOUT\n")
        log_fh.flush()
        print(f"  ✗ Timeout after 5 min — skipping")
        return 'timeout'
    except Exception as e:
        progress['failed'].append({'handle': handle, 'code': str(e)})
        save_progress(progress)
        log_fh.write(f"✗ ERROR: {e}\n")
        log_fh.flush()
        print(f"  ✗ Error: {e}")
        return 'err'

def main():
    args = sys.argv[1:]
    resume = '--resume' in args
    vendor_filter = None
    chunk = None
    limit = None
    if '--vendor' in args:
        i = args.index('--vendor')
        vendor_filter = args[i + 1] if i + 1 < len(args) else None
    if '--chunk' in args:
        i = args.index('--chunk')
        chunk = int(args[i + 1]) if i + 1 < len(args) else None
    if '--limit' in args:
        i = args.index('--limit')
        limit = int(args[i + 1]) if i + 1 < len(args) else 1

    env_token = os.environ.get('SHOPIFY_TOKEN')
    if env_token:
        global TOKEN
        TOKEN = env_token

    print("⚡ BATCH GOD SEO ENGINE")
    print(f"  Resume: {resume}  |  Vendor filter: {vendor_filter or 'ALL'}  |  Chunk: {chunk or 'ALL'}")
    print("  Fetching products...")

    prods = get_all_products(vendor_filter)
    progress = load_progress() if resume else {"done": [], "failed": [], "skipped": []}

    # Sort: named brands first, Emirates Vapor (juices/own brand) last
    PRIORITY = ['GeekVape','Elfbar','NASTY','AL FAKHER','Aspire','FUMMO','Uwell',
                'Voopoo','Vaporesso','Air Bar','SMOK','MYLE','POD SALT']
    def sort_key(p):
        v = p.get('vendor','')
        for i, brand in enumerate(PRIORITY):
            if brand.lower() in v.lower(): return i
        return 999

    prods.sort(key=sort_key)

    # Chunk: 6 chunks of ~167 each covers all ~1000 products in one pass
    if chunk:
        chunk_size = 167
        start = (chunk - 1) * chunk_size
        end   = start + chunk_size
        prods = prods[start:end]
        print(f"  Chunk {chunk}: products {start+1}–{start+len(prods)}")

    if limit:
        prods = prods[:limit]
        print(f"  LIMIT MODE: running only first {limit} product(s)")

    # Skip already-done handles if resuming
    if resume:
        done_set = set(progress['done'])
        prods = [p for p in prods if p['handle'] not in done_set]
        print(f"  Resuming — {len(done_set)} already done, {len(prods)} remaining")

    total = len(prods)
    print(f"  Total to process: {total} products")
    print()

    log_path = os.path.join(REPORTS, 'batch_god_run.log')
    ok = fail = skip = 0

    with open(log_path, 'a' if resume else 'w') as log_fh:
        log_fh.write(f"BATCH GOD SEO — {time.strftime('%Y-%m-%d %H:%M')}\n")
        log_fh.write(f"Total: {total}  Vendor: {vendor_filter or 'ALL'}\n")

        for i, prod in enumerate(prods, 1):
            print(f"\n[{i}/{total}] Processing...")
            status = run_god(prod, progress, log_fh)
            if status == 'ok':     ok   += 1
            elif status == 'skip': skip += 1
            else:                  fail += 1
            if status != 'skip':
                time.sleep(3)

    print(f"\n{'='*65}")
    print(f"BATCH COMPLETE — OK:{ok}  FAILED:{fail}  SKIPPED:{skip}")
    print(f"Log: {log_path}")
    print(f"Progress: {PROGRESS_FILE}")
    if progress['failed']:
        print(f"\nFailed handles:")
        for f in progress['failed']:
            print(f"  {f}")

if __name__ == '__main__':
    main()
