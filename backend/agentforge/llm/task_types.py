"""Task types for LLM routing."""

from enum import Enum


class TaskType(str, Enum):
    """Supported routing task categories."""

    CODING = "coding"
    CODE_REVIEW = "code_review"
    ARCHITECTURE = "architecture"
    RESEARCH = "research"
    DOCUMENTATION = "documentation"
    COORDINATION = "coordination"
    SQL = "sql"
    VISION = "vision"
    FINANCE = "finance"
    GENERAL = "general"
    TITLE = "title"
