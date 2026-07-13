"""Heuristic routing from user input to SDLC agent roles."""

from __future__ import annotations

import re

AUTO_ROLE = "auto"
DEFAULT_SDLC_ROLE = "developer"

VALID_SDLC_ROLES = frozenset({
    "developer",
    "software_tester",
    "reviewer",
    "security",
    "architect",
    "devops",
    "researcher",
    "documentation",
    "project_manager",
})

SECURITY_KEYWORDS = re.compile(
    r"\b(security|sicherheit|vulnerability|schwachstelle|owasp|xss|csrf|"
    r"sql injection|exploit|cve|authentication|authorization|authentifizierung|"
    r"auth flow|auth token|api key|secrets|hardcoded|penetration|pentest|"
    r"threat|malware|sanitize)\b",
    re.IGNORECASE,
)
TEST_KEYWORDS = re.compile(
    r"\b(test|testing|tester|qa|quality assurance|pytest|jest|unittest|"
    r"unit test|integration test|e2e|regression|test case|test cases|testfall|"
    r"testfälle|edge case|coverage|assertion|mock|fixture)\b",
    re.IGNORECASE,
)
REVIEW_KEYWORDS = re.compile(
    r"\b(review|code review|pr review|pull request|best practice|lint|"
    r"code quality|refactor review|peer review|feedback on code)\b",
    re.IGNORECASE,
)
DEVOPS_KEYWORDS = re.compile(
    r"\b(devops|deploy|deployment|ci/?cd|pipeline|docker|kubernetes|k8s|"
    r"github actions|gitlab ci|terraform|ansible|helm|build script|release)\b",
    re.IGNORECASE,
)
ARCHITECT_KEYWORDS = re.compile(
    r"\b(architect|architecture|architektur|system design|design pattern|"
    r"module structure|microservice|monolith|scalability|schnittstelle|"
    r"interface design|technical design)\b",
    re.IGNORECASE,
)
DOC_KEYWORDS = re.compile(
    r"\b(documentation|dokumentation|readme|api doc|user guide|docs|"
    r"changelog|handbuch|technical writing|docstring|jsdoc|phpdoc)\b",
    re.IGNORECASE,
)
RESEARCH_KEYWORDS = re.compile(
    r"\b(research|recherche|compare|vergleich|evaluate|alternatives|"
    r"pros and cons|investigate|analyse approach|technology choice|"
    r"which library|welche bibliothek)\b",
    re.IGNORECASE,
)
PM_KEYWORDS = re.compile(
    r"\b(project plan|roadmap|break down|zerlegen|prioritize|priorisieren|"
    r"sprint|estimate|schätzen|coordinate|koordinieren|milestones|"
    r"project manager|projektleiter|task breakdown|work breakdown)\b",
    re.IGNORECASE,
)
CODE_KEYWORDS = re.compile(
    r"\b(code|coding|programm|implement|implementation|function|class|bug|"
    r"fix|refactor|feature|git|npm|python|php|typescript|javascript|java|"
    r"react|vue|angular|api endpoint|endpoint)\b",
    re.IGNORECASE,
)


def detect_sdlc_role(user_content: str) -> str:
    """
    Detect the best SDLC agent role for a user message.

    :param user_content: User message text
    :return: Registered role identifier
    """
    text = (user_content or "").strip()
    if not text:
        return DEFAULT_SDLC_ROLE

    checks: list[tuple[re.Pattern[str], str]] = [
        (SECURITY_KEYWORDS, "security"),
        (TEST_KEYWORDS, "software_tester"),
        (DEVOPS_KEYWORDS, "devops"),
        (REVIEW_KEYWORDS, "reviewer"),
        (ARCHITECT_KEYWORDS, "architect"),
        (DOC_KEYWORDS, "documentation"),
        (PM_KEYWORDS, "project_manager"),
        (RESEARCH_KEYWORDS, "researcher"),
        (CODE_KEYWORDS, "developer"),
    ]

    for pattern, role_id in checks:
        if pattern.search(text):
            return role_id

    return DEFAULT_SDLC_ROLE


def resolve_single_role(role_ids: list[str], user_content: str) -> tuple[str, bool]:
    """
    Resolve the effective single-agent role and whether auto-routing was used.

    :param role_ids: Selected role IDs from the client
    :param user_content: User message text
    :return: Tuple of (role_id, used_auto)
    """
    requested = role_ids[0] if role_ids else AUTO_ROLE
    if requested == AUTO_ROLE:
        return detect_sdlc_role(user_content), True
    if requested in VALID_SDLC_ROLES:
        return requested, False
    return DEFAULT_SDLC_ROLE, False
