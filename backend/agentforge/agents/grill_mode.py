"""Grill mode: serial clarification before planning and execution."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from agentforge.memory.store import memory_store

GRILL_SESSION_MEMORY_KEY = "_agentforge_grill_session"
MAX_GRILL_QUESTIONS = 15
GRILL_COMPLETE_MARKER = "[GRILL_COMPLETE]"

FALLBACK_GRILL_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "What outcome defines success for this feature?",
        "A working first version that matches the core user need.",
        "Defines the minimum viable scope.",
    ),
    (
        "What runtime or hosting environment should this use?",
        "A simple local PHP setup on the user's machine.",
        "Deployment constraints affect libraries and configuration.",
    ),
    (
        "Are there must-have constraints for security, data, or compliance?",
        "No secrets in source code; use environment variables for credentials.",
        "Security constraints must be settled before implementation.",
    ),
    (
        "Who is the primary user and what is their main workflow?",
        "A developer running the script manually from the command line.",
        "User workflow determines interfaces and error handling.",
    ),
)


class GrillPhase(StrEnum):
    """High-level grill workflow phase."""

    IDEA = "idea"
    CLARIFY = "clarify"
    PLAN = "plan"
    EXECUTE = "execute"
    TEST = "test"
    DONE = "done"


@dataclass
class GrillAnswer:
    """One answered clarification question."""

    question: str
    recommended_answer: str
    answer: str


@dataclass
class GrillSession:
    """Persisted grill-mode state for one chat."""

    chat_id: str
    phase: GrillPhase = GrillPhase.IDEA
    idea: str = ""
    answers: list[GrillAnswer] = field(default_factory=list)
    plan_markdown: str = ""
    summary: str = ""
    role_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize the session for persistence.

        :return: JSON-serializable dict
        """
        return {
            "chat_id": self.chat_id,
            "phase": self.phase.value,
            "idea": self.idea,
            "answers": [asdict(item) for item in self.answers],
            "plan_markdown": self.plan_markdown,
            "summary": self.summary,
            "role_ids": list(self.role_ids),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GrillSession:
        """
        Restore a session from persisted JSON.

        :param payload: Serialized session dict
        :return: GrillSession instance
        """
        answers = [
            GrillAnswer(
                question=str(item.get("question") or ""),
                recommended_answer=str(item.get("recommended_answer") or ""),
                answer=str(item.get("answer") or ""),
            )
            for item in payload.get("answers") or []
            if isinstance(item, dict)
        ]
        phase_value = str(payload.get("phase") or GrillPhase.IDEA.value)
        try:
            phase = GrillPhase(phase_value)
        except ValueError:
            phase = GrillPhase.IDEA
        return cls(
            chat_id=str(payload.get("chat_id") or ""),
            phase=phase,
            idea=str(payload.get("idea") or ""),
            answers=answers,
            plan_markdown=str(payload.get("plan_markdown") or ""),
            summary=str(payload.get("summary") or ""),
            role_ids=[str(role_id) for role_id in payload.get("role_ids") or []],
        )


GRILL_INTERVIEW_SYSTEM = """You are a requirements interviewer in Grill Mode.
Your job is to clarify a software idea through a focused interview BEFORE any implementation.

Rules:
- Ask exactly ONE question at a time.
- Provide a concrete recommended_answer the user can accept quickly.
- Ask dependency questions before detail questions.
- Use prior answers; do not repeat settled topics.
- Stop when critical scope, constraints, and acceptance criteria are clear.
- Typical sessions need 5-12 questions; never exceed 15.
- Do NOT write code or implementation steps yet.

Respond with JSON only, no markdown fences:
{"status":"question","question":"...","recommended_answer":"...","why":"..."}
or when done:
{"status":"complete","summary":"..."}
"""


GRILL_PLAN_SYSTEM = """You are a technical planner. Create an implementation plan markdown document.

Rules:
- Use the original idea and every clarified answer.
- Include: Goal, Decisions, Step-by-step tasks, Files/areas to touch, Acceptance criteria.
- Be specific enough that a developer agent can execute without guessing.
- Do NOT include code blocks — only the plan.
- Write in clear English (headings and bullets are fine).
"""


def resolve_grill_execution_mode(
    chat_mode: str | object,
    role_ids: list[str],
) -> str:
    """
    Resolve whether grill execute phase should use single- or multi-agent orchestration.

    Legacy grill chats store mode as ``grill``; infer from selected roles when needed.

    :param chat_mode: Stored chat orchestration mode
    :param role_ids: Selected role identifiers
    :return: ``single`` or ``multi``
    """
    mode_value = chat_mode.value if hasattr(chat_mode, "value") else str(chat_mode)
    if mode_value == "single":
        return "single"
    if mode_value == "multi":
        return "multi"
    return "single" if len(role_ids) <= 1 else "multi"


def format_grill_context_block(session: GrillSession) -> str:
    """
    Format idea and prior Q&A for LLM prompts.

    :param session: Active grill session
    :return: Context block text
    """
    lines = [f"Original idea:\n{session.idea.strip()}"]
    if session.answers:
        lines.append("\nClarifications so far:")
        for index, item in enumerate(session.answers, start=1):
            lines.append(
                f"{index}. Q: {item.question}\n"
                f"   Recommended: {item.recommended_answer}\n"
                f"   Chosen: {item.answer}",
            )
        lines.append("\nAlready asked — do NOT repeat these questions:")
        for item in session.answers:
            lines.append(f"- {item.question.strip()}")
    if session.summary:
        lines.append(f"\nInterview summary:\n{session.summary}")
    return "\n".join(lines)


def parse_grill_interview_response(content: str) -> dict[str, Any] | None:
    """
    Parse LLM JSON for the next grill question or completion.

    :param content: Raw model output
    :return: Parsed payload or None
    """
    text = (content or "").strip()
    if not text:
        return None
    if text.startswith(GRILL_COMPLETE_MARKER):
        return {"status": "complete", "summary": text.removeprefix(GRILL_COMPLETE_MARKER).strip()}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def normalize_grill_question(text: str) -> str:
    """
    Normalize a grill question for duplicate detection.

    :param text: Raw question text
    :return: Normalized comparison string
    """
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower(), flags=re.UNICODE)
    return " ".join(cleaned.split())


def grill_question_already_asked(session: GrillSession, question: str) -> bool:
    """
    Return True when the question was already asked in this grill session.

    :param session: Active grill session
    :param question: Candidate next question
    :return: True when the question repeats a prior one
    """
    normalized = normalize_grill_question(question)
    if not normalized:
        return False
    for item in session.answers:
        if normalize_grill_question(item.question) == normalized:
            return True
    return False


def fallback_grill_interview_step(session: GrillSession) -> dict[str, Any]:
    """
    Return a deterministic fallback question when the LLM response is unusable.

    :param session: Active grill session
    :return: Interview step payload
    """
    for question, recommended, why in FALLBACK_GRILL_QUESTIONS:
        if not grill_question_already_asked(session, question):
            return {
                "status": "question",
                "question": question,
                "recommended_answer": recommended,
                "why": why,
            }
    return {
        "status": "complete",
        "summary": "Enough clarification collected; proceeding to planning.",
    }


def build_grill_execution_prompt(session: GrillSession) -> str:
    """
    Build the user prompt handed to normal orchestration during execute phase.

    :param session: Completed grill session with plan
    :return: Execution prompt text
    """
    lines = [
        "Execute the following approved plan.",
        "",
        f"## Original idea\n{session.idea.strip()}",
    ]
    if session.answers:
        lines.append("\n## Clarifications")
        for item in session.answers:
            lines.append(f"- **Q:** {item.question}")
            lines.append(f"  **A:** {item.answer}")
    if session.summary:
        lines.append(f"\n## Interview summary\n{session.summary}")
    lines.append(f"\n## Approved plan\n{session.plan_markdown.strip()}")
    lines.append(
        "\nImplement the plan completely. Use workspace tools for all file changes.",
    )
    return "\n".join(lines)


def build_grill_test_prompt(session: GrillSession) -> str:
    """
    Build the verification prompt for the grill test phase.

    :param session: Grill session with approved plan and completed build
    :return: Test-phase prompt text
    """
    lines = [
        "Grill Mode — Test phase: verify the implementation against the approved plan.",
        "",
        f"## Original idea\n{session.idea.strip()}",
    ]
    if session.answers:
        lines.append("\n## Clarifications")
        for item in session.answers:
            lines.append(f"- **Q:** {item.question}")
            lines.append(f"  **A:** {item.answer}")
    if session.summary:
        lines.append(f"\n## Interview summary\n{session.summary}")
    if session.plan_markdown.strip():
        lines.append(f"\n## Approved plan\n{session.plan_markdown.strip()}")
    lines.append(
        "\n## Your task\n"
        "- Verify created or changed files exist and match the plan\n"
        "- Run applicable smoke checks or tests (syntax checks, test runners, etc.)\n"
        "- Confirm acceptance criteria from the plan\n"
        "- Report PASS or FAIL with concise findings",
    )
    return "\n".join(lines)


def build_grill_ui_payload(session: GrillSession) -> dict[str, Any]:
    """
    Build a WebSocket payload for grill phase UI updates.

    :param session: Active grill session
    :return: Serializable event payload
    """
    return {
        "type": "grill_phase_updated",
        "phase": session.phase.value,
        "idea": session.idea,
        "question_count": len(session.answers),
        "has_plan": bool(session.plan_markdown.strip()),
        "summary": session.summary,
    }


async def load_grill_session(chat_id: str) -> GrillSession | None:
    """
    Load persisted grill session for a chat.

    :param chat_id: Chat session ID
    :return: GrillSession or None
    """
    entry = await memory_store.get_entry(chat_id, "chat", GRILL_SESSION_MEMORY_KEY)
    if not entry:
        return None
    try:
        payload = json.loads(entry)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    session = GrillSession.from_dict(payload)
    session.chat_id = chat_id
    return session


async def persist_grill_session(session: GrillSession) -> None:
    """
    Persist grill session state for follow-up turns.

    :param session: Active grill session
    """
    payload = json.dumps(session.to_dict(), ensure_ascii=False)
    await memory_store.set_entry(session.chat_id, "chat", GRILL_SESSION_MEMORY_KEY, payload)


async def clear_grill_session(chat_id: str) -> None:
    """
    Remove persisted grill session data.

    :param chat_id: Chat session ID
    """
    await memory_store.set_entry(chat_id, "chat", GRILL_SESSION_MEMORY_KEY, "")


def new_grill_answer_id() -> str:
    """
    Generate a short identifier for grill approval payloads.

    :return: Unique id string
    """
    return uuid.uuid4().hex[:12]
