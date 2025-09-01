#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Einstein Primary-Only Preserver (Trusted-Scan Patch)
====================================================
- Strict primary-host whitelist
- PDF-first; HTML->PDF only on primary hosts (e.g., Gutenberg)
- DOI + ADS (+ Unpaywall optional)
- Robust HTTP (headers, backoff, cookies)
- Optional Playwright browser-assisted downloads (--use-browser)
- NEW: --accept-scan-only + --trust-host HOST to accept scanned PDFs (no OCR) from trusted hosts

Install:
  pip install requests httpx beautifulsoup4 pandas tqdm pdfkit pdfminer.six pypdf
  pip install playwright
  playwright install chromium
  sudo apt-get install -y wkhtmltopdf

Usage:
  export ADS_TOKEN=...              # optional
  export UNPAYWALL_EMAIL=...        # optional
  python einstein_primary_preserver.py --biblio einstein_primary_ads.csv --out ./einstein_primary \
    --allow-licensed --accept-scan-only \
    --trust-host adsabs.harvard.edu --trust-host archive.org --trust-host echo.mpiwg-berlin.mpg.de
"""

import argparse, os, re, time, json, shutil, hashlib, sys, random
from typing import List, Dict, Optional
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import httpx
import pandas as pd
from bs4 import BeautifulSoup
from tqdm import tqdm

# PDF text extraction
from pdfminer.high_level import extract_text as pdf_extract_text
from PyPDF2 import PdfReader

# Optional renderers
HAVE_PDFKIT = False
HAVE_PLAYWRIGHT = False
try:
    import pdfkit
    HAVE_PDFKIT = shutil.which("wkhtmltopdf") is not None
except Exception:
    HAVE_PDFKIT = False
try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except Exception:
    HAVE_PLAYWRIGHT = False

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8,de;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

PRIMARY_WHITELIST = [
    "doi.org",
    "onlinelibrary.wiley.com",
    "ui.adsabs.harvard.edu",
    "adsabs.harvard.edu",
    "archive.org",
    "echo.mpiwg-berlin.mpg.de",
    "mpiwg-berlin.mpg.de",
    "digi.ub.uni-heidelberg.de",
    "heidicon.ub.uni-heidelberg.de",
    "retro.seals.ch",
    "e-periodica.ch",
    "e-rara.ch",
    "journals.aps.org",
    "projecteuclid.org",
    "jstor.org",
    "gallica.bnf.fr",
    "nobelprize.org",
    "nature.com",
    "scientificamerican.com",
    "link.springer.com",
    "springer.com",
    "gutenberg.org",
]

VENUE_KEYWORDS = [
    "Annalen der Physik",
    "Sitzungsberichte der Preußischen Akademie",
    "Physikalische Zeitschrift",
    "Zeitschrift für Physik",
    "Physical Review",
    "Reviews of Modern Physics",
    "Journal of the Franklin Institute",
    "Annals of Mathematics",
    "Canadian Journal of Mathematics",
    "Nature",
    "Science",
    "Comptes Rendus",
]

def is_primary_url(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(netloc.endswith(host) or host in netloc for host in PRIMARY_WHITELIST)

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def safe_slug(s: str, maxlen=140) -> str:
    s = re.sub(r"[^\w\s\-\.]", "", s, flags=re.UNICODE).strip()
    s = re.sub(r"\s+", "_", s)
    return (s[:maxlen] or "untitled")

def guess_pdf(url: str) -> bool:
    p = url.lower()
    return p.endswith(".pdf") or "pdf" in p or "format=pdf" in p

def backoff_sleep(attempt):
    base = min(60, (2 ** attempt))
    time.sleep(base + random.uniform(0, 1.0))

def resolve_doi(doi: str, client: httpx.Client, insecure: bool) -> Optional[str]:
    if not doi: return None
    try:
        r = client.get(f"https://doi.org/{doi}", follow_redirects=True, timeout=60.0, verify=not insecure)
        if r.status_code < 400:
            return str(r.url)
    except Exception:
        return None
    return None

def try_unpaywall(doi: str, client: httpx.Client) -> Optional[str]:
    email = os.environ.get("UNPAYWALL_EMAIL","").strip()
    if not email or not doi: return None
    try:
        r = client.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email}, timeout=30.0)
        if r.status_code >= 400: return None
        data = r.json()
        loc = data.get("best_oa_location") or {}
        pdf = loc.get("url_for_pdf") or ""
        if pdf and is_primary_url(pdf): return pdf
        for L in (data.get("oa_locations") or []):
            pdf = L.get("url_for_pdf") or ""
            if pdf and is_primary_url(pdf): return pdf
    except Exception:
        return None
    return None

def try_ads_pdf(bibcode: str) -> Optional[str]:
    token = os.environ.get("ADS_TOKEN","").strip()
    if not token or not bibcode: return None
    try:
        return f"https://ui.adsabs.harvard.edu/link_gateway/{bibcode}/PUB_PDF"
    except Exception:
        return None

def html_to_pdf(url: str, out_pdf: str) -> bool:
    if HAVE_PDFKIT:
        try:
            pdfkit.from_url(url, out_pdf, options={"quiet":"","page-size":"A4","encoding":"UTF-8","print-media-type":""})
            return os.path.exists(out_pdf) and os.path.getsize(out_pdf)>500
        except Exception:
            pass
    if HAVE_PLAYWRIGHT:
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
                page = b.new_page()
                page.goto(url, wait_until="networkidle", timeout=120000)
                page.pdf(path=out_pdf, format="A4", print_background=True)
                b.close()
            return os.path.exists(out_pdf) and os.path.getsize(out_pdf)>500
        except Exception:
            pass
    return False

def validate_pdf(path: str, source_url: Optional[str], accept_scan_only: bool, trusted_hosts: List[str], book: bool=False) -> Dict[str, bool]:
    try:
        reader = PdfReader(path)
        pages = len(reader.pages)
        # extract short prefix text
        text = pdf_extract_text(path, maxpages=min(3, pages)) or ""
        text_low = text.lower()
        text_len = len(text_low)
        checks = {
            "has_einstein": ("einstein" in text_low),
            "has_venue": any(v.lower() in text_low for v in VENUE_KEYWORDS),
            "page_sane": (1 <= pages <= 200) or (book and pages>50),
        }
        valid = checks["has_einstein"] and checks["page_sane"]
        # Relaxation for scanned PDFs from trusted primary hosts
        host = urlparse(source_url).netloc.lower() if source_url else ""
        is_trusted = any(host.endswith(h) or h in host for h in trusted_hosts)
        if accept_scan_only and is_trusted:
            # If there's effectively no OCR text but pages look like an article, accept.
            if (text_len < 100) and (2 <= pages <= 80):
                valid = True
        checks["valid_primary"] = valid
        checks["text_len"] = text_len
        checks["pages"] = pages
        checks["host"] = host
        return checks
    except Exception:
        return {"has_einstein": False, "has_venue": False, "page_sane": False, "valid_primary": False, "text_len": 0, "pages": 0, "host": ""}

def browser_fetch_pdf(url: str, out_pdf: str, timeout_ms=180000) -> bool:
    if not HAVE_PLAYWRIGHT:
        return False
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
            ctx = b.new_context(accept_downloads=True)
            page = ctx.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(url, wait_until="networkidle")
            # Try clicking PDF links/buttons
            selectors = ["a[href$='.pdf']", "a:has-text('PDF')", "button:has-text('PDF')", "a:has-text('Download')"]
            for sel in selectors:
                try:
                    with page.expect_download() as dl:
                        el = page.locator(sel).first
                        if el.count() > 0:
                            el.click()
                        else:
                            continue
                    download = dl.value
                    download.save_as(out_pdf)
                    b.close()
                    return os.path.exists(out_pdf) and os.path.getsize(out_pdf)>500
                except Exception:
                    pass
            # Fallback: print the HTML (last resort, not ideal for articles)
            page.pdf(path=out_pdf, format="A4", print_background=True)
            b.close()
            return os.path.exists(out_pdf) and os.path.getsize(out_pdf)>500
    except Exception:
        return False

def main():
    ap = argparse.ArgumentParser(description="Einstein Primary-Only Preserver (Trusted-Scan Patch)")
    ap.add_argument("--biblio", required=True, help="CSV/JSONL with title,year,doi,bibcode,url_hint")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--allow-licensed", action="store_true", help="Allow licensed primary hosts (JSTOR/Springer)")
    ap.add_argument("--use-browser", action="store_true", help="Use Playwright to capture real PDF downloads")
    ap.add_argument("--max-downloads", type=int, default=0, help="Limit number of items (0 = no limit)")
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verification (not recommended)")
    ap.add_argument("--accept-scan-only", action="store_true", help="Accept image-only PDFs from trusted hosts (if page count sane)")
    ap.add_argument("--trust-host", action="append", default=[], help="Mark a host as trusted for scan-only acceptance; can be repeated")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    ledger_csv = os.path.join(args.out, "ledger.csv")
    ledger_jsonl = os.path.join(args.out, "ledger.jsonl")
    quarantine_dir = os.path.join(args.out, "quarantine")
    os.makedirs(quarantine_dir, exist_ok=True)

    # Default trusted hosts
    trusted_hosts = set(args.trust_host or [])
    for h in ["adsabs.harvard.edu", "ui.adsabs.harvard.edu", "archive.org", "echo.mpiwg-berlin.mpg.de", "digi.ub.uni-heidelberg.de"]:
        trusted_hosts.add(h)
    trusted_hosts = list(trusted_hosts)

    # Load biblio
    if args.biblio.lower().endswith(".csv"):
        df = pd.read_csv(args.biblio)
        items = df.to_dict(orient="records")
    else:
        items = []
        with open(args.biblio, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))

    # HTTP clients
    client = httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=True, timeout=60.0)
    sess = requests.Session()
    sess.headers.update(DEFAULT_HEADERS)

    def resolve_candidates(rec):
        title = str(rec.get("title","")).strip()
        year = str(rec.get("year","")).strip()
        doi = str(rec.get("doi","")).strip()
        bibcode = str(rec.get("bibcode","")).strip()
        url_hint = str(rec.get("url_hint","")).strip()

        candidates = []
        if doi:
            u = resolve_doi(doi, client, args.insecure)
            if u: candidates.append(u)
            u2 = try_unpaywall(doi, client)
            if u2: candidates.append(u2)
        if bibcode:
            u = try_ads_pdf(bibcode)
            if u: candidates.append(u)
        if url_hint:
            candidates.append(url_hint)

        candidates = [u for u in candidates if u and is_primary_url(u)]
        if not candidates and bibcode:
            u = f"http://adsabs.harvard.edu/pdf/{bibcode}"
            if is_primary_url(u):
                candidates.append(u)
        return title, year, candidates

    def fetch_one(rec):
        title, year, candidates = resolve_candidates(rec)
        status = {
            "title": title, "year": year, "chosen_url": None, "saved_as": None, "sha256": None,
            "validated": False, "has_einstein": False, "has_venue": False, "page_sane": False, "note": "",
            "text_len": 0, "pages": 0, "host": ""
        }
        if not candidates:
            status["note"] = "no_primary_candidate"
            return status

        base = f"{year + '_' if year else ''}{safe_slug(title)}".strip("_") or "einstein_pub"
        out_pdf = os.path.join(args.out, base + ".pdf")

        for url in candidates:
            if (("jstor.org" in url or "springer" in url) and not args.allow_licensed):
                continue
            status["chosen_url"] = url

            for attempt in range(args.retries):
                try:
                    r = sess.get(url, timeout=args.timeout, allow_redirects=True, stream=True, verify=not args.insecure)
                    ctype = (r.headers.get("Content-Type","").lower())
                    final = r.url
                    if r.status_code in (429, 403, 500, 502, 503, 504):
                        backoff_sleep(attempt); continue

                    if "pdf" in ctype or guess_pdf(final):
                        with open(out_pdf, "wb") as f:
                            for chunk in r.iter_content(65536):
                                if chunk: f.write(chunk)
                        checks = validate_pdf(out_pdf, final, args.accept_scan_only, trusted_hosts, book=False)
                        if checks["valid_primary"]:
                            status.update({"saved_as": out_pdf, "sha256": sha256_file(out_pdf), **checks})
                            return status
                        else:
                            # quarantine but keep diagnostics
                            qpath = os.path.join(quarantine_dir, os.path.basename(out_pdf))
                            shutil.move(out_pdf, qpath)
                            status.update({"saved_as": qpath, **checks, "note": "quarantined_failed_validation"})
                            return status

                    elif "html" in ctype or "text/" in ctype or ctype == "":
                        # Browser-assisted?
                        if args.use_browser and HAVE_PLAYWRIGHT:
                            ok = browser_fetch_pdf(final, out_pdf)
                            if ok:
                                checks = validate_pdf(out_pdf, final, args.accept_scan_only, trusted_hosts, book=False)
                                if checks["valid_primary"]:
                                    status.update({"saved_as": out_pdf, "sha256": sha256_file(out_pdf), **checks})
                                    return status
                        # Gutenberg-case (book)
                        if "gutenberg.org" in final:
                            ok = html_to_pdf(final, out_pdf)
                            if ok:
                                checks = validate_pdf(out_pdf, final, args.accept_scan_only, trusted_hosts, book=True)
                                status.update({"saved_as": out_pdf, "sha256": sha256_file(out_pdf), **checks})
                                return status
                        status["note"] = "html_no_primary_pdf"
                        break
                    else:
                        status["note"] = f"unsupported_ctype:{ctype}"
                        break
                except Exception as e:
                    status["note"] = f"error:{e}"
                    backoff_sleep(attempt)
                    continue
        return status

    items_iter = items[:args.max_downloads] if args.max_downloads and args.max_downloads>0 else items
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as ex:
        futs = [ex.submit(fetch_one, rec) for rec in items_iter]
        for fut in tqdm(as_completed(futs), total=len(futs), desc="Primary-archiving"):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"note": f"fatal:{e}"})

    df_out = pd.DataFrame(results)
    df_out.to_csv(ledger_csv, index=False)
    with open(ledger_jsonl, "w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    ok = sum(1 for r in results if r.get("saved_as") and r.get("valid_primary"))
    print(f"[DONE] Validated OK: {ok} / {len(results)}. Ledger at {ledger_csv}")
    # Show a couple of diagnostics
    bad = [r for r in results if not r.get("valid_primary")]
    if bad[:5]:
        print("[DIAG] Examples of non-validated entries:")
        for r in bad[:5]:
            print(" -", r.get("title","?"), "| note:", r.get("note"), "| host:", r.get("host"), "| pages:", r.get("pages"), "| text_len:", r.get("text_len"))

if __name__ == "__main__":
    main()
