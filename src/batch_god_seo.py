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

# Products already GOD-SEO'd (body_backups = ground truth)
DONE_IDS = {8242398986314, 8242439422026, 8242493915210, 8247941988426, 8247942152266, 8247941890122}

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        return json.load(open(PROGRESS_FILE))
    return {"done": [], "failed": [], "skipped": []}

def save_progress(p):
    os.makedirs(REPORTS, exist_ok=True)
    json.dump(p, open(PROGRESS_FILE, 'w'), indent=2)

def get_all_products(vendor_filter=None):
    prods, pi = [], None
    while True:
        params = 'limit=250&fields=id,handle,title,vendor,product_type,status&status=active'
        if pi: params += f'&page_info={pi}'
        r = requests.get(f'https://{SHOP}/admin/api/2024-01/products.json?{params}',
            headers={'X-Shopify-Access-Token': TOKEN}, timeout=60)
        batch = r.json().get('products', [])
        prods.extend(batch)
        m = re.search(r'page_info=([^&>]+).*?rel="next"', r.headers.get('Link', ''))
        pi = m.group(1) if m else None
        if not pi: break

    # Filter out already done
    remaining = [p for p in prods if p['id'] not in DONE_IDS]

    if vendor_filter:
        remaining = [p for p in remaining if vendor_filter.lower() in p.get('vendor','').lower()]

    return remaining

def run_god(prod, progress, log_fh):
    handle = prod['handle']
    url    = f"{DOMAIN}/products/{handle}"

    # Skip if already done in this batch session
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
    cmd = [sys.executable, '-u', god_py, url, '--no-ai', '--no-backlinks']

    try:
        result = subprocess.run(
            cmd,
            capture_output=False,
            text=True,
            timeout=300,   # 5 min per product max
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

    # Support SHOPIFY_TOKEN env var (for GitHub Actions secrets)
    import os as _os
    env_token = _os.environ.get('SHOPIFY_TOKEN')
    if env_token:
        global TOKEN
        TOKEN = env_token

    print("⚡ BATCH GOD SEO ENGINE")
    print(f"  Resume: {resume}  |  Vendor filter: {vendor_filter or 'ALL'}  |  Chunk: {chunk or 'ALL'}")
    print("  Fetching products...")

    prods = get_all_products(vendor_filter)
    progress = load_progress() if resume else {"done": [], "failed": [], "skipped": []}

    # Chunk: split products into 6 chunks of ~41 each (~3.5 hrs/chunk, well under 6hr GH limit)
    if chunk:
        chunk_size = 41
        start = (chunk - 1) * chunk_size
        end   = start + chunk_size
        prods = prods[start:end]
        print(f"  Chunk {chunk}: products {start+1}–{start+len(prods)}")

    # Limit: test mode — process only N products
    if limit:
        prods = prods[:limit]
        print(f"  LIMIT MODE: running only first {limit} product(s)")

    # Sort: named brands first, Emirates Vapor (juices) last
    PRIORITY = ['GeekVape','Elfbar','NASTY','AL FAKHER','Aspire','FUMMO','Uwell',
                'Voopoo','Vaporesso','Air Bar','SMOK','MYLE','POD SALT']
    def sort_key(p):
        v = p.get('vendor','')
        for i, brand in enumerate(PRIORITY):
            if brand.lower() in v.lower(): return i
        return 999  # Emirates Vapor / unknown last

    prods.sort(key=sort_key)

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
            # Brief pause between products to avoid rate limits
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
