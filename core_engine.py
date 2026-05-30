#!/usr/bin/env python3
"""
core_engine.py — HermesProjectCore 基类
========================================

所有引擎类（FinanceEngine、DocumentEngine 等）都应继承此基类，
以统一获得以下能力：

  • query_d1(sql, params)        — Cloudflare D1 SQL 查询（含自动重试）
  • sync_to_chroma(...)          — ChromaDB 语义索引写入（HttpClient v2）
  • archive_file(src, sub, name) — 本地文件归档 + MD5 去重
  • get_md5(path)                — 计算文件 MD5 指纹

继承关系：

    HermesProjectCore
        ├── FinanceEngine   (invoice_engine.py)
        └── DocumentEngine  (doc_engine.py)

设计原则：
  - 不持有任何业务逻辑，只封装基础 I/O 能力
  - 不硬编码路径或密钥；所有配置由 hermes_core.get_config() 注入
  - 子类 __init__ 必须调用 super().__init__() 以正确初始化 config / chroma 等字段
"""

import os
import json
import shutil
import hashlib
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_core import query_d1 as _query_d1, get_config, get_project_root


class HermesProjectCore:
    """
    所有业务引擎的基类。

    实例属性（由 __init__ 初始化）：
      self.config       : dict   — 项目配置，来源于 .env / 环境变量
      self.project_root : Path   — 项目根目录
      self.chroma_host  : str    — ChromaDB 主机（默认 localhost）
      self.chroma_port  : int    — ChromaDB 端口（默认 8000）
      self.chroma_url   : str    — ChromaDB HTTP API base URL
      self.base_dir     : str    — 归档根目录（即 project_root 字符串形式）
    """

    def __init__(self):
        self.config = get_config()
        self.project_root = get_project_root()
        # ChromaDB 连接参数
        self.chroma_host = self.config.get("CHROMA_HOST", "localhost")
        self.chroma_port = int(self.config.get("CHROMA_PORT", "8000"))
        self.chroma_url = f"http://{self.chroma_host}:{self.chroma_port}/api/v2"
        # 本地归档根目录（子类可基于此构建子路径）
        self.base_dir = str(self.project_root)

    # ─────────────────────────────────────────────────────────────
    # D1 查询
    # ─────────────────────────────────────────────────────────────

    def query_d1(self, sql: str, params=None) -> dict:
        """
        执行 Cloudflare D1 SQL 语句。

        Args:
            sql    : 合法的 SQL 语句（支持 ? 参数占位符）
            params : 参数列表，与 SQL 占位符一一对应

        Returns:
            Cloudflare D1 API 返回的原始 dict，通常结构为：
            {
                "success": bool,
                "result": [{"results": [...], "meta": {...}}],
                "errors": [...]        # 仅 success=False 时有值
            }

        注意：
            内部使用带指数退避重试的 requests.Session（最多重试 3 次），
            会在 429 / 5xx 时自动重试，无需调用方处理。
        """
        return _query_d1(sql, params)

    # ─────────────────────────────────────────────────────────────
    # ChromaDB 向量同步
    # ─────────────────────────────────────────────────────────────

    def sync_to_chroma(
        self,
        collection_name: str,
        document_id: str,
        text: str,
        metadata: dict,
    ) -> bool:
        """
        将一条文档写入 ChromaDB，供后续语义检索（RAG）使用。

        采用 upsert 语义：若 document_id 已存在则覆盖更新，否则新增。

        Args:
            collection_name : ChromaDB 集合名称，例如 "invoices" / "documents"
            document_id     : 集合内唯一 ID（通常用发票号、文件名等业务主键）
            text            : 要索引的原始文本内容
            metadata        : 随文档一同存储的元数据（键值均需为 str/int/float/bool）

        Returns:
            True  — 索引成功
            False — text 为空或写入异常（异常已在内部打印，不上抛）

        依赖：
            chromadb（可选依赖，未安装时写入失败但不影响 D1 入库流程）
        """
        if not text:
            print(f"⚠️ [VectorDB] {document_id} 没有提取到文本，跳过索引。")
            return False

        print(f"📡 [VectorDB] 正在为 {document_id} 建立语义索引...")
        try:
            import chromadb
            client = chromadb.HttpClient(host=self.chroma_host, port=self.chroma_port)
            col = client.get_or_create_collection(name=collection_name)
            col.upsert(
                ids=[str(document_id)],
                documents=[str(text)],
                metadatas=[metadata],
            )
            print(f"✅ [VectorDB] {document_id} 索引建立完成。")
            return True
        except Exception as e:
            print(f"❌ [VectorDB] {document_id} 同步 Chroma 失败: {e}")
            return False

    # ─────────────────────────────────────────────────────────────
    # 文件归档
    # ─────────────────────────────────────────────────────────────

    def archive_file(self, src_path: str, sub_dir: str, new_name: str) -> str:
        """
        将源文件归档到 <project_root>/archive/<sub_dir>/<new_name>。

        内置 MD5 去重：若目标文件已存在且与源文件内容相同，则跳过拷贝。

        Args:
            src_path : 源文件绝对路径
            sub_dir  : 归档子目录，例如 "invoices" / "documents"
            new_name : 归档后的文件名（含扩展名），建议含日期前缀以便排序

        Returns:
            归档目标文件的绝对路径（无论是否发生了实际拷贝）
        """
        dest_dir = os.path.join(self.base_dir, "archive", sub_dir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, new_name)

        if os.path.exists(dest_path) and self.get_md5(src_path) == self.get_md5(dest_path):
            print(f"⏭️  [Archive] 内容相同，跳过重复归档: {new_name}")
            return dest_path

        shutil.copy(src_path, dest_path)
        return dest_path

    # ─────────────────────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────────────────────

    def get_md5(self, path: str) -> str:
        """
        以流式方式计算文件 MD5 哈希，适用于大文件（无需全量读入内存）。

        Args:
            path : 文件绝对路径

        Returns:
            32 位十六进制 MD5 字符串
        """
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
