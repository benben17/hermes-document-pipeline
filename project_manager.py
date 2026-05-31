#!/usr/bin/env python3
"""
project_manager.py — 顶层 CLI 路由
=====================================

本模块是 ./project-tool 的实际执行入口，负责将子命令路由到
对应的业务引擎（FinanceEngine / DocumentEngine）。

设计原则（轻量路由层）：
  - 不包含任何业务逻辑或 SQL
  - 只做命令解析、JSON 读取、引擎实例化与方法调用
  - 所有业务逻辑下沉到 FinanceEngine / DocumentEngine

支持的子命令：

  invoice             — 从 stdin 读取发票 JSON，写入 D1 + ChromaDB
  doc                 — 从 stdin 读取文档分析 JSON，归档 + 写入 D1 + ChromaDB
  report [invoices|documents]
                      — 统计报表（默认: documents）

用法示例：

  # 处理发票（推荐管道方式，避免 ARG_MAX 限制）
  echo '{"invoice_number": "INV-001", ...}' | ./project-tool invoice

  # 处理文档（需同时提供文件路径和分析结果）
  echo '{"title": "合同", "company": "XX", "file_path_src": "/tmp/a.pdf"}' | ./project-tool doc

  # 报表
  ./project-tool report invoices
  ./project-tool report documents
"""

import sys
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from invoice_engine import FinanceEngine
from doc_engine import DocumentEngine


def show_help(exit_code: int = 0):
    """打印帮助信息并退出。"""
    print("Usage:")
    print("  project_manager.py invoice                — process invoice JSON from stdin")
    print("  project_manager.py doc                    — process document JSON from stdin")
    print("  project_manager.py doc_summarize <file>   — get summary anchor (JSON)")
    print("  project_manager.py doc_search <query>     — global search D1 + ChromaDB (JSON)")
    print("  project_manager.py report [invoices|documents]")
    sys.exit(exit_code)


def load_json_stdin() -> dict:
    """
    从 stdin 读取并解析 JSON 载荷。

    使用 stdin 而非命令行参数，是为了规避大 payload 可能超出
    操作系统 ARG_MAX 限制的问题。

    Returns:
        解析后的 dict

    Raises:
        SystemExit(2) — stdin 为空、内容为 --help 标记、或 JSON 解析失败
    """
    data = sys.stdin.read().strip()
    if not data or data in ("-h", "--help"):
        show_help(2)
    try:
        return json.loads(data)
    except Exception as e:
        print(f"❌ invalid JSON payload: {e}")
        sys.exit(2)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        show_help(0)

    cmd = sys.argv[1]

    if cmd == "invoice":
        # 从 stdin 读取发票 JSON，交由 FinanceEngine 写入 D1 + ChromaDB
        data = load_json_stdin()
        FinanceEngine().insert_confirmed_data(data)

    elif cmd == "doc":
        # 从 stdin 读取文档分析 JSON
        # JSON 中需包含 file_path_src（源文件路径）或 file_path 字段
        data = load_json_stdin()
        file_path = data.pop("file_path_src", data.get("file_path", ""))
        DocumentEngine().process(file_path, data)

    elif cmd == "doc_summarize":
        # 获取文档摘要锚点（不读取全文入上下文）
        if len(sys.argv) < 3:
            sys.exit(1)
        print(json.dumps(DocumentEngine().get_summary_anchor(sys.argv[2]), ensure_ascii=False))

    elif cmd == "doc_search":
        # 全域检索：D1 元数据 + ChromaDB 语义
        if len(sys.argv) < 3:
            sys.exit(1)
        print(json.dumps(DocumentEngine().search_all(sys.argv[2]), ensure_ascii=False))

    elif cmd == "process":
        # 自动识别文档类型（发票 / 普通文档），路由到对应引擎
        if len(sys.argv) < 3:
            print("Usage: project_manager.py process <file_path>")
            sys.exit(1)
        from auto_router import AutoRouter
        AutoRouter().process(sys.argv[2])

    elif cmd == "report":
        # 统计报表：支持 invoices / documents 两张表
        table = sys.argv[2] if len(sys.argv) > 2 else "documents"

        if table == "invoices":
            # 委托 FinanceEngine 按购买方汇总
            FinanceEngine().report()

        elif table == "documents":
            # 直接查 D1，按类型统计文档数量
            from hermes_core import query_d1
            res = query_d1(
                "SELECT category, COUNT(*) AS cnt FROM documents GROUP BY category"
            )
            print(json.dumps(
                res.get("result", [{}])[0].get("results", []),
                ensure_ascii=False,
                indent=2,
            ))

        else:
            print(f"❌ invalid report table: {table} (expected: invoices|documents)")
            sys.exit(2)

    else:
        print(f"❌ unknown command: {cmd}")
        show_help(2)
