"""Unit tests for pre-orchestration prompt normalization."""

from __future__ import annotations

import pytest

from agentforge.agents.prompt_normalizer import (
    normalize_user_prompt,
    prompt_normalization_metadata,
)
from agentforge.agents.workspace_intent import detect_workspace_intent


@pytest.mark.parametrize(
    ("prompt", "wrong_token", "correct_token"),
    [
        (
            "erstelle mir einen Ordenr mit dem Namen Test12",
            "Ordenr",
            "Ordner",
        ),
        (
            "lees danach die Datei GitHub/test.txt aus",
            "lees",
            "lese",
        ),
        (
            "erstlle eine Datei mit dem Namen index.htlm",
            "erstlle",
            "erstelle",
        ),
        (
            "bearbeitte danach die Datei index.html",
            "bearbeitte",
            "bearbeite",
        ),
    ],
)
def test_normalize_user_prompt_fixes_common_typos(
    prompt: str,
    wrong_token: str,
    correct_token: str,
) -> None:
    """Intent-critical typos and extension mistakes are corrected."""
    result = normalize_user_prompt(prompt)

    assert result.changed
    assert correct_token in result.normalized
    assert any(
        correction.original == wrong_token and correction.corrected == correct_token
        for correction in result.corrections
    )


def test_normalize_user_prompt_fixes_extension_typo() -> None:
    """Common file extension typos are corrected without touching folder names."""
    result = normalize_user_prompt("schreibe index.htlm in den Ordner Test12")

    assert "index.html" in result.normalized
    assert any(correction.reason == "extension" for correction in result.corrections)


def test_normalize_user_prompt_leaves_free_text_unchanged() -> None:
    """Quoted content and arbitrary names are not rewritten."""
    prompt = (
        'erstelle test.txt mit dem Inhalt "Hello Wrld" im Ordner MyCustmName'
    )
    result = normalize_user_prompt(prompt)

    assert "Hello Wrld" in result.normalized
    assert "MyCustmName" in result.normalized


def test_normalize_user_prompt_improves_intent_detection() -> None:
    """Corrected keywords allow read/create intent detection to succeed."""
    prompt = (
        "erstlle einen Ordenr Test12\n"
        "lees die Datei GitHub/Test12/test.txt"
    )
    broken_intent = detect_workspace_intent(prompt)
    fixed_intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)

    assert not broken_intent.wants_file_read
    assert fixed_intent.wants_file_creation
    assert fixed_intent.wants_file_read


def test_normalize_user_prompt_detects_adjacent_letter_swap() -> None:
    """Adjacent letter swaps in keywords are corrected."""
    result = normalize_user_prompt("lees mir die datei bitte")

    assert "lese" in result.normalized
    assert any(correction.original == "lees" for correction in result.corrections)


def test_prompt_normalization_metadata_contains_corrections() -> None:
    """Applied corrections are exposed as chat message metadata."""
    result = normalize_user_prompt("lees die Datei index.htlm")
    metadata = prompt_normalization_metadata(result)

    assert metadata
    assert metadata["interpreted_request"] == "lese die Datei index.html"
    assert metadata["prompt_corrections"]
    assert any(item["original"] == "lees" for item in metadata["prompt_corrections"])


def test_prompt_normalization_metadata_empty_when_unchanged() -> None:
    """Clean prompts do not produce correction metadata."""
    result = normalize_user_prompt("lese die Datei index.html")
    assert prompt_normalization_metadata(result) == {}


def test_normalize_user_prompt_no_change_for_clean_prompt() -> None:
    """Clean prompts pass through unchanged."""
    prompt = (
        "erstelle einen Ordner mit dem Namen Test12\n"
        "lese die Datei GitHub/Test12/index.html"
    )
    result = normalize_user_prompt(prompt)

    assert not result.changed
    assert result.normalized == prompt
    assert result.corrections == []
