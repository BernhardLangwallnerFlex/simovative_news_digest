# Pipedrive Integration — Implementation Plan

> **Status:** PROPOSAL — pending teamlead decision (meeting 2026-04-28)
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow digest recipients to push individual articles into the team's shared Pipedrive account from the email, attached to the correct university Organization.

**Architecture:** Each article in the HTML digest gets one or more "Add to Pipedrive" links — one per detected university. A click hits a small Azure Function (HMAC-signed URL), which looks up the article + canonical university, creates a Pipedrive Note attached to the corresponding Organization, and redirects the user to that Note in Pipedrive. Article payloads are persisted to Azure Blob so the Function can read them on click. University extraction is hardened by constraining the classifier's `entities.universities` output to a canonical catalog.

**Tech Stack:** Python 3.11, OpenAI API, Azure Blob Storage, Azure Functions (Python, Consumption plan), Pipedrive REST API v1, HMAC-SHA256 link signing.

---

## 1. Decision Context (read this before the meeting)

### 1.1 Why this is feasible

Three things make the integration tractable:

1. **The classifier already extracts universities** (`analysis.entities.universities`). Spot-check on 30 fresh articles: 63% had ≥1 university extracted; the misses are largely correct (HRK/BMBF policy pieces with no specific institution named).
2. **Pipedrive's Notes API supports attaching to Organizations.** A Note accepts HTML content; a `POST /v1/notes` with `org_id` + `content` is all we need per article.
3. **We already run on Azure** with Blob Storage, ACR, Container Apps. Adding an HTTP-triggered Function in the same resource group is incremental.

### 1.2 What the spot-check revealed (and how this plan handles each)

| Finding | Handling |
|---------|----------|
| `"LMU"`, `"LMU München"`, `"HM"`, `"Hochschule München"` returned as separate strings | Canonical catalog `universities.json` with `aliases[]`; classifier prompt is constrained to return canonical IDs |
| 37% of sampled articles had no university extracted — but most of those were genuinely about national-level news | "Add to Pipedrive (general)" fallback link with no `org_id` |
| Digest-relevant categories (Regulatory, Digital Strategy) had the lowest hit rate | Accept the limitation — those are inherently macro stories. A general-Note fallback is acceptable. |
| Some articles legitimately mention 2+ universities | One link per uni in the digest card |

### 1.3 Architecture, end-to-end

```
Pipeline run (Tue/Fri 10:00 CEST)
      │
      ▼
classify_articles  ──── prompt now lists canonical uni IDs ────▶  entities.university_ids = ["lmu_muenchen", ...]
      │
      ▼
persist classified articles → Azure Blob (news-digest-articles/<article_id>.json)
      │
      ▼
generate_html_digest → renders, per article, one signed link per university:
   https://<func>.azurewebsites.net/api/pipedrive/add?aid=<article_id>&uid=<uni_id>&sig=<hmac>
      │
      ▼
email sent ──▶ user clicks link
                       │
                       ▼
              Azure Function:
                 1. validate HMAC
                 2. idempotency check (does Note with this article_id+uid already exist?)
                 3. download article from blob
                 4. lookup uni_id → pipedrive_org_id
                 5. POST /v1/notes (HTML body, org_id)
                 6. 302 redirect to Pipedrive Note URL
```

### 1.4 Effort estimate

Roughly 2-3 engineer days, broken down:
- ~0.5d: canonical catalog seed + classifier prompt change + verification re-run
- ~0.5d: blob persistence of classified articles
- ~1d: Azure Function (code + Bicep + deploy script)
- ~0.5d: HTML link rendering + signing + integration test against staging Pipedrive

### 1.5 Operational cost

- Azure Function on Consumption plan: well within free tier at expected click volumes (a few dozen clicks/week).
- Pipedrive API: rate-limited but unlikely to be hit (each click = 1-2 API calls).
- New blob container `news-digest-articles`: ~50KB per article × hundreds per run × few months ≈ <1GB total. Negligible.

### 1.6 Risks / things that could go wrong

- **Pipedrive Organizations don't exist for all DACH unis.** The bootstrap script needs human review — someone has to either auto-create ~80 Orgs (noisy) or accept partial coverage.
- **Classifier returning bogus canonical IDs.** Mitigation: post-validation rejects unknown IDs; the digest falls back to a "general" link. Plan task includes this validation.
- **Email link tampering.** Mitigation: HMAC-SHA256 over `(aid, uid)` with a server-only secret. Without the secret no one can forge a link; only article+uni pairs Simovative generated will be accepted.
- **Repeated clicks creating duplicate Notes.** Mitigation: idempotency check — search Pipedrive for an existing Note tagged with `article_id` before creating.

### 1.7 Open questions for teamlead

1. **Pipedrive admin token** — who can issue an API token for the shared account? (Required for both the Function and the bootstrap script.)
2. **Existing Organizations** — are DACH universities already in the Pipedrive Org list? If not, are we OK with the bootstrap auto-creating them, or should it produce a CSV for manual review?
3. **Catalog scope** — start with the ~50 universities already in `UNIVERSITY_NEWS_URLS`, or aim for full DACH coverage (~400 institutions)? Recommendation: start with the 50, expand on demand.
4. **Click-to-push vs auto-push** — this plan implements click-to-push (cleaner data hygiene). Pre-push every digest article would skip the Function entirely but pollutes Pipedrive. Confirm preference.
5. **Multi-uni rendering** — for an article mentioning 3 universities, render 3 separate links, or one dropdown? Plan assumes 3 separate links (simpler).
6. **Function hosting** — Azure Functions Python (this plan) or extend the existing Container Apps env? Functions is cheaper for sub-1k req/month and has native HTTP trigger; recommended.

---

## 2. File Structure

**New files:**
- `config/universities.json` — canonical catalog: id, canonical_name, aliases[], pipedrive_org_id (initially null)
- `src/processing/university_normalizer.py` — alias→canonical_id mapping logic
- `src/storage/article_store.py` — persist & retrieve full classified articles in blob
- `src/digest/pipedrive_links.py` — link generation + HMAC signing
- `functions/pipedrive_add/__init__.py` — Azure Function HTTP trigger
- `functions/pipedrive_add/function.json` — trigger binding
- `functions/host.json` — Functions runtime config
- `functions/requirements.txt` — Function dependencies
- `functions/pipedrive_client.py` — thin Pipedrive API wrapper
- `scripts/bootstrap_pipedrive_orgs.py` — interactive bootstrap to populate `pipedrive_org_id`s
- `tests/test_university_normalizer.py`
- `tests/test_pipedrive_links.py`
- `tests/test_pipedrive_function.py`

**Modified files:**
- `src/processing/classifier.py` — pass canonical IDs into prompt; rename `universities` → `university_ids`
- `main.py` — call `persist_classified_articles()` after classification
- `src/digest/html_generator.py` — render per-uni "Add to Pipedrive" links
- `config.py` — add `UNIVERSITIES_CATALOG_PATH`, `PIPEDRIVE_FUNCTION_URL`, `PIPEDRIVE_LINK_SECRET`, `AZURE_ARTICLES_CONTAINER`
- `infra/main.bicep` — add Function App, Storage Account share for Functions, App Insights, secrets
- `infra/deploy.sh` — add `--functions-only` mode; build & publish Function
- `requirements.txt` — add `pytest>=8.0.0` (dev), `requests-mock>=1.11.0` (dev)
- `.env` (and `infra/main.bicepparam`) — `PIPEDRIVE_API_TOKEN`, `PIPEDRIVE_LINK_SECRET`

---

## 3. Implementation Plan — Milestone A: Foundation

These tasks ship a stronger classifier + persisted articles. They're useful even if Pipedrive integration is later abandoned.

### Task A1: Create canonical universities catalog

**Files:**
- Create: `config/universities.json`

- [ ] **Step 1: Seed the catalog from existing config**

Source the canonical names from `UNIVERSITY_NEWS_URLS` in `config.py`. For each entry, derive an `id` (slug), a `canonical_name`, and a starter `aliases[]` list.

```json
[
  {
    "id": "lmu_muenchen",
    "canonical_name": "Ludwig-Maximilians-Universität München",
    "aliases": ["LMU", "LMU München", "Ludwig-Maximilians-Universität", "Universität München"],
    "region": "DE",
    "pipedrive_org_id": null
  },
  {
    "id": "tum",
    "canonical_name": "Technische Universität München",
    "aliases": ["TUM", "TU München", "Technische Universität München"],
    "region": "DE",
    "pipedrive_org_id": null
  },
  {
    "id": "hochschule_muenchen",
    "canonical_name": "Hochschule München",
    "aliases": ["HM", "HM München", "Munich University of Applied Sciences"],
    "region": "DE",
    "pipedrive_org_id": null
  },
  {
    "id": "uni_bamberg",
    "canonical_name": "Otto-Friedrich-Universität Bamberg",
    "aliases": ["Universität Bamberg", "Uni Bamberg"],
    "region": "DE",
    "pipedrive_org_id": null
  },
  {
    "id": "fau_erlangen",
    "canonical_name": "Friedrich-Alexander-Universität Erlangen-Nürnberg",
    "aliases": ["FAU", "FAU Erlangen", "Universität Erlangen-Nürnberg"],
    "region": "DE",
    "pipedrive_org_id": null
  }
]
```

Continue for all entries in `UNIVERSITY_NEWS_URLS`. Aim for ~50 entries. Aliases can be expanded later as misses surface.

- [ ] **Step 2: Commit**

```bash
git add config/universities.json
git commit -m "feat(catalog): seed canonical universities catalog from UNIVERSITY_NEWS_URLS"
```

### Task A2: University normalizer with alias lookup

**Files:**
- Create: `src/processing/university_normalizer.py`
- Test: `tests/test_university_normalizer.py`

- [ ] **Step 1: Add pytest to dev dependencies**

Edit `requirements.txt`, append:

```
# dev
pytest>=8.0.0
requests-mock>=1.11.0
```

Then `pip install -r requirements.txt` in `.venv`.

- [ ] **Step 2: Write failing test**

Create `tests/test_university_normalizer.py`:

```python
import pytest
from src.processing.university_normalizer import (
    UniversityCatalog,
    normalize_universities,
)


@pytest.fixture
def catalog(tmp_path):
    catalog_file = tmp_path / "universities.json"
    catalog_file.write_text("""[
      {"id": "lmu_muenchen", "canonical_name": "LMU München",
       "aliases": ["LMU", "Ludwig-Maximilians-Universität"], "pipedrive_org_id": null, "region": "DE"},
      {"id": "tum", "canonical_name": "TU München",
       "aliases": ["TUM", "Technische Universität München"], "pipedrive_org_id": null, "region": "DE"}
    ]""")
    return UniversityCatalog.load(catalog_file)


def test_canonical_name_resolves(catalog):
    assert catalog.resolve("LMU München") == "lmu_muenchen"


def test_alias_resolves(catalog):
    assert catalog.resolve("LMU") == "lmu_muenchen"
    assert catalog.resolve("TUM") == "tum"


def test_unknown_returns_none(catalog):
    assert catalog.resolve("Universität Atlantis") is None


def test_case_insensitive(catalog):
    assert catalog.resolve("lmu münchen") == "lmu_muenchen"


def test_normalize_universities_filters_unknown(catalog):
    raw = ["LMU", "Universität Atlantis", "TUM"]
    assert normalize_universities(raw, catalog) == ["lmu_muenchen", "tum"]


def test_normalize_universities_dedupes(catalog):
    assert normalize_universities(["LMU", "LMU München"], catalog) == ["lmu_muenchen"]
```

- [ ] **Step 3: Run test to verify failure**

Run: `cd /Users/bernhardlangwallner/Documents/05\ Coding/Simovative/news_digest && source .venv/bin/activate && pytest tests/test_university_normalizer.py -v`
Expected: ImportError / module not found.

- [ ] **Step 4: Implement the normalizer**

Create `src/processing/university_normalizer.py`:

```python
"""Maps free-form university name strings to canonical IDs from the catalog."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UniversityCatalog:
    """In-memory catalog with case-insensitive lookup by canonical name or alias."""

    _by_lookup: dict[str, str]  # lowercased name/alias -> canonical id
    entries: list[dict]

    @classmethod
    def load(cls, path: str | Path) -> "UniversityCatalog":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        lookup: dict[str, str] = {}
        for entry in data:
            uid = entry["id"]
            lookup[entry["canonical_name"].lower().strip()] = uid
            for alias in entry.get("aliases", []):
                lookup[alias.lower().strip()] = uid
        return cls(_by_lookup=lookup, entries=data)

    def resolve(self, name: str) -> str | None:
        return self._by_lookup.get((name or "").lower().strip())

    def all_ids(self) -> list[str]:
        return [e["id"] for e in self.entries]

    def org_id(self, uni_id: str) -> int | None:
        for e in self.entries:
            if e["id"] == uni_id:
                return e.get("pipedrive_org_id")
        return None

    def canonical_name(self, uni_id: str) -> str | None:
        for e in self.entries:
            if e["id"] == uni_id:
                return e["canonical_name"]
        return None


def normalize_universities(raw_names: list[str], catalog: UniversityCatalog) -> list[str]:
    """Resolve a list of raw university names to canonical IDs.

    Unknown names are dropped. Result is deduplicated, order preserved.
    """
    seen: set[str] = set()
    result: list[str] = []
    for name in raw_names or []:
        uid = catalog.resolve(name)
        if uid and uid not in seen:
            seen.add(uid)
            result.append(uid)
    return result
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_university_normalizer.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add src/processing/university_normalizer.py tests/test_university_normalizer.py requirements.txt
git commit -m "feat(processing): add UniversityCatalog with alias-based normalization"
```

### Task A3: Constrain classifier output to canonical IDs

**Files:**
- Modify: `src/processing/classifier.py`
- Modify: `main.py:42-48` (catalog warm-up)
- Modify: `config.py` (add `UNIVERSITIES_CATALOG_PATH`)

- [ ] **Step 1: Add config path**

Edit `config.py`, add near the other config constants:

```python
UNIVERSITIES_CATALOG_PATH = "config/universities.json"
```

- [ ] **Step 2: Update classifier prompt and parsing**

In `src/processing/classifier.py`, change the `USER_PROMPT_TEMPLATE` to instruct the model to return canonical IDs only:

```python
USER_PROMPT_TEMPLATE = """\
Classify the following German university-sector news article.

Title: {title}

Text: {text}

Allowed primary categories: {categories}

Allowed university IDs (return ONLY IDs from this list; if none apply, return []):
{university_ids}

Output a JSON object with exactly these fields:
- "primary_category": exactly one of the allowed categories above
- "secondary_tags": list of 0-5 short descriptive labels (strings)
- "relevance_score": float 0.0-1.0
- "priority_score": float 0.0-1.0
- "confidence_score": float 0.0-1.0
- "entities": object with keys "university_ids" (list of IDs from the allowed list above), \
"persons", "roles", "vendors", "technologies", "regions" — each a list of strings
- "signal_summary": 1-2 sentence German summary
- "sales_relevance": 1 sentence explaining sales relevance

Output STRICT JSON only."""
```

Update `classify_article(article, client, model, catalog)` to accept the catalog and pass `university_ids=", ".join(catalog.all_ids())` into the format call. After parsing, validate that returned `university_ids` are a subset of `catalog.all_ids()`; drop any unknowns silently. Also fall through `normalize_universities()` against any free-form names the model may still emit under a legacy `universities` key, so the field is always populated:

```python
from src.processing.university_normalizer import UniversityCatalog, normalize_universities

# inside classify_article, after _validate_llm_output passes:
entities = data.get("entities", {})
returned_ids = [
    uid for uid in entities.get("university_ids", []) if uid in catalog._by_lookup.values()
]
# Backstop: if the model returned free-form names instead, normalize them
if not returned_ids and entities.get("universities"):
    returned_ids = normalize_universities(entities["universities"], catalog)
entities["university_ids"] = returned_ids
entities.pop("universities", None)
article["analysis"]["entities"] = entities
```

Update `classify_articles()` signature to load the catalog once and pass to each worker:

```python
def classify_articles(articles: list[dict]) -> list[dict]:
    from config import CLASSIFIER_WORKERS, UNIVERSITIES_CATALOG_PATH
    catalog = UniversityCatalog.load(UNIVERSITIES_CATALOG_PATH)
    # ... pass catalog into _classify_one and classify_article
```

- [ ] **Step 3: Update normalizer to use the new field**

Edit `src/processing/normalizer.py:107-114`. Change the empty entities scaffold:

```python
"entities": {
    "university_ids": [],
    "persons": [],
    "roles": [],
    "vendors": [],
    "technologies": [],
    "regions": [],
},
```

- [ ] **Step 4: Update HTML generator to read the new field**

Edit `src/digest/html_generator.py:123`. Replace:

```python
universities = ", ".join(analysis.get("entities", {}).get("universities", []))
```

with:

```python
from config import UNIVERSITIES_CATALOG_PATH
from src.processing.university_normalizer import UniversityCatalog
_catalog = UniversityCatalog.load(UNIVERSITIES_CATALOG_PATH)  # module-level, not per-card

# inside loop:
uni_ids = analysis.get("entities", {}).get("university_ids", [])
universities = ", ".join(_catalog.canonical_name(uid) or uid for uid in uni_ids)
```

- [ ] **Step 5: Re-run the spot-check script**

Run: `python scripts/check_university_entities.py`

Expected: with the canonical-ID prompt, the "naming inconsistency" cases should collapse — e.g. an article should produce `university_ids: ["lmu_muenchen"]`, not `["LMU"]` or `["LMU München"]`. Hit rate should be similar (~60-70%) but every hit is now a clean ID.

If hit rate drops noticeably, the alias list is too narrow — expand `aliases[]` for the most-mentioned-but-missed names visible in the script output.

- [ ] **Step 6: Update the spot-check script**

Edit `scripts/check_university_entities.py` to print `university_ids` instead of free-form names. (Surface bug: a low hit rate now indicates either bad aliases or a real classification gap.)

- [ ] **Step 7: Commit**

```bash
git add src/processing/classifier.py src/processing/normalizer.py src/digest/html_generator.py config.py scripts/check_university_entities.py
git commit -m "feat(classifier): constrain entities.university_ids to canonical catalog"
```

### Task A4: Persist classified articles to blob

**Files:**
- Create: `src/storage/article_store.py`
- Modify: `main.py` (call after classification step)
- Modify: `config.py` (add `AZURE_ARTICLES_CONTAINER`)

- [ ] **Step 1: Add config**

Edit `config.py`:

```python
AZURE_ARTICLES_CONTAINER = "news-digest-articles"
```

- [ ] **Step 2: Implement article_store**

Create `src/storage/article_store.py`:

```python
"""Per-article persistence in Azure Blob Storage, keyed by article_id.

Used by the Pipedrive Function to read article content on click.
TTL: rely on Azure lifecycle management for cleanup (out of scope for v1).
"""

import logging

from src.storage.blob_store import download_json, upload_json

logger = logging.getLogger(__name__)


def _blob_name(article_id: str) -> str:
    # Article IDs are sha256 hex (64 chars). Shard by first 2 chars to avoid
    # large flat listings if the container grows.
    return f"{article_id[:2]}/{article_id}.json"


def save_article(article: dict, container: str) -> None:
    upload_json(container, _blob_name(article["article_id"]), article)


def load_article(article_id: str, container: str) -> dict | None:
    data = download_json(container, _blob_name(article_id))
    if data is None:
        return None
    if not isinstance(data, dict):
        logger.warning("Article %s: unexpected blob shape", article_id)
        return None
    return data


def save_articles(articles: list[dict], container: str) -> int:
    saved = 0
    for a in articles:
        try:
            save_article(a, container)
            saved += 1
        except Exception as e:
            logger.warning("Failed to save article %s: %s", a.get("article_id"), e)
    logger.info("Persisted %d/%d articles to %s", saved, len(articles), container)
    return saved
```

- [ ] **Step 3: Wire into main.py**

In `main.py`, after `save_processed(classified, "articles_classified.json", run_date)` (around line 148), before the history-update step, persist only the digest-relevant articles to keep blob usage tight:

```python
from src.storage.article_store import save_articles
from config import AZURE_ARTICLES_CONTAINER

# ... after classification, before digest filter:
save_articles(
    [a for a in classified if a["analysis"].get("processed")],
    AZURE_ARTICLES_CONTAINER,
)
```

- [ ] **Step 4: Manually verify a single run**

Run: `python main.py` (or temporarily reduce sources in `config.py` to keep it fast).

Then in Azure Portal or CLI:
```bash
az storage blob list --container-name news-digest-articles --account-name simovativedigest -o table | head
```

Expected: blobs sharded under 2-char prefixes, payloads parse as the canonical schema.

- [ ] **Step 5: Commit**

```bash
git add src/storage/article_store.py main.py config.py
git commit -m "feat(storage): persist classified articles to news-digest-articles blob"
```

### Task A5: Bicep — create articles container

**Files:**
- Modify: `infra/main.bicep`

- [ ] **Step 1: Add container resource (or document manual creation)**

The existing Bicep doesn't manage blob containers (they're created on first write by SDK if the storage account allows). Confirm this still works — `BlobClient.upload_blob` does NOT auto-create containers. So either (a) add the container in Bicep, or (b) precreate manually.

Add to `infra/main.bicep` (after the Job resource):

```bicep
// ── Articles container (per-article persistence for Pipedrive Function) ──
resource storageAccount 'Microsoft.Storage/storageAccounts@2024-01-01' existing = {
  name: 'simovativedigest'
}

resource articlesContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  name: '${storageAccount.name}/default/news-digest-articles'
  properties: {
    publicAccess: 'None'
  }
}
```

- [ ] **Step 2: Deploy**

```bash
./infra/deploy.sh --infra-only
```

Expected: container created without disturbing existing resources.

- [ ] **Step 3: Commit**

```bash
git add infra/main.bicep
git commit -m "infra: provision news-digest-articles blob container"
```

---

## 4. Implementation Plan — Milestone B: Pipedrive Integration

### Task B1: Pipedrive bootstrap script

**Files:**
- Create: `scripts/bootstrap_pipedrive_orgs.py`

This script lists existing Pipedrive Organizations, fuzzy-matches them against the catalog, prints unmatched entries, and updates `config/universities.json` with `pipedrive_org_id`s. It does NOT auto-create — humans review unmatched.

- [ ] **Step 1: Implement bootstrap**

Create `scripts/bootstrap_pipedrive_orgs.py`:

```python
"""Match Pipedrive Organizations to the canonical universities catalog.

Reads PIPEDRIVE_API_TOKEN from env. Fetches all orgs, fuzzy-matches each
catalog entry by canonical_name + aliases, updates config/universities.json
in place, and prints a report of unmatched entries.

Run interactively: python scripts/bootstrap_pipedrive_orgs.py
"""

import json
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "universities.json"
PIPEDRIVE_BASE = "https://api.pipedrive.com/v1"
TOKEN = os.getenv("PIPEDRIVE_API_TOKEN")
MIN_SIMILARITY = 0.85  # tighter = fewer false positives


def fetch_all_orgs() -> list[dict]:
    orgs: list[dict] = []
    start = 0
    while True:
        r = requests.get(
            f"{PIPEDRIVE_BASE}/organizations",
            params={"api_token": TOKEN, "start": start, "limit": 500},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        orgs.extend(body.get("data") or [])
        pagination = body.get("additional_data", {}).get("pagination", {})
        if not pagination.get("more_items_in_collection"):
            break
        start = pagination["next_start"]
    return orgs


def best_match(name_candidates: list[str], orgs: list[dict]) -> tuple[dict, float] | None:
    best: tuple[dict, float] | None = None
    for org in orgs:
        org_name = (org.get("name") or "").lower().strip()
        for cand in name_candidates:
            sim = SequenceMatcher(None, cand.lower().strip(), org_name).ratio()
            if best is None or sim > best[1]:
                best = (org, sim)
    if best and best[1] >= MIN_SIMILARITY:
        return best
    return None


def main():
    if not TOKEN:
        sys.exit("PIPEDRIVE_API_TOKEN not set in env")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    orgs = fetch_all_orgs()
    print(f"Fetched {len(orgs)} Pipedrive Organizations")

    matched = 0
    unmatched: list[str] = []
    for entry in catalog:
        if entry.get("pipedrive_org_id"):
            continue  # don't overwrite existing
        candidates = [entry["canonical_name"]] + entry.get("aliases", [])
        m = best_match(candidates, orgs)
        if m:
            entry["pipedrive_org_id"] = m[0]["id"]
            matched += 1
            print(f"  ✓ {entry['canonical_name']:50s} → '{m[0]['name']}' (id={m[0]['id']}, sim={m[1]:.2f})")
        else:
            unmatched.append(entry["canonical_name"])

    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nMatched: {matched}")
    print(f"Unmatched ({len(unmatched)}):")
    for name in unmatched:
        print(f"  · {name}")
    print("\nFor unmatched entries: either create the Org in Pipedrive manually, or extend aliases[] in config/universities.json and re-run.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the bootstrap (after teamlead provides PIPEDRIVE_API_TOKEN)**

```bash
PIPEDRIVE_API_TOKEN=xxx python scripts/bootstrap_pipedrive_orgs.py
```

Expected: report of matched/unmatched. `config/universities.json` has `pipedrive_org_id`s populated where matches were found.

- [ ] **Step 3: Commit (token NOT included)**

```bash
git add scripts/bootstrap_pipedrive_orgs.py config/universities.json
git commit -m "feat(pipedrive): bootstrap script + populate org_ids for matched universities"
```

### Task B2: HMAC link signing module

**Files:**
- Create: `src/digest/pipedrive_links.py`
- Test: `tests/test_pipedrive_links.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipedrive_links.py`:

```python
import os
import pytest
from src.digest.pipedrive_links import sign, verify, build_link

SECRET = "test-secret-do-not-use-in-prod"
FUNC_URL = "https://example.azurewebsites.net/api/pipedrive/add"


def test_sign_is_deterministic():
    assert sign("aid123", "lmu_muenchen", SECRET) == sign("aid123", "lmu_muenchen", SECRET)


def test_sign_differs_per_input():
    assert sign("aid123", "lmu", SECRET) != sign("aid123", "tum", SECRET)
    assert sign("aid123", "lmu", SECRET) != sign("aid124", "lmu", SECRET)


def test_verify_accepts_valid_signature():
    sig = sign("aid", "uid", SECRET)
    assert verify("aid", "uid", sig, SECRET) is True


def test_verify_rejects_tampered():
    sig = sign("aid", "uid", SECRET)
    assert verify("aid", "OTHER", sig, SECRET) is False
    assert verify("aid", "uid", sig + "x", SECRET) is False
    assert verify("aid", "uid", sig, "wrong-secret") is False


def test_build_link_contains_all_params():
    link = build_link(FUNC_URL, "aid123", "lmu_muenchen", SECRET)
    assert "aid=aid123" in link
    assert "uid=lmu_muenchen" in link
    assert "sig=" in link
    assert link.startswith(FUNC_URL)


def test_build_link_for_general_uses_empty_uid():
    link = build_link(FUNC_URL, "aid123", None, SECRET)
    assert "uid=" in link  # empty uid still present and signed
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_pipedrive_links.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/digest/pipedrive_links.py`:

```python
"""Generate HMAC-signed 'Add to Pipedrive' links for digest articles.

The Function endpoint validates the signature; without the secret no one
can forge a link, so only article+uni pairs Simovative generated will be
accepted by the webhook.
"""

import hashlib
import hmac
from urllib.parse import urlencode


def _payload(article_id: str, uni_id: str | None) -> bytes:
    return f"{article_id}|{uni_id or ''}".encode("utf-8")


def sign(article_id: str, uni_id: str | None, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), _payload(article_id, uni_id), hashlib.sha256).hexdigest()


def verify(article_id: str, uni_id: str | None, signature: str, secret: str) -> bool:
    expected = sign(article_id, uni_id, secret)
    return hmac.compare_digest(expected, signature)


def build_link(function_url: str, article_id: str, uni_id: str | None, secret: str) -> str:
    sig = sign(article_id, uni_id, secret)
    params = urlencode({"aid": article_id, "uid": uni_id or "", "sig": sig})
    return f"{function_url}?{params}"
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest tests/test_pipedrive_links.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/digest/pipedrive_links.py tests/test_pipedrive_links.py
git commit -m "feat(digest): HMAC-signed Pipedrive add links"
```

### Task B3: Render Pipedrive links into the digest HTML

**Files:**
- Modify: `src/digest/html_generator.py`
- Modify: `config.py` (add `PIPEDRIVE_FUNCTION_URL`, `PIPEDRIVE_LINK_SECRET`)

- [ ] **Step 1: Add config**

Edit `config.py`:

```python
PIPEDRIVE_FUNCTION_URL = _os.getenv(
    "PIPEDRIVE_FUNCTION_URL",
    "",  # empty → links not rendered
)
PIPEDRIVE_LINK_SECRET = _os.getenv("PIPEDRIVE_LINK_SECRET", "")
```

- [ ] **Step 2: Render links per uni in the article card**

Edit `src/digest/html_generator.py`. Add near the top:

```python
from config import PIPEDRIVE_FUNCTION_URL, PIPEDRIVE_LINK_SECRET, UNIVERSITIES_CATALOG_PATH
from src.digest.pipedrive_links import build_link
from src.processing.university_normalizer import UniversityCatalog

_catalog = UniversityCatalog.load(UNIVERSITIES_CATALOG_PATH)


def _render_pipedrive_links(article: dict) -> str:
    if not PIPEDRIVE_FUNCTION_URL or not PIPEDRIVE_LINK_SECRET:
        return ""
    aid = article["article_id"]
    uni_ids = article["analysis"].get("entities", {}).get("university_ids") or []
    if not uni_ids:
        link = build_link(PIPEDRIVE_FUNCTION_URL, aid, None, PIPEDRIVE_LINK_SECRET)
        return f'          <div class="pipedrive-actions"><a href="{escape(link)}">+ Add to Pipedrive (general)</a></div>\n'
    items = []
    for uid in uni_ids:
        name = escape(_catalog.canonical_name(uid) or uid)
        link = escape(build_link(PIPEDRIVE_FUNCTION_URL, aid, uid, PIPEDRIVE_LINK_SECRET))
        items.append(f'<a href="{link}">+ Add to Pipedrive ({name})</a>')
    return f'          <div class="pipedrive-actions">{" · ".join(items)}</div>\n'
```

In the card-rendering loop, insert `{_render_pipedrive_links(a)}` after `_render_also_reported(a)`. Add a CSS rule in the `<style>` block:

```css
.pipedrive-actions { margin-top: 8px; font-size: 0.85em; }
.pipedrive-actions a { color: #1a73e8; text-decoration: none; padding: 2px 6px; border: 1px solid #1a73e8; border-radius: 3px; display: inline-block; margin-right: 4px; }
.pipedrive-actions a:hover { background: #1a73e8; color: white; }
```

- [ ] **Step 3: Smoke-test the digest HTML**

Run: `python main.py` end-to-end (or load a stale `articles_classified.json` and call `generate_html_digest` directly in a REPL).

Open the generated `digest_<date>.html` in a browser. Expected: each card shows "+ Add to Pipedrive (Universität X)" buttons. Hover/click goes to the Function URL — at this stage the URL won't resolve yet, that's fine.

- [ ] **Step 4: Commit**

```bash
git add src/digest/html_generator.py config.py
git commit -m "feat(digest): render Pipedrive add buttons per university"
```

### Task B4: Pipedrive client wrapper

**Files:**
- Create: `functions/pipedrive_client.py`
- Test: `tests/test_pipedrive_client.py`

- [ ] **Step 1: Write failing tests with requests-mock**

Create `tests/test_pipedrive_client.py`:

```python
import pytest
import requests_mock as rm_lib
from functions.pipedrive_client import PipedriveClient


@pytest.fixture
def client():
    return PipedriveClient(token="test-token")


def test_create_note_attaches_org(client, requests_mock):
    requests_mock.post(
        "https://api.pipedrive.com/v1/notes",
        json={"success": True, "data": {"id": 42}},
    )
    note_id = client.create_note(content="<b>hi</b>", org_id=7)
    assert note_id == 42
    assert requests_mock.last_request.qs == {"api_token": ["test-token"]}
    body = requests_mock.last_request.json()
    assert body["content"] == "<b>hi</b>"
    assert body["org_id"] == 7


def test_search_notes_by_article_tag(client, requests_mock):
    requests_mock.get(
        "https://api.pipedrive.com/v1/notes",
        json={"success": True, "data": [{"id": 99, "content": "tag:abc"}]},
    )
    found = client.search_notes_containing("tag:abc")
    assert len(found) == 1
    assert found[0]["id"] == 99


def test_create_note_raises_on_api_failure(client, requests_mock):
    requests_mock.post(
        "https://api.pipedrive.com/v1/notes",
        status_code=401,
        json={"success": False, "error": "unauthorized"},
    )
    with pytest.raises(RuntimeError, match="unauthorized"):
        client.create_note(content="x", org_id=1)
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_pipedrive_client.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement client**

Create `functions/pipedrive_client.py`:

```python
"""Thin Pipedrive REST client. Used by the Azure Function on link click."""

import requests

BASE = "https://api.pipedrive.com/v1"


class PipedriveClient:
    def __init__(self, token: str, timeout: int = 15):
        self._token = token
        self._timeout = timeout

    def create_note(self, content: str, org_id: int | None = None) -> int:
        body: dict = {"content": content}
        if org_id is not None:
            body["org_id"] = org_id
        r = requests.post(
            f"{BASE}/notes",
            params={"api_token": self._token},
            json=body,
            timeout=self._timeout,
        )
        if r.status_code != 200:
            data = r.json() if r.content else {}
            raise RuntimeError(f"Pipedrive note create failed: {data.get('error', r.status_code)}")
        return r.json()["data"]["id"]

    def search_notes_containing(self, needle: str) -> list[dict]:
        """Idempotency helper: notes containing a tag string in their content.

        Pipedrive's /notes endpoint supports ?term-style filtering only via
        /searchResults. For now do a small list-and-filter, paginated.
        """
        out: list[dict] = []
        start = 0
        while True:
            r = requests.get(
                f"{BASE}/notes",
                params={"api_token": self._token, "start": start, "limit": 100},
                timeout=self._timeout,
            )
            r.raise_for_status()
            body = r.json()
            for note in body.get("data") or []:
                if needle in (note.get("content") or ""):
                    out.append(note)
            pag = body.get("additional_data", {}).get("pagination", {})
            if not pag.get("more_items_in_collection"):
                return out
            start = pag["next_start"]
```

> **Note for the engineer:** `search_notes_containing` paginates the entire Notes list — fine for tens-of-thousands of notes but won't scale to millions. If Pipedrive volume grows, switch to `/itemSearch` (full-text) or store our own idempotency map in blob.

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_pipedrive_client.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add functions/pipedrive_client.py tests/test_pipedrive_client.py
git commit -m "feat(pipedrive): thin REST client with create_note + search_notes_containing"
```

### Task B5: Azure Function HTTP trigger

**Files:**
- Create: `functions/host.json`
- Create: `functions/requirements.txt`
- Create: `functions/pipedrive_add/function.json`
- Create: `functions/pipedrive_add/__init__.py`
- Test: `tests/test_pipedrive_function.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipedrive_function.py`:

```python
import json
from unittest.mock import MagicMock, patch

import pytest

from functions.pipedrive_add import handler


def _req(aid="aid1", uid="lmu_muenchen", sig="bad"):
    req = MagicMock()
    req.params = {"aid": aid, "uid": uid, "sig": sig}
    return req


def test_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("PIPEDRIVE_LINK_SECRET", "secret")
    monkeypatch.setenv("PIPEDRIVE_API_TOKEN", "t")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "fake")
    monkeypatch.setenv("UNIVERSITIES_CATALOG_PATH", "config/universities.json")
    resp = handler(_req(sig="forged"))
    assert resp.status_code == 403


def test_unknown_uni_returns_400(monkeypatch):
    monkeypatch.setenv("PIPEDRIVE_LINK_SECRET", "secret")
    from src.digest.pipedrive_links import sign
    sig = sign("aid1", "uni-does-not-exist", "secret")
    resp = handler(_req(uid="uni-does-not-exist", sig=sig))
    assert resp.status_code == 400


def test_idempotency_returns_existing_note_url(monkeypatch):
    monkeypatch.setenv("PIPEDRIVE_LINK_SECRET", "secret")
    monkeypatch.setenv("PIPEDRIVE_API_TOKEN", "t")
    from src.digest.pipedrive_links import sign
    sig = sign("aid1", "lmu_muenchen", "secret")
    with patch("functions.pipedrive_add.PipedriveClient") as Pd, \
         patch("functions.pipedrive_add.load_article", return_value={"article_id": "aid1", "content": {"title": "T", "raw_text": "..."}, "source": {"url": "https://x"}, "analysis": {"primary_category": "Funding & Investment Signals", "signal_summary": "s", "sales_relevance": "r"}}), \
         patch("functions.pipedrive_add.UniversityCatalog") as Cat:
        Cat.load.return_value.org_id.return_value = 7
        Cat.load.return_value.canonical_name.return_value = "LMU"
        Pd.return_value.search_notes_containing.return_value = [{"id": 555}]
        resp = handler(_req(sig=sig))
    assert resp.status_code == 302
    assert "555" in resp.headers["Location"]


def test_creates_note_when_none_exists(monkeypatch):
    monkeypatch.setenv("PIPEDRIVE_LINK_SECRET", "secret")
    monkeypatch.setenv("PIPEDRIVE_API_TOKEN", "t")
    from src.digest.pipedrive_links import sign
    sig = sign("aid1", "lmu_muenchen", "secret")
    with patch("functions.pipedrive_add.PipedriveClient") as Pd, \
         patch("functions.pipedrive_add.load_article", return_value={"article_id": "aid1", "content": {"title": "T", "raw_text": "..."}, "source": {"url": "https://x", "name": "n"}, "analysis": {"primary_category": "Funding & Investment Signals", "signal_summary": "s", "sales_relevance": "r"}}), \
         patch("functions.pipedrive_add.UniversityCatalog") as Cat:
        Cat.load.return_value.org_id.return_value = 7
        Cat.load.return_value.canonical_name.return_value = "LMU"
        Pd.return_value.search_notes_containing.return_value = []
        Pd.return_value.create_note.return_value = 999
        resp = handler(_req(sig=sig))
    assert resp.status_code == 302
    assert "999" in resp.headers["Location"]
    Pd.return_value.create_note.assert_called_once()
    call_kwargs = Pd.return_value.create_note.call_args.kwargs
    assert call_kwargs["org_id"] == 7
    assert "tag:aid1" in call_kwargs["content"]
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest tests/test_pipedrive_function.py -v`
Expected: ImportError on `functions.pipedrive_add`.

- [ ] **Step 3: Implement Function**

Create `functions/host.json`:

```json
{
  "version": "2.0",
  "extensionBundle": {
    "id": "Microsoft.Azure.Functions.ExtensionBundle",
    "version": "[4.*, 5.0.0)"
  }
}
```

Create `functions/requirements.txt`:

```
azure-functions
azure-storage-blob>=12.19.0
requests>=2.31.0
```

Create `functions/pipedrive_add/function.json`:

```json
{
  "scriptFile": "__init__.py",
  "bindings": [
    {
      "authLevel": "anonymous",
      "type": "httpTrigger",
      "direction": "in",
      "name": "req",
      "methods": ["get"],
      "route": "pipedrive/add"
    },
    {
      "type": "http",
      "direction": "out",
      "name": "$return"
    }
  ]
}
```

Create `functions/pipedrive_add/__init__.py`:

```python
"""HTTP-triggered Function: validates HMAC, creates Pipedrive Note, redirects."""

import logging
import os
import sys
from html import escape

import azure.functions as func

# Add repo root for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.digest.pipedrive_links import verify
from src.processing.university_normalizer import UniversityCatalog
from src.storage.article_store import load_article
from functions.pipedrive_client import PipedriveClient

CATALOG_PATH = os.getenv("UNIVERSITIES_CATALOG_PATH", "config/universities.json")
ARTICLES_CONTAINER = os.getenv("AZURE_ARTICLES_CONTAINER", "news-digest-articles")
PIPEDRIVE_BASE_UI = "https://app.pipedrive.com"

logger = logging.getLogger("pipedrive_add")


def _build_note_html(article: dict, uni_name: str | None) -> str:
    a = article["analysis"]
    src = article["source"]
    parts = [
        f"<b>{escape(article['content'].get('title') or '')}</b><br>",
        f"<i>{escape(a.get('primary_category') or '')}</i>",
    ]
    if uni_name:
        parts.append(f" — {escape(uni_name)}")
    parts.append("<br><br>")
    if a.get("signal_summary"):
        parts.append(f"{escape(a['signal_summary'])}<br><br>")
    if a.get("sales_relevance"):
        parts.append(f"<i>Sales relevance:</i> {escape(a['sales_relevance'])}<br><br>")
    if src.get("url"):
        parts.append(f'Source: <a href="{escape(src["url"])}">{escape(src.get("name") or src["url"])}</a><br>')
    parts.append(f"<br><small>tag:{article['article_id']}</small>")
    return "".join(parts)


def handler(req: func.HttpRequest) -> func.HttpResponse:
    aid = req.params.get("aid", "")
    uid = req.params.get("uid", "") or None
    sig = req.params.get("sig", "")
    secret = os.getenv("PIPEDRIVE_LINK_SECRET", "")

    if not secret or not aid or not sig or not verify(aid, uid, sig, secret):
        return func.HttpResponse("Forbidden", status_code=403)

    catalog = UniversityCatalog.load(CATALOG_PATH)
    org_id: int | None = None
    uni_name: str | None = None
    if uid:
        if uid not in catalog.all_ids():
            return func.HttpResponse("Unknown university", status_code=400)
        org_id = catalog.org_id(uid)
        uni_name = catalog.canonical_name(uid)

    article = load_article(aid, ARTICLES_CONTAINER)
    if not article:
        return func.HttpResponse("Article not found", status_code=404)

    token = os.getenv("PIPEDRIVE_API_TOKEN", "")
    if not token:
        return func.HttpResponse("Server misconfigured", status_code=500)

    pd = PipedriveClient(token=token)
    tag = f"tag:{aid}"
    existing = pd.search_notes_containing(tag)
    if existing:
        note_id = existing[0]["id"]
    else:
        note_id = pd.create_note(content=_build_note_html(article, uni_name), org_id=org_id)

    return func.HttpResponse(
        "",
        status_code=302,
        headers={"Location": f"{PIPEDRIVE_BASE_UI}/note/{note_id}"},
    )


# Module-level export expected by Azure Functions
main = handler
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_pipedrive_function.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add functions/ tests/test_pipedrive_function.py
git commit -m "feat(functions): pipedrive_add HTTP trigger with HMAC + idempotency"
```

### Task B6: Bicep — provision Function App + secrets

**Files:**
- Modify: `infra/main.bicep`
- Modify: `infra/main.bicepparam` (params, secrets)

- [ ] **Step 1: Add Function App resources to Bicep**

Append to `infra/main.bicep`:

```bicep
@secure()
@description('Pipedrive API token (shared team account)')
param pipedriveApiToken string

@secure()
@description('Secret used to sign HMAC links in digest emails')
param pipedriveLinkSecret string

// ── Storage account already exists from milestone A; reuse for Function ──

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'simovativedigest-func-plan'
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'  // Consumption
    tier: 'Dynamic'
  }
  properties: {
    reserved: true  // Linux
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: 'simovativedigest-pipedrive'
  location: location
  kind: 'functionapp,linux'
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: azureStorageConnectionString }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AZURE_STORAGE_CONNECTION_STRING', value: azureStorageConnectionString }
        { name: 'PIPEDRIVE_API_TOKEN', value: pipedriveApiToken }
        { name: 'PIPEDRIVE_LINK_SECRET', value: pipedriveLinkSecret }
        { name: 'AZURE_ARTICLES_CONTAINER', value: 'news-digest-articles' }
        { name: 'UNIVERSITIES_CATALOG_PATH', value: 'config/universities.json' }
      ]
    }
  }
}

output functionUrl string = 'https://${functionApp.properties.defaultHostName}/api/pipedrive/add'
```

Also propagate `PIPEDRIVE_FUNCTION_URL` and `PIPEDRIVE_LINK_SECRET` into the existing Container Apps Job env block so the digest renders signed links:

```bicep
// inside the job's env array:
{ name: 'PIPEDRIVE_FUNCTION_URL', value: 'https://${functionApp.properties.defaultHostName}/api/pipedrive/add' }
{ name: 'PIPEDRIVE_LINK_SECRET', secretRef: 'pipedrive-link-secret' }
```

Add `pipedriveLinkSecret` to the job's `secrets` array similar to other secrets.

- [ ] **Step 2: Update bicepparam**

Add to `infra/main.bicepparam` (already gitignored):

```bicep
param pipedriveApiToken = readEnvironmentVariable('PIPEDRIVE_API_TOKEN')
param pipedriveLinkSecret = readEnvironmentVariable('PIPEDRIVE_LINK_SECRET')
```

And to `.env`:

```
PIPEDRIVE_API_TOKEN=...
PIPEDRIVE_LINK_SECRET=<random 32-byte hex>
```

Generate the secret once: `python -c "import secrets; print(secrets.token_hex(32))"`

- [ ] **Step 3: Deploy infra**

```bash
./infra/deploy.sh --infra-only
```

Expected: Function App created. Note the `functionUrl` output.

- [ ] **Step 4: Commit**

```bash
git add infra/main.bicep
git commit -m "infra: provision pipedrive Function App + propagate link config to digest job"
```

### Task B7: Function deploy script

**Files:**
- Modify: `infra/deploy.sh`

- [ ] **Step 1: Add `--functions-only` mode**

Edit `infra/deploy.sh`. Add a new mode that runs `func azure functionapp publish simovativedigest-pipedrive --python` from the `functions/` directory. (Requires the Functions Core Tools installed locally; document this.)

```bash
deploy_functions() {
    echo "==> Publishing Functions..."
    cd "$(dirname "$0")/../functions"
    func azure functionapp publish simovativedigest-pipedrive --python
}
```

Wire it into the existing argument parser; `--functions-only` calls `deploy_functions` and exits.

- [ ] **Step 2: Run it**

```bash
./infra/deploy.sh --functions-only
```

Expected: Function visible in Azure Portal under `simovativedigest-pipedrive`. Hit it once with a forged sig:

```bash
curl -i "https://simovativedigest-pipedrive.azurewebsites.net/api/pipedrive/add?aid=test&uid=lmu_muenchen&sig=forged"
```

Expected: HTTP 403.

- [ ] **Step 3: End-to-end smoke test**

Manually generate a valid link locally:

```bash
python -c "
from src.digest.pipedrive_links import build_link
import os
print(build_link('https://simovativedigest-pipedrive.azurewebsites.net/api/pipedrive/add',
                 '<an article_id from a recent run>', 'lmu_muenchen', os.environ['PIPEDRIVE_LINK_SECRET']))
"
```

Open the printed URL in a browser. Expected: 302 to Pipedrive, Note created on LMU's Organization. A second click goes to the same Note (idempotency).

- [ ] **Step 4: Commit**

```bash
git add infra/deploy.sh
git commit -m "infra(deploy): add --functions-only mode for pipedrive Function publish"
```

---

## 5. Implementation Plan — Milestone C: Production rollout

### Task C1: Run the next pipeline manually with the new digest

- [ ] **Step 1: Trigger the job manually**

```bash
az containerapp job start --name news-digest-job -g simovativedigest
```

- [ ] **Step 2: Inspect the rendered email**

Open the latest digest in Outlook / your client. Click one "Add to Pipedrive" link.

Expected: lands on the freshly created Pipedrive Note.

- [ ] **Step 3: Confirm with the team**

Have one or two recipients click links in their actual email and confirm Notes appear under the right Organization.

### Task C2: Add basic Function logging / alerting

- [ ] **Step 1: Wire Function App into existing Log Analytics workspace**

Add `appInsights` resource to Bicep (linked to the existing `simovativedigest-logs` workspace). Add `APPLICATIONINSIGHTS_CONNECTION_STRING` to Function App settings.

- [ ] **Step 2: Add a basic KQL alert for 4xx/5xx spikes** (covered by Azure plugin's `azure-diagnostics` skill if needed).

### Task C3: Documentation

- [ ] **Step 1: Update CLAUDE.md**

Append a section under `## Architecture` describing:
- canonical universities catalog at `config/universities.json`
- the Function at `functions/pipedrive_add/`
- bootstrap script `scripts/bootstrap_pipedrive_orgs.py`
- env vars: `PIPEDRIVE_API_TOKEN`, `PIPEDRIVE_LINK_SECRET`, `PIPEDRIVE_FUNCTION_URL`

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document pipedrive integration architecture"
```

---

## 6. What's explicitly out of scope (v1)

- **Auto-creating Pipedrive Organizations** for unmatched catalog entries. v1 produces a list, humans decide.
- **Activities / Deals.** v1 only creates Notes. If the team wants article-driven Activities (call reminders), that's a follow-up.
- **Per-recipient Pipedrive accounts.** Confirmed shared account.
- **Removing duplicate Notes** if the same article touches multiple unis — v1 creates one Note per (article, uni) click. Acceptable noise.
- **Non-DACH universities.** Catalog stops at DACH for now.
- **Lifecycle management** on `news-digest-articles` blob. Add a TTL policy as a follow-up if storage grows.

---

## 7. Self-review notes (for the author of this plan)

- All file paths are explicit. ✓
- Each task has TDD steps where logic is non-trivial (normalizer, link signing, Pipedrive client, Function). ✓
- Bicep changes include all new params and propagation to existing Job. ✓
- Open questions for the teamlead are sectioned at the top, not buried. ✓
- Secret handling: `PIPEDRIVE_API_TOKEN` and `PIPEDRIVE_LINK_SECRET` go via `.env` + `bicepparam`, never committed. ✓
- HMAC verification uses `hmac.compare_digest` (timing-safe). ✓
- Idempotency: tag-search before create. Acceptable for current Pipedrive volume; called out as future scaling concern. ✓
