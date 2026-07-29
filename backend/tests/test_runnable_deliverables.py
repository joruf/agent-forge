"""Tests for runnable Python deliverable checks and write deduplication."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentforge.config import settings

from agentforge.agents.task_state import (
    build_task_state,
    check_completion,
    collect_required_write_paths,
    seed_write_facts,
)
from agentforge.agents.workspace_executor import (
    analyze_python_file,
    analyze_python_file_details,
    collect_non_runnable_implementation_paths,
    install_python_requirements,
    is_runnable_python_content,
    prefers_python_stdlib_only,
    python_stdlib_delivery_rules,
    should_skip_redundant_write,
    sync_requirements_txt,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.tools.registry import WriteFileTool
from tests.test_placeholder_implementation import (
    PYTHON_TOOL_PROMPT,
    REAL_MAIN,
)


BROKEN_PYGLET = """import pyglet

window = pyglet.window.Window(640, 480)
start = time.time()

@window.event
def on_draw():
    window.clear()

pyglet.app.run()
"""

SYNTAX_ERROR_MAIN = """def main(
    print("broken")
"""


def test_broken_pyglet_missing_time_import_is_non_runnable() -> None:
    """Structurally valid pyglet skeleton with missing time import fails runnable check."""
    issues = analyze_python_file(BROKEN_PYGLET, "GitHub/Test1234/main.py")
    assert issues
    assert any("time" in issue for issue in issues)
    assert is_runnable_python_content(BROKEN_PYGLET, "GitHub/Test1234/main.py") is False


def test_real_main_without_requirements_txt_is_non_runnable() -> None:
    """Third-party imports require requirements.txt in the project directory."""
    issues = analyze_python_file(REAL_MAIN, "GitHub/Test12345/main.py")
    assert any("requirements.txt" in issue for issue in issues)
    assert is_runnable_python_content(REAL_MAIN, "GitHub/Test12345/main.py") is False


def test_real_main_with_requirements_txt_is_runnable(
    temp_workspace: Path,
) -> None:
    """Runnable Python passes when requirements.txt lists third-party packages."""
    intent = detect_workspace_intent(PYTHON_TOOL_PROMPT)
    target = collect_required_write_paths(build_task_state(PYTHON_TOOL_PROMPT, intent))[0]
    target_path = temp_workspace / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(REAL_MAIN, encoding="utf-8")
    sync_requirements_txt(str(Path(target).parent), ["pygame"])

    assert is_runnable_python_content(REAL_MAIN, target) is True


@pytest.mark.asyncio
async def test_write_file_tool_skips_identical_rewrite(temp_workspace: Path) -> None:
    """WriteFileTool deduplicates identical rewrites without audit spam."""
    relative = "GitHub/Test12345/main.py"
    tool = WriteFileTool()
    first = await tool.execute({"path": relative, "content": REAL_MAIN})
    second = await tool.execute({"path": relative, "content": REAL_MAIN})
    assert first.success is True
    assert second.success is True
    assert "Skipped redundant write" in second.output


def test_should_skip_redundant_write_same_compile_error(temp_workspace: Path) -> None:
    """Repeated broken rewrites with the same compile error are skipped."""
    relative = "GitHub/Test12345/main.py"
    target_path = temp_workspace / relative
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(SYNTAX_ERROR_MAIN, encoding="utf-8")

    rewritten = SYNTAX_ERROR_MAIN.replace("main(", "main (")
    assert should_skip_redundant_write(relative, rewritten) is True


def test_check_completion_fails_for_syntax_error_python(temp_workspace: Path) -> None:
    """Write tasks stay incomplete when Python deliverables have syntax errors."""
    intent = detect_workspace_intent(PYTHON_TOOL_PROMPT)
    state = build_task_state(PYTHON_TOOL_PROMPT, intent)
    target = collect_required_write_paths(state)[0]
    target_path = temp_workspace / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(SYNTAX_ERROR_MAIN, encoding="utf-8")

    seed_write_facts(state, [target], agent_id="developer", round_num=0)
    report = check_completion(state)
    assert report.complete is False
    assert "compile" in report.reason.lower() or "dependencies" in report.reason.lower()
    assert target in report.missing


def test_collect_non_runnable_implementation_paths(
    temp_workspace: Path,
) -> None:
    """Non-runnable path collection finds broken Python on disk."""
    intent = detect_workspace_intent(PYTHON_TOOL_PROMPT)
    target = "GitHub/Test12345/main.py"
    target_path = temp_workspace / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(BROKEN_PYGLET, encoding="utf-8")

    found = collect_non_runnable_implementation_paths(
        PYTHON_TOOL_PROMPT,
        intent,
        [target],
    )
    assert found == [target]


def test_prefers_python_stdlib_only_for_simple_animation_prompt() -> None:
    """Simple Python animation requests prefer stdlib unless a library is named."""
    prompt = "Erstelle ein Python 3D Animation Tool unter Test1234"
    assert prefers_python_stdlib_only(prompt) is True
    assert "tkinter" in python_stdlib_delivery_rules(prompt)
    assert prefers_python_stdlib_only("Use pyglet for a 3D animation") is False


def test_matplotlib_without_explicit_request_is_non_runnable() -> None:
    """Matplotlib deliverables fail when the user did not request third-party libraries."""
    prompt = (
        "erstelle ein python programm das 30 sekunden lang eine 3D Animation "
        "anzeigt und danach beendet"
    )
    body = """import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure()
plt.show()
"""
    issues = analyze_python_file(body, "GitHub/TEst123456/main.py", user_content=prompt)
    assert any("stdlib" in issue for issue in issues)
    assert is_runnable_python_content(
        body,
        "GitHub/TEst123456/main.py",
        user_content=prompt,
    ) is False


def test_mpl_toolkits_maps_to_matplotlib_only() -> None:
    """mpl_toolkits imports should not create a separate invalid pip package."""
    body = """from mpl_toolkits.mplot3d import Axes3D
import matplotlib.pyplot as plt
"""
    analysis = analyze_python_file_details(body, "GitHub/demo/main.py")
    assert analysis.third_party_packages == ["matplotlib"]


@pytest.mark.asyncio
async def test_install_python_requirements_runs_pip(
    temp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """requirements.txt triggers pip install in the project directory."""
    req_dir = temp_workspace / "GitHub" / "Test1234"
    req_dir.mkdir(parents=True)
    (req_dir / "requirements.txt").write_text("pyglet\n", encoding="utf-8")
    monkeypatch.setattr(settings, "workspace_root", temp_workspace)

    captured: dict[str, object] = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")

        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"Successfully installed pyglet", b""

        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    ok, message = await install_python_requirements("GitHub/Test1234")
    assert ok is True
    assert "requirements.txt" in message
    assert captured["cwd"] == str(req_dir)


@pytest.mark.asyncio
async def test_write_file_tool_installs_requirements_after_sync(
    temp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WriteFileTool installs dependencies when third-party imports are written."""
    monkeypatch.setattr(settings, "workspace_root", temp_workspace)
    installed_dirs: list[str] = []

    async def fake_install(relative_dir: str) -> tuple[bool, str]:
        installed_dirs.append(relative_dir)
        return True, "Installed dependencies from requirements.txt"

    monkeypatch.setattr(
        "agentforge.agents.workspace_executor.install_python_requirements",
        fake_install,
    )

    tool = WriteFileTool()
    result = await tool.execute({"path": "GitHub/Test1234/main.py", "content": REAL_MAIN})
    assert result.success is True
    assert installed_dirs == ["GitHub/Test1234"]
    assert "Installed dependencies" in result.output
