# Hermes Document Pipeline

A production-ready CLI pipeline for document and invoice ingestion, health checks, and semantic retrieval.

[中文文档](./README.zh-CN.md)

## Why this project exists

Hermes Agent excels at orchestration, but business workflows need their own stable runtime and repeatable tooling. This repository packages that layer as a focused Python project with a unified shell entrypoint `./project-tool`.

A new user can:

1. Clone the repo
2. Create a virtualenv
3. Fill in a `.env`
4. Run one or two commands
5. Have a working invoice/document pipeline with health checks

## Architecture

```
Operator / CLI / Hermes
        │
        ▼
   ./project-tool
        │
        ├── project_manager.py   ──→  invoice / document ingestion (thin router)
        └── project_doctor.py    ──→  health checks / reports
                │
                ├── FinanceEngine   (invoice_engine.py)  ─→  D1 + ChromaDB
                ├── DocumentEngine  (doc_engine.py)       ─→  D1 + ChromaDB
                └── HermesProjectCore (core_engine.py)   ─→  shared D1 / Chroma / archive
                        │
                        ├── Cloudflare D1   (structured records)
                        ├── ChromaDB        (semantic retrieval)
                        └── local archive   (documents / reports / outputs)
```

### Module responsibilities

| File | Role |
|---|---|
| `hermes_core.py` | Config loading (env → .env → defaults), D1 HTTP session with retry |
| `core_engine.py` | `HermesProjectCore` base class — `query_d1`, `sync_to_chroma`, `archive_file`, `get_md5` |
| `invoice_engine.py` | `FinanceEngine(HermesProjectCore)` — invoice upsert + per-buyer report |
| `doc_engine.py` | `DocumentEngine(HermesProjectCore)` — multi-format text extraction, archive, D1 upsert, ChromaDB index |
| `project_manager.py` | Top-level CLI router — no business logic, just routes subcommands to engines |
| `project_doctor.py` | Health checker — verifies runtime, imports, D1, ChromaDB, entrypoints; exports JSON/MD reports |

## Repository layout

```
.
├── .github/
│   └── workflows/
│       └── bootstrap.yml       # CI: validates clone → venv → doctor --bootstrap
├── .env.example                # Config template (no secrets)
├── .gitignore
├── README.md
├── README.zh-CN.md
├── examples/
│   ├── README.md
│   ├── invoice.sample.json     # Sample invoice payload for smoke test
│   ├── document.sample.json    # Sample document analysis payload
│   └── sample_document.txt     # Sample document file
├── meeting/
│   ├── meeting_bot.py          # Meeting automation: QR login, auto-join, record, transcribe, summarize
│   ├── recordings/             # Audio recordings (chunked WAV, gitignored)
│   └── transcripts/            # Transcription outputs (gitignored)
├── project-tool                # Shell entrypoint (calls project_manager.py / project_doctor.py)
├── requirements.txt
├── hermes_core.py              # Config + D1 HTTP session
├── core_engine.py              # HermesProjectCore base class
├── invoice_engine.py           # FinanceEngine
├── doc_engine.py               # DocumentEngine
├── project_manager.py          # CLI router
├── project_doctor.py           # Health checker
└── pdf_engine.py               # PDF helpers (used by doc_engine)
```

## Core capabilities

- **Invoice ingestion** — Accept JSON payloads, upsert to Cloudflare D1, sync invoice text to ChromaDB
- **Document ingestion** — Extract text from PDF / DOCX / TXT / MD / LOG / CSV / XLSX / XLS, archive locally with MD5 dedup, upsert metadata to D1, index text to ChromaDB
- **Health checks** — Verify Python runtime, imports, D1, ChromaDB, and CLI entrypoints; export JSON + Markdown reports; auto-fix common drift with `doctor --fix`
- **Meeting automation (Tencent Meeting)** — Headless QR login, auto-join scheduled meetings, audio capture, chunked transcription + AI summary, push results back via Telegram

## Quick start

### 1) Clone

```bash
git clone https://github.com/benben17/hermes-document-pipeline.git
cd hermes-document-pipeline
```

### 2) Create the Python environment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 3) Configure environment

```bash
cp .env.example .env
```

Fill in at minimum:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_FINANCE_D1_DATABASE_ID`
- `CHROMA_HOST`
- `CHROMA_PORT`

Optional (for Hermes delivery probes):
- `HERMES_NEWS_TARGET`

### 4) Verify the CLI

```bash
./project-tool --help
```

### 5) Bootstrap check (safe, no side effects)

```bash
./project-tool doctor --bootstrap --json
```

Verifies the local Python runtime, dependencies, and CLI entrypoints without requiring D1 or ChromaDB to be configured.

### 6) Full integration check (after D1 + ChromaDB are ready)

```bash
./project-tool doctor --no-qq-send
```

## CLI usage

```bash
# Invoice
echo '{"invoice_number": "INV-001", ...}' | ./project-tool invoice

# Document
echo '{"title": "Contract", "company": "Acme", "file_path_src": "/tmp/a.pdf"}' | ./project-tool doc

# Reports
./project-tool report invoices
./project-tool report documents

# Health checks
./project-tool doctor --bootstrap --json   # first-install smoke test
./project-tool doctor --json               # full check
./project-tool doctor --fix                # auto-fix runtime drift
```

## Input contracts

### `invoice` command

JSON keys:

| Key | Required | Description |
|---|---|---|
| `invoice_number` | ✅ | Unique invoice ID (primary key) |
| `invoice_date` | — | Issue date (yyyy-mm-dd) |
| `buyer_name` | — | Buyer |
| `seller_name` | — | Seller |
| `item_name` | — | Goods / service description |
| `amount_net` | — | Pre-tax amount |
| `tax_amount` | — | Tax amount |
| `total_amount` | — | Total (tax-inclusive) |
| `file_path` | — | Source file path (default: `manual_entry`) |
| `raw_text` | — | Full OCR text, used for ChromaDB indexing |

### `doc` command

JSON keys:

| Key | Required | Description |
|---|---|---|
| `file_path_src` | ✅ | Source file absolute path |
| `title` | ✅ | Document title (used as ChromaDB ID) |
| `company` | — | Company / organization |
| `category` | — | Document type (contract / report / etc.) |
| `summary` | — | Content summary |
| `tags` | — | Comma-separated tags |
| `raw_text` | — | Pre-extracted text (skips re-extraction if provided) |

## D1 schema

The pipeline expects two tables in your Cloudflare D1 database:

```sql
CREATE TABLE invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT UNIQUE NOT NULL,
    invoice_date   TEXT,
    buyer_name     TEXT,
    seller_name    TEXT,
    item_name      TEXT,
    amount_net     REAL,
    tax_amount     REAL,
    total_amount   REAL,
    file_path      TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT,
    company    TEXT,
    category   TEXT,
    summary    TEXT,
    tags       TEXT,
    file_path  TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

## Configuration

Configuration is loaded in this order (later entries override earlier):

1. `$HERMES_PROJECT_ENV` (explicit path, if set)
2. `<project_root>/.env`
3. `$HERMES_HOME/.env`
4. Process environment variables (always wins)
5. Built-in safe defaults

Key variables:

| Variable | Default | Description |
|---|---|---|
| `PROJECT_ROOT` | script directory | Project root path |
| `HERMES_HOME` | `~/.hermes` | Hermes config home |
| `CLOUDFLARE_API_TOKEN` | — | **Required** for D1 |
| `CLOUDFLARE_ACCOUNT_ID` | — | **Required** for D1 |
| `CLOUDFLARE_FINANCE_D1_DATABASE_ID` | — | **Required** for D1 |
| `CHROMA_HOST` | `localhost` | ChromaDB host |
| `CHROMA_PORT` | `8000` | ChromaDB port |
| `HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR` | `<root>/archive/documents` | Document archive path |
| `HERMES_PROJECT_REPORT_DIR` | `<root>/doctor-reports` | Doctor report output |
| `HERMES_NEWS_TARGET` | `qqbot` | Hermes delivery target for probes |
| `FIRECRAWL_API_KEY` | — | Optional; enables Firecrawl probe in doctor |

## What `doctor` checks

| Check | Flag |
|---|---|
| `.venv` exists | always |
| Required Python imports | always |
| Entrypoint integrity (`project-tool`) | always |
| D1 connectivity + schema | `--json` / default |
| ChromaDB connectivity + collections | `--json` / default |
| Firecrawl key + search probe | `--json` / default |
| Hermes delivery target | `--json` / default |

Reports are written to `doctor-reports/` as `.json` and `.md`.

## Public repo safety

This repository is designed to be publishable without leaking secrets.

Before pushing:
- Never commit `.env` (already in `.gitignore`)
- Never commit runtime outputs (`archive/`, `doctor-reports/`)
- Never commit real bot IDs, tokens, chat IDs, or personal paths
- Use `.env.example` for config documentation

The repository already ignores: `.venv/`, `venv/`, `archive/`, `doctor-reports/`, generated markdown outputs, and local env files.

## CI

`.github/workflows/bootstrap.yml` validates the documented bootstrap flow on every push:

1. Create `.venv`
2. Install `requirements.txt`
3. Copy `.env.example` → `.env`
4. `./project-tool --help`
5. `./project-tool doctor --bootstrap --json`
6. `python -m py_compile *.py`

## Known assumptions

- D1 database must have `invoices` and `documents` tables (DDL above)
- ChromaDB must be reachable over HTTP
- Hermes CLI must be installed if you want delivery checks (`--no-qq-send` flag)
- `doctor --bootstrap --json` is the recommended first-install validation

## License

[MIT](./LICENSE)

## Contributing and security

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [SECURITY.md](./SECURITY.md)
