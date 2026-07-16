"""Tests for PDF and Word document read/write helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentforge.agents.workspace_executor import read_workspace_file
from agentforge.tools.registry import ReadFileTool, WriteFileTool
from agentforge.utils.document_io import (
    is_document_path,
    read_document_text,
    write_document_text,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("report.pdf", True),
        ("notes.docx", True),
        ("README.md", False),
        ("data.json", False),
        (Path("folder/file.PDF"), True),
        (Path("folder/file.DOCX"), True),
    ],
)
def test_is_document_path(path: str | Path, expected: bool) -> None:
    """Document helper applies only to PDF and DOCX extensions."""
    assert is_document_path(path) is expected


def test_write_and_read_docx_roundtrip(temp_workspace: Path) -> None:
    """DOCX files round-trip plain text through document_io."""
    target = temp_workspace / "docs" / "report.docx"
    body = "Quarterly summary\nLine two\n"

    write_document_text(target, body)
    assert target.is_file()

    restored = read_document_text(target)
    assert "Quarterly summary" in restored
    assert "Line two" in restored


def test_write_and_read_pdf_roundtrip(temp_workspace: Path) -> None:
    """PDF files round-trip plain text through document_io."""
    target = temp_workspace / "exports" / "report.pdf"
    body = "Invoice total: 42 EUR\n"

    write_document_text(target, body)
    assert target.is_file()

    restored = read_document_text(target)
    assert "Invoice total: 42 EUR" in restored


@pytest.mark.asyncio
async def test_write_and_read_docx_via_tools(temp_workspace: Path) -> None:
    """WriteFileTool and ReadFileTool handle DOCX documents."""
    write_tool = WriteFileTool()
    read_tool = ReadFileTool()
    body = "Meeting notes\nAction items\n"

    write_result = await write_tool.execute(
        {"path": "notes/meeting.docx", "content": body},
    )
    assert write_result.success is True

    read_result = await read_tool.execute({"path": "notes/meeting.docx"})
    assert read_result.success is True
    assert "Meeting notes" in read_result.output
    assert "Action items" in read_result.output


@pytest.mark.asyncio
async def test_write_and_read_pdf_via_tools(temp_workspace: Path) -> None:
    """WriteFileTool and ReadFileTool handle PDF documents."""
    write_tool = WriteFileTool()
    read_tool = ReadFileTool()
    body = "Status report\nAll green\n"

    write_result = await write_tool.execute(
        {"path": "reports/status.pdf", "content": body},
    )
    assert write_result.success is True

    read_result = await read_tool.execute({"path": "reports/status.pdf"})
    assert read_result.success is True
    assert "Status report" in read_result.output
    assert "All green" in read_result.output


def test_read_workspace_file_reads_docx(temp_workspace: Path) -> None:
    """read_workspace_file extracts text from DOCX deliverables."""
    target = temp_workspace / "GitHub" / "Project" / "report.docx"
    target.parent.mkdir(parents=True)
    write_document_text(target, "Annual report\n")

    success, content = read_workspace_file("GitHub/Project/report.docx")
    assert success is True
    assert "Annual report" in content
