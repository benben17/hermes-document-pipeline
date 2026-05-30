#!/usr/bin/env python3
import os, json, shutil, hashlib
from datetime import datetime

# 统一使用 hermes_core 的 D1 session（含指数退避重试）
import sys
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

    def query_d1(self, sql, params=None):
        return _query_d1(sql, params)

    def sync_to_chroma(self, collection_name, document_id, text, metadata):
        """将内容同步到向量数据库 (RAG)"""
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
                metadatas=[metadata]
            )
            print(f"✅ [VectorDB] {document_id} 索引建立完成。")
            return True
        except Exception as e:
            print(f"❌ [VectorDB] {document_id} 同步 Chroma 失败: {e}")
            return False

    def archive_file(self, src_path, sub_dir, new_name):
        dest_dir = os.path.join(self.base_dir, "archive", sub_dir)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, new_name)
        # MD5 去重：若内容相同则跳过拷贝
        if os.path.exists(dest_path) and self.get_md5(src_path) == self.get_md5(dest_path):
            print(f"⏭️  [Archive] 内容相同，跳过重复归档: {new_name}")
            return dest_path
        shutil.copy(src_path, dest_path)
        return dest_path

    def get_md5(self, path):
        hasher = hashlib.md5()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
