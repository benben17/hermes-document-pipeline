#!/usr/bin/env python3
"""Fetch Bloomberg Markets RSS and store a lightweight feed snapshot in ChromaDB."""

from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

import chromadb
import requests
from chromadb.api.types import Metadata

from hermes_core import get_config, get_project_root
from news_sources import enrich_source

CFG = get_config()
PROJECT_ROOT = get_project_root()
RSS_URL = "https://feeds.bloomberg.com/markets/news.rss"
CHROMA_HOST = CFG.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(CFG.get("CHROMA_PORT", "8000"))
COLLECTION_NAME = "bloomberg_rss"
OUTPUT_MD = Path(CFG.get("BLOOMBERG_RSS_OUTPUT_FILE", str(PROJECT_ROOT / "bloomberg_rss_latest.md")))
NEWS_TARGET = CFG.get("HERMES_NEWS_TARGET", "qqbot")
SOURCE_NAME = "Bloomberg"
SOURCE_HOMEPAGE = "https://www.bloomberg.com"


def fetch_rss(url: str) -> list[dict]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item")[:10]:
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None:
            continue
        items.append(
            enrich_source(
                {
                    "title": (title_el.text or "").strip(),
                    "link": (link_el.text or "").strip(),
                },
                source_name=SOURCE_NAME,
                source_homepage=SOURCE_HOMEPAGE,
                source_feed=RSS_URL,
                source_transport="rss",
            )
        )
    return items


def store_chroma(docs: list[dict]):
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    coll = client.get_or_create_collection(name=COLLECTION_NAME)
    ids = [f"bloomberg-rss-{i}" for i in range(len(docs))]
    texts = [f"{d['title']}\n{d['link']}" for d in docs]
    metadatas = cast(list[Metadata], [{
        "source": "bloomberg_rss",
        "source_name": d.get("source_name", SOURCE_NAME),
        "source_url": d.get("source_url", SOURCE_HOMEPAGE),
        "source_domain": d.get("source_domain", "bloomberg.com"),
        "source_feed": d.get("source_feed", RSS_URL),
        "source_transport": d.get("source_transport", "rss"),
    } for d in docs])
    coll.upsert(ids=ids, documents=texts, metadatas=metadatas)


def write_md(items: list[dict]):
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_MD.open("w", encoding="utf-8") as f:
        f.write(f"# Bloomberg Markets – Latest {len(items)} items (generated {time.strftime('%Y-%m-%d %H:%M UTC')})\n\n")
        for i, it in enumerate(items, 1):
            f.write(f"{i}. [{it['title']}]({it['link']})\n\n")
            f.write(f"   - Source: {it.get('source_name', SOURCE_NAME)} / {it.get('source_domain', 'bloomberg.com')} / {it.get('source_transport', 'rss')}\n")
            if it.get('source_feed'):
                f.write(f"   - Feed: {it['source_feed']}\n")
            f.write("\n")


def send_marker():
    print(f"Generated markdown snapshot: {OUTPUT_MD}")


def main():
    items = fetch_rss(RSS_URL)
    if not items:
        print("No items fetched", file=sys.stderr)
        sys.exit(1)
    store_chroma(items)
    write_md(items)
    send_marker()


if __name__ == "__main__":
    main()
