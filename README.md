# Hermes Document Pipeline

A production-ready CLI pipeline for document and invoice ingestion, health checks, and semantic retrieval. Designed for repeatable onboarding, clear contracts, and safe public publishing.

[中文文档](./README.zh-CN.md)

## Why this project exists

Hermes Agent handles orchestration well. Business workflows still need an independent, stable runtime and tooling this repository provides through a unified entrypoint: `./project-tool`.

A new user can go from clone to verified pipeline in minutes:
1. Clone the repo.
2. Create a virtualenv.
3. Fill in a `.env`.
4. Run one or two verification commands.
5. Have a working invoice and document pipeline with health checks.

## What it does

- Invoice ingestion — accept JSON payloads, upsert to Cloudflare D1, and sync invoice text into ChromaDB for retrieval.
- Document ingestion — extract text from PDF / DOCX / TXT / MD / LOG / CSV / XLSX / XLS, archive with MD5 dedup, record metadata to D1, and index content into ChromaDB.
- Health checks — verify Python runtime, imports, D1, ChromaDB, and CLI integrity; export JSON and Markdown reports; auto-fix common drift with `doctor --fix`.
- Meeting automation (Tencent Meeting) — headless QR login, auto-join meetings, record audio, chunked transcription plus AI summary, and push results via Telegram.

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

Minimum required:
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_FINANCE_D1_DATABASE_ID`
- `CHROMA_HOST`
- `CHROMA_PORT`

Optional:
- `HERMES_NEWS_TARGET`

### 4) Verify the CLI

```bash
./project-tool --help
```

### 5) Bootstrap check (safe, no side effects)

```bash
./project-tool doctor --bootstrap --json
```

Checks local runtime, dependencies, and entrypoints without D1 or ChromaDB.

### 6) Full integration check (after D1 and ChromaDB are ready)

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

| Key | Required | Description |
|---|---|---|
| `invoice_number` | Yes | Unique invoice ID (primary key) |
| `invoice_date` | No | Issue date (yyyy-mm-dd) |
| `buyer_name` | No | Buyer |
| `seller_name` | No | Seller |
| `item_name` | No | Goods or service description |
| `amount_net` | No | Pre-tax amount |
| `tax_amount` | No | Tax amount |
| `total_amount` | No | Total (tax inclusive) |
| `file_path` | No | Source file path (default: `manual_entry`) |
| `raw_text` | No | Full extracted text, used for semantic indexing |

### `doc` command

| Key | Required | Description |
|---|---|---|
| `file_path_src` | Yes | Source file absolute path |
| `title` | Yes | Document title (used as ChromaDB ID) |
| `company` | No | Company or organization |
| `category` | No | Document type (contract, report, etc.) |
| `summary` | No | Content summary |
| `tags` | No | Comma-separated tags |
| `raw_text` | No | Pre-extracted text (skips re-extraction if provided) |

## D1 schema

Expected tables in your Cloudflare D1 database:

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

This repository is publishable without leaking secrets.

Before pushing:
- never commit `.env` or `.env.local`
- never commit bot IDs, tokens, chat IDs, or personal paths
- use `.env.example` for configuration documentation
- treat `archive/` and `doctor-reports/` as runtime output only

The repository ignores runtime artifacts by default.

## CI

`.github/workflows/bootstrap.yml` validates the documented bootstrap flow on every push:
1. create `.venv`
2. install `requirements.txt`
3. copy `.env.example` -> `.env`
4. `./project-tool --help`
5. `./project-tool doctor --bootstrap --json`
6. `python -m py_compile *.py`

## Known assumptions

- D1 database must have `invoices` and `documents` tables (DDL above)
- ChromaDB must be reachable over HTTP
- Hermes CLI should be installed for delivery checks (`--no-qq-send`)
- `doctor --bootstrap --json` is the recommended first-install validation

## Document retrieval protocol

This repository follows a strict document retrieval protocol for agent-facing queries. The workflow is:
- D1 metadata first
- ChromaDB semantic chunk search next
- `doc_summarize` after targeted search
- targeted file inspection only when needed
- required source citations in the form `[Source: <title> (id:<id>)]`

Use search before reading full documents. Do not return web-only answers when the answer exists in stored documents.

## License

[MIT](./LICENSE)

## Contributing and security

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [SECURITY.md](./SECURITY.md)
