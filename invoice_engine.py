#!/usr/bin/env python3
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_engine import HermesProjectCore


class FinanceEngine(HermesProjectCore):
    """Invoice engine — inherits D1/Chroma/archive from HermesProjectCore."""

    def insert_confirmed_data(self, data):
        """写入经模型确认后的数据"""
        sql = """INSERT INTO invoices (invoice_number, invoice_date, buyer_name, seller_name, item_name, amount_net, tax_amount, total_amount, file_path)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(invoice_number) DO UPDATE SET 
                   buyer_name=excluded.buyer_name, 
                   seller_name=excluded.seller_name, 
                   item_name=excluded.item_name, 
                   total_amount=excluded.total_amount, 
                   invoice_date=excluded.invoice_date"""
        params = [
            data.get('invoice_number'), data.get('invoice_date'),
            data.get('buyer_name'), data.get('seller_name'),
            data.get('item_name'), data.get('amount_net'),
            data.get('tax_amount'), data.get('total_amount'),
            data.get('file_path', 'manual_entry')
        ]
        res = self.query_d1(sql, params)
        if res.get("success"):
            metadata = {k: v for k, v in data.items()
                        if k != 'raw_text' and isinstance(v, (str, int, float, bool))}
            self.sync_to_chroma("invoices", data.get('invoice_number', ''),
                                data.get('raw_text', ''), metadata)
            print(f"✅ 模型确认数据已同步 D1+Chroma: {data.get('invoice_number')} | {data.get('buyer_name')} | ¥{data.get('total_amount')}")
        else:
            print(f"❌ 写入失败: {res.get('errors')}")

    def report(self):
        res = self.query_d1(
            "SELECT buyer_name, COUNT(*) as cnt, SUM(total_amount) as total "
            "FROM invoices GROUP BY buyer_name ORDER BY total DESC"
        )
        if not res.get("success"):
            print(f"❌ 查询失败: {res.get('errors')}")
            return
        print("#### 🏢 财务审计报表 (模型验证版)")
        rows = res.get("result", [{}])[0].get("results", [])
        for b in rows:
            print(f"  {b['buyer_name']}  {b['cnt']} 张  ¥{b['total']:.2f}")

if __name__ == "__main__":
    engine = FinanceEngine()
    if len(sys.argv) < 2:
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "report":
        engine.report()
    elif cmd == "insert":
        data = json.loads(sys.argv[2])
        engine.insert_confirmed_data(data)
