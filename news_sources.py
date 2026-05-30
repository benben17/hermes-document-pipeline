#!/usr/bin/env python3
"""Shared source/provenance helpers for news/feed items."""

from __future__ import annotations

from urllib.parse import urlparse


def _normalize_url(url: str | None) -> str:
    return (url or "").strip()


def _domain_from_url(url: str | None) -> str:
    parsed = urlparse(_normalize_url(url))
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def enrich_source(
    item: dict,
    *,
    source_name: str,
    source_homepage: str,
    source_feed: str,
    source_transport: str,
) -> dict:
    article_url = _normalize_url(item.get("url"))
    source_domain = _domain_from_url(source_homepage) or _domain_from_url(article_url)
    enriched = dict(item)
    enriched.update(
        {
            "source_name": source_name,
            "source_url": _normalize_url(source_homepage),
            "source_domain": source_domain,
            "source_feed": _normalize_url(source_feed),
            "source_transport": source_transport,
        }
    )
    return enriched


def source_markdown(item: dict) -> str:
    source_name = (item.get("source_name") or "未知来源").strip()
    source_domain = (item.get("source_domain") or "").strip()
    source_transport = (item.get("source_transport") or "").strip()
    parts = [source_name]
    if source_domain:
        parts.append(source_domain)
    if source_transport:
        parts.append(source_transport)
    return " / ".join(parts)
