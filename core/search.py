"""
Web search — DuckDuckGo (no API key, works offline via local SearXNG if configured).

Primary:  duckduckgo-search Python package (scrapes DDG Lite, no key needed)
Fallback: SearXNG self-hosted instance (set SEARXNG_URL in .env)
Offline:  returns empty results gracefully when no internet available

Usage:
  results = await web_search("python asyncio tutorial", max_results=5)
"""
import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SEARXNG_URL: str = os.getenv("SEARXNG_URL", "")


async def web_search(query: str, max_results: int = 6) -> list[dict]:
    """
    Search the web. Returns list of {title, url, snippet}.
    Tries DDG first, falls back to SearXNG, then returns empty list.
    """
    try:
        return await asyncio.to_thread(_ddg_search, query, max_results)
    except Exception as exc:
        logger.debug("DDG search failed (%s), trying SearXNG", exc)

    if SEARXNG_URL:
        try:
            return await _searxng_search(query, max_results)
        except Exception as exc:
            logger.debug("SearXNG search failed: %s", exc)

    logger.warning("All search backends unavailable for query: %s", query)
    return []


def _ddg_search(query: str, max_results: int) -> list[dict]:
    from duckduckgo_search import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title", ""),
                "url":     r.get("href", ""),
                "snippet": r.get("body", ""),
            })
    return results


async def _searxng_search(query: str, max_results: int) -> list[dict]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json", "engines": "google,bing,duckduckgo"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])[:max_results]
    ]


async def fetch_page(url: str, max_chars: int = 8000) -> str:
    """Fetch a webpage and return cleaned plain text."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            raw = resp.text
        return _strip_html(raw)[:max_chars]
    except Exception as exc:
        return f"[Fetch failed: {exc}]"


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>",  "", text,  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()
