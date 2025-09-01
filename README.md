# Einstein Primary Preservation

**Mission:** safeguard access to Albert Einstein’s *original* publications (1901–1955) by assembling a verifiable, script‑reproducible index of primary sources and—where legally permitted—archiving scans. We focus on **Einstein’s own texts** (journal articles, academy reports, book chapters, proceedings, letters he published as articles). We explicitly avoid secondary commentary.

---

## Why some Einstein materials are offline or behind paywalls

Digital access to historical scientific literature is messy. Even for Einstein’s writings, availability has **fluctuated over time**. Common reasons:

* **Publisher rights & licensing.** Many journals retain copyright in the *edition* or the journal layout even when the scientific content is early 20th‑century. Access can depend on publisher policies, national copyright terms, and contracts.
* **Platform migrations & link rot.** Institutional sites and projects change URLs, catalogs, and hosting vendors. Old links die or redirect to new platforms that require sign‑in.
* **Embargoes & subscriptions.** Aggregators (publisher portals, JSTOR‑like services) may restrict PDFs to subscribers, even for historic issues, while still exposing metadata.
* **Robots and crawl restrictions.** Some archives block scripted access or deep crawling, which makes automated scholarly preservation brittle.
* **Institutional policies.** University archives can change access modes (open vs. gated) for digitized holdings or edited collections as licensing evolves.

**Our position.** Primary sources that underpin modern physics should remain accessible to scholars and the public. When foundational documents disappear behind paywalls or links simply vanish, it creates a serious risk for scholarship, reproducibility, and historical memory. In plain words: this feels like a **scientific scandal**. This repository is a constructive response: build a durable, verifiable path to the originals.

> We do not claim any institution is acting in bad faith; we are documenting the practical reality that access can regress and that independent, reproducible preservation matters.

---

## What this repository contains

* A **machine‑readable catalog** (`CSV`) of Einstein’s primary publications with: `title, year, bibcode, doi, url_hint`.
* **Stable primary links** wherever possible, preferring canonical hosts (ADS/Publisher/Library digitizations):

  * `adsabs.harvard.edu` / `ui.adsabs.harvard.edu` (ADS scans and link‑gateway)
  * publisher archives (e.g., APS journals)
  * national/library digitizations (e.g., Gallica/BnF, university libraries, ECHO/MPIWG, Heidelberg DIGI)
* **Provenance logs** (ledgers) with download host, page counts, text statistics, and checksums when files are legally mirrored.

**We exclude**: ResearchGate, Academia, blog mirrors, course sites, derivative PDFs, and commentary. When in doubt, we err on the side of *not* including a link.

---

## Ethics, legality, and scope

* We prioritize **links** to primary hosts. Where we make local copies, we do so only when **legally permitted** (public domain, explicit open license, or host terms allow it). If you are a rights holder and want a file removed, see the takedown policy below.
* Translations: we prefer the original‑language publication. We include official translations only if the source edition is itself a primary publication authored (or officially authorized) by Einstein.

---

## Reproduce the catalog (scripts)

These steps rebuild the catalog and fetch **only primary** items. You need Python 3.10+, `requests`, and `pandas`.

1. **Gather candidates from ADS (including venues that often get missed by generic queries):**

```bash
python einstein_missing_from_ads.py \
  --master ./catalog/master_catalog.csv \
  --outdir ./einstein_run \
  --token "$ADS_TOKEN"
```

This writes:

* `./einstein_run/ads_all_candidates.csv` (deduplicated union from ADS)
* `./einstein_run/missing_only.csv` (**only items not already in your catalog**)
* `./einstein_run/ads_queries.log` (diagnostics)

2. **Archive new primary items** (trusted hosts, scans allowed):

```bash
python einstein_primary_preserver.py \
  --biblio ./einstein_run/missing_only.csv \
  --out ./einstein_primary_new \
  --concurrency 4 --timeout 90 --retries 4 \
  --allow-licensed --accept-scan-only \
  --trust-host adsabs.harvard.edu --trust-host ui.adsabs.harvard.edu \
  --trust-host articles.adsabs.harvard.edu \
  --trust-host journals.aps.org --trust-host link.aps.org \
  --trust-host archive.org --trust-host gallica.bnf.fr \
  --trust-host digi.ub.uni-heidelberg.de
```

**Security note:** keep your `ADS_TOKEN` out of logs. Prefer `read -s` to input it interactively and rotate tokens regularly.

---

## What counts as “primary” here?

* **Journals:** *Annalen der Physik*, *Sitzungsberichte der Preußischen Akademie der Wissenschaften*, *Physikalische Zeitschrift*, *Zeitschrift für Physik*, *Nature*, *Science*, *Reviews of Modern Physics*, *Annals of Mathematics*, *Journal of the Franklin Institute*, *Canadian Journal of Mathematics*, and similar venues.
* **Proceedings & reports:** Solvay conference reports, academy notes, Festschrift contributions **authored by Einstein**.
* **Books & chapters** written by Einstein.

We aim to include **only** items authored by Einstein. Editorial commentary by others is out of scope.

---

## Known gaps & roadmap

* Some academy reports and short notices have incomplete metadata in aggregators. We iteratively extend venue‑specific queries and add library scans where available.
* We welcome pull requests with **verified** primary links (please include bibcodes/DOIs and host provenance).

---

## Takedown & contact

If you believe a file here infringes your rights, open an issue or email the maintainers with the URL and proof of ownership. We will review promptly and remove any material that is not legally hostable.

**Acknowledgment.** We stand on the shoulders of librarians, archivists, and the ADS team who have preserved and indexed much of this record. Our goal is to make scholarly access more robust—not to compete with, but to complement, institutional preservation.
