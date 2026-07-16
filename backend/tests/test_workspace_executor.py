"""Tests for workspace file execution helpers."""

from pathlib import Path

import pytest

from agentforge.agents.workspace_executor import (
    build_implementation_prompt,
    extract_content_source_heading,
    extract_explicit_filename_from_clause,
    extract_h1_text,
    extract_h1_text_from_request,
    extract_hN_text,
    extract_html_tag_insert_from_clause,
    fallback_file_content,
    infer_requested_files,
    insert_heading_after_heading,
    missing_requested_files,
    plan_deliverable_files,
    plan_derived_txt_from_h1,
    plan_derived_txt_from_heading,
    plan_write_body_from_html_source,
    resolve_write_path,
    sanitize_filename_from_text,
    strip_code_fences,
    write_file_direct,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.config import settings


def test_infer_requested_files_for_php_prompt() -> None:
    """Explicit filenames and target folders resolve to workspace paths."""
    prompt = (
        "Erstelle index.php mit Header, Menü, Content und Footer und speichere unter "
        "/home/joruf/Dokumente/GitHub/Test"
    )
    intent = detect_workspace_intent(prompt)
    files = infer_requested_files(prompt, intent)
    assert files == ["GitHub/Test/index.php"]


def test_plan_deliverable_files_for_html_without_filename() -> None:
    """HTML file type without explicit filename resolves to index.html."""
    prompt = (
        "Erstelle html Datei mit Header, Menü, Content und Footer und speichere unter "
        "/home/joruf/Dokumente/GitHub/Test2"
    )
    intent = detect_workspace_intent(prompt)
    assert intent.wants_file_creation is True
    assert intent.target_dirs == ["GitHub/Test2"]
    files = plan_deliverable_files(prompt, intent)
    assert files == ["GitHub/Test2/index.html"]


@pytest.mark.asyncio
async def test_missing_requested_files_for_html_without_filename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """HTML requests without explicit filenames are tracked as missing deliverables."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    prompt = (
        "Erstelle html Datei mit Header, Menü, Content und Footer und speichere unter "
        f"{tmp_path}/GitHub/Test2"
    )
    intent = detect_workspace_intent(prompt)
    missing = missing_requested_files(prompt, intent)
    assert missing == ["GitHub/Test2/index.html"]


def test_fallback_file_content_html_scaffold() -> None:
    """HTML fallback content includes header, nav, main, and footer."""
    content = fallback_file_content("GitHub/Test2/index.html", "Create html page")
    assert "<header>" in content
    assert "<nav>" in content
    assert "<main>" in content
    assert "<footer>" in content


def test_fallback_file_content_html_includes_quoted_literal() -> None:
    """Quoted body text from the user request is embedded in HTML fallbacks."""
    content = fallback_file_content(
        "GitHub/Test12/index.html",
        'Create index.html with the text "Hello World"',
    )
    assert "Hello World" in content


def test_build_implementation_prompt_lists_targets() -> None:
    """Implementation prompt names each required file path."""
    prompt = build_implementation_prompt(
        "Create index.php",
        ["GitHub/Test/index.php"],
    )
    assert "write_file" in prompt
    assert "GitHub/Test/index.php" in prompt


def test_strip_code_fences() -> None:
    """Generated content is cleaned before writing."""
    raw = "```php\n<?php echo 'ok';\n```"
    assert strip_code_fences(raw) == "<?php echo 'ok';"


@pytest.mark.asyncio
async def test_write_file_direct_creates_nested_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Direct writes create parent directories inside the workspace."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    success, output = await write_file_direct(
        "GitHub/Test/index.php",
        "<?php echo 'Hello World';",
    )
    target = tmp_path / "GitHub" / "Test" / "index.php"
    assert success is True
    assert target.is_file()
    assert "Hello World" in target.read_text(encoding="utf-8")
    assert "Written:" in output


@pytest.mark.asyncio
async def test_missing_requested_files_detects_absent_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Missing requested files are returned when they do not exist yet."""
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    prompt = (
        "Erstelle index.php mit Header, Menü, Content und Footer und speichere unter "
        f"{tmp_path}/GitHub/Test"
    )
    intent = detect_workspace_intent(prompt)
    missing = missing_requested_files(prompt, intent)
    assert missing == ["GitHub/Test/index.php"]


def test_extract_h1_text_from_html() -> None:
    """H1 text is extracted from HTML markup."""
    html = "<!DOCTYPE html><html><body><h1>Hello Bot</h1></body></html>"
    assert extract_h1_text(html) == "Hello Bot"


def test_extract_h1_text_from_request() -> None:
    """Natural-language H1 instructions extract the quoted label."""
    assert extract_h1_text_from_request('füge den text "Hello World" als H1-Tag hinzu') == "Hello World"
    prompt = (
        'füge den text "Hello World" hinzu. '
        "Erstelle danach eine txt-Datei mit dem Namen des H1-Tag Inhalts."
    )
    assert extract_h1_text_from_request(prompt) == "Hello World"


def test_plan_derived_txt_from_h1() -> None:
    """Derived txt path uses sanitized H1 text beside the HTML file."""
    html = "<html><body><h1>Hello Bot</h1></body></html>"
    planned = plan_derived_txt_from_h1("GitHub/Test12/index.html", html)
    assert planned == ("GitHub/Test12/Hello Bot.txt", "Hello Bot\n")


def test_extract_hN_text_reads_requested_level() -> None:
    """HN extraction returns text for the requested heading level."""
    html = "<html><body><h1>Hello World</h1><h2>Hello Bot</h2></body></html>"
    assert extract_hN_text(html, 1) == "Hello World"
    assert extract_hN_text(html, 2) == "Hello Bot"


def test_plan_derived_txt_from_heading_uses_h2() -> None:
    """Derived txt planning can name files after H2 content."""
    html = "<html><body><h1>Hello World</h1><h2>Hello Bot</h2></body></html>"
    planned = plan_derived_txt_from_heading(
        "GitHub/Test12/index.html",
        html,
        naming_source="h2",
    )
    assert planned == ("GitHub/Test12/Hello Bot.txt", "Hello Bot\n")


def test_insert_heading_after_heading() -> None:
    """Heading insertion adds a new HN element after an existing heading."""
    html = "<html><body><h1>Hello World</h1></body></html>"
    updated = insert_heading_after_heading(html, 1, 2, "Hello Bot")
    assert "<h2>Hello Bot</h2>" in updated
    assert updated.index("<h2>Hello Bot</h2>") > updated.index("</h1>")


def test_extract_html_tag_insert_from_clause() -> None:
    """German HTML insertion clauses parse after/insert levels and label."""
    clause = (
        "erstelle in der datei GitHub/Test12/index.html unter dem H1 Tag "
        'einen H2 Tag mit der Beschriftung "Hello Bot".'
    )
    parsed = extract_html_tag_insert_from_clause(clause)
    assert parsed == (1, 2, "Hello Bot")


def test_sanitize_filename_from_text() -> None:
    """Filename sanitization keeps readable words and strips unsafe characters."""
    assert sanitize_filename_from_text('Hello "Bot"!') == "Hello Bot"


def test_extract_explicit_filename_from_clause() -> None:
    """Bare txt filenames are extracted from German write clauses."""
    clause = "erstelle danach die txt datei 1.txt und schreibe den text vom H1 Tag"
    assert extract_explicit_filename_from_clause(clause) == "1.txt"


def test_extract_content_source_heading() -> None:
    """Content-source clauses identify the heading level to copy."""
    clause = "schreibe den text vom H1 Tag des HTML Datei rein"
    assert extract_content_source_heading(clause) == "h1"


def test_resolve_write_path_uses_primary_dir() -> None:
    """Bare filenames resolve beside the primary deliverable directory."""
    assert resolve_write_path("1.txt", "GitHub/Test12", "GitHub/Test12/index.html") == (
        "GitHub/Test12/1.txt"
    )


def test_plan_write_body_from_html_source() -> None:
    """HTML heading text becomes a plain-text file body."""
    html = "<html><body><h1>Hello World</h1><h3>Hello Bot</h3></body></html>"
    assert plan_write_body_from_html_source(html, "h1") == "Hello World\n"
