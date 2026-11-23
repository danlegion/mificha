# MiFicha

Your medical records are scattered — a lab result in your email, a
vaccine card in a drawer, a scan on a portal you can't log into
anymore. When you need one, you can't find it.

MiFicha keeps them all in one place, saved over time, and owned only
by you. Your lab results, your vaccine record, your scans — on your
own machine, in plain files you can read even without this app. So
when you need to check when your last tetanus booster was, or how
your cholesterol has moved over the last three years, the answer is
there.

And when a result comes back in words you don't understand, MiFicha
explains it in plain language: what each value means, which ones are
out of range and by how much, and how soon it's worth bringing up
with a doctor.

It never diagnoses, never prescribes, and never pretends to be a
doctor. It's a translator, not a clinician — and it says so on every
explanation.

You run it yourself, at home, on a small computer. The code is open
for anyone to read. This is an early version, built for one family's
real use and shared in case it helps yours.

## How it works

1. **Sign in and upload.** Hosted, sign-in is a magic link by email; self-hosted with the single-password gate, it's one username and password. Either way the first sign-in creates your account — there is no guest upload, and everything you upload is saved to your vault.
2. **The app reads the document.** Text comes straight out of the PDF; a scanned page goes through OCR (Tesseract first, Google Document AI if set up). A page that still can't be read is marked uncertain, never guessed at.
3. **An AI explains it in plain language.** Gemini, Claude, or OpenAI — whichever you chose — or the built-in demo explainer with no key. You get what each value means, which are out of range and by how much, and how soon to bring it up with a doctor.
4. **The explanation is checked first.** It must match a fixed format, and hard rules run over it: no invented reference ranges, no made-up dates, no diagnosis. Anything uncertain is flagged in the open.
5. **It lands in your vault, under the right person.** Pick the family member on upload (or add one on the spot). Biomarkers build a history per person, vaccines feed the registry, and you can reassign a document, download the original, or share a read-only link that expires in 7 days.

### Where your data lives

Self-hosted, everything is one folder on your machine,
`./mificha-data` (movable with `MIFICHA_DATA_DIR`). The files are
the source of truth, organized per person — deleting the app or
the container never destroys your content:

```
mificha-data/
  mificha.db                     # the index only — see below
  members/
    <member-id>/
      member.json                # who this person is
      biomarkers.csv             # this person's biomarker history
      documents/
        <document-id>/
          original.pdf           # your uploaded PDF, untouched
          explanation.json       # the plain-language explanation
          meta.json              # ids and dates, for the index
    unassigned/
      documents/<document-id>/...  # uploads with no member yet
  quarantine/*.pdf               # uploads held aside while they're
                                 # being processed
```

The originals are plain PDFs and the explanations are plain JSON —
readable without this app, forever. Each person's biomarker
history is a CSV you can open in any spreadsheet. The SQLite
database is only an index over those files: if it is ever lost,
rebuild it with `python -m app.file_store rebuild` and the vault
comes back. (Share links are the one exception: they are
short-lived and live only in the database.)

If you are upgrading from an older version, the old flat layout
(`originales/<id>.pdf` with everything in the database) migrates
itself: run `python -m app.file_store migrate` once and your
documents move into their members' folders, with the index
updated to point at the new locations.

The container holds no data: delete it, rebuild it, move it to
another machine — as long as you keep the folder, you keep
everything.

**Backing up** is copying that one folder. A plain copy works, or
sync it offsite:

```bash
cp -r ./mificha-data /path/to/backup/
# or to a bucket / Drive folder, e.g.:
rclone sync ./mificha-data remote:mificha-backup
```

where `remote` is any rclone destination — Google Drive, S3,
another machine. To restore, copy the folder back and start the
container. No cloud provider is needed to read your files back.

On the hosted deployment (Render + Supabase) the same split
applies, just in the cloud: originals in the Supabase `originales`
bucket, everything else in the Supabase database — back that up
with Supabase's own backups or exports, not with rclone. The
per-person file layout is a self-hosting feature; the hosted
deployment keeps its bucket storage unchanged.

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

Your data lives in `./mificha-data` (see "Where your data lives"
above) and is never committed to git (it is in `.gitignore` and
`.dockerignore`). The container is disposable: you can delete it and
recreate it without losing data, as long as you do not delete the
data folder. Back up that folder, not the container. Without an LLM
key, the app runs in local demo mode, with no network calls.

## Accounts

There is no guest access: uploading requires signing in, and every
upload is saved to that account's vault automatically. How you sign
in depends on the deployment:

- **Hosted (Supabase):** a magic link by email (`/entrar`). The first sign-in creates the account — there is no password and no separate sign-up page.
- **Self-hosted with the single-password gate:** one username and password (`/acceso`), set in `.env` as `APP_USERNAME` and `APP_PASSWORD`. The first login creates the one owner account. See "Single-password gate" below.
- **Self-hosted without the gate:** email only, no password and no verification — anyone who can reach the port can sign in as any email. Local development only; set the gate for any real use.

## Family members

The private/family version has no guest accounts. The account holder
creates family-member profiles under their own account — members are
profiles, not separate logins, so they need no email or password — and
assigns each upload to a member:

- On upload (signed in): pick a member, or type a new member's name to
  create one inline.
- In the vault (`/ficha`): create members and filter by member.
- On a saved document's detail page: reassign it to another member.
  Biomarker history on that page stays within the same member, so one
  person's values never mix with another's.

Each self-hosted instance has its own account holder and its own
members; there is no central family registry.

## Single-password gate (self-hosting)

A self-hosted instance is reachable by anyone on the network that can
reach its port, and the no-Supabase login is email-only with no
password — not real authentication. Set `APP_USERNAME` and
`APP_PASSWORD` in `.env` and the whole instance locks behind that one
credential: every page except `/acceso`, statics, the language switch,
and `/s/` share links (read-only snapshots the owner created on
purpose) requires the single-user session. The single login creates
the one owner account, so members, the vault, and the vaccine registry
work unchanged. Leave `APP_PASSWORD` empty and there is no gate.
The password lives only in `.env` (git-ignored, kept out of the image)
and is compared in constant time; login attempts are rate-limited
(10 per 15 minutes per IP). This is an access lock, not encryption at
rest, and it sits on top of Tailscale rather than replacing it.

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
- (Optional) One API key for an explanation provider (Gemini, Claude, or OpenAI) — without one, the app runs in demo mode

## Installation

```bash
cd mificha
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## How to run

```bash
cd mificha
source .venv/bin/activate
uvicorn app.main:app --reload --port 18473
```

The app is then at http://localhost:18473.

## Environment variables

Most setups only need a few of these. They're grouped by what
they're for — skip any group that doesn't apply to you. Anything
with a default works if you leave it out.

### Everyone

| Variable | What it does | Default |
|---|---|---|
| `SECRET_KEY` | Signs sessions. **Required in production** — generate a random one, e.g. `python3 -c "import secrets; print(secrets.token_hex(32))"`. Never use the code's built-in default. | — |
| `BASE_URL` | Your app's address, used to build share links. On a home machine, your Tailscale address, e.g. `http://mini-pc:18473`. | `http://localhost:18473` |
| `PORT` | The port the server listens on. | `18473` |

### Explaining exams — pick one provider

| Variable | What it does | Default |
|---|---|---|
| `LLM_PROVIDER` | Which provider explains the exams: `gemini`, `anthropic`, `openai`, `demo`, or `auto`. `auto` uses the first one that has a key, in that order. If the provider you pick has no key, the app falls through to the next one with a key and logs a warning — uploads never break. | `auto` |
| `GEMINI_API_KEY` | Gemini key (AI Studio). | demo mode |
| `GEMINI_MODEL` | Gemini model. | `gemini-3.5-flash-lite` |
| `ANTHROPIC_API_KEY` | Claude key (console.anthropic.com). | demo mode |
| `ANTHROPIC_MODEL` | Claude model. The old name `LLM_MODEL` still works. | `claude-sonnet-5-5` |
| `OPENAI_API_KEY` | OpenAI key — a platform key from platform.openai.com, not the ChatGPT login. | demo mode |
| `OPENAI_MODEL` | OpenAI model. | `gpt-5-mini` |

With no key at all, the app runs in demo mode: a local,
deterministic explanation with no network calls.

### Cloud OCR fallback — optional

Only for pages Tesseract can't read; see "Cloud OCR" below.
Tesseract is the main OCR (`OCR_PROVIDER=tesseract`, set in
`.env.example` and on Render — the code default is no OCR provider).

| Variable | What it does | Default |
|---|---|---|
| `DOCAI_PROCESSOR` | Google Document AI processor (`projects/.../processors/...`). | — |
| `DOCAI_CREDENTIALS_JSON` | Document AI service-account JSON, on one line. | — |

### Hosted deployment only (Render) — skip if self-hosting

These connect a cloud deployment to Supabase (database, storage,
and magic-link sign-in). A self-hosted instance leaves them empty
and uses local SQLite instead.

| Variable | What it does |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL. |
| `SUPABASE_ANON_KEY` | Anonymous key, used only for Supabase Auth (magic links). |
| `SUPABASE_SERVICE_KEY` | Server-only key for data and storage. Never expose it to the browser. |

### Single-password gate — self-hosting only

| Variable | What it does |
|---|---|
| `APP_USERNAME` | Together with `APP_PASSWORD`, locks the whole instance behind one login. |
| `APP_PASSWORD` | Leave empty and there is no gate. Only works when Supabase is not set. |

## What works without keys (demo mode)

- Local deterministic explanation (no LLM): plain-language templates, no network calls.
- Development auth (no Supabase).
- Local SQLite index plus per-person files (originals, explanations,
  biomarker CSVs) under the data folder (`./data` in development,
  `./mificha-data` with Docker).
- Scanned pages are read with local Tesseract OCR when
  `OCR_PROVIDER=tesseract` is set (it is in `.env.example` and on
  Render); a page Tesseract can't read stays marked as unscannable.

## For production, connect

1. **Explanation provider**: pick one with `LLM_PROVIDER` (`gemini`, `anthropic`, or `openai`) and set its key — `GEMINI_API_KEY` (+ `GEMINI_MODEL` to pin the model), `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL`, or `OPENAI_API_KEY` + `OPENAI_MODEL`. With `auto` (default) the app uses the first provider that has a key, in that order.
2. **OCR**: local Tesseract (configured in `.env.example` and on Render), with a garbled-text quality gate (`app/services/ocr_quality.py`): text that is clearly garbled is not saved as document text — it goes to the Google Document AI fallback instead, and if the fallback is also garbled (or not configured) the page stays marked as scanned/uncertain. The gate is character-level only and conservative: numbers, units, abbreviations, and dates all count as clean.
3. **Hosted deployment only**: if you're deploying to Render (or another cloud host) instead of self-hosting, add the three Supabase variables — `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`. A self-hosted instance doesn't need them at all.

### Cloud OCR (Google Document AI, optional fallback)

Tesseract handles most scans. Document AI is only the rescue path for
pages Tesseract can't read or reads as garbled. It is **not** configured
yet — today a garbled scanned page is rejected as unscannable. Setup
(only you can do this; never paste credentials in chat or git):

1. In Google Cloud Console, pick or create a project and enable the
   **Document AI API**.
2. Document AI → Create processor, type **Document OCR**, location
   **`us`**. Copy the processor resource name:
   `projects/<project>/locations/us/processors/<id>`.
3. IAM → Service Accounts → create one with the **Document AI API User**
   role → Keys → Add key (JSON) → download it.
4. Set the two variables, on Render (Dashboard → mificha → Environment)
   or in the mini-PC `.env`:
   - `DOCAI_PROCESSOR=projects/<project>/locations/us/processors/<id>`
   - `DOCAI_CREDENTIALS_JSON=<the whole JSON on one line>`
     (collapse it with `jq -c . key.json`).
5. Redeploy (Render) or restart the container (mini PC). To verify,
   upload a scanned page Tesseract can't read: it should come back
   explained instead of unscannable.

Pricing: first 1,000 pages/month free, then $1.50 per 1,000 pages;
failed calls are not billed.

## Costs

All prices in USD, checked November 19, 2026 against the providers'
own pricing pages. They change often — check the provider's page
before budgeting.

### Explaining one exam (LLM)

Each upload is one request to the provider you choose. What it costs
depends on how long the document is, but in general:

| Provider | Rough cost per exam |
|---|---|
| Gemini | Less than 2 cents |
| Claude | 2 to 7 cents |
| OpenAI (ChatGPT) | Less than 2 cents |

So explaining 100 exams costs roughly $1–$2 on Gemini or OpenAI, and
roughly $2–$7 on Claude. Longer documents cost a bit more, and if a
request has to retry, that retry costs extra too. With no API key the
app runs in demo mode, which costs nothing. Gemini also has a free
tier with rate limits.

Exact per-token rates change often — the providers' own pricing pages
have the current numbers.

Model retirements to watch: Claude Sonnet 4.5 retires November 30,
2026, so the default is now Sonnet 5.5 (which is also cheaper).
OpenAI retires `gpt-5-mini` on December 11, 2026 — after that, set
`OPENAI_MODEL=gpt-6.1-terra`.

### Reading a scanned page (OCR)

- Local Tesseract (the default): free, runs inside the container.
- Google Document AI (only the rescue path for pages Tesseract can't
  read): first 1,000 pages/month free, then $1.50 per 1,000 pages. A
  typical exam is a few pages, so even beyond the free tier it is a
  fraction of a cent per exam. Failed calls are not billed.

## How to run the tests

```bash
cd mificha
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
│   ├── auth.py                 # sessions (Supabase magic link / gate / dev)
│   ├── config.py               # environment variables
│   ├── db.py                   # SQLite + Supabase data access
│   ├── file_store.py           # per-person files (self-hosting)
│   ├── i18n.py                 # Spanish / English strings
│   ├── schemas.py              # pydantic models for the Explanation
│   ├── storage.py              # original PDFs (bucket / disk)
│   ├── routers/                # pages: upload, vault, results, vaccines
│   └── services/
│       ├── llm.py              # explanation providers (Gemini/Claude/OpenAI/demo)
│       ├── ocr.py              # Tesseract + Document AI fallback
│       ├── ocr_quality.py      # garbled-text gate
│       ├── pdf_extract.py      # extract_document(pdf_bytes) -> dict
│       ├── pipeline.py         # read → explain → validate → save
│       ├── urgency.py          # decide_urgency(signals) -> dict
│       └── vaccines.py         # vaccine registry aggregation
├── static/                     # frontend assets
├── templates/                  # views
├── tests/                      # pytest suite (contracts)
├── supabase/                   # schema for the hosted deployment
├── .env.example
├── docker-compose.yml
├── Dockerfile
├── render.yaml
├── requirements.txt
└── README.md
```

## What is implemented vs. what is a stub

- **Real**: text extraction from digital PDFs, OCR of scanned pages
  with local Tesseract (Google Document AI as fallback when
  configured), scanned-page detection (fewer than 20 characters →
  scanned), urgency rules, Explanation schemas.
- **Stub / demo**: plain-language explanation without an LLM
  (deterministic templates).

## Hard rules

1. **Reference ranges come only from the document.** Never invent ranges or values.
2. **No diagnoses.** Values and findings are explained; the user is never told what disease they have.
3. **An order's priority is quoted literally** in the explanation; never invented.
4. **When critical data is uncertain, be conservative**: never `no_urgente`.
5. **Plain language**: out-of-range values are marked with how far off they are, with visible guidance on whether/when to seek medical attention.
