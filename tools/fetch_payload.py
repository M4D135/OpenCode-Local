#!/usr/bin/env python3
"""Download only SHA-256-pinned upstream artifacts. Never runs on the target PC."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / 'downloads'

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def fetch(item, offline=False):
    DOWNLOADS.mkdir(exist_ok=True)
    dest = DOWNLOADS / item['file']
    if dest.is_file() and dest.stat().st_size == item['size'] and digest(dest) == item['sha256']:
        print('VERIFIED ' + dest.name, flush=True)
        return dest
    if offline:
        raise RuntimeError('Missing or corrupt pinned download: ' + dest.name)
    partial = dest.with_name(dest.name + '.partial')
    request = urllib.request.Request(item['url'], headers={'User-Agent': 'OpenCodeLocal-Builder/1.0'})
    sha = hashlib.sha256()
    count = 0
    last = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as source, partial.open('wb') as target:
            while chunk := source.read(4 * 1024 * 1024):
                target.write(chunk)
                sha.update(chunk)
                count += len(chunk)
                if time.monotonic() - last > 15:
                    print(f'DOWNLOAD {dest.name}: {count / 1e6:.0f} MB', flush=True)
                    last = time.monotonic()
        if count != item['size'] or sha.hexdigest() != item['sha256']:
            raise RuntimeError('Size or SHA-256 mismatch: ' + dest.name)
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)
    print('VERIFIED ' + dest.name, flush=True)
    return dest

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Verify cached downloads without any network request')
    parser.add_argument('--with-linux-tests', action='store_true')
    args = parser.parse_args()
    items = json.loads((ROOT / 'config/artifacts.lock.json').read_text())
    if not args.with_linux_tests:
        items = [item for item in items if not item['file'].endswith('.tar.gz')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda item: fetch(item, args.offline), items))

if __name__ == '__main__':
    main()
