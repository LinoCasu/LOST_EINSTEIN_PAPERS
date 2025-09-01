#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, json, re, csv, argparse, requests
from typing import List, Dict, Optional, Tuple
import pandas as pd

BASE_URL = "https://api.adsabs.harvard.edu/v1/search/query"
QUERIES = [
    ('base',   '(author:"Einstein, A." OR author:"Einstein, Albert") year:1901-1955'),
    ('spaw',   'author:"Einstein" bibstem:SPAW year:1914-1932'),
    ('vdpg',   'author:"Einstein" (pub:"Verhandlungen der Deutschen Physikalischen Gesellschaft" OR bibstem:VDPG)'),
    ('natur',  'author:"Einstein" (pub:"Die Naturwissenschaften" OR bibstem:Natur)'),
    ('cras',   'author:"Einstein" (pub:"Comptes Rendus" OR bibstem:CRAS)'),
    ('jfi',    'author:"Einstein" pub:"Journal of the Franklin Institute"'),
    ('cjm',    'author:"Einstein" (pub:"Canadian Journal of Mathematics" OR bibstem:CJM)'),
    ('phyz',   'author:"Einstein" (pub:"Physikalische Zeitschrift" OR bibstem:PhyZ)'),
    ('zphy',   'author:"Einstein" (pub:"Zeitschrift für Physik" OR bibstem:ZPhy)'),
    ('nature', 'author:"Einstein" pub:"Nature"'),
    ('science','author:"Einstein" pub:"Science"'),
    ('rvmp',   'author:"Einstein" (pub:"Reviews of Modern Physics" OR bibstem:RvMP)'),
    ('sciam',  'author:"Einstein" pub:"Scientific American"'),
    ('annmath','author:"Einstein" pub:"Annals of Mathematics"'),
]
FIELDS = "bibcode,title,year,doi"

def get_token(cmd_token: Optional[str]) -> str:
    if cmd_token: return cmd_token.strip()
    tok = os.environ.get("ADS_TOKEN","").strip()
    if not tok: raise SystemExit("[ERR] ADS_TOKEN not provided. Pass --token or set ADS_TOKEN.")
    return tok

def ads_search(token: str, query: str, rows: int = 2000) -> Dict:
    r = requests.get(
        BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "rows": rows, "fl": FIELDS, "wt": "json"},
        timeout=60
    )
    if r.status_code != 200:
        raise SystemExit(f"[ERR] ADS query failed ({r.status_code}): {r.text[:300]}")
    return r.json()

def first_title(d):
    t = d.get("title", [])
    if isinstance(t, list):
        return (t[0] if t else "") or "Untitled"
    if t is None: return "Untitled"
    return str(t) or "Untitled"

def to_rows(data: Dict) -> List[Dict]:
    docs = (data.get("response") or {}).get("docs") or []
    out = []
    for d in docs:
        row = {
            "title": first_title(d),
            "year": str(d.get("year","") or ""),
            "bibcode": str(d.get("bibcode","") or ""),
            "doi": ( (d.get("doi") or [None])[0] if isinstance(d.get("doi"), list) else (d.get("doi") or "") ),
        }
        bc = row["bibcode"]
        row["url_hint"] = f"https://ui.adsabs.harvard.edu/link_gateway/{bc}/PUB_PDF" if bc else ""
        out.append(row)
    return out

def norm_title(x) -> str:
    s = "" if x is None else str(x)
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def year_int(y) -> Optional[int]:
    try: return int(str(y)[:4])
    except Exception: return None

def load_master(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in ["title","year","bibcode","doi","url","url_hint"]:
        if col not in df.columns: df[col] = None
    # robust coercion
    df["title"] = df["title"].astype(str).fillna("")
    df["year"]  = df["year"].astype(str).fillna("")
    df["bibcode"] = df["bibcode"].astype(str).replace("nan","")
    df["doi"]   = df["doi"].astype(str).replace("nan","").str.lower()
    df["title_norm"] = df["title"].apply(norm_title)
    df["year_int"]   = df["year"].apply(year_int)
    return df

def dedupe_rows(rows: List[Dict]) -> List[Dict]:
    seen_bib, seen_doi = set(), set()
    out = []
    for r in rows:
        b = (r.get("bibcode") or "").strip()
        d = (r.get("doi") or "").strip().lower()
        if b and b in seen_bib:  continue
        if d and d in seen_doi:  continue
        if b: seen_bib.add(b)
        if d: seen_doi.add(d)
        out.append(r)
    return out

def missing_vs_master(cands: List[Dict], master: pd.DataFrame) -> List[Dict]:
    have_bib = set([str(b) for b in master["bibcode"].dropna().astype(str) if str(b)!=""])
    have_doi = set([str(d).lower() for d in master["doi"].dropna().astype(str) if str(d)!=""])
    # fallback bucket by (normalized title, ±1y)
    base_buckets = set()
    for _, row in master.iterrows():
        t = norm_title(row.get("title",""))
        y = year_int(row.get("year"))
        for dy in (-1,0,1):
            base_buckets.add((t, None if y is None else y+dy))
    miss = []
    for r in cands:
        b = (r.get("bibcode") or "").strip()
        d = (r.get("doi") or "").strip().lower()
        t = norm_title(r.get("title",""))
        y = year_int(r.get("year"))
        if b and b in have_bib: continue
        if d and d in have_doi: continue
        if (t, y) in base_buckets: continue
        miss.append(r)
    return miss

def main():
    ap = argparse.ArgumentParser(description="Find Einstein items missing from master via ADS (primary-only bibcodes).")
    ap.add_argument("--master", required=True, help="Path to existing master catalog CSV (will NOT be modified)")
    ap.add_argument("--outdir", required=True, help="Output directory to write candidate lists")
    ap.add_argument("--token", default=None, help="ADS API token (otherwise read from env ADS_TOKEN)")
    args = ap.parse_args()

    token = get_token(args.token)
    os.makedirs(args.outdir, exist_ok=True)

    all_rows, log_lines = [], []
    for name, q in QUERIES:
        try:
            data = ads_search(token, q, rows=2000 if name=='base' else 1000)
            num = (data.get("response") or {}).get("numFound", 0)
            rows = to_rows(data)
            all_rows.extend(rows)
            log_lines.append(f"[{name}] numFound={num} rows_collected={len(rows)}")
        except Exception as e:
            log_lines.append(f"[{name}] ERROR: {e}")

    all_rows = dedupe_rows(all_rows)
    union_path = os.path.join(args.outdir, "ads_all_candidates.csv")
    with open(union_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title","year","bibcode","doi","url_hint"])
        w.writeheader(); w.writerows(all_rows)

    master = load_master(args.master)
    missing = missing_vs_master(all_rows, master)
    miss_path = os.path.join(args.outdir, "missing_only.csv")
    with open(miss_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title","year","bibcode","doi","url_hint"])
        w.writeheader(); w.writerows(missing)

    with open(os.path.join(args.outdir, "ads_queries.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    print(f"[OK] Union candidates: {len(all_rows)} -> {union_path}")
    print(f"[OK] Missing vs master: {len(missing)} -> {miss_path}")
    print(f"[LOG] {os.path.join(args.outdir, 'ads_queries.log')}")
if __name__ == "__main__":
    main()
