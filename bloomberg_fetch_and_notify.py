#!/usr/bin/env python3
"""Fetch latest Bloomberg news, write concise markdown summary, optionally send via Hermes."""

from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from hermes_core import get_config, get_project_root
from news_sources import enrich_source, source_markdown

CFG = get_config()
PROJECT_ROOT = get_project_root()
RSS_URL = "https://feeds.bloomberg.com/markets/news.rss"
NEWS_TARGET = CFG.get("HERMES_NEWS_TARGET", "qqbot")
OUTPUT_FILE = Path(CFG.get("BLOOMBERG_OUTPUT_FILE", str(PROJECT_ROOT / "bloomberg_latest.md")))
SOURCE_NAME = "Bloomberg"
SOURCE_HOMEPAGE = "https://www.bloomberg.com"


def fetch_rss(url: str) -> list[dict]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        pub_el = item.find("pubDate")
        if title_el is None or link_el is None:
            continue
        title = (title_el.text or "").strip()
        link = (link_el.text or "").strip()
        desc = (desc_el.text or "").strip() if desc_el is not None and desc_el.text else ""
        pub_date = (pub_el.text or "").strip() if pub_el is not None and pub_el.text else ""
        items.append(
            enrich_source(
                {
                    "title": title,
                    "url": link,
                    "summary": desc[:300],
                    "pub_date": pub_date,
                },
                source_name=SOURCE_NAME,
                source_homepage=SOURCE_HOMEPAGE,
                source_feed=RSS_URL,
                source_transport="rss",
            )
        )

    def sort_key(x: dict):
        try:
            return parsedate_to_datetime(x.get("pub_date", "")).timestamp()
        except Exception:
            return 0

    items.sort(key=sort_key, reverse=True)
    return items[:10]


def write_summary(items: list[dict], out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# Bloomberg 最新新闻（{time.strftime('%Y-%m-%d %H:%M UTC')}）\n\n")
        for i, it in enumerate(items[:10], 1):
            f.write(f"## {i}. [{it['title']}]({it['url']})\n\n")
            f.write(f"**来源：** {source_markdown(it)}\n\n")
            summary = (it.get("summary") or "").strip() or "暂无摘要"
            f.write(f"**总结：** {summary}\n\n")
            pub_date = (it.get("pub_date") or "").strip()
            if pub_date:
                f.write(f"**发布时间：** {pub_date}\n\n")
            f.write("---\n\n")


def send_via_hermes(file_path: Path):
    result = subprocess.run(
        [
            "hermes",
            "send",
            "-t",
            NEWS_TARGET,
            "-f",
            str(file_path),
            "-s",
            f"📊 Bloomberg 最新资讯 {time.strftime('%Y-%m-%d %H:%M UTC')}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def main():
    items = fetch_rss(RSS_URL)
    if not items:
        raise SystemExit("No Bloomberg items fetched")
    write_summary(items, OUTPUT_FILE)
    send_via_hermes(OUTPUT_FILE)
    print(f"✅ Bloomberg 推送完成: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
