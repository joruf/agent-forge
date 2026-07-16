"""Tests for context-based deliverable file type inference."""

from __future__ import annotations

import pytest

from agentforge.agents.deliverable_types import match_deliverable_file_type
from agentforge.agents.workspace_executor import plan_deliverable_files
from agentforge.agents.workspace_intent import detect_workspace_intent

BASE_DIR = "/home/joruf/Dokumente/GitHub/Project"
RELATIVE_DIR = "GitHub/Project"


@pytest.mark.parametrize(
    ("prompt", "expected_filename"),
    [
        (
            f"Erstelle js Datei und speichere unter {BASE_DIR}",
            "app.js",
        ),
        (
            f"Erstelle JavaScript Code und speichere unter {BASE_DIR}",
            "app.js",
        ),
        (
            f"Create javascript file and save to {BASE_DIR}",
            "app.js",
        ),
        (
            f"Erstelle TypeScript Datei und speichere unter {BASE_DIR}",
            "index.ts",
        ),
        (
            f"Create a tsx react component and save to {BASE_DIR}",
            "App.tsx",
        ),
        (
            f"Erstelle jsx Komponente und speichere unter {BASE_DIR}",
            "App.jsx",
        ),
        (
            f"Erstelle Stylesheet mit Flexbox Layout und speichere unter {BASE_DIR}",
            "styles.css",
        ),
        (
            f"Erstelle css Datei und speichere unter {BASE_DIR}",
            "styles.css",
        ),
        (
            f"Create stylesheet and save to {BASE_DIR}",
            "styles.css",
        ),
        (
            f"Erstelle html Datei mit Header, Menü, Content und Footer und speichere unter {BASE_DIR}",
            "index.html",
        ),
        (
            f"Erstelle PHP Website und speichere unter {BASE_DIR}",
            "index.php",
        ),
        (
            f"Erstelle Python Skript und speichere unter {BASE_DIR}",
            "main.py",
        ),
        (
            f"Erstelle JSON Datei und speichere unter {BASE_DIR}",
            "data.json",
        ),
        (
            f"Write markdown readme and save to {BASE_DIR}",
            "README.md",
        ),
        (
            f"Erstelle Vue Komponente und speichere unter {BASE_DIR}",
            "App.vue",
        ),
        (
            f"Erstelle SQL Datei und speichere unter {BASE_DIR}",
            "schema.sql",
        ),
        (
            f"Erstelle bash shell skript und speichere unter {BASE_DIR}",
            "script.sh",
        ),
        (
            f"Create yaml config and save to {BASE_DIR}",
            "config.yaml",
        ),
        (
            f"Erstelle xml Datei und speichere unter {BASE_DIR}",
            "data.xml",
        ),
        (
            f"Erstelle Word Dokument und speichere unter {BASE_DIR}",
            "document.docx",
        ),
        (
            f"Create pdf document and save to {BASE_DIR}",
            "document.pdf",
        ),
    ],
)
def test_plan_deliverable_files_infers_type_from_context(
    prompt: str,
    expected_filename: str,
) -> None:
    """Common DE/EN prompts resolve to the correct extension and default filename."""
    intent = detect_workspace_intent(prompt)
    files = plan_deliverable_files(prompt, intent)
    assert files == [f"{RELATIVE_DIR}/{expected_filename}"]


@pytest.mark.parametrize(
    ("prompt", "expected_type_id", "expected_extension"),
    [
        ("Erstelle js Datei", "javascript", ".js"),
        ("Erstelle JSON Datei", "json", ".json"),
        ("Erstelle TypeScript Datei", "typescript", ".ts"),
        ("Create stylesheet", "css", ".css"),
        ("tsx component", "tsx", ".tsx"),
        ("Erstelle Word Dokument", "docx", ".docx"),
        ("Create pdf document", "pdf", ".pdf"),
    ],
)
def test_match_deliverable_file_type(
    prompt: str,
    expected_type_id: str,
    expected_extension: str,
) -> None:
    """Direct type matcher returns the most specific file type."""
    matched = match_deliverable_file_type(prompt)
    assert matched is not None
    assert matched.type_id == expected_type_id
    assert matched.extension == expected_extension


def test_typescript_wins_over_javascript_for_ts_prompt() -> None:
    """TypeScript prompts must not fall back to JavaScript."""
    matched = match_deliverable_file_type("Erstelle TypeScript Datei")
    assert matched is not None
    assert matched.type_id == "typescript"
    assert matched.extension == ".ts"


def test_json_does_not_match_javascript() -> None:
    """JSON requests must not be interpreted as JavaScript."""
    matched = match_deliverable_file_type("Erstelle JSON Datei")
    assert matched is not None
    assert matched.type_id == "json"


def test_html_structure_fallback_without_explicit_html_word() -> None:
    """Header/menu/content/footer structure implies HTML even without the word html."""
    prompt = (
        f"Erstelle Seite mit Header, Menü, Content und Footer und speichere unter {BASE_DIR}"
    )
    intent = detect_workspace_intent(prompt)
    files = plan_deliverable_files(prompt, intent)
    assert files == [f"{RELATIVE_DIR}/index.html"]


def test_plan_deliverable_files_for_html_css_js_web_project() -> None:
    """HTML, stylesheet, and JS requests plan a complete web project."""
    prompt = (
        "Erstelle eine HTML-Datei mit Header, Menü, Content und Footer und speichere unter "
        f"{BASE_DIR}. Es soll über eine stylesheet datei verfügen um das html layout zu "
        "definieren und eine JS-Datei die auf html den browser typ ausgibt."
    )
    intent = detect_workspace_intent(prompt)
    files = plan_deliverable_files(prompt, intent)
    assert files == [
        f"{RELATIVE_DIR}/index.html",
        f"{RELATIVE_DIR}/styles.css",
        f"{RELATIVE_DIR}/app.js",
    ]


def test_match_deliverable_file_types_finds_all_web_stack_types() -> None:
    """Multiple file types in one prompt are all detected."""
    from agentforge.agents.deliverable_types import match_deliverable_file_types

    prompt = (
        "HTML-Datei mit stylesheet datei und JS-Datei für browser typ"
    )
    matched = match_deliverable_file_types(prompt)
    assert [spec.type_id for spec in matched] == ["html", "css", "javascript"]
