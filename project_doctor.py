#!/usr/bin/env python3
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from hermes_core import query_d1, get_config, get_project_root

CFG = get_config()
HERMES_HOME = Path(CFG.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
PROJECT_ROOT = get_project_root()
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_PY = VENV_DIR / "bin" / "python"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
QQ_TARGET = CFG.get("HERMES_NEWS_TARGET", "qqbot")
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"
PROJECT_SHEBANG = "#!/usr/bin/env python3"
OLD_HERMES_SHEBANG = "#!/usr/local/lib/hermes-agent/venv/bin/python3"
REPORT_DIR = Path(CFG.get("HERMES_PROJECT_REPORT_DIR", str(PROJECT_ROOT / "doctor-reports")))
CRON_JOBS_FILE = Path(CFG.get("HERMES_CRON_JOBS_FILE", str(HERMES_HOME / "cron" / "jobs.json")))
LATEST_JSON = REPORT_DIR / "latest.json"
LATEST_MD = REPORT_DIR / "latest.md"
PROJECT_SCRIPTS = [
    PROJECT_ROOT / "project_manager.py",
    PROJECT_ROOT / "invoice_engine.py",
    PROJECT_ROOT / "doc_engine.py",
    PROJECT_ROOT / "core_engine.py",
    PROJECT_ROOT / "reuters_fetcher.py",
    PROJECT_ROOT / "reuters_push.py",
    PROJECT_ROOT / "bloomberg_fetch_and_notify.py",
    PROJECT_ROOT / "pdf_engine.py",
    PROJECT_ROOT / "bloomberg_rss_fetch.py",
    PROJECT_ROOT / "project_doctor.py",
]
WRAPPERS = {
    Path(HERMES_HOME / "scripts" / "reuters_push_wrapper.sh"): f"#!/bin/bash\nset -euo pipefail\ncd {PROJECT_ROOT}\nexec {VENV_PY} {PROJECT_ROOT / 'reuters_push.py'}\n",
    Path(HERMES_HOME / "scripts" / "bloomberg_push_wrapper.sh"): f"#!/bin/bash\nset -euo pipefail\ncd {PROJECT_ROOT}\nexec {VENV_PY} {PROJECT_ROOT / 'bloomberg_fetch_and_notify.py'}\n",
}
PROJECT_TOOL = Path(CFG.get("HERMES_PROJECT_TOOL_PATH", str(PROJECT_ROOT / "project-tool")))
REQUIRED_MODULES = ["requests", "chromadb", "docx", "openpyxl", "pdf_inspector", "httpx"]
CHROMA_HOST = CFG.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(CFG.get("CHROMA_PORT", "8000"))
EXPECTED_D1_TABLES = {
    "invoices": [
        "id",
        "invoice_number",
        "invoice_date",
        "buyer_name",
        "seller_name",
        "item_name",
        "amount_net",
        "tax_amount",
        "total_amount",
        "file_path",
        "created_at",
    ],
    "documents": [
        "id",
        "title",
        "company",
        "category",
        "summary",
        "tags",
        "file_path",
        "created_at",
    ],
}
EXPECTED_CHROMA_COLLECTIONS = ["documents", "invoices", "bloomberg_rss"]
EXPECTED_NEWS_CRON = [
    {
        "name": "Reuters 早报 (UTC+8 09:00)",
        "schedule": "0 1 * * *",
        "script": "reuters_push_wrapper.sh",
        "no_agent": True,
    },
    {
        "name": "Reuters 晚报 (UTC+8 21:00)",
        "schedule": "0 13 * * *",
        "script": "reuters_push_wrapper.sh",
        "no_agent": True,
    },
    {
        "name": "Bloomberg 早报 (UTC+8 09:00)",
        "schedule": "0 1 * * *",
        "script": "bloomberg_push_wrapper.sh",
        "no_agent": True,
    },
    {
        "name": "Bloomberg 晚报 (UTC+8 21:00)",
        "schedule": "0 13 * * *",
        "script": "bloomberg_push_wrapper.sh",
        "no_agent": True,
    },
]
LEGACY_NEWS_CRON_NAMES = ["bloomberg_morning_briefing"]


def _run(cmd, timeout=120, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def _load_env_key(key_name: str) -> str:
    val = os.getenv(key_name, "")
    if val:
        return val
    cfg_val = CFG.get(key_name, "")
    if cfg_val:
        return cfg_val
    return ""


def _is_executable(path: Path) -> bool:
    return path.exists() and os.access(path, os.X_OK)


def _first_line(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.readline().rstrip("\n")
    except Exception:
        return ""


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def _write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(content)


def _chmod_x(path: Path):
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _query_rows(sql: str, params=None) -> List[dict]:
    resp = query_d1(sql, params)
    if not resp.get("success"):
        raise RuntimeError(str(resp.get("errors") or resp))
    return resp.get("result", [{}])[0].get("results", [])


def _check_imports():
    if not VENV_PY.exists():
        return {m: "missing project python" for m in REQUIRED_MODULES}
    code = (
        "mods=" + repr(REQUIRED_MODULES) + "\n"
        "import importlib, json\n"
        "out={}\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "        out[m]=True\n"
        "    except Exception as e:\n"
        "        out[m]=str(e)\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    )
    proc = _run([str(VENV_PY), "-c", code], timeout=60)
    if proc.returncode != 0:
        return {m: (proc.stderr or proc.stdout).strip() for m in REQUIRED_MODULES}
    return json.loads(proc.stdout.strip() or "{}")


def check_venv():
    imports = _check_imports()
    ok = VENV_PY.exists() and REQUIREMENTS.exists() and all(v is True for v in imports.values())
    return {
        "name": ".venv",
        "ok": ok,
        "details": {
            "python": VENV_PY.exists(),
            "requirements.txt": REQUIREMENTS.exists(),
            "imports": imports,
        },
        **({"error": "project .venv incomplete"} if not ok else {}),
    }


def check_d1():
    result = {"name": "D1", "ok": False}
    try:
        ping_rows = _query_rows("SELECT 1 as ok")
        tables = [r.get("name") for r in _query_rows("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        schema = {}
        counts = {}
        missing_tables = []
        schema_mismatches = {}
        for table, expected_cols in EXPECTED_D1_TABLES.items():
            if table not in tables:
                missing_tables.append(table)
                continue
            cols = [r.get("name") for r in _query_rows(f"PRAGMA table_info({table})")]
            schema[table] = cols
            missing_cols = [c for c in expected_cols if c not in cols]
            if missing_cols:
                schema_mismatches[table] = missing_cols
            count_rows = _query_rows(f"SELECT COUNT(*) as cnt FROM {table}")
            counts[table] = count_rows[0].get("cnt", 0) if count_rows else 0

        ok = (
            bool(ping_rows)
            and ping_rows[0].get("ok") == 1
            and not missing_tables
            and not schema_mismatches
        )
        result["ok"] = ok
        result["details"] = {
            "ping": ping_rows[:1],
            "tables": tables,
            "required_tables": list(EXPECTED_D1_TABLES.keys()),
            "schema": schema,
            "row_counts": counts,
        }
        if missing_tables or schema_mismatches:
            result["error"] = {
                "missing_tables": missing_tables,
                "schema_mismatches": schema_mismatches,
            }
    except Exception as e:
        result["error"] = str(e)
    return result


def check_chroma():
    result = {"name": "Chroma", "ok": False}
    try:
        import chromadb

        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        heartbeat = client.heartbeat()
        cols = client.list_collections()
        col_names = [getattr(c, "name", str(c)) for c in cols]
        missing = [c for c in EXPECTED_CHROMA_COLLECTIONS if c not in col_names]
        probes = {}
        for name in EXPECTED_CHROMA_COLLECTIONS:
            if name in col_names:
                try:
                    coll = client.get_collection(name=name)
                    probes[name] = {"count": coll.count()}
                except Exception as e:
                    probes[name] = {"error": str(e)}
                    missing.append(name)
        result["ok"] = not missing
        result["details"] = {
            "heartbeat": heartbeat,
            "host": CHROMA_HOST,
            "port": CHROMA_PORT,
            "collections": col_names,
            "required_collections": EXPECTED_CHROMA_COLLECTIONS,
            "probes": probes,
            "collection_count": len(col_names),
        }
        if missing:
            result["error"] = {"missing_or_unreadable": sorted(set(missing))}
    except Exception as e:
        result["error"] = str(e)
    return result


def check_firecrawl():
    result = {"name": "Firecrawl", "ok": False}
    try:
        import requests

        key = _load_env_key("FIRECRAWL_API_KEY")
        if not key:
            result["error"] = "FIRECRAWL_API_KEY missing"
            return result
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {"query": "site:reuters.com reuters", "limit": 1}
        resp = requests.post(FIRECRAWL_SEARCH_URL, headers=headers, json=payload, timeout=30)
        body = {}
        try:
            body = resp.json()
        except Exception:
            body = {"text": resp.text[:200]}
        result["details"] = {"status_code": resp.status_code}
        if resp.status_code == 200 and isinstance(body, dict) and body.get("success") is not False:
            result["ok"] = True
        else:
            result["error"] = body.get("error") if isinstance(body, dict) else str(body)
    except Exception as e:
        result["error"] = str(e)
    return result


def check_qq_send(send_probe: bool = True):
    result = {"name": "QQ Send", "ok": False, "details": {"probe_sent": send_probe}}
    try:
        if send_probe:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            proc = _run(["hermes", "send", "-t", QQ_TARGET, f"project-tool doctor probe @ {stamp}"], timeout=30)
            result["details"]["stdout"] = (proc.stdout or "").strip()
            if proc.returncode == 0:
                result["ok"] = True
            else:
                result["error"] = (proc.stderr or proc.stdout).strip()
        else:
            proc = _run(["hermes", "send", "--list", "qqbot"], timeout=30)
            result["details"]["stdout"] = (proc.stdout or "").strip()[:500]
            result["ok"] = proc.returncode == 0
            if not result["ok"]:
                result["error"] = (proc.stderr or proc.stdout).strip()
    except Exception as e:
        result["error"] = str(e)
    return result


def check_entrypoints(require_wrappers: bool = True):
    script_details = {}
    scripts_ok = True
    for path in PROJECT_SCRIPTS:
        exists = path.exists()
        shebang = _first_line(path) if exists else ""
        exec_ok = _is_executable(path)
        ok = exists and shebang == PROJECT_SHEBANG and exec_ok
        script_details[str(path)] = {"exists": exists, "shebang": shebang, "executable": exec_ok}
        scripts_ok = scripts_ok and ok

    wrapper_details = {}
    wrappers_ok = True
    for path, expected in WRAPPERS.items():
        exists = path.exists()
        content_ok = exists and _read_text(path) == expected
        exec_ok = _is_executable(path)
        wrapper_details[str(path)] = {"exists": exists, "content_ok": content_ok, "executable": exec_ok}
        wrappers_ok = wrappers_ok and exists and content_ok and exec_ok

    project_tool_text = _read_text(PROJECT_TOOL) if PROJECT_TOOL.exists() else ""
    project_tool_details = {
        "exists": PROJECT_TOOL.exists(),
        "executable": _is_executable(PROJECT_TOOL),
        "uses_project_venv": ".venv/bin/python" in project_tool_text,
        "has_doctor_case": "doctor)" in project_tool_text,
    }
    project_tool_ok = (
        PROJECT_TOOL.exists()
        and _is_executable(PROJECT_TOOL)
        and project_tool_details["uses_project_venv"]
        and project_tool_details["has_doctor_case"]
    )

    wrappers_gate = wrappers_ok if require_wrappers else True
    ok = scripts_ok and wrappers_gate and project_tool_ok
    result = {
        "name": "Entrypoints",
        "ok": ok,
        "details": {
            "project_scripts": script_details,
            "wrappers": wrapper_details,
            "wrappers_required": require_wrappers,
            "project_tool": project_tool_details,
        },
    }
    if not ok:
        result["error"] = "entrypoints or wrappers drifted"
    return result


def _load_cron_jobs() -> List[dict]:
    if not CRON_JOBS_FILE.exists():
        return []
    data = json.loads(_read_text(CRON_JOBS_FILE))
    return data.get("jobs", [])


def check_news_cron():
    result = {"name": "News Cron", "ok": False}
    try:
        jobs = _load_cron_jobs()
        active_jobs = [j for j in jobs if j.get("enabled")]
        by_name = {j.get("name"): j for j in active_jobs}
        missing = []
        mismatches = {}
        for expected in EXPECTED_NEWS_CRON:
            job = by_name.get(expected["name"])
            if not job:
                missing.append(expected["name"])
                continue
            actual = {
                "schedule": job.get("schedule_display") or job.get("schedule", {}).get("display"),
                "script": job.get("script"),
                "no_agent": job.get("no_agent"),
                "deliver": job.get("deliver"),
                "last_status": job.get("last_status"),
                "next_run_at": job.get("next_run_at"),
            }
            bad = {}
            for key in ["schedule", "script", "no_agent"]:
                if actual[key] != expected[key]:
                    bad[key] = {"expected": expected[key], "actual": actual[key]}
            if bad:
                mismatches[expected["name"]] = bad

        duplicates = []
        for legacy_name in LEGACY_NEWS_CRON_NAMES:
            if legacy_name in by_name:
                duplicates.append(legacy_name)

        result["ok"] = not missing and not mismatches and not duplicates
        result["details"] = {
            "required_jobs": EXPECTED_NEWS_CRON,
            "detected_news_jobs": [
                {
                    "name": j.get("name"),
                    "schedule": j.get("schedule_display") or j.get("schedule", {}).get("display"),
                    "script": j.get("script"),
                    "no_agent": j.get("no_agent"),
                    "last_status": j.get("last_status"),
                    "next_run_at": j.get("next_run_at"),
                }
                for j in active_jobs
                if any(t in (j.get("name") or "") for t in ["Reuters", "Bloomberg", "bloomberg"])
            ],
            "cron_jobs_file": str(CRON_JOBS_FILE),
        }
        if missing or mismatches or duplicates:
            result["error"] = {
                "missing": missing,
                "mismatches": mismatches,
                "duplicates_or_legacy": duplicates,
            }
    except Exception as e:
        result["error"] = str(e)
    return result


def _rewrite_shebang(path: Path, new_shebang: str):
    text = _read_text(path)
    lines = text.splitlines()
    if lines:
        lines[0] = new_shebang
        new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    else:
        new_text = new_shebang + "\n"
    _write_text(path, new_text)


def fix_venv(initial_venv_check):
    actions = []
    if not VENV_PY.exists():
        proc = _run(["python3", "-m", "venv", str(VENV_DIR)], timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"create venv failed: {(proc.stderr or proc.stdout).strip()}")
        actions.append(f"created venv: {VENV_DIR}")

    missing_imports = [k for k, v in initial_venv_check.get("details", {}).get("imports", {}).items() if v is not True]
    if not REQUIREMENTS.exists():
        raise RuntimeError(f"missing requirements: {REQUIREMENTS}")

    if missing_imports or not initial_venv_check.get("details", {}).get("requirements.txt"):
        proc = _run([str(VENV_PY), "-m", "pip", "install", "-r", str(REQUIREMENTS)], timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"pip install failed: {(proc.stderr or proc.stdout).strip()[:500]}")
        actions.append("installed requirements into project .venv")
    return actions


def fix_entrypoints():
    actions = []
    for path in PROJECT_SCRIPTS:
        if not path.exists():
            continue
        shebang = _first_line(path)
        if shebang != PROJECT_SHEBANG:
            _rewrite_shebang(path, PROJECT_SHEBANG)
            actions.append(f"normalized shebang: {path}")
        if not _is_executable(path):
            _chmod_x(path)
            actions.append(f"chmod +x: {path}")

    for path, expected in WRAPPERS.items():
        if not path.exists() or _read_text(path) != expected:
            _write_text(path, expected)
            actions.append(f"rewrote wrapper: {path}")
        if not _is_executable(path):
            _chmod_x(path)
            actions.append(f"chmod +x: {path}")

    if PROJECT_TOOL.exists():
        text = _read_text(PROJECT_TOOL)
        replaced = text.replace(OLD_HERMES_SHEBANG, PROJECT_SHEBANG)
        replaced = replaced.replace('/usr/local/lib/hermes-agent/venv/bin/python3', '/usr/bin/env python3')
        if replaced != text:
            _write_text(PROJECT_TOOL, replaced)
            actions.append(f"normalized project-tool runtime refs: {PROJECT_TOOL}")
        if not _is_executable(PROJECT_TOOL):
            _chmod_x(PROJECT_TOOL)
            actions.append(f"chmod +x: {PROJECT_TOOL}")
    return actions


def fix_compile():
    proc = _run([str(VENV_PY), "-m", "py_compile", *[str(p) for p in PROJECT_ROOT.glob('*.py')]], timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"py_compile failed: {(proc.stderr or proc.stdout).strip()}")
    return ["py_compile passed"]


def apply_fixes():
    actions = []
    initial_venv = check_venv()
    actions.extend(fix_venv(initial_venv))
    actions.extend(fix_entrypoints())
    actions.extend(fix_compile())
    return actions


def _render_markdown(report: dict) -> str:
    lines = []
    lines.append("# project-tool doctor report")
    lines.append("")
    lines.append(f"- overall: {'OK' if report.get('ok') else 'FAIL'}")
    lines.append(f"- timestamp_utc: {report.get('timestamp_utc')}")
    lines.append(f"- fix_mode: {report.get('fix_mode')}")
    if report.get("fixes_applied"):
        lines.append("- fixes_applied:")
        for item in report["fixes_applied"]:
            lines.append(f"  - {item}")
    if report.get("fix_error"):
        lines.append(f"- fix_error: {report['fix_error']}")
    lines.append("")
    for item in report.get("checks", []):
        lines.append(f"## {item.get('name')}")
        lines.append(f"- status: {'OK' if item.get('ok') else 'FAIL'}")
        if item.get("error") is not None:
            lines.append(f"- error: `{json.dumps(item['error'], ensure_ascii=False)}`")
        if item.get("details"):
            lines.append("- details:")
            pretty = json.dumps(item["details"], ensure_ascii=False, indent=2)
            for ln in pretty.splitlines():
                lines.append(f"  {ln}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _persist_report(report: dict):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    slug = _timestamp_slug()
    json_path = REPORT_DIR / f"doctor-{slug}.json"
    md_path = REPORT_DIR / f"doctor-{slug}.md"
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    md_text = _render_markdown(report)
    _write_text(json_path, json_text)
    _write_text(md_path, md_text)
    _write_text(LATEST_JSON, json_text)
    _write_text(LATEST_MD, md_text)
    report["artifacts"] = {
        "json": str(json_path),
        "markdown": str(md_path),
        "latest_json": str(LATEST_JSON),
        "latest_markdown": str(LATEST_MD),
    }
    return report


def run_doctor(send_probe: bool = True, fix: bool = False, bootstrap: bool = False):
    fixes = []
    fix_error = None
    if fix:
        try:
            fixes = apply_fixes()
        except Exception as e:
            fix_error = str(e)
    checks = [
        check_venv(),
        check_entrypoints(require_wrappers=not bootstrap),
    ]
    if bootstrap:
        report = {
            "ok": all(item.get("ok") for item in checks) and not fix_error,
            "timestamp_utc": _now_utc(),
            "checks": checks,
            "fix_mode": fix,
            "bootstrap_mode": True,
            "fixes_applied": fixes,
        }
        if fix_error:
            report["fix_error"] = fix_error
        return _persist_report(report)

    checks.extend([
        check_d1(),
        check_chroma(),
        check_news_cron(),
        check_firecrawl(),
        check_qq_send(send_probe=send_probe),
    ])
    overall = all(item.get("ok") for item in checks) and not fix_error
    report = {
        "ok": overall,
        "timestamp_utc": _now_utc(),
        "checks": checks,
        "fix_mode": fix,
        "bootstrap_mode": False,
        "fixes_applied": fixes,
    }
    if fix_error:
        report["fix_error"] = fix_error
    return _persist_report(report)


def print_human(report: dict):
    print("# project-tool doctor")
    print(f"overall: {'OK' if report['ok'] else 'FAIL'}")
    print(f"timestamp_utc: {report['timestamp_utc']}")
    print(f"fix_mode: {report.get('fix_mode')}")
    print(f"bootstrap_mode: {report.get('bootstrap_mode')}")
    if report.get("artifacts"):
        print("artifacts:")
        for k, v in report["artifacts"].items():
            print(f"  {k}: {v}")
    if report.get("fixes_applied"):
        print("fixes_applied:")
        for item in report["fixes_applied"]:
            print(f"  - {item}")
    if report.get("fix_error"):
        print(f"fix_error: {report['fix_error']}")
    for item in report["checks"]:
        print()
        print(f"- {item['name']}: {'OK' if item.get('ok') else 'FAIL'}")
        if item.get("error"):
            print(f"  error: {item['error']}")
        details = item.get("details")
        if details:
            for k, v in details.items():
                print(f"  {k}: {v}")


if __name__ == "__main__":
    send_probe = "--no-qq-send" not in sys.argv
    fix = "--fix" in sys.argv
    bootstrap = "--bootstrap" in sys.argv
    report = run_doctor(send_probe=send_probe, fix=fix, bootstrap=bootstrap)
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    sys.exit(0 if report["ok"] else 1)
