"""Online web search without API keys."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from agentforge.config import settings

USER_AGENT = "AgentForge/0.1 (+local research tool)"
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_INSTANT_URL = "https://api.duckduckgo.com/"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"

DEFAULT_PROVIDERS = ("duckduckgo", "wikipedia", "duckduckgo_instant")


@dataclass(frozen=True)
class SearchResult:
    """One web search hit."""

    title: str
    url: str
    snippet: str
    source: str


def _unwrap_duckduckgo_url(href: str) -> str:
    """
    Resolve DuckDuckGo redirect links to the target URL.

    :param href: Raw href from DuckDuckGo HTML
    :return: Destination URL
    """
    if not href:
        return ""

    normalized = href if "://" in href else f"https:{href}" if href.startswith("//") else href
    parsed = urlparse(normalized)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        redirect = parse_qs(parsed.query).get("uddg", [])
        if redirect:
            return unquote(redirect[0])
    return normalized


def _parse_duckduckgo_html(html_text: str, max_results: int) -> list[SearchResult]:
    """
    Parse DuckDuckGo HTML result pages.

    :param html_text: HTML response body
    :param max_results: Maximum number of hits
    :return: Parsed search results
    """
    blocks = re.split(r'<div class="result\s', html_text, flags=re.IGNORECASE)
    results: list[SearchResult] = []

    for block in blocks[1:]:
        link_match = re.search(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not link_match:
            continue

        title = html.unescape(re.sub(r"<[^>]+>", "", link_match.group(2))).strip()
        url = _unwrap_duckduckgo_url(html.unescape(link_match.group(1)))
        snippet_match = re.search(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet = ""
        if snippet_match:
            snippet = html.unescape(re.sub(r"<[^>]+>", "", snippet_match.group(1))).strip()

        if title and url:
            results.append(SearchResult(title=title, url=url, snippet=snippet, source="duckduckgo"))
        if len(results) >= max_results:
            break

    return results


def _parse_duckduckgo_instant(payload: dict[str, Any], max_results: int) -> list[SearchResult]:
    """
    Parse DuckDuckGo instant-answer JSON.

    :param payload: JSON payload from DuckDuckGo API
    :param max_results: Maximum number of hits
    :return: Parsed search results
    """
    results: list[SearchResult] = []

    abstract = (payload.get("AbstractText") or "").strip()
    abstract_url = (payload.get("AbstractURL") or "").strip()
    if abstract:
        title = (payload.get("Heading") or payload.get("AbstractSource") or "Summary").strip()
        results.append(SearchResult(
            title=title,
            url=abstract_url,
            snippet=abstract,
            source="duckduckgo_instant",
        ))

    for topic in payload.get("RelatedTopics") or []:
        if len(results) >= max_results:
            break
        if not isinstance(topic, dict):
            continue
        text = (topic.get("Text") or "").strip()
        url = (topic.get("FirstURL") or "").strip()
        if not text:
            continue
        title, _, snippet = text.partition(" - ")
        results.append(SearchResult(
            title=title.strip() or text,
            url=url,
            snippet=snippet.strip() or text,
            source="duckduckgo_instant",
        ))

    return results[:max_results]


def _parse_wikipedia(payload: list[Any], max_results: int) -> list[SearchResult]:
    """
    Parse Wikipedia opensearch JSON.

    :param payload: JSON list from Wikipedia API
    :param max_results: Maximum number of hits
    :return: Parsed search results
    """
    if not payload or len(payload) < 4:
        return []

    titles = payload[1] if isinstance(payload[1], list) else []
    descriptions = payload[2] if isinstance(payload[2], list) else []
    urls = payload[3] if isinstance(payload[3], list) else []

    results: list[SearchResult] = []
    for index, title in enumerate(titles[:max_results]):
        results.append(SearchResult(
            title=str(title),
            url=str(urls[index]) if index < len(urls) else "",
            snippet=str(descriptions[index]) if index < len(descriptions) else "",
            source="wikipedia",
        ))
    return results


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """
    Remove duplicate hits by URL or title.

    :param results: Search hits
    :return: Unique hits preserving order
    """
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        key = (result.url or result.title).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


async def _fetch_duckduckgo_html(client: httpx.AsyncClient, query: str, max_results: int) -> list[SearchResult]:
    """Search DuckDuckGo HTML results."""
    response = await client.post(
        DDG_HTML_URL,
        data={"q": query, "b": "", "kl": ""},
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    return _parse_duckduckgo_html(response.text, max_results)


async def _fetch_duckduckgo_instant(client: httpx.AsyncClient, query: str, max_results: int) -> list[SearchResult]:
    """Search DuckDuckGo instant answers."""
    response = await client.get(
        DDG_INSTANT_URL,
        params={
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
        },
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return []
    return _parse_duckduckgo_instant(payload, max_results)


async def _fetch_wikipedia(client: httpx.AsyncClient, query: str, max_results: int) -> list[SearchResult]:
    """Search Wikipedia titles and summaries."""
    response = await client.get(
        WIKIPEDIA_API_URL,
        params={
            "action": "opensearch",
            "search": query,
            "limit": max_results,
            "namespace": 0,
            "format": "json",
        },
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []
    return _parse_wikipedia(payload, max_results)


async def search_web(
    query: str,
    max_results: int | None = None,
    providers: list[str] | None = None,
) -> list[SearchResult]:
    """
    Search the public web using providers that do not require API keys.

    Supported providers:
    - duckduckgo: HTML web results
    - duckduckgo_instant: DuckDuckGo instant answers and related topics
    - wikipedia: Wikipedia article matches

    :param query: Search query
    :param max_results: Maximum number of results to return overall
    :param providers: Provider ids in priority order
    :return: Combined unique search results
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        return []

    limit = max_results or settings.web_search_max_results
    provider_list = providers or settings.web_search_providers
    combined: list[SearchResult] = []
    errors: list[str] = []

    timeout = httpx.Timeout(settings.web_search_timeout, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for provider in provider_list:
            if len(combined) >= limit:
                break
            remaining = limit - len(combined)
            try:
                if provider == "duckduckgo":
                    hits = await _fetch_duckduckgo_html(client, cleaned_query, remaining)
                elif provider == "duckduckgo_instant":
                    hits = await _fetch_duckduckgo_instant(client, cleaned_query, remaining)
                elif provider == "wikipedia":
                    hits = await _fetch_wikipedia(client, cleaned_query, remaining)
                else:
                    continue
                combined.extend(hits)
            except Exception as exc:
                errors.append(f"{provider}: {exc}")

    unique = _dedupe_results(combined)[:limit]
    if unique:
        return unique

    if errors:
        raise RuntimeError("; ".join(errors))
    return []


def format_search_results(query: str, results: list[SearchResult]) -> str:
    """
    Format search hits for LLM consumption.

    :param query: Original query
    :param results: Search hits
    :return: Plain-text summary
    """
    if not results:
        return f"No web results found for: {query}"

    lines = [f"Web search results for: {query}", ""]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.title}")
        if result.url:
            lines.append(f"   URL: {result.url}")
        if result.snippet:
            lines.append(f"   Snippet: {result.snippet}")
        lines.append(f"   Source: {result.source}")
        lines.append("")
    return "\n".join(lines).strip()
