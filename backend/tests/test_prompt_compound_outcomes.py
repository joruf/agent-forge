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
            f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
            f"lees danach die Datei {read_target} aus und geb den Inhalt hier im Prompt aus.\n"
            f'bearbeitte danach die {edit_target} und tausche "Hello World" aus gegen "Hello Bot".'
        )

    return (
        f"erstelle mir einen Ordner mit dem Namen Test12\n"
        f"im Verzeichnis\n{base}\n"
        f"darin eine Datei mit dem Namen index.html\n"
        f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
        f"lese danach die Datei {read_target} aus und gebe den Inhalt hier im Prompt aus.\n"
        f'bearbeite danach die {edit_target} und tausche "Hello World" aus gegen "Hello Bot".'
    )


def _test12_workflow_with_derived_txt_prompt(
    workspace: Path,
    *,
    wrong_paths: bool = True,
) -> str:
    """
    Build the full six-step Test12 workflow including H1-named .txt creation.

    :param workspace: Temp workspace root
    :param wrong_paths: Use GitHub/index.html instead of GitHub/Test12/index.html
    :return: User prompt text
    """
    base_prompt = _test12_workflow_prompt(workspace, typos=False, wrong_paths=wrong_paths)
    return (
        f"{base_prompt}\n"
        "erstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
        "H1-Tag der erstellten HTML-Datei und hat die Dateiendung .txt"
    )


def _test12_workflow_with_h2_insert_and_derived_txt_prompt(
    workspace: Path,
    *,
    wrong_paths: bool = True,
) -> str:
    """
    Build the six-step Test12 workflow with H2 insertion and H2-named .txt.

    :param workspace: Temp workspace root
    :param wrong_paths: Use GitHub/index.html instead of GitHub/Test12/index.html
    :return: User prompt text
    """
    base = workspace / "GitHub"
    read_target = f"{base}/index.html" if wrong_paths else f"{base}/Test12/index.html"
    return (
        f"erstelle mir einen Ordner mit dem Namen Test12\n"
        f"im Verzeichnis\n{base}\n"
        f"darin eine Datei mit dem Namen index.html\n"
        f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
        f"lese danach die Datei {read_target} aus und gebe den Inhalt hier im Prompt aus.\n"
        f"erstelle danach in der datei {read_target} unter dem H1 Tag einen H2 Tag "
        f'mit der Beschriftung "Hello Bot".\n'
        "erstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
        "H2-Tag der erstellten HTML-Datei und hat die Dateiendung .txt"
    )


def _test12_workflow_with_h3_insert_derived_and_1txt_prompt(
    workspace: Path,
    *,
    wrong_paths: bool = True,
) -> str:
    """
    Build the seven-step Test12 workflow with H3-derived txt and explicit 1.txt.

    :param workspace: Temp workspace root
    :param wrong_paths: Use GitHub/index.html instead of GitHub/Test12/index.html
    :return: User prompt text
    """
    base = workspace / "GitHub"
    read_target = f"{base}/index.html" if wrong_paths else f"{base}/Test12/index.html"
    return (
        f"erstelle mir Verzeichnis mit dem Namen Test12\n"
        f"im Ordner {base}\n"
        f"dort eine Datei mit dem Namen index.html erstellen\n"
        f'darin fügst du in html code den text "Hello World" als H1-Tag hinzu.\n'
        f"lese danach die Datei {read_target} aus und geb den Inhalt hier im Prompt aus.\n"
        f"erstelle danach in der datei {read_target} unter dem H1 Tag einen H3 Tag "
        f'mit der Beschriftung "Hello Bot".\n'
        "erstelle danach eine neue datei. Die Datei hat den Namen des Inhalts des "
        "H3-Tag der erstellten HTML-Datei und hat die Dateiendung .txt\n"
        "erstelle danach die txt datei 1.txt und schreibe den text vom H1 Tag des HTML Datei rein"
    )


def _test12_workflow_with_h3_derived_1txt_and_fitxt_prompt(
    workspace: Path,
    *,
    wrong_paths: bool = True,
) -> str:
    """
    Build the eight-step Test12 workflow including fi.txt from H2 text.

    :param workspace: Temp workspace root
    :param wrong_paths: Use GitHub/index.html instead of GitHub/Test12/index.html
    :return: User prompt text
    """
    return (
        f"{_test12_workflow_with_h3_insert_derived_and_1txt_prompt(workspace, wrong_paths=wrong_paths)}\n"
        "erstelle danach die txt datei fi.txt und schreibe den text vom H2 Tag des HTML Datei rein"
    )


@pytest.mark.asyncio
async def test_seven_step_workflow_creates_h3_named_txt_and_1txt(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Seven-step workflow creates Hello Bot.txt from H3 and 1.txt from H1 text."""
    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_with_h3_insert_derived_and_1txt_prompt(
        temp_workspace,
        wrong_paths=True,
    )

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(role_responses={"developer": '{"status": "success"}'}),
    )

    html_path = temp_workspace / "GitHub" / "Test12" / "index.html"
    derived_txt = temp_workspace / "GitHub" / "Test12" / "Hello Bot.txt"
    explicit_txt = temp_workspace / "GitHub" / "Test12" / "1.txt"
    html_content = html_path.read_text(encoding="utf-8")
    assert html_path.is_file()
    assert "Hello World" in html_content
    assert "<h3>Hello Bot</h3>" in html_content
    assert derived_txt.is_file()
    assert derived_txt.read_text(encoding="utf-8").strip() == "Hello Bot"
    assert explicit_txt.is_file()
    assert explicit_txt.read_text(encoding="utf-8").strip() == "Hello World"
    assert_final_message_contains(
        result,
        "Hello Bot.txt",
        "1.txt",
        "GitHub/Test12/index.html",
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
async def test_six_step_workflow_creates_h1_named_txt_after_edit(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """After edit, a .txt file is created using the post-edit H1 text as its basename."""
    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_with_derived_txt_prompt(temp_workspace, wrong_paths=True)

    intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)
    assert intent.wants_derived_file is True

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(role_responses={"developer": '{"status": "success"}'}),
    )

    html_path = temp_workspace / "GitHub" / "Test12" / "index.html"
    txt_path = temp_workspace / "GitHub" / "Test12" / "Hello Bot.txt"
    assert html_path.is_file()
    assert "Hello Bot" in html_path.read_text(encoding="utf-8")
    assert txt_path.is_file()
    assert txt_path.read_text(encoding="utf-8").strip() == "Hello Bot"
    assert_final_message_contains(result, "Hello Bot.txt", "GitHub/Test12/index.html")


@pytest.mark.asyncio
async def test_six_step_workflow_creates_h2_named_txt_after_h2_insert(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """After H2 insertion, a .txt file is created using the H2 text as its basename."""
    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_with_h2_insert_and_derived_txt_prompt(
        temp_workspace,
        wrong_paths=True,
    )

    intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)
    assert intent.wants_derived_file is True
    assert intent.wants_file_edit is True

    result = await run_orchestration(
        monkeypatch,
        temp_workspace,
        prompt,
        agent_loop=make_team_loop(role_responses={"developer": '{"status": "success"}'}),
    )

    html_path = temp_workspace / "GitHub" / "Test12" / "index.html"
    txt_path = temp_workspace / "GitHub" / "Test12" / "Hello Bot.txt"
    html_content = html_path.read_text(encoding="utf-8")
    assert html_path.is_file()
    assert "Hello World" in html_content
    assert "<h2>Hello Bot</h2>" in html_content
    assert txt_path.is_file()
    assert txt_path.read_text(encoding="utf-8").strip() == "Hello Bot"
    assert_final_message_contains(result, "Hello Bot.txt", "GitHub/Test12/index.html")


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
async def test_agenda_pipeline_pauses_for_missing_h2_with_alternate_headings(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Missing requested heading pauses with user-choice options when alternates exist."""
    from agentforge.agents.approval_manager import approval_manager
    from agentforge.agents.orchestrator import AgentOrchestrator
    from agentforge.agents.task_state import build_task_state
    from agentforge.agents.workspace_intent import detect_workspace_intent
    from tests.helpers.orchestration_test_helpers import write_workspace_file

    write_workspace_file(
        temp_workspace,
        "GitHub/Test12/index.html",
        "<html><body><h1>Hello World</h1><h3>Hello Bot</h3></body></html>",
    )

    prompt = _test12_workflow_with_h3_derived_1txt_and_fitxt_prompt(
        temp_workspace,
        wrong_paths=True,
    )
    intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)
    task_state = build_task_state(prompt, intent)
    orchestrator = AgentOrchestrator()

    summary, _reads, paused = await orchestrator._execute_workspace_agenda_pipeline(
        "pipeline-test",
        prompt,
        intent,
        task_state,
        None,
    )

    assert paused is True
    assert summary == ""
    assert not (temp_workspace / "GitHub" / "Test12" / "fi.txt").exists()
    pending = approval_manager.list_pending("pipeline-test")
    assert len(pending) == 1
    assert pending[0].action_type == "user_choice"
    assert "no `<h2>` found" in pending[0].description
    option_ids = [option["id"] for option in pending[0].payload["options"]]
    assert "use_h1" in option_ids
    assert "use_h3" in option_ids
    assert "skip" in option_ids
    assert "abort" in option_ids
    error_facts = [fact for fact in task_state.facts if fact.kind == "file_write_error"]
    assert not error_facts


@pytest.mark.asyncio
async def test_agenda_pipeline_resume_uses_alternate_heading(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """User choice to use an alternate heading resumes the write step."""
    from agentforge.agents.approval_manager import approval_manager
    from agentforge.agents.orchestrator import AgentOrchestrator
    from agentforge.agents.task_state import (
        build_task_board_ui_payload,
        build_task_state,
        load_task_board_memory,
    )
    from agentforge.agents.workspace_intent import detect_workspace_intent
    from agentforge.models.schemas import ApprovalResponse
    from tests.helpers.orchestration_test_helpers import write_workspace_file

    write_workspace_file(
        temp_workspace,
        "GitHub/Test12/index.html",
        "<html><body><h1>Hello World</h1><h3>Hello Bot</h3></body></html>",
    )

    prompt = _test12_workflow_with_h3_derived_1txt_and_fitxt_prompt(
        temp_workspace,
        wrong_paths=True,
    )
    intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)
    task_state = build_task_state(prompt, intent)
    orchestrator = AgentOrchestrator()

    _summary, _reads, paused = await orchestrator._execute_workspace_agenda_pipeline(
        "pipeline-resume-test",
        prompt,
        intent,
        task_state,
        None,
    )
    assert paused is True
    pending = approval_manager.list_pending("pipeline-resume-test")
    assert pending

    message = await orchestrator.execute_approved_command(
        "pipeline-resume-test",
        pending[0].id,
        ApprovalResponse(approved=True, choice_id="use_h3"),
    )
    assert message is not None
    assert "Created `GitHub/Test12/fi.txt`" in message.content
    assert (temp_workspace / "GitHub" / "Test12" / "fi.txt").read_text(
        encoding="utf-8",
    ) == "Hello Bot\n"
    assert approval_manager.list_pending("pipeline-resume-test") == []

    board = await load_task_board_memory("pipeline-resume-test")
    assert board is not None
    refreshed_state = build_task_state(prompt, intent, board)
    payload = build_task_board_ui_payload(refreshed_state)
    fi_step = next(
        step for step in payload["steps"]
        if step.get("path") == "GitHub/Test12/fi.txt"
    )
    assert fi_step["status"] == "done"


@pytest.mark.asyncio
async def test_agenda_pipeline_reports_missing_h2_for_content_from_heading(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Missing h2 still offers recovery when other headings exist in the source HTML."""
    from agentforge.agents.orchestrator import AgentOrchestrator
    from agentforge.agents.task_state import build_task_state
    from agentforge.agents.workspace_intent import detect_workspace_intent
    from tests.helpers.orchestration_test_helpers import write_workspace_file

    write_workspace_file(
        temp_workspace,
        "GitHub/Test12/index.html",
        "<html><body><h1>Hello World</h1><h3>Hello Bot</h3></body></html>",
    )

    prompt = _test12_workflow_with_h3_derived_1txt_and_fitxt_prompt(
        temp_workspace,
        wrong_paths=True,
    )
    intent = detect_workspace_intent(normalize_user_prompt(prompt).normalized)
    task_state = build_task_state(prompt, intent)
    orchestrator = AgentOrchestrator()

    summary, _reads, paused = await orchestrator._execute_workspace_agenda_pipeline(
        "pipeline-test",
        prompt,
        intent,
        task_state,
        None,
    )

    assert paused is True
    from agentforge.agents.approval_manager import approval_manager

    pending = approval_manager.list_pending("pipeline-test")
    assert pending
    option_ids = [option["id"] for option in pending[0].payload["options"]]
    assert "use_h1" in option_ids
    assert "use_h3" in option_ids
    assert "skip" in option_ids
    assert "abort" in option_ids
    assert summary == ""
    assert not (temp_workspace / "GitHub" / "Test12" / "fi.txt").exists()


def test_build_content_from_heading_choice_options_without_alternates() -> None:
    """When no alternate headings exist, only skip and abort are offered."""
    from agentforge.agents.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator()
    options = orchestrator._build_content_from_heading_choice_options("h2", [])
    assert [option.id for option in options] == ["skip", "abort"]


@pytest.mark.asyncio
async def test_eight_step_workflow_missing_h2_skips_endless_multi_agent_discussion(
    monkeypatch,
    temp_workspace: Path,
) -> None:
    """Missing H2 for fi.txt pauses for user choice without multi-round agent chatter."""
    (temp_workspace / "GitHub").mkdir()
    prompt = _test12_workflow_with_h3_derived_1txt_and_fitxt_prompt(
        temp_workspace,
        wrong_paths=True,
    )
    captured: dict[str, list] = {}
    weak = '{"status": "success"}'

    result = await run_orchestration(
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
            capture=captured,
        ),
    )

    html_path = temp_workspace / "GitHub" / "Test12" / "index.html"
    derived_txt = temp_workspace / "GitHub" / "Test12" / "Hello Bot.txt"
    explicit_txt = temp_workspace / "GitHub" / "Test12" / "1.txt"
    fi_txt = temp_workspace / "GitHub" / "Test12" / "fi.txt"

    assert html_path.is_file()
    assert derived_txt.is_file()
    assert explicit_txt.is_file()
    assert not fi_txt.exists()

    assert result.pending_approvals
    pending = result.pending_approvals[0]
    assert pending.action_type == "user_choice"
    assert "no `<h2>` found" in pending.description
    assert "GitHub/Test12/fi.txt" in pending.description
    assert "GitHub/Test12/index.html" in pending.description

    reviewer_turns = len(captured.get("reviewer", []))
    pm_turns = len(captured.get("project_manager", []))
    assert reviewer_turns <= 1
    assert pm_turns <= 1


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
