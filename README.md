# MiFicha v0.1 — prototype

MiFicha is a health app prototype that explains medical documents in plain language: lab results, narrative reports (for example a PET scan), and exam orders. For each document it says what each value means, how far out of range it is, what effect it could have on your health, and how urgently you should seek medical attention — without giving diagnoses.

## Self-hosting with Docker: code and data separate

This is the recommended setup for personal/family use, and the one the
private deployment uses: the code lives in this repo and in the Docker
image; the data lives outside the image, in a plain folder on the host.

```bash
git clone https://github.com/danlegion/mificha.git
cd mificha
cp .env.example .env
# Edit .env: GEMINI_API_KEY, SECRET_KEY (generate a random one) and
# BASE_URL (e.g. http://mini-pc:18473 if you use Tailscale).
# Leave SUPABASE_URL empty to use local SQLite.
docker compose up -d --build
```

Open the app at `http://<your-machine>:18473`, or only over Tailscale in a
private deployment. Do not port-forward your router to this port.

The data folder (by default `./mificha-data`, configurable with
`MIFICHA_DATA_DIR`) is the source of truth and is never committed to git
(it is in `.gitignore` and `.dockerignore`):

```
mificha-data/
  mificha.db         # SQLite index: users, documents, intakes, shares
  originales/*.pdf   # original uploaded PDFs, untouched
  quarantine/*.pdf   # uploads held aside during processing
```

The container is disposable: you can delete it and recreate it without
losing data, as long as you do not delete the data folder. Back up that
folder, not the container:

```bash
cp -r ./mificha-data /path/to/backup/
# or a plain sync to a bucket / Drive folder, e.g.:
rclone sync ./mificha-data remote:mificha-backup
```

Restoring is copying the folder back. The app can be rebuilt from it;
no cloud provider is needed to read the files (they are PDFs, SQLite
and, in the future, JSON/CSV per person). Without a Gemini key, the app
runs in local demo mode, with no network calls.

## Family members

The private/family version has no guest accounts. The account holder
creates family-member profiles under their own account — members are
profiles, not separate logins, so they need no email or password — and
assigns each upload to a member:

- On upload (signed in): pick a member, or type a new member's name to
  create one inline.
- In the vault (`/ficha`): create members, filter by member, and see
  documents grouped by member, unassigned last.
- On a saved document's detail page: reassign it to another member.
  Biomarker history on that page stays within the same member, so one
  person's values never mix with another's.

Each self-hosted instance has its own account holder and its own
members; there is no central family registry.

## Vaccine registry

Upload a vaccination record and MiFicha treats it as its own document
type (`vaccine_record`): it extracts each vaccine already received —
name, date administered, dose, lot, manufacturer, and provider — but
only as the document states them. The registry at `/vacunas` aggregates
those entries per family member, deduped by name + date + dose and
sorted newest first, with a link back to the source document for each
entry. It never computes next doses and never recommends vaccines: it
is only a record of what was already received.

License: GNU Affero General Public License v3.0, see `LICENSE`.

## Requirements

- Python 3.11 or later
- (Optional) API keys for real mode: Anthropic, Supabase, OCR provider

## Installation

```bash
cd ~/workspace/mificha
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## How to run

```bash
cd ~/workspace/mificha
source .venv/bin/activate
uvicorn app.main:app --reload --port 18473
```

The app is then at http://localhost:18473.

## Environment variables

| Variable | Required | Default (if empty) | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | No | demo mode | Gemini key (AI Studio). First option if present. |
| `GEMINI_MODEL` | No | `gemini-3.5-flash-lite` | Gemini model (verified on the free tier). |
| `ANTHROPIC_API_KEY` | No | demo mode | Anthropic key (alternative). With both empty, the explanation is local and deterministic, with no network calls. |
| `LLM_MODEL` | No | backend default | LLM model for Anthropic. |
| `SUPABASE_URL` | No | local SQLite | Supabase project URL (production). |
| `SUPABASE_ANON_KEY` | No | — | Anonymous key: only for Supabase Auth (magic links). With RLS enabled it no longer accesses the tables. |
| `SUPABASE_SERVICE_KEY` | Yes, with RLS enabled | — | service_role key (server only, never to the browser): data and Storage access. Required since RLS was enabled. |
| `OCR_PROVIDER` | No | `tesseract` | Local Tesseract (Spanish + English, inside the Docker image). |
| `DOCAI_PROCESSOR` | Fallback only | — | Google Document AI processor (`projects/.../processors/...`); only used if Tesseract extracts no useful text or clearly garbled text. |
| `DOCAI_CREDENTIALS_JSON` | Fallback only | — | Document AI service-account JSON. |
| `SECRET_KEY` | Yes | — | Session/token signing key. **Change it in production.** |
| `DATABASE_URL` / `STORAGE_DIR` | — | — | No longer read by the current code: SQLite lives in the data folder (`./data/mificha.db` locally, `./mificha-data/mificha.db` with Docker) and PDFs in `originales/` inside that same folder. |
| `PORT` | No | `18473` | Server port. |
| `BASE_URL` | No | `http://localhost:18473` | Public URL; builds the WhatsApp links. In production use your domain. |

## What works without keys (demo mode)

- Local deterministic explanation (no LLM): plain-language templates, no network calls.
- Development auth (no Supabase).
- Local SQLite in `./data/mificha.db` and PDFs in `./data/originales`.
- Scanned-page detection: pages are flagged as scanned (without OCR their text is not extracted).

## For production, connect

1. **Supabase**: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY` (and `DATABASE_URL` if you use Postgres directly).
2. **Gemini (or Anthropic)**: `GEMINI_API_KEY` (and `GEMINI_MODEL` if you want to pin the model) for the natural-language explanation. Alternative: `ANTHROPIC_API_KEY` + `LLM_MODEL`.
3. **OCR**: local Tesseract by default, with a garbled-text quality gate (`app/services/ocr_quality.py`): text that is clearly garbled is not saved as document text — it goes to the Google Document AI fallback instead, and if the fallback is also garbled (or not configured) the page stays marked as scanned/uncertain. The gate is character-level only and conservative: numbers, units, abbreviations, and dates all count as clean.

## How to run the tests

```bash
cd ~/workspace/mificha
python -m pytest -q
```

- The tests make no network calls and generate their PDFs in memory (PyMuPDF).
- `tests/test_explain_llm.py` tests the providers with the network mocked.
- Contracts covered: `tests/test_pdf_extract.py` (PDF extraction), `tests/test_urgency.py` (urgency levels), `tests/test_schemas.py` (Explanation model).

## Project structure

```
mificha/
├── app/
│   ├── main.py                 # FastAPI app (backend)
│   ├── schemas.py              # pydantic models for the Explanation
│   └── services/
│       ├── pdf_extract.py      # extract_document(pdf_bytes) -> dict
│       ├── urgency.py          # decide_urgency(signals) -> dict
│       └── explain.py          # explanation (local demo or LLM)
├── static/                     # frontend assets
├── templates/                  # views
├── tests/                      # pytest suite (contracts)
├── data/                       # SQLite + PDFs (local, not committed to git)
├── .env.example
├── requirements.txt
└── README.md
```

## What is implemented vs. what is a stub

- **Real**: text extraction from digital PDFs, scanned-page detection (fewer than 20 characters → scanned), urgency rules, Explanation schemas.
- **Stub / demo**: plain-language explanation without an LLM (deterministic templates); OCR of scans (only flagged until a provider is connected); dev auth and local SQLite.

## Hard rules

1. **Reference ranges come only from the document.** Never invent ranges or values.
2. **No diagnoses.** Values and findings are explained; the user is never told what disease they have.
3. **An order's priority is quoted literally** in the explanation; never invented.
4. **When critical data is uncertain, be conservative**: never `no_urgente`.
5. **Plain language**: out-of-range values are marked with how far off they are, with visible guidance on whether/when to seek medical attention.
