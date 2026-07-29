"""Tests for workspace path validation and multi-file LLM output parsing."""

from __future__ import annotations

import pytest

from agentforge.agents.workspace_executor import (
    looks_like_source_code,
    maybe_swap_write_file_arguments,
    parse_multifile_llm_output,
    validate_workspace_relative_path,
)
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.tools.registry import WriteFileTool


USER_ANIMATION_PROMPT = (
    "erstelle unter:\n"
    "/home/joruf/Dokumente/GitHub/TEst123456\n"
    "ein python programm das 30 sekunden lang eine 3D Animation anzeigen "
    "und danach beendet"
)

BROKEN_LLM_OUTPUT = """```python
import time
import numpy as np
from matplotlib import pyplot as plt

def animate_3d(duration):
    pass
```

requirements.txt
```
numpy
matplotlib
```"""


def test_validate_workspace_relative_path_rejects_source_code_as_path() -> None:
    """LLM code bodies must not be accepted as filesystem paths."""
    valid, reason = validate_workspace_relative_path(BROKEN_LLM_OUTPUT)
    assert valid is False
    assert "source code" in reason.lower() or "newline" in reason.lower()


def test_looks_like_source_code_detects_python_body() -> None:
    """Multi-line Python is recognized as source code."""
    assert looks_like_source_code("import time\n\ndef main():\n    pass\n") is True
    assert looks_like_source_code("GitHub/Test123456/main.py") is False


def test_maybe_swap_write_file_arguments_recover_swapped_fields() -> None:
    """Swapped write_file path/content pairs are corrected."""
    path, content = maybe_swap_write_file_arguments(
        "import time\n\ndef main():\n    pass\n",
        "GitHub/Test123456/main.py",
    )
    assert path == "GitHub/Test123456/main.py"
    assert "import time" in content


def test_parse_multifile_llm_output_splits_requirements_txt() -> None:
    """Markdown multi-file responses map to separate deliverable paths."""
    files = parse_multifile_llm_output(
        BROKEN_LLM_OUTPUT,
        "GitHub/TEst123456/main.py",
    )
    assert "GitHub/TEst123456/main.py" in files
    assert "import time" in files["GitHub/TEst123456/main.py"]
    assert any(
        path.endswith("requirements.txt") and "numpy" in body
        for path, body in files.items()
    )


def test_program_animation_prompt_does_not_trigger_read_intent() -> None:
    """Display verbs inside program descriptions must not become read tasks."""
    intent = detect_workspace_intent(USER_ANIMATION_PROMPT)
    assert intent.wants_file_creation is True
    assert intent.wants_file_read is False


def test_should_skip_redundant_write_does_not_stat_python_body_as_path(
    temp_workspace,
    monkeypatch,
) -> None:
    """Existing Python file bodies must not be treated as filesystem paths."""
    from agentforge.config import settings
    from agentforge.agents.workspace_executor import should_skip_redundant_write

    monkeypatch.setattr(settings, "workspace_root", temp_workspace)
    target = temp_workspace / "GitHub" / "TEst123456" / "main.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        "import time\nfrom tkinter import Tk\n\n"
        "def main():\n    root = Tk()\n    time.sleep(30)\n    root.destroy()\n"
    )
    target.write_text(existing, encoding="utf-8")
    updated = (
        "import time\nfrom tkinter import Tk, Canvas\n\n"
        "def main():\n    root = Tk()\n    time.sleep(30)\n    root.destroy()\n"
    )

    assert should_skip_redundant_write("GitHub/TEst123456/main.py", updated) is False


@pytest.mark.asyncio
async def test_write_file_tool_swaps_swapped_path_and_content(temp_workspace, monkeypatch) -> None:
    """write_file recovers when the model swaps path and content."""
    from agentforge.config import settings

    monkeypatch.setattr(settings, "workspace_root", temp_workspace)
    tool = WriteFileTool()
    result = await tool.execute({
        "path": "import pyglet\n\ndef main():\n    pass\n",
        "content": "GitHub/Test/main.py",
    })
    assert result.success is True
    assert "GitHub/Test/main.py" in result.output
    written = (temp_workspace / "GitHub" / "Test" / "main.py").read_text(encoding="utf-8")
    assert "import pyglet" in written
