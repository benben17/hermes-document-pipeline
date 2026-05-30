# Hermes Document Pipeline

Production-ready document + invoice processing toolkit built around a single CLI, Cloudflare D1 for structured data, and ChromaDB for semantic retrieval.

[中文文档](./README.zh-CN.md)

## Why this project exists

Hermes Agent is excellent at orchestration, but business workflows need their own stable runtime and repeatable tooling. This repository packages that layer as a small Python project with a simple shell entrypoint.

It is designed so that a new user can:

1. clone the repo,
2. create a virtualenv,
3. fill a `.env`,
4. run one or two commands,
5. and have a working invoice/document pipeline with health checks.

## Core capabilities

- **Invoice ingestion**
  - Accept JSON payloads
  - Upsert records into Cloudflare D1
  - Sync invoice text into ChromaDB
- **Document ingestion**
  - Extract text from PDF, DOCX, TXT, MD, LOG, CSV, XLSX, XLS
  - Archive normalized copies locally
  - Upsert metadata into D1
  - Index extracted text into ChromaDB
- **Operational health checks**
  - Verify Python runtime, imports, D1, ChromaDB, news cron wrappers, and Hermes delivery path
  - Export JSON + Markdown reports
  - Auto-fix common runtime drift with `doctor --fix`
- **News delivery utilities**
  - Reuters summary push
  - Bloomberg summary push
  - Source-enhanced output with publisher/domain/feed provenance in markdown and Chroma metadata

## Project highlights

- **Tool-first**: core workflows live in reusable scripts and CLI commands, not chat-only glue.
- **Hybrid storage model**: D1 for structured records, ChromaDB for semantic text retrieval.
- **Production-oriented observability**: built-in `doctor` command with artifacts.
- **Open-source onboarding**: sample payloads under `examples/` and a GitHub Actions bootstrap workflow keep clone-to-first-run reproducible.
- **Public-repo-friendly cleanup**: secrets and personal targets are environment-driven, not hardcoded.
- **Fast onboarding**: local wrapper `./project-tool` works immediately after venv setup.

## Quick start

### 1) Clone

```bash
git clone <your-repo-url>
cd project
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

Fill in at least:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_FINANCE_D1_DATABASE_ID`
- `CHROMA_HOST`
- `CHROMA_PORT`
- `HERMES_NEWS_TARGET` if you want news or doctor probes delivered via Hermes

### 4) Confirm the CLI works

```bash
./project-tool --help
```

### 5) Run a safe bootstrap check

```bash
./project-tool doctor --bootstrap --json
```

This is the recommended zero-side-effect smoke test right after install. It verifies the local Python runtime, dependencies, entrypoints, and CLI bootstrap without requiring D1, ChromaDB, cron wrappers, or Hermes delivery to already be wired.

### 6) Run the full integration check (optional)

```bash
./project-tool doctor --no-qq-send
```

Use this after you have configured D1, ChromaDB, and (optionally) Hermes delivery.

## Minimal install commands

For users who just want the shortest path:

```bash
git clone <your-repo-url>
cd project
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
./project-tool doctor --bootstrap --json
```

## CLI usage

```bash
./project-tool invoice payload.json
./project-tool doc payload.json
./project-tool report invoices
./project-tool doctor --bootstrap --json
./project-tool doctor --json
./project-tool doctor --fix
```

You can also pipe JSON from stdin:

```bash
cat payload.json | ./project-tool invoice -
cat payload.json | ./project-tool doc -
```

## Input contracts

### Invoice command

Expected JSON keys typically include:

- `invoice_number`
- `invoice_date`
- `buyer_name`
- `seller_name`
- `item_name`
- `amount_net`
- `tax_amount`
- `total_amount`
- `file_path`
- optional: `raw_text`

### Document command

Expected JSON keys typically include:

- `title`
- `company`
- `category`
- `summary`
- `tags`
- `file_path`
- optional: `raw_text`

## Architecture

```text
Operator / CLI / Hermes
        │
        ▼
   ./project-tool
        │
        ├── project_manager.py
        ├── project_doctor.py
        ├── reuters_push.py
        └── bloomberg_fetch_and_notify.py
                │
                ├── Cloudflare D1   (structured records)
                ├── ChromaDB        (semantic retrieval)
                └── local archive   (documents / reports / outputs)
```

## Repository layout

```text
.
├── .github/
│   └── workflows/
│       └── bootstrap.yml
├── .env.example
├── .gitignore
├── README.md
├── README.zh-CN.md
├── examples/
│   ├── README.md
│   ├── invoice.sample.json
│   ├── document.sample.json
│   └── sample_document.txt
├── project-tool
├── requirements.txt
├── hermes_core.py
├── core_engine.py
├── project_manager.py
├── project_doctor.py
├── invoice_engine.py
├── doc_engine.py
├── pdf_engine.py
├── reuters_fetcher.py
├── reuters_push.py
├── bloomberg_fetch_and_notify.py
└── bloomberg_rss_fetch.py
```

## Configuration model

Configuration is loaded in this order:

1. process environment variables
2. local project `.env`
3. `$HERMES_HOME/.env`
4. built-in safe defaults

Important variables:

- `PROJECT_ROOT`
- `HERMES_HOME`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_FINANCE_D1_DATABASE_ID`
- `CHROMA_HOST`
- `CHROMA_PORT`
- `HERMES_NEWS_TARGET`
- `HERMES_PROJECT_REPORT_DIR`
- `HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR`
- `REUTERS_OUTPUT_FILE`
- `BLOOMBERG_OUTPUT_FILE`
- `BLOOMBERG_RSS_OUTPUT_FILE`
- `HERMES_CRON_JOBS_FILE`
- `HERMES_PROJECT_TOOL_PATH`
- `FIRECRAWL_API_KEY`

## What `doctor` checks

- project `.venv`
- required Python imports
- entrypoint integrity
- D1 connectivity and expected schema
- ChromaDB connectivity and expected collections
- Firecrawl key presence / search probe
- Hermes delivery target availability
- news cron governance and wrapper expectations

Reports are written to:

```text
doctor-reports/
```

## Public GitHub safety rules

This repository is intended to be publishable.

Before pushing publicly:

- keep `.env` out of git
- keep runtime outputs out of git
- do not commit real bot IDs, tokens, chat IDs, or personal paths unless they are genericized
- prefer `.env.example` for documentation

The repository already ignores:

- `.venv/`
- `venv/`
- `archive/`
- `doctor-reports/`
- generated markdown outputs
- local env files

## Examples and CI

- `examples/invoice.sample.json` provides a public-safe invoice payload for `./project-tool invoice`
- `examples/document.sample.json` plus `examples/sample_document.txt` provide a document smoke-test path for `./project-tool doc`
- `.github/workflows/bootstrap.yml` validates the documented bootstrap flow on GitHub Actions:
  - create `.venv`
  - install `requirements.txt`
  - copy `.env.example` to `.env`
  - run `./project-tool --help`
  - run `./project-tool doctor --bootstrap --json`
  - run `python -m py_compile *.py`

## Known assumptions

- Current business logic expects Cloudflare D1 schemas for `invoices` and `documents`
- ChromaDB should be reachable over HTTP
- Hermes CLI must be installed if you want delivery checks or push jobs
- `doctor --bootstrap --json` is the recommended first-install validation
- `doctor --no-qq-send` is the recommended integration validation once D1/Chroma/Hermes are configured

## Suggested next improvements

- add tests with sample fixtures
- add Docker / Compose bootstrap for Chroma + app runtime
- add richer schema migration support
- move publishing and architecture notes under `docs/` if you want to keep them in the public repo

## License

This repository is licensed under [MIT](./LICENSE).

## Contributing and security

See:

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [SECURITY.md](./SECURITY.md)
- [docs/fenchuan-monitoring.md](./docs/fenchuan-monitoring.md)
