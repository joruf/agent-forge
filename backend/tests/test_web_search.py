"""Tests for online web search."""

import pytest

from agentforge.tools.registry import WebSearchTool
from agentforge.tools.web_search import (
    SearchResult,
    _dedupe_results,
    _parse_duckduckgo_html,
    _parse_duckduckgo_instant,
    _parse_wikipedia,
    _unwrap_duckduckgo_url,
    format_search_results,
    search_web,
)


def test_unwrap_duckduckgo_url() -> None:
    """DuckDuckGo redirect links resolve to target URLs."""
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage"
    assert _unwrap_duckduckgo_url(href) == "https://example.com/page"


def test_parse_duckduckgo_html() -> None:
    """DuckDuckGo HTML blocks are parsed into search hits."""
    html_text = """
    <div class="result results_links results_links_deep web-result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">Example Title</a>
      <a class="result__snippet">Example snippet text</a>
    </div>
    """
    results = _parse_duckduckgo_html(html_text, 5)
    assert len(results) == 1
    assert results[0].title == "Example Title"
    assert results[0].url == "https://example.com"
    assert "snippet" in results[0].snippet.lower()


def test_parse_duckduckgo_instant() -> None:
    """Instant answers and related topics are parsed."""
    payload = {
        "Heading": "Python",
        "AbstractText": "Python is a programming language.",
        "AbstractURL": "https://example.com/python",
        "RelatedTopics": [
            {"Text": "Asyncio - Event loop docs", "FirstURL": "https://example.com/asyncio"},
        ],
    }
    results = _parse_duckduckgo_instant(payload, 5)
    assert len(results) == 2
    assert results[0].source == "duckduckgo_instant"
    assert "programming language" in results[0].snippet


def test_parse_wikipedia() -> None:
    """Wikipedia opensearch payloads are parsed."""
    payload = [
        "Python",
        ["Python (programming language)"],
        ["High-level programming language"],
        ["https://en.wikipedia.org/wiki/Python_(programming_language)"],
    ]
    results = _parse_wikipedia(payload, 3)
    assert len(results) == 1
    assert results[0].source == "wikipedia"
    assert results[0].title.startswith("Python")


def test_dedupe_results() -> None:
    """Duplicate URLs are removed."""
    results = [
        SearchResult("A", "https://example.com", "one", "duckduckgo"),
        SearchResult("A duplicate", "https://example.com", "two", "wikipedia"),
    ]
    assert len(_dedupe_results(results)) == 1


def test_format_search_results_empty() -> None:
    """Empty result sets produce a helpful message."""
    text = format_search_results("missing topic", [])
    assert "No web results found" in text


@pytest.mark.asyncio
async def test_web_search_tool_success(monkeypatch) -> None:
    """Web search tool formats provider hits."""
    async def fake_search(query: str, max_results: int | None = None, providers=None):
        return [
            SearchResult("Hit", "https://example.com", "Snippet", "duckduckgo"),
        ]

    monkeypatch.setattr("agentforge.tools.web_search.search_web", fake_search)
    tool = WebSearchTool()
    result = await tool.execute({"query": "AgentForge"})
    assert result.success is True
    assert "Hit" in result.output
    assert "https://example.com" in result.output


@pytest.mark.asyncio
async def test_web_search_tool_requires_query() -> None:
    """Empty queries are rejected."""
    tool = WebSearchTool()
    result = await tool.execute({"query": "   "})
    assert result.success is False


@pytest.mark.asyncio
async def test_search_web_live_smoke() -> None:
    """Live DuckDuckGo search returns at least one hit."""
    results = await search_web("Python programming language", max_results=3)
    assert len(results) >= 1
    assert results[0].title
