#!/usr/bin/env python3
import sys, json
from core_engine import HermesProjectCore


def show_help(exit_code: int = 0):
    print("Usage:")
    print("  project_manager.py invoice [json_inline]")
    print("  project_manager.py doc [json_inline]")
    print("  project_manager.py report [invoices|documents]")
    sys.exit(exit_code)


class ProjectManager(HermesProjectCore):
    def process_invoice(self, data):
        sql = """INSERT INTO invoices (invoice_number, invoice_date, buyer_name, seller_name, item_name, amount_net, tax_amount, total_amount, file_path)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(invoice_number) DO UPDATE SET buyer_name=excluded.buyer_name, total_amount=excluded.total_amount"""
        params = [
            data['invoice_number'], data['invoice_date'], data['buyer_name'],
            data['seller_name'], data['item_name'], data.get('amount_net', 0),
            data.get('tax_amount', 0), data['total_amount'], data['file_path']
        ]
        res = self.query_d1(sql, params)
        if res.get("success"):
            metadata = {k: v for k, v in data.items() if k != 'raw_text' and isinstance(v, (str, int, float, bool))}
            self.sync_to_chroma("invoices", data['invoice_number'], data.get('raw_text', ''), metadata)
            print(f"✅ 发票已存入云端并向量化: {data['invoice_number']}")
        else:
            print(f"❌ 发票入库 D1 失败: {res.get('errors', res)}")
            sys.exit(3)

    def process_document(self, data):
        sql = """INSERT INTO documents (title, company, category, summary, tags, file_path)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT(file_path) DO UPDATE SET summary=excluded.summary, tags=excluded.tags"""
        params = [
            data['title'], data['company'], data['category'],
            data['summary'], data['tags'], data['file_path']
        ]
        res = self.query_d1(sql, params)
        if res.get("success"):
            metadata = {k: v for k, v in data.items() if k != 'raw_text' and isinstance(v, (str, int, float, bool))}
            self.sync_to_chroma("documents", data['title'], data.get('raw_text', ''), metadata)
            print(f"✅ 文档已存入云端并向量化: {data['title']}")
        else:
            print(f"❌ 文档入库 D1 失败: {res.get('errors', res)}")
            sys.exit(3)

    def report(self, table="documents"):
        if table == "invoices":
            sql = "SELECT buyer_name, COUNT(*) as cnt, SUM(total_amount) as total FROM invoices GROUP BY buyer_name"
        elif table == "documents":
            sql = "SELECT category, COUNT(*) as cnt FROM documents GROUP BY category"
        else:
            print(f"❌ invalid report table: {table} (expected: invoices|documents)")
            sys.exit(2)
        res = self.query_d1(sql)
        print(json.dumps(res.get("result", [{}])[0].get("results", []), ensure_ascii=False, indent=2))


def load_json_arg_or_stdin() -> dict:
    data = sys.stdin.read().strip()
    raw = data if data else (sys.argv[2] if len(sys.argv) > 2 else '')
    if not raw or raw in ('-h', '--help'):
        show_help(2)
    try:
        return json.loads(raw)
    except Exception as e:
        print(f"❌ invalid JSON payload: {e}")
        sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        show_help(0)

    pm = ProjectManager()
    cmd = sys.argv[1]
    if cmd == "invoice":
        pm.process_invoice(load_json_arg_or_stdin())
    elif cmd == "doc":
        pm.process_document(load_json_arg_or_stdin())
    elif cmd == "report":
        pm.report(sys.argv[2] if len(sys.argv) > 2 else "documents")
    else:
        print(f"❌ unknown command: {cmd}")
        show_help(2)
