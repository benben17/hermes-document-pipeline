#!/usr/bin/env python3
"""
doc_engine.py — 文档处理引擎
==============================

DocumentEngine 负责多格式文档的文本提取、本地归档、D1 元数据入库与
ChromaDB 向量索引，继承 HermesProjectCore 以复用基础 I/O 能力。

支持格式：
    PDF  — 通过 pdf_inspector（本地快速提取，无需 OCR）
    DOCX — 正文段落 + 表格（python-docx）
    TXT / MD / LOG / CSV — 多编码自动探测（utf-8 → gbk → latin-1）
    XLSX / XLS — 多 Sheet Markdown 表格（openpyxl）

典型调用方式（通过 project_manager.py 路由）：

    echo '{"title": "合同", "company": "XXX", ...}' | ./project-tool doc /path/to/file.pdf

也可直接实例化：

    from doc_engine import DocumentEngine
    engine = DocumentEngine()
    engine.process("/path/to/file.pdf", analysis_json_dict)

D1 表结构依赖（需提前建表）：

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

归档命名规则：
    YYYYMMDD_<公司前10字符>_<标题前15字符>_<MD5前8位>.<ext>

配置项：
    HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR — 文档归档目录
        默认: <project_root>/archive/documents
"""

import sys
import os
import re
import json
import shutil
import hashlib
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core_engine import HermesProjectCore


class DocumentEngine(HermesProjectCore):
    """
    文档业务引擎。

    方法：
      process(path, analysis_json)  — 归档 + D1 入库 + ChromaDB 索引（主流程）
      extract_text(path)            — 多格式文本提取（供外部调用或单独测试）
    """

    def __init__(self):
        super().__init__()
        # 文档归档目录，可通过环境变量 HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR 覆盖
        self.archive_dir = Path(
            self.config.get(
                "HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR",
                str(self.project_root / "archive" / "documents"),
            )
        )

    # ─────────────────────────────────────────────────────────────
    # 文本提取
    # ─────────────────────────────────────────────────────────────

    def extract_text(self, path: str) -> str:
        """
        根据文件扩展名自动选择提取策略，返回纯文本字符串。

        支持格式与底层实现：
          .pdf            — pdf_inspector.extract_text（本地，无网络，快速）
          .docx           — python-docx（段落 + 表格 → _extract_docx）
          .txt/.md/.log/.csv — 多编码尝试（utf-8 / gbk / gb2312 / latin-1）
          .xlsx/.xls      — openpyxl → Markdown 表格（_extract_excel）
          其他格式        — 返回空字符串（不报错）

        Args:
            path : 文件绝对路径

        Returns:
            提取到的文本内容；若格式不支持或文件为空则返回 ""
        """
        ext = os.path.splitext(path)[1].lower()

        if ext == ".pdf":
            import pdf_inspector
            return pdf_inspector.extract_text(path)

        elif ext == ".docx":
            return self._extract_docx(path)

        elif ext in (".txt", ".md", ".log", ".csv"):
            # 按优先级依次尝试多种编码，避免 GBK 文件以 utf-8 解码乱码
            for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
                try:
                    with open(path, "r", encoding=enc, errors="strict") as f:
                        return f.read()
                except (UnicodeDecodeError, LookupError):
                    continue
            # 兜底：忽略解码错误
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

        elif ext in (".xlsx", ".xls"):
            return self._extract_excel(path)

        else:
            return ""

    def _extract_docx(self, path: str) -> str:
        """
        提取 DOCX 文件内容，保留正文段落与表格结构。

        表格按行提取，单元格以 Tab 分隔，保留表格序号标记。

        Args:
            path : .docx 文件路径

        Returns:
            合并后的文本字符串（段落 + 表格）
        """
        import docx
        doc = docx.Document(path)
        chunks = []
        # 1. 正文段落（过滤空行）
        for p in doc.paragraphs:
            if p.text.strip():
                chunks.append(p.text)
        # 2. 表格（逐行提取，保留结构层次）
        for tbl_idx, table in enumerate(doc.tables):
            chunks.append(f"\n[表格 {tbl_idx + 1}]")
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                chunks.append("\t".join(cells))
        return "\n".join(chunks)

    def _extract_excel(self, path: str) -> str:
        """
        提取 XLSX/XLS 文件内容，多 Sheet 合并输出为 Markdown 表格。

        处理规则：
          - 跳过全空行
          - 第一行视为表头，自动追加 Markdown 分隔线（---）
          - None 单元格输出为空字符串

        Args:
            path : .xlsx / .xls 文件路径

        Returns:
            Markdown 格式的多 Sheet 文本
        """
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        chunks = []
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            # 裁剪全空行
            rows = [r for r in rows if any(v is not None for v in r)]
            if not rows:
                continue
            chunks.append(f"\n## Sheet: {ws.title}")
            for i, row in enumerate(rows):
                cells = ["" if v is None else str(v) for v in row]
                chunks.append(" | ".join(cells))
                # 第一行（表头）后插入 Markdown 分隔线
                if i == 0:
                    chunks.append(" | ".join(["---"] * len(cells)))
        return "\n".join(chunks)

    # ─────────────────────────────────────────────────────────────
    # 主处理流程
    # ─────────────────────────────────────────────────────────────

    def process(self, path: str, analysis_json: dict):
        """
        文档处理主流程：归档 → D1 入库 → ChromaDB 索引。

        流程说明：
          1. 校验文件存在性与格式支持
          2. 按命名规则生成归档文件名（含日期、公司、标题、MD5 前缀）
          3. 拷贝至归档目录（MD5 去重：内容相同则跳过）
          4. 提取文本（供向量化）
          5. UPSERT 元数据到 D1（ON CONFLICT(file_path)）
          6. 同步文本索引到 ChromaDB

        归档命名规则：
          YYYYMMDD_<公司前10字>_<标题前15字>_<MD5前8位>.<ext>

        Args:
            path          : 源文件绝对路径
            analysis_json : 模型分析结果 dict，常用字段：
                            title    — 文档标题（必填，作为 ChromaDB ID）
                            company  — 所属公司/机构
                            category — 文档类型（合同/报告/协议等）
                            summary  — 内容摘要
                            tags     — 标签（逗号分隔字符串）
                            raw_text — 若已有提取文本可直接传入（优先使用）

        Returns:
            归档后的文件 Path 对象（成功）；None（失败）

        注意：
            若 D1 写入失败，仍然完成了本地归档（文件已拷贝），
            ChromaDB 索引会被跳过。
        """
        if not os.path.isfile(path):
            print(f"❌ 文件不存在: {path}")
            return None

        ext = os.path.splitext(path)[1].lower()
        supported = {".pdf", ".docx", ".txt", ".md", ".log", ".csv", ".xlsx", ".xls"}
        if ext not in supported:
            print(f"⚠️ 不支持的格式: {ext}，跳过")
            return None

        # ── Step 1: 生成归档文件名 ────────────────────────────────
        with open(path, "rb") as f:
            src_md5 = hashlib.md5(f.read()).hexdigest()[:8]
        date_str = datetime.now().strftime("%Y%m%d")
        # 清理文件系统非法字符，防止路径注入
        safe_company = re.sub(r'[\\/*?:"<>|]', "", analysis_json.get("company", "Unknown"))[:10]
        safe_title   = re.sub(r'[\\/*?:"<>|]', "", analysis_json.get("title", "Doc"))[:15]
        new_name  = f"{date_str}_{safe_company}_{safe_title}_{src_md5}{ext}"
        dest_path = self.archive_dir / new_name

        # ── Step 2: 归档拷贝（MD5 去重） ─────────────────────────
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        if not dest_path.exists():
            shutil.copy(path, dest_path)
        else:
            print(f"ℹ️ 文件已归档（跳过重复）: {new_name}")

        # ── Step 3: 提取文本 ──────────────────────────────────────
        raw_text = analysis_json.get("raw_text") or self.extract_text(path)

        # ── Step 4: D1 UPSERT ─────────────────────────────────────
        sql = """
            INSERT INTO documents (title, company, category, summary, tags, file_path)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                title   = excluded.title,
                summary = excluded.summary,
                tags    = excluded.tags
        """
        params = [
            analysis_json.get("title"),
            analysis_json.get("company"),
            analysis_json.get("category"),
            analysis_json.get("summary"),
            analysis_json.get("tags"),
            str(dest_path),
        ]
        res = self.query_d1(sql, params)

        if res.get("success"):
            # ── Step 5: ChromaDB 向量化 ───────────────────────────
            metadata = {
                k: v for k, v in analysis_json.items()
                if k != "raw_text" and isinstance(v, (str, int, float, bool))
            }
            self.sync_to_chroma(
                collection_name="documents",
                document_id=analysis_json.get("title", new_name),
                text=raw_text,
                metadata=metadata,
            )
            print(f"✅ 文档已归档并同步 D1+Chroma: {new_name}")
            return dest_path
        else:
            print(f"❌ 文档入库 D1 失败: {res.get('errors', res)}")
            return None


if __name__ == "__main__":
    engine = DocumentEngine()
    if len(sys.argv) < 2:
        print("Usage: doc-tool <file_path> '<json_data>'")
        sys.exit(0)
    file_path = sys.argv[1]
    data = json.loads(sys.argv[2]) if len(sys.argv) > 2 else json.loads(sys.stdin.read())
    engine.process(file_path, data)
