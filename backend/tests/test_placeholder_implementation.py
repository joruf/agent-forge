"""Tests for placeholder detection and substantive software completion gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge.agents.task_state import (
    build_task_state,
    check_completion,
    collect_required_write_paths,
    seed_write_facts,
)
from agentforge.agents.workspace_executor import (
    collect_placeholder_implementation_paths,
    is_placeholder_content,
    is_substantive_software_creation,
    plan_deliverable_files,
    should_skip_redundant_write,
    write_file_direct,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.config import settings


PYTHON_TOOL_PROMPT = (
    "ich habe ein größeres projekt für dich. du arbeitest im verzeichnis "
    "/home/joruf/Dokumente/GitHub/Test12345 dort erstellst du ein tool in python "
    "das 30 sekunden lange echte bewegliche 3d effekte abspielt."
)

PLACEHOLDER_MAIN = (
    "# This is a placeholder for the main.py file.\n"
    "# You need to implement the logic to play 30 seconds of "
    "real-time moving 3D effects here.\n"
)

REAL_MAIN = """#!/usr/bin/env python3
import math
import time

import pygame


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((640, 480))
    clock = pygame.time.Clock()
    start = time.time()
    running = True
    while running and time.time() - start < 30:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        angle = (time.time() - start) * 2
        screen.fill((10, 10, 30))
        x = 320 + int(120 * math.cos(angle))
        y = 240 + int(80 * math.sin(angle * 1.3))
        pygame.draw.circle(screen, (80, 180, 255), (x, y), 24)
        pygame.display.flip()
        clock.tick(60)
    pygame.quit()


if __name__ == "__main__":
    main()
"""


def test_is_placeholder_content_detects_stub_main_py() -> None:
    """Known placeholder stub text is flagged."""
    assert is_placeholder_content(PLACEHOLDER_MAIN, "GitHub/Test12345/main.py") is True


def test_is_placeholder_content_accepts_real_python() -> None:
    """Runnable Python with logic is not treated as placeholder."""
    assert is_placeholder_content(REAL_MAIN, "GitHub/Test12345/main.py") is False


def test_is_substantive_software_creation_for_python_tool_prompt() -> None:
    """Python tool creation prompts require implementation-quality checks."""
    intent = detect_workspace_intent(PYTHON_TOOL_PROMPT)
    assert intent.wants_file_creation is True
    assert is_substantive_software_creation(PYTHON_TOOL_PROMPT, intent) is True
    assert plan_deliverable_files(PYTHON_TOOL_PROMPT, intent) == [
        "GitHub/Test12345/main.py"
    ]


def test_literal_text_prompt_is_not_substantive_software() -> None:
    """Simple quoted-text file requests skip implementation-quality gates."""
    prompt = (
        "Erstelle test.txt unter /home/joruf/Dokumente/GitHub/Test123 "
        'mit dem Text "Hello World"'
    )
    intent = detect_workspace_intent(prompt)
    assert is_substantive_software_creation(prompt, intent) is False


def test_check_completion_fails_for_placeholder_software_write(
    temp_workspace: Path,
) -> None:
    """Write tasks stay incomplete when substantive deliverables are placeholders."""
    intent = detect_workspace_intent(PYTHON_TOOL_PROMPT)
    state = build_task_state(PYTHON_TOOL_PROMPT, intent)
    target = collect_required_write_paths(state)[0]
    target_path = temp_workspace / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(PLACEHOLDER_MAIN, encoding="utf-8")

    seed_write_facts(state, [target], agent_id="developer", round_num=0)
    report = check_completion(state)
    assert report.complete is False
    assert "placeholder" in report.reason.lower()
    assert target in report.missing


def test_check_completion_passes_for_real_implementation(
    temp_workspace: Path,
) -> None:
    """Substantive software tasks complete once real code is on disk."""
    intent = detect_workspace_intent(PYTHON_TOOL_PROMPT)
    state = build_task_state(PYTHON_TOOL_PROMPT, intent)
    target = collect_required_write_paths(state)[0]
    target_path = temp_workspace / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(REAL_MAIN, encoding="utf-8")
    req_path = target_path.parent / "requirements.txt"
    req_path.write_text("pygame\n", encoding="utf-8")

    seed_write_facts(state, [target], agent_id="developer", round_num=0)
    assert check_completion(state).complete is True


def test_collect_placeholder_implementation_paths(
    temp_workspace: Path,
) -> None:
    """Placeholder path collection finds stub files on disk."""
    intent = detect_workspace_intent(PYTHON_TOOL_PROMPT)
    target = "GitHub/Test12345/main.py"
    target_path = temp_workspace / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(PLACEHOLDER_MAIN, encoding="utf-8")

    found = collect_placeholder_implementation_paths(
        PYTHON_TOOL_PROMPT,
        intent,
        [target],
    )
    assert found == [target]


def test_should_skip_redundant_write_blocks_placeholder_over_implementation(
    temp_workspace: Path,
) -> None:
    """Good on-disk code must not be replaced by placeholder content."""
    relative = "GitHub/Test12345/main.py"
    target_path = temp_workspace / relative
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(REAL_MAIN, encoding="utf-8")

    assert should_skip_redundant_write(relative, PLACEHOLDER_MAIN) is True


@pytest.mark.asyncio
async def test_write_file_direct_skips_identical_rewrite(
    temp_workspace: Path,
) -> None:
    """Duplicate writes of identical content are deduplicated."""
    relative = "GitHub/Test12345/main.py"
    first_ok, _ = await write_file_direct(relative, REAL_MAIN)
    second_ok, second_output = await write_file_direct(relative, REAL_MAIN)
    assert first_ok is True
    assert second_ok is True
    assert "Skipped redundant write" in second_output
    assert (temp_workspace / relative).read_text(encoding="utf-8") == REAL_MAIN
