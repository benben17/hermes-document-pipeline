#!/usr/bin/env python3
"""Top-level CLI entry — routes subcommands to FinanceEngine / DocumentEngine."""
import sys, json

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from invoice_engine import FinanceEngine
from doc_engine import DocumentEngine


def show_help(exit_code: int = 0):
    print("Usage:")
    print("  project_manager.py invoice   — process invoice JSON from stdin")
    print("  project_manager.py doc       — process document JSON from stdin")
    print("  project_manager.py report [invoices|documents]")
    sys.exit(exit_code)


def load_json_stdin() -> dict:
    """Read JSON payload exclusively from stdin (avoids ARG_MAX limits)."""
    data = sys.stdin.read().strip()
    if not data or data in ('-h', '--help'):
        show_help(2)
    try:
        return json.loads(data)
    except Exception as e:
        print(f"❌ invalid JSON payload: {e}")
        sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        show_help(0)

    cmd = sys.argv[1]

    if cmd == "invoice":
        data = load_json_stdin()
        engine = FinanceEngine()
        engine.insert_confirmed_data(data)

    elif cmd == "doc":
        data = load_json_stdin()
        file_path = data.pop("file_path_src", data.get("file_path", ""))
        engine = DocumentEngine()
        engine.process(file_path, data)

    elif cmd == "report":
        table = sys.argv[2] if len(sys.argv) > 2 else "documents"
        if table == "invoices":
            FinanceEngine().report()
        elif table == "documents":
            from hermes_core import query_d1
            res = query_d1(
                "SELECT category, COUNT(*) as cnt FROM documents GROUP BY category"
            )
            print(json.dumps(
                res.get("result", [{}])[0].get("results", []),
                ensure_ascii=False, indent=2
            ))
        else:
            print(f"❌ invalid report table: {table} (expected: invoices|documents)")
            sys.exit(2)

    else:
        print(f"❌ unknown command: {cmd}")
        show_help(2)
