#!/usr/bin/env python3
"""DocumentEngine — 统一文档提取+归档+D1+Chroma 入口。
支持格式：PDF / DOCX / TXT / XLSX
"""
import sys, os, re, json, shutil, hashlib
from pathlib import Path
from datetime import datetime

# 统一使用 hermes_core 的 D1 session（含重试机制）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_core import query_d1 as _query_d1, get_config, get_project_root


class DocumentEngine:
    def __init__(self):
        self.config = get_config()
        self.project_root = get_project_root()
        self.chroma_host = self.config.get("CHROMA_HOST", "localhost")
        self.chroma_port = int(self.config.get("CHROMA_PORT", "8000"))
        self.archive_dir = Path(
            self.config.get(
                "HERMES_PROJECT_DOCUMENT_ARCHIVE_DIR",
                str(self.project_root / "archive" / "documents"),
            )
        )

    # ── 文本提取 ────────────────────────────────────────────────
    def extract_text(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()

        if ext == '.pdf':
            import pdf_inspector
            return pdf_inspector.extract_text(path)

        elif ext == '.docx':
            return self._extract_docx(path)

        elif ext in ('.txt', '.md', '.log', '.csv'):
            # 自动尝试多种编码，抵御 GBK 文件
            for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
                try:
                    with open(path, 'r', encoding=enc, errors='strict') as f:
                        return f.read()
                except (UnicodeDecodeError, LookupError):
                    continue
            # 最终兜底
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()

        elif ext in ('.xlsx', '.xls'):
            return self._extract_excel(path)

        else:
            return ""

    def _extract_docx(self, path: str) -> str:
        """提取 DOCX：段落 + 表格（按行拼成 TSV）"""
        import docx
        doc = docx.Document(path)
        chunks = []
        # 1. 正文段落
        for p in doc.paragraphs:
            if p.text.strip():
                chunks.append(p.text)
        # 2. 表格（逐行提取，保留结构）
        for tbl_idx, table in enumerate(doc.tables):
            chunks.append(f"\n[表格 {tbl_idx + 1}]")
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                chunks.append('\t'.join(cells))
        return '\n'.join(chunks)

    def _extract_excel(self, path: str) -> str:
        """提取 XLSX：多 sheet 合并，自动裁剪空行/空列，输出 Markdown 表格"""
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
                cells = ['' if v is None else str(v) for v in row]
                line = ' | '.join(cells)
                chunks.append(line)
                # 第一行后插入 Markdown 表格分隔线
                if i == 0:
                    chunks.append(' | '.join(['---'] * len(cells)))
        return '\n'.join(chunks)

    # ── D1 / Chroma ─────────────────────────────────────────────
    def query_d1(self, sql, params=None):
        return _query_d1(sql, params)

    def sync_to_chroma(self, title: str, content: str, metadata: dict):
        """将文档内容写入 ChromaDB (HttpClient, v2 API)"""
        if not content:
            return
        try:
            import chromadb
            client = chromadb.HttpClient(host=self.chroma_host, port=self.chroma_port)
            col = client.get_or_create_collection(name="documents")
            col.upsert(
                ids=[str(title)],
                documents=[str(content)],
                metadatas=[metadata]
            )
        except Exception as e:
            print(f"⚠️ [ChromaDB] 文档向量化失败: {e}")

    # ── 主流程 ───────────────────────────────────────────────────
    def process(self, path: str, analysis_json: dict):
        """
        analysis_json: 模型预先分析好的结构化数据
        支持格式: PDF / DOCX / TXT / XLSX
        """
        if not os.path.isfile(path):
            print(f"❌ 文件不存在: {path}")
            return None

        ext = os.path.splitext(path)[1].lower()
        supported = {'.pdf', '.docx', '.txt', '.md', '.log', '.csv', '.xlsx', '.xls'}
        if ext not in supported:
            print(f"⚠️ 不支持的格式: {ext}，跳过")
            return None

        # 1. 归档（MD5 去重，避免重复归档）
        archive_dir = self.archive_dir
        archive_dir.mkdir(parents=True, exist_ok=True)
        with open(path, 'rb') as src_file:
            src_md5 = hashlib.md5(src_file.read()).hexdigest()[:8]
        date_str = datetime.now().strftime("%Y%m%d")
        safe_company = re.sub(r'[\\/*?:"<>|]', "", analysis_json.get('company', 'Unknown'))[:10]
        safe_title = re.sub(r'[\\/*?:"<>|]', "", analysis_json.get('title', 'Doc'))[:15]
        new_name = f"{date_str}_{safe_company}_{safe_title}_{src_md5}{ext}"
        dest_path = archive_dir / new_name
        if not dest_path.exists():
            shutil.copy(path, dest_path)
        else:
            print(f"ℹ️ 文件已归档（跳过重复）: {new_name}")

        # 2. 提取文本（供 Chroma 向量化）
        raw_text = self.extract_text(path)

        # 3. 同步 D1
        sql = """INSERT INTO documents (title, company, category, summary, tags, file_path)
                 VALUES (?, ?, ?, ?, ?, ?)
                 ON CONFLICT(file_path) DO UPDATE SET title=excluded.title, summary=excluded.summary, tags=excluded.tags"""
        params = [
            analysis_json.get('title'), analysis_json.get('company'),
            analysis_json.get('category'), analysis_json.get('summary'),
            analysis_json.get('tags'), dest_path
        ]
        res = self.query_d1(sql, params)

        if res.get("success"):
            # 4. Chroma 向量化
            metadata = {k: v for k, v in analysis_json.items()
                        if k != 'raw_text' and isinstance(v, (str, int, float, bool))}
            self.sync_to_chroma(analysis_json.get('title', new_name), raw_text, metadata)
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
