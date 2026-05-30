#!/usr/bin/env python3
"""Fetch Reuters latest 10 articles, write concise markdown summary, optionally send via Hermes."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from hermes_core import get_config, get_project_root
from news_sources import source_markdown
from reuters_fetcher import fetch_reuters_news

CFG = get_config()
PROJECT_ROOT = get_project_root()
NEWS_TARGET = CFG.get("HERMES_NEWS_TARGET", "qqbot")
OUTPUT_FILE = Path(CFG.get("REUTERS_OUTPUT_FILE", str(PROJECT_ROOT / "reuters_latest.md")))


def write_summary(items, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# Reuters 最新新闻（{time.strftime('%Y-%m-%d %H:%M UTC')}）\n\n")
        for i, item in enumerate(items[:10], 1):
            f.write(f"## {i}. [{item['title']}]({item['url']})\n\n")
            f.write(f"**来源：** {source_markdown(item)}\n\n")
            summary = (item.get("summary") or "").strip() or "暂无摘要"
            f.write(f"**总结：** {summary}\n\n")
            pub_date = (item.get("pub_date") or "").strip()
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
            f"📰 Reuters 最新资讯 {time.strftime('%Y-%m-%d %H:%M UTC')}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def main():
    items = fetch_reuters_news(limit=10)
    if not items:
        raise SystemExit("No Reuters items fetched")
    write_summary(items, OUTPUT_FILE)
    send_via_hermes(OUTPUT_FILE)
    print(f"✅ Reuters 推送完成: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
