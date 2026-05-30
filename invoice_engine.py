#!/usr/bin/env python3
"""
invoice_engine.py — 发票处理引擎
==================================

FinanceEngine 负责发票数据的落库与统计报表，继承 HermesProjectCore
以复用 D1 查询、ChromaDB 向量同步等基础能力。

典型调用方式（通过 project_manager.py 路由）：

    echo '{"invoice_number": "INV-001", ...}' | ./project-tool invoice

也可直接实例化：

    from invoice_engine import FinanceEngine
    engine = FinanceEngine()
    engine.insert_confirmed_data(data_dict)
    engine.report()

D1 表结构依赖（需提前建表）：

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
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_engine import HermesProjectCore


class FinanceEngine(HermesProjectCore):
    """
    发票业务引擎。

    方法：
      insert_confirmed_data(data) — 将模型解析后的发票 dict 写入 D1 并索引 ChromaDB
      report()                    — 按购买方汇总统计发票张数与金额
    """

    def insert_confirmed_data(self, data: dict):
        """
        将经模型确认的发票数据写入 Cloudflare D1，并同步语义索引到 ChromaDB。

        采用 UPSERT 语义（ON CONFLICT(invoice_number)）：
          - 若 invoice_number 已存在，更新 buyer_name / seller_name /
            item_name / total_amount / invoice_date 五个字段；
          - 若不存在，全量插入。

        Args:
            data : 发票信息 dict，常用字段：
                   invoice_number  — 发票号（主键，必填）
                   invoice_date    — 开票日期（yyyy-mm-dd）
                   buyer_name      — 购买方名称
                   seller_name     — 销售方名称
                   item_name       — 商品/服务名称
                   amount_net      — 不含税金额
                   tax_amount      — 税额
                   total_amount    — 价税合计
                   file_path       — 原始文件路径（可选，默认 'manual_entry'）
                   raw_text        — 发票 OCR 全文（可选，用于 ChromaDB 索引）

        副作用：
            1. D1 upsert
            2. ChromaDB upsert（仅当 raw_text 非空时生效）
        """
        sql = """
            INSERT INTO invoices (
                invoice_number, invoice_date, buyer_name, seller_name,
                item_name, amount_net, tax_amount, total_amount, file_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_number) DO UPDATE SET
                buyer_name    = excluded.buyer_name,
                seller_name   = excluded.seller_name,
                item_name     = excluded.item_name,
                total_amount  = excluded.total_amount,
                invoice_date  = excluded.invoice_date
        """
        params = [
            data.get("invoice_number"),
            data.get("invoice_date"),
            data.get("buyer_name"),
            data.get("seller_name"),
            data.get("item_name"),
            data.get("amount_net"),
            data.get("tax_amount"),
            data.get("total_amount"),
            data.get("file_path", "manual_entry"),
        ]

        # ── 前置查重：invoice_number 已存在则跳过，不执行 UPSERT ────
        dup = self.query_d1(
            "SELECT invoice_number, created_at FROM invoices WHERE invoice_number = ?",
            [data.get("invoice_number")],
        )
        dup_rows = dup.get("result", [{}])[0].get("results", [])
        if dup_rows:
            print(
                f"⚠️  重复发票，已跳过入库: "
                f"{data.get('invoice_number')} "
                f"（首次入库于 {dup_rows[0].get('created_at')}）"
            )
            return

        res = self.query_d1(sql, params)
        if res.get("success"):
            # 过滤掉 raw_text 和非基本类型字段，以满足 ChromaDB metadata 要求
            metadata = {
                k: v for k, v in data.items()
                if k != "raw_text" and isinstance(v, (str, int, float, bool))
            }
            self.sync_to_chroma(
                collection_name="invoices",
                document_id=data.get("invoice_number", ""),
                text=data.get("raw_text", ""),
                metadata=metadata,
            )
            print(
                f"✅ 发票已同步 D1+Chroma: "
                f"{data.get('invoice_number')} | "
                f"{data.get('buyer_name')} | "
                f"¥{data.get('total_amount')}"
            )
        else:
            print(f"❌ 发票写入 D1 失败: {res.get('errors')}")

    def report(self):
        """
        按购买方汇总统计发票数量与合计金额，结果降序打印到 stdout。

        依赖 D1 中 invoices 表存在 buyer_name / total_amount 字段。
        """
        res = self.query_d1(
            "SELECT buyer_name, COUNT(*) AS cnt, SUM(total_amount) AS total "
            "FROM invoices "
            "GROUP BY buyer_name "
            "ORDER BY total DESC"
        )
        if not res.get("success"):
            print(f"❌ 查询失败: {res.get('errors')}")
            return

        print("#### 🏢 财务审计报表")
        rows = res.get("result", [{}])[0].get("results", [])
        if not rows:
            print("  （暂无记录）")
            return
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
