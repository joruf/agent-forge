"""Tests for generic HTML tag parsing helpers."""

from __future__ import annotations

from agentforge.utils.html_tags import (
    extract_tag_insert_from_clause,
    extract_tag_text_from_html,
    insert_tag_after_tag,
    parse_tag_reference,
)

from agentforge.agents.workspace_executor import plan_derived_txt_from_tag


def test_parse_tag_reference_accepts_common_tags() -> None:
    """Natural-language prompts may reference any HTML tag name."""
    assert parse_tag_reference('als p-Tag hinzufügen') == "p"
    assert parse_tag_reference("unter dem div Tag") == "div"
    assert parse_tag_reference("vom strong-Tag des HTML") == "strong"
    assert parse_tag_reference("Inhalt des H3-Tags") == "h3"


def test_extract_tag_text_from_html_reads_non_heading_tags() -> None:
    """Text extraction works for headings and semantic inline tags."""
    html = "<html><body><p>Paragraph</p><strong>Bold</strong></body></html>"
    assert extract_tag_text_from_html(html, "p") == "Paragraph"
    assert extract_tag_text_from_html(html, "strong") == "Bold"


def test_extract_tag_insert_from_clause_supports_paragraph_tags() -> None:
    """Insertion parsing accepts any valid HTML tag pair."""
    clause = (
        'unter dem div Tag einen p Tag mit der Beschriftung "Intro" '
        "in der datei GitHub/demo.html"
    )
    parsed = extract_tag_insert_from_clause(clause)
    assert parsed == ("div", "p", "Intro")


def test_insert_tag_after_tag_inserts_paragraph_after_div() -> None:
    """Generic tag insertion works outside heading levels."""
    html = "<html><body><div>Section</div></body></html>"
    updated = insert_tag_after_tag(html, "div", "p", "Intro")
    assert "<p>Intro</p>" in updated
    assert updated.index("<p>Intro</p>") > updated.index("</div>")


def test_plan_derived_txt_from_tag_uses_paragraph_content() -> None:
    """Derived filenames may come from any HTML element text."""
    html = "<html><body><p>Chapter One</p></body></html>"
    planned = plan_derived_txt_from_tag(
        "GitHub/Test12/index.html",
        html,
        naming_source="p",
    )
    assert planned is not None
    assert planned[0].endswith("Chapter One.txt")
    assert planned[1].strip() == "Chapter One"
