#!/usr/bin/env python3
"""Translate news titles and summaries to Chinese using OpenAI-compatible API.

Uses the project's configured LLM provider (NVIDIA GLM-5.1 by default).
Batch-translates multiple items in a single API call for efficiency.
"""

from __future__ import annotations

import json
import os
import requests
from pathlib import Path
from typing import Any

# ── Config ──────────────────────────────────────────────────────────────
# Read from hermes_core config or env; fall back to sensible defaults
_PROJECT_ROOT = Path(__file__).resolve().parent


def _get_llm_config() -> dict[str, str]:
    """Resolve LLM endpoint from hermes config / env."""
    # 1) Explicit env overrides
    base_url = os.getenv("TRANSLATE_LLM_BASE_URL", "").rstrip("/")
    api_key = os.getenv("TRANSLATE_LLM_API_KEY", "")
    model = os.getenv("TRANSLATE_LLM_MODEL", "")

    if base_url and api_key:
        return {"base_url": base_url, "api_key": api_key, "model": model or "gpt-4o-mini"}

    # 2) Read from project .env (hermes_core style)
    env_file = _PROJECT_ROOT / ".env"
    env_vars: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip()

    # 3) Try Hermes config.yaml for custom provider
    config_path = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))) / "config.yaml"
    if config_path.exists() and not base_url:
        try:
            import yaml
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            providers = cfg.get("custom_providers", [])
            for p in providers:
                name = p.get("name", "")
                # Prefer a fast/cheap model for translation; use first available
                if p.get("base_url") and p.get("api_key"):
                    base_url = p["base_url"].rstrip("/")
                    api_key = p["api_key"]
                    # Prefer nvidia/glm for translation (cheap + good Chinese)
                    if "nvidia" in name.lower() or "glm" in p.get("model", "").lower():
                        model = p.get("model", "gpt-4o-mini")
                        break
                    # Fallback to first provider
                    if not model:
                        model = p.get("model", "gpt-4o-mini")
        except Exception:
            pass

    # 4) Fallback to env vars
    if not base_url:
        base_url = env_vars.get("TRANSLATE_LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
    if not api_key:
        api_key = env_vars.get("TRANSLATE_LLM_API_KEY", env_vars.get("NVIDIA_API_KEY", ""))
    if not model:
        model = env_vars.get("TRANSLATE_LLM_MODEL", "z-ai/glm-5.1")

    return {"base_url": base_url, "api_key": api_key, "model": model}


def translate_items(items: list[dict], *, source_name: str = "News") -> list[dict]:
    """Translate title + summary of each news item to Chinese in-place.

    Batched: sends all items in one API call to save tokens and latency.
    Falls back to original text on any failure.
    """
    if not items:
        return items

    cfg = _get_llm_config()
    if not cfg["api_key"]:
        # No API key available — skip translation silently
        return items

    # Build the batch prompt
    numbered = []
    for i, it in enumerate(items, 1):
        title = it.get("title", "")
        summary = it.get("summary", "")
        numbered.append(f'{i}. TITLE: "{title}"\n   SUMMARY: "{summary}"')

    block = "\n".join(numbered)

    system_msg = (
        "你是一个专业的财经新闻翻译。将英文新闻标题和摘要翻译为简洁流畅的中文。"
        "保留专有名词（人名、公司名、地名）的英文原文或通用中文译名。"
        "输出严格的 JSON 数组格式，每个元素是 {\"title_zh\": \"...\", \"summary_zh\": \"...\"}。"
        "不要输出任何其他内容。"
    )
    user_msg = f"将以下{source_name}新闻翻译为中文：\n\n{block}"

    try:
        resp = requests.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON — handle markdown code fences
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        translations = json.loads(content)

        if not isinstance(translations, list):
            raise ValueError(f"Expected list, got {type(translations)}")

        for i, t in enumerate(translations):
            if i >= len(items):
                break
            if isinstance(t, dict):
                if t.get("title_zh"):
                    items[i]["title_zh"] = t["title_zh"]
                if t.get("summary_zh"):
                    items[i]["summary_zh"] = t["summary_zh"]

    except Exception as exc:
        # Translation failed — keep original English, don't break the pipeline
        print(f"⚠️  翻译失败（{source_name}）: {exc}")
        print("   将使用原文推送。")

    return items
