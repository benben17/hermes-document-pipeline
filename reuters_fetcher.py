#!/usr/bin/env python3
"""Fetch latest Reuters items with title + summary only.

使用 Google News RSS 搜索 Reuters 新闻，输出统一结构：
- title
- url
- summary
- pub_date
"""
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import requests

from news_sources import enrich_source

RSS_URL = "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SOURCE_NAME = "Reuters"
SOURCE_HOMEPAGE = "https://www.reuters.com"


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_reuters_news(limit: int = 10) -> list[dict]:
    resp = requests.get(RSS_URL, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    items = []
    seen = set()
    for item in root.findall('.//item'):
        title = _clean_text(item.findtext('title', default=''))
        link = _clean_text(item.findtext('link', default=''))
        desc = _clean_text(item.findtext('description', default=''))
        pub_date = _clean_text(item.findtext('pubDate', default=''))

        if not title or not link:
            continue

        if title.endswith(' - Reuters'):
            title = title[:-10].strip()

        # Google News RSS 的链接不是直接 Reuters 链接，但标题可稳定标注 Reuters 来源
        if 'Reuters' not in (item.findtext('title', default='')) and 'reuters' not in desc.lower():
            continue

        if link in seen:
            continue
        seen.add(link)

        items.append(enrich_source({
            'title': title,
            'url': link,
            'summary': desc[:300] if desc else '暂无摘要',
            'pub_date': pub_date,
        },
            source_name=SOURCE_NAME,
            source_homepage=SOURCE_HOMEPAGE,
            source_feed=RSS_URL,
            source_transport='google-news-rss'
        ))

    def sort_key(x: dict):
        try:
            return parsedate_to_datetime(x.get('pub_date', '')).timestamp()
        except Exception:
            return 0

    items.sort(key=sort_key, reverse=True)
    return items[:limit]


if __name__ == '__main__':
    items = fetch_reuters_news(limit=10)
    for i, item in enumerate(items, 1):
        print(f"{i}. {item['title']}")
        print(item['summary'])
        print(item['url'])
        print()
