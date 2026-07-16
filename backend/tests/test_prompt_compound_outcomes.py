"""Compound prompt outcome tests: typos, wrong paths, and multi-step workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.quality

from agentforge.agents.prompt_normalizer import normalize_user_prompt
from agentforge.agents.task_state import TaskType, load_task_board_memory
from agentforge.agents.workspace_intent import detect_workspace_intent
from agentforge.models.schemas import OrchestrationMode
from tests.helpers.orchestration_test_helpers import (
    assert_file_content,
    assert_final_message_contains,
    make_team_loop,
    run_orchestration,
    write_workspace_file,
)


def _test12_workflow_prompt(
    workspace: Path,
    *,
    typos: bool = False,
    wrong_paths: bool = True,
) -> str:
    """
    Build the Test12 create-write-read-edit workflow prompt.

    :param workspace: Temp workspace root
    :param typos: Include common DE keyword typos for normalizer coverage
    :param wrong_paths: Use GitHub/index.html instead of GitHub/Test12/index.html
    :return: User prompt text
    """
    base = workspace / "GitHub"
    read_target = f"{base}/index.html" if wrong_paths else f"{base}/Test12/index.html"
    edit_target = read_target

    if typos:
        return (
            f"erstlle mir einen Ordenr mit dem Namen Test12\n"
            f"im Verzeichnis\n{base}\n"
            f"darin eine Datei mit dem Namen index.htlm\n"
            f'darin fügst du in html code den text "Hello World" hinzu.\n'
            f"lees danach die Datei {read_target} aus und geb den Inhalt hier im Prompt aus.\n"
            f'bearbeitte danach die {edit_target} und tausche "Hello World" aus gegen "Hello Bot".'
        )

    return (
        f"erstelle mir einen Ordner mit dem Namen Test12\n"
        f"im Verzeichnis\n{base}\n"
        f"darin eine Datei mit dem Namen index.html\n"
        f'darin fügst du in html code den text "Hello World" hinzu.\n'
        f"lese danach die Datei {read_target} aus und gebe den Inhalt hier im Prompt aus.\n"
        f'bearbeite danach die {edit_target} und tausche "Hello World" aus gegen "Hello Bot".'
    )


@pytest.mark.asyncio
async def test_typo_workflow_normalizes_and_applies_edit(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Typo-heavy DE workflow prompts still create, read, and edit the Test12 file."""
    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_prompt(temp_workspace, typos=True)

    normalization = normalize_user_prompt(prompt)
    assert normalization.changed
    assert "Ordner" in normalization.normalized
    assert "index.html" in normalization.normalized

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    target = "GitHub/Test12/index.html"
    assert (temp_workspace / target).is_file()
    content = (temp_workspace / target).read_text(encoding="utf-8")
    assert "Hello Bot" in content
    assert "Hello World" not in content
    assert_final_message_contains(result, target)


@pytest.mark.asyncio
async def test_wrong_path_workflow_resolves_test12_index_end_to_end(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Wrong GitHub/index.html paths in read/edit steps resolve to GitHub/Test12/index.html."""
    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_prompt(temp_workspace, typos=False, wrong_paths=True)

    intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)
    assert intent.wants_file_edit is True

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(role_responses={"developer": '{"status": "success"}'}),
    )

    target = "GitHub/Test12/index.html"
    content = (temp_workspace / target).read_text(encoding="utf-8")
    assert "Hello Bot" in content
    assert not (temp_workspace / "GitHub" / "index.html").exists()
    assert_final_message_contains(result, "GitHub/Test12/index.html")


@pytest.mark.asyncio
async def test_single_mode_typo_read_returns_verified_content(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Single-agent read prompts with lees typo still return verified disk content."""
    write_workspace_file(temp_workspace, "GitHub/Demo/notes.txt", "Compound read OK")
    prompt = (
        f"lees die Datei {temp_workspace}/GitHub/Demo/notes.txt "
        "und zeige den Inhalt hier"
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        mode=OrchestrationMode.SINGLE,
        role_ids=["developer"],
        agent_loop=make_team_loop(role_responses={"developer": '{"status": "ok"}'}),
    )

    assert_final_message_contains(result, "Compound read OK", "GitHub/Demo/notes.txt")


@pytest.mark.asyncio
async def test_extension_typo_htlm_creates_html_file_on_disk(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Extension typo index.htlm is normalized and written as index.html."""
    (temp_workspace / "GitHub" / "Site").mkdir(parents=True)
    prompt = (
        f"Erstelle unter {temp_workspace}/GitHub/Site eine Datei index.htlm "
        'mit dem Text "Landing page"'
    )

    await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    assert (temp_workspace / "GitHub" / "Site" / "index.html").is_file()
    assert not (temp_workspace / "GitHub" / "Site" / "index.htlm").exists()


@pytest.mark.asyncio
async def test_en_compound_write_then_read_returns_literal_content(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """English write-then-read compound prompts return on-disk literal text."""
    (temp_workspace / "GitHub").mkdir()
    prompt = (
        f"Create folder TestEN under {temp_workspace}/GitHub\n"
        "create file greeting.txt with the text \"Good morning\"\n"
        f"then read {temp_workspace}/GitHub/greeting.txt and show the content here"
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    assert_file_content(temp_workspace, "GitHub/TestEN/greeting.txt", "Good morning")
    assert_final_message_contains(result, "Good morning", "GitHub/TestEN/greeting.txt")


@pytest.mark.asyncio
async def test_workflow_persists_interpreted_request_on_task_board(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Typo normalization stores interpreted_request on the persisted task board."""
    from agentforge.agents.orchestrator import AgentOrchestrator
    from tests.helpers.orchestration_test_helpers import create_test_chat, patch_agent_loop, patch_chat_ready, patch_materialize_with_fallback

    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_prompt(temp_workspace, typos=True)

    patch_chat_ready(monkeypatch)
    patch_materialize_with_fallback(monkeypatch)
    patch_agent_loop(monkeypatch, make_team_loop())

    chat = await create_test_chat(title="Interpreted request persistence")
    orchestrator = AgentOrchestrator()
    await orchestrator.run(
        chat.id,
        prompt,
        OrchestrationMode.MULTI,
        ["developer", "reviewer"],
    )

    board = await load_task_board_memory(chat.id)
    assert board is not None
    assert board.get("last_task_type") == TaskType.WORKFLOW.value
    interpreted = str(board.get("interpreted_request") or "")
    assert "Ordner" in interpreted
    assert "Ordenr" not in interpreted


@pytest.mark.asyncio
async def test_workflow_weak_agents_still_apply_agenda_edit(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Weak JSON from all agents must not block deterministic agenda edit steps."""
    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_prompt(temp_workspace, typos=False, wrong_paths=True)
    weak = '{"status": "success"}'

    await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        role_ids=["developer", "reviewer", "project_manager"],
        agent_loop=make_team_loop(
            role_responses={
                "developer": weak,
                "reviewer": weak,
                "project_manager": weak,
            },
        ),
    )

    content = (temp_workspace / "GitHub" / "Test12" / "index.html").read_text(
        encoding="utf-8",
    )
    assert "Hello Bot" in content


@pytest.mark.asyncio
async def test_list_directory_prompt_lists_folder_contents(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """German list-directory prompts surface scanned folder entries in the final answer."""
    folder = temp_workspace / "GitHub" / "Reports"
    folder.mkdir(parents=True)
    (folder / "one.txt").write_text("1", encoding="utf-8")
    (folder / "two.txt").write_text("2", encoding="utf-8")

    prompt = f"List directory {temp_workspace}/GitHub/Reports"
    intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)

    assert intent.wants_list_directory is True
    assert any("Reports" in path for path in intent.target_dirs)

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(),
    )

    final = result.messages[-1].content
    assert "one.txt" in final or "Reports" in final
