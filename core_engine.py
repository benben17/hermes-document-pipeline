#!/usr/bin/env python3
"""
core_engine.py — HermesProjectCore 基类
========================================

所有引擎类（FinanceEngine、DocumentEngine 等）都继承此基类，
以统一获得以下能力：

  • query_d1(sql, params)        — Cloudflare D1 SQL 查询（含自动重试）
  • sync_to_chroma_chunks(...)   — ChromaDB 批量分片索引写入
  • query_chroma(...)            — ChromaDB 语义检索
  • archive_file(src, sub, name) — 本地文件归档 + MD5 去重
  • get_md5(path)                — 计算文件 MD5 指纹

设计原则：
  - 不持有任何业务逻辑，只封装基础 I/O 能力
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
    def __init__(self):
        self.config = get_config()
        self.project_root = get_project_root()
        self.chroma_host = self.config.get("CHROMA_HOST", "localhost")
        self.chroma_port = int(self.config.get("CHROMA_PORT", "8000"))
        self.chroma_url = f"http://{self.chroma_host}:{self.chroma_port}/api/v2"
        self.base_dir = str(self.project_root)

    def query_d1(self, sql: str, params=None) -> dict:
        return _query_d1(sql, params)

    def sync_to_chroma_chunks(
        self,
        collection_name: str,
        document_id: str,
        chunks: list[str],
        metadata: dict,
    ) -> bool:
        """
        批量写入分片，并强制附加 doc_id 标签以便精准过滤。
        """
        if not chunks:
            return False
        try:
            import chromadb
            client = chromadb.HttpClient(host=self.chroma_host, port=self.chroma_port)
            col = client.get_or_create_collection(name=collection_name)
            
            ids = [f"{document_id}_ch{i}" for i in range(len(chunks))]
            # 强制注入 doc_id 字段用于 Step 4 的精准过滤
            metas = [{**metadata, "doc_id": document_id, "chunk_idx": i} for i in range(len(chunks))]
            
            col.upsert(
                ids=ids,
                documents=chunks,
                metadatas=metas,
            )
            print(f"✅ [VectorDB] {document_id} 批量索引完成 ({len(chunks)} chunks)")
            return True
        except Exception as e:
            print(f"❌ [VectorDB] 批量同步失败: {e}")
            return False

    def query_chroma(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 3,
        where: dict = None,
    ) -> dict:
        try:
            import chromadb
            client = chromadb.HttpClient(host=self.chroma_host, port=self.chroma_port)
            col = client.get_collection(name=collection_name)
            return col.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where
            )
        except Exception as e:
            print(f"❌ [VectorDB] 查询 Chroma 失败: {e}")
            return {}

    def archive_file(self, src_path: str, sub_dir: str, new_name: str) -> str:
        dest_dir = os.path.join(self.base_dir, "archive", sub_dir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, new_name)
        if os.path.exists(dest_path) and self.get_md5(src_path) == self.get_md5(dest_path):
            return dest_path
        shutil.copy(src_path, dest_path)
        return dest_path

    def get_md5(self, path: str) -> str:
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
