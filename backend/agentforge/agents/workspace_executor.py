"""Execute workspace file creation when user intent is explicit."""

from __future__ import annotations

import ast
import asyncio
import builtins
import importlib.util
import py_compile
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentforge.agents.deliverable_types import (
    HTML_STRUCTURE,
    match_deliverable_file_type,
    match_deliverable_file_types,
)
from agentforge.agents.workspace_intent import WorkspaceIntent, extract_named_folder
from agentforge.config import settings
from agentforge.tools.registry import WriteFileTool, _resolve_path, normalize_workspace_relative_path
from agentforge.utils.document_io import is_document_path, read_document_text
from agentforge.utils.html_tags import (
    CONTENT_FROM_TAG,
    TAG_LITERAL_IN_REQUEST,
    extract_tag_insert_from_clause,
    extract_tag_text_from_html,
    insert_tag_after_tag,
    parse_tag_reference,
)
from agentforge.utils.optional_deps import OptionalDependencyError

REQUESTED_FILE = re.compile(
    r"\b([\w.-]+\.(?:php|html|htm|css|js|ts|tsx|jsx|py|md|json|txt|vue|sql|xml|yaml|yml|sh|pdf|docx))\b",
    re.IGNORECASE,
)
NAMED_FILE = re.compile(
    r"(?:datei|file)\s+mit\s+(?:dem\s+)?namen[\s.:,]+([\w.-]+\.\w+)",
    re.IGNORECASE,
)
CODE_FENCE = re.compile(r"^```[\w.-]*\n(.*?)```$", re.DOTALL | re.IGNORECASE)
MAX_PATH_COMPONENT_LENGTH = 255
MAX_WORKSPACE_PATH_LENGTH = 512
_SOURCE_CODE_PATH_MARKERS = re.compile(
    r"^(import |from .+ import |def |class |#!/|# )",
    re.MULTILINE,
)
_NAMED_FILE_BLOCK = re.compile(
    r"(?:^|\n)(?P<name>(?:[\w./-]+/)?[\w.-]+\.(?:py|txt|toml|cfg|ini|md|json|yaml|yml|sh))"
    r"\s*\n```[\w.-]*\n(?P<body>.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_FENCED_BLOCK = re.compile(
    r"```[\w.-]*\n(?P<body>.*?)```",
    re.DOTALL,
)
LITERAL_TEXT = re.compile(
    r'(?:text|inhalt|content|schreib(?:e|en|st)?|write(?:s|ing)?)\s+["\«„]([^"\»""]+)["\»""]',
    re.IGNORECASE,
)
H1_LITERAL = TAG_LITERAL_IN_REQUEST
H1_TAG = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
HEADING_TAG = re.compile(
    r"<h([1-6])\b[^>]*>(.*?)</h\1>",
    re.IGNORECASE | re.DOTALL,
)
CONTENT_SOURCE_TAG = CONTENT_FROM_TAG
STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
ABSOLUTE_ASSET = re.compile(
    r"""(?P<attr>href|src)=["'](?P<path>/[^"']+\.(?:css|js))["']""",
    re.IGNORECASE,
)
EXTERNAL_SCRIPT = re.compile(
    r"""<script\b[^>]*\bsrc=["']https?://[^"']+["'][^>]*>\s*</script>""",
    re.IGNORECASE,
)
EXPLICIT_BARE_FILENAME = re.compile(
    r"\b(?:txt\s+)?datei\s+([\w.-]+\.\w+)\b",
    re.IGNORECASE,
)
CONTENT_SOURCE_HEADING = CONTENT_SOURCE_TAG

_PLACEHOLDER_MARKERS = re.compile(
    r"|".join(
        (
            r"\bplaceholder\b",
            r"\bTODO\b",
            r"\bFIXME\b",
            r"you need to implement",
            r"implement the logic",
            r"not implemented",
            r"replace this",
            r"add (?:your|the) code here",
            r"placeholder for",
            r"simulates (?:animation|frames)",
            r"Generated for request:",
        )
    ),
    re.IGNORECASE,
)

_CODE_FILE_EXTENSIONS = frozenset(
    {".py", ".js", ".ts", ".tsx", ".jsx", ".php", ".sh", ".vue", ".sql"}
)

_SUBSTANTIVE_CODE_TYPE_IDS = frozenset(
    {"python", "javascript", "typescript", "tsx", "jsx", "vue", "php", "shell", "sql"}
)

_SUBSTANTIVE_CREATION_HINT = re.compile(
    r"\b("
    r"tool|implement|implementation|build|app|script|programm|software|"
    r"module|class|function|api|library|package|3d|game|server|cli"
    r")\b",
    re.IGNORECASE,
)


def looks_like_source_code(text: str) -> bool:
    """
    Detect when text is program source rather than a filesystem path.

    :param text: Candidate path or file body
    :return: True when the text resembles source code
    """
    sample = (text or "").strip()
    if not sample or "\n" not in sample:
        return False
    if _SOURCE_CODE_PATH_MARKERS.search(sample):
        return True
    if sample.count("\n") >= 2 and ("import " in sample or "def " in sample):
        return True
    return False


def validate_workspace_relative_path(path_str: str) -> tuple[bool, str]:
    """
    Validate a workspace-relative path before touching the filesystem.

    :param path_str: Absolute or workspace-relative path candidate
    :return: Tuple of validity flag and error reason
    """
    raw = (path_str or "").strip().strip("'\"")
    if not raw:
        return False, "Empty path"
    if "\n" in raw or "\r" in raw:
        return False, "Path contains newline characters"
    if len(raw) > MAX_WORKSPACE_PATH_LENGTH:
        return False, "Path exceeds maximum length"
    if looks_like_source_code(raw):
        return False, "Path looks like source code, not a file path"
    if "```" in raw:
        return False, "Path contains markdown code fences"
    for part in Path(raw).parts:
        if part in {".", ".."}:
            continue
        if len(part) > MAX_PATH_COMPONENT_LENGTH:
            return False, "Path component exceeds filesystem limit"
    return True, ""


def maybe_swap_write_file_arguments(path: str, content: str) -> tuple[str, str]:
    """
    Recover from models that swap write_file path and content arguments.

    :param path: Declared target path
    :param content: Declared file body
    :return: Corrected path and content
    """
    path_ok, _ = validate_workspace_relative_path(path)
    content_ok, _ = validate_workspace_relative_path(content)
    if not path_ok and content_ok:
        return content, path
    return path, content


def parse_multifile_llm_output(raw_content: str, primary_path: str) -> dict[str, str]:
    """
    Split a multi-file markdown LLM response into workspace-relative paths.

    :param raw_content: Raw model output that may contain several fenced files
    :param primary_path: Canonical path for the main deliverable
    :return: Mapping of workspace-relative path to file body
    """
    text = (raw_content or "").strip()
    if not text:
        return {primary_path: ""}

    parent = str(Path(primary_path).parent)
    files: dict[str, str] = {}

    for match in _NAMED_FILE_BLOCK.finditer(text):
        name = match.group("name").strip()
        body = match.group("body").strip()
        if "/" not in name:
            name = f"{parent}/{name}" if parent != "." else name
        files[name] = body

    if not files:
        blocks = [match.group("body").strip() for match in _FENCED_BLOCK.finditer(text)]
        if len(blocks) >= 2:
            files[primary_path] = blocks[0]
            req_path = f"{parent}/requirements.txt" if parent != "." else "requirements.txt"
            files[req_path] = blocks[1]
            return files
        if len(blocks) == 1:
            return {primary_path: blocks[0]}
        return {primary_path: strip_code_fences(text)}

    primary_name = Path(primary_path).name
    if primary_path not in files:
        blocks = [match.group("body").strip() for match in _FENCED_BLOCK.finditer(text)]
        if blocks and primary_name.endswith(".py"):
            files[primary_path] = blocks[0]
        else:
            for candidate_path, candidate_body in list(files.items()):
                if Path(candidate_path).name == primary_name:
                    files[primary_path] = candidate_body
                    if candidate_path != primary_path:
                        del files[candidate_path]
                    break
            else:
                if blocks:
                    files[primary_path] = blocks[0]
                elif files:
                    first_path = next(iter(files))
                    files[primary_path] = files[first_path]
                    if first_path != primary_path and not first_path.endswith("requirements.txt"):
                        del files[first_path]
    return files


def is_placeholder_content(content: str, relative_path: str = "") -> bool:
    """
    Heuristically detect stub or placeholder file bodies.

    :param content: File body text
    :param relative_path: Workspace-relative path for extension-aware checks
    :return: True when the content looks like a placeholder rather than code
    """
    text = (content or "").strip()
    if not text:
        return True

    suffix = Path(relative_path).suffix.lower() if relative_path else ""
    if _PLACEHOLDER_MARKERS.search(text):
        if not suffix or suffix in _CODE_FILE_EXTENSIONS:
            return True

    if suffix == ".py":
        import ast

        try:
            tree = ast.parse(text)
        except SyntaxError:
            return True
        has_logic = any(
            isinstance(
                node,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                    ast.Import,
                    ast.ImportFrom,
                ),
            )
            for node in ast.walk(tree)
        )
        if not has_logic and _PLACEHOLDER_MARKERS.search(text):
            return True

    if suffix in _CODE_FILE_EXTENSIONS and len(text.splitlines()) <= 3:
        return _PLACEHOLDER_MARKERS.search(text) is not None

    return False


def is_substantive_software_creation(
    user_content: str,
    intent: WorkspaceIntent,
) -> bool:
    """
    Return True when the user expects runnable code rather than literal text.

    :param user_content: Original user message
    :param intent: Parsed workspace intent
    :return: Whether implementation-quality checks apply
    """
    if not intent.wants_file_creation:
        return False
    if extract_literal_text_content(user_content) is not None:
        return False

    text = user_content or ""
    matched = match_deliverable_file_types(text)
    if any(spec.type_id in _SUBSTANTIVE_CODE_TYPE_IDS for spec in matched):
        return True
    return _SUBSTANTIVE_CREATION_HINT.search(text) is not None


def collect_placeholder_implementation_paths(
    user_content: str,
    intent: WorkspaceIntent,
    required_paths: list[str],
) -> list[str]:
    """
    Return required write paths whose on-disk content is placeholder-only.

    :param user_content: Original user request
    :param intent: Parsed workspace intent
    :param required_paths: Canonical paths that must be written
    :return: Paths containing placeholder or stub content
    """
    if not is_substantive_software_creation(user_content, intent):
        return []

    placeholders: list[str] = []
    for path in required_paths:
        if not file_exists_in_workspace(path):
            continue
        success, content = read_workspace_file(path)
        if success and is_placeholder_content(content, path):
            placeholders.append(path)
    return placeholders


_STDLIB_MODULE_NAMES = frozenset(getattr(sys, "stdlib_module_names", set()))

_IMPORT_TO_PIP: dict[str, str] = {
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "numpy": "numpy",
    "np": "numpy",
    "pandas": "pandas",
    "pd": "pandas",
    "pygame": "pygame",
    "pyglet": "pyglet",
    "vpython": "vpython",
    "matplotlib": "matplotlib",
    "plt": "matplotlib",
    "sklearn": "scikit-learn",
    "scipy": "scipy",
    "requests": "requests",
    "flask": "flask",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "django": "django",
    "sqlalchemy": "SQLAlchemy",
    "pytest": "pytest",
    "yaml": "PyYAML",
    "OpenGL": "PyOpenGL",
    "serial": "pyserial",
    "bs4": "beautifulsoup4",
    "docx": "python-docx",
    "pypdf": "pypdf",
    "fpdf": "fpdf2",
    "mpl_toolkits": "matplotlib",
}

_PIP_TO_IMPORT: dict[str, str] = {
    "Pillow": "PIL",
    "opencv-python": "cv2",
    "scikit-learn": "sklearn",
    "PyYAML": "yaml",
    "python-docx": "docx",
    "fpdf2": "fpdf",
    "PyOpenGL": "OpenGL",
    "beautifulsoup4": "bs4",
    "pyserial": "serial",
}


@dataclass(frozen=True)
class PythonAnalysis:
    """Static analysis summary for one Python source body."""

    issues: list[str]
    third_party_packages: list[str]
    compile_error: str | None = None


def _python_source_from_path_or_content(path_or_content: str) -> str:
    """
    Resolve Python source from a workspace path or an inline code body.

    :param path_or_content: Workspace-relative path or raw Python source
    :return: Python source text
    """
    text = path_or_content or ""
    if not text.strip():
        return ""
    if "\n" in text or looks_like_source_code(text):
        return text
    if len(text) > MAX_WORKSPACE_PATH_LENGTH:
        return text
    valid, _ = validate_workspace_relative_path(text)
    if not valid:
        return text
    try:
        absolute = (settings.workspace_root.resolve() / text).resolve()
        root = settings.workspace_root.resolve()
        if absolute.is_file() and str(absolute).startswith(str(root)):
            return absolute.read_text(encoding="utf-8")
    except (OSError, PermissionError, ValueError):
        return text
    return text


def _pip_package_for_module(module_name: str) -> str | None:
    root = (module_name or "").split(".")[0]
    if not root or root.startswith("_"):
        return None
    if root in _STDLIB_MODULE_NAMES:
        return None
    if root == "mpl_toolkits":
        return "matplotlib"
    return _IMPORT_TO_PIP.get(root, root)


def _import_name_for_pip_package(package_name: str) -> str:
    """
    Map a pip package name to the top-level Python module used for import checks.

    :param package_name: Pip requirement name
    :return: Module name for importlib lookup
    """
    normalized = package_name.split("==")[0].split(">=")[0].split("[")[0].strip()
    if normalized in _PIP_TO_IMPORT:
        return _PIP_TO_IMPORT[normalized]
    return normalized.replace("-", "_").lower()


def _third_party_imports_unavailable(packages: list[str]) -> list[str]:
    """
    Return pip package names that are not importable in the active interpreter.

    :param packages: Third-party pip package names
    :return: Packages that cannot be imported
    """
    missing: list[str] = []
    for package in packages:
        module_name = _import_name_for_pip_package(package)
        if importlib.util.find_spec(module_name) is None:
            missing.append(package)
    return missing


def _collect_python_import_bindings(tree: ast.AST) -> tuple[set[str], list[str]]:
    bound_names: set[str] = set()
    third_party_packages: list[str] = []
    seen_packages: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                bound_names.add(bound)
                package = _pip_package_for_module(alias.name)
                if package and package not in seen_packages:
                    seen_packages.add(package)
                    third_party_packages.append(package)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                package = _pip_package_for_module(node.module)
                if package and package not in seen_packages:
                    seen_packages.add(package)
                    third_party_packages.append(package)
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound_names.add(alias.asname or alias.name)

    return bound_names, third_party_packages


def _collect_undefined_loaded_names(tree: ast.AST, bound_names: set[str]) -> list[str]:
    builtin_names = set(dir(builtins))
    module_scope: set[str] = set(bound_names)
    undefined: list[str] = []
    seen: set[str] = set()

    class _ScopeVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scopes: list[set[str]] = [set()]

        def _bind(self, name: str) -> None:
            self.scopes[-1].add(name)

        def _bind_target(self, node: ast.AST) -> None:
            if isinstance(node, ast.Name):
                self._bind(node.id)
            elif isinstance(node, (ast.Tuple, ast.List)):
                for elt in node.elts:
                    self._bind_target(elt)

        def _is_bound(self, name: str) -> bool:
            if name in builtin_names or name.startswith("__") and name.endswith("__"):
                return True
            if name in module_scope:
                return True
            return any(name in scope for scope in reversed(self.scopes))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            module_scope.add(node.name)
            arg_names = {arg.arg for arg in node.args.args}
            arg_names.update(arg.arg for arg in node.args.kwonlyargs)
            if node.args.vararg:
                arg_names.add(node.args.vararg.arg)
            if node.args.kwarg:
                arg_names.add(node.args.kwarg.arg)
            self.scopes.append(set(arg_names))
            self.generic_visit(node)
            self.scopes.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)  # type: ignore[arg-type]

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            module_scope.add(node.name)
            self.scopes.append(set())
            self.generic_visit(node)
            self.scopes.pop()

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                self._bind_target(target)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if node.target is not None:
                self._bind_target(node.target)
            self.generic_visit(node)

        def visit_For(self, node: ast.For) -> None:
            self._bind_target(node.target)
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
            self.visit_For(node)  # type: ignore[arg-type]

        def visit_With(self, node: ast.With) -> None:
            for item in node.items:
                if item.optional_vars is not None:
                    self._bind_target(item.optional_vars)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                self._bind(node.name)
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if not isinstance(node.ctx, ast.Load):
                return
            name = node.id
            if self._is_bound(name):
                return
            if name in seen:
                return
            seen.add(name)
            undefined.append(name)

    _ScopeVisitor().visit(tree)
    return undefined


def analyze_python_file(
    path_or_content: str,
    relative_path: str = "",
    *,
    user_content: str = "",
) -> list[str]:
    """
    Return static analysis issues for Python source content.

    Checks syntax, py_compile, likely missing imports, and dependency metadata.

    :param path_or_content: File path or raw Python source
    :param relative_path: Workspace-relative path for requirements.txt checks
    :param user_content: Original user request for stdlib preference checks
    :return: Human-readable issue strings; empty when no issues found
    """
    return analyze_python_file_details(
        path_or_content,
        relative_path,
        user_content=user_content,
    ).issues


def analyze_python_file_details(
    path_or_content: str,
    relative_path: str = "",
    *,
    user_content: str = "",
) -> PythonAnalysis:
    """
    Return structured Python analysis for one file body.

    :param path_or_content: File path or raw Python source
    :param relative_path: Workspace-relative path for requirements.txt checks
    :return: Structured analysis summary
    """
    content = _python_source_from_path_or_content(path_or_content)
    issues: list[str] = []
    compile_error: str | None = None
    if not content.strip():
        return PythonAnalysis(["empty Python file"], [], None)

    try:
        tree = ast.parse(content, filename=relative_path or "<string>")
    except SyntaxError as exc:
        message = f"syntax error: {exc.msg} (line {exc.lineno})"
        return PythonAnalysis([message], [], message)

    bound_names, third_party_packages = _collect_python_import_bindings(tree)
    undefined_names = _collect_undefined_loaded_names(tree, bound_names)
    if undefined_names:
        preview = ", ".join(undefined_names[:8])
        issues.append(f"likely missing imports for: {preview}")

    try:
        temp_path = ""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(content)
            temp_path = handle.name
        py_compile.compile(temp_path, doraise=True)
    except py_compile.PyCompileError as exc:
        compile_error = f"compile error: {exc.msg}"
        issues.append(compile_error)
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass

    if user_content and prefers_python_stdlib_only(user_content) and third_party_packages:
        packages = ", ".join(third_party_packages)
        issues.append(
            "third-party libraries used without explicit request; "
            f"prefer Python stdlib (tkinter, math, time): {packages}"
        )

    if third_party_packages and relative_path:
        req_path = Path(relative_path).parent / "requirements.txt"
        if not file_exists_in_workspace(str(req_path)):
            packages = ", ".join(third_party_packages)
            issues.append(
                f"missing requirements.txt for third-party packages: {packages}"
            )
        else:
            unavailable = _third_party_imports_unavailable(third_party_packages)
            if unavailable:
                packages = ", ".join(unavailable)
                issues.append(f"third-party packages not installed: {packages}")

    return PythonAnalysis(issues, third_party_packages, compile_error)


def is_runnable_python_content(
    content: str,
    relative_path: str = "",
    *,
    user_content: str = "",
) -> bool:
    """
    Return True when Python content passes static runnable checks.

    :param content: Python source body
    :param relative_path: Workspace-relative path for dependency checks
    :return: Whether the file appears runnable
    """
    if not relative_path.endswith(".py"):
        return True
    return not analyze_python_file(content, relative_path, user_content=user_content)


def collect_non_runnable_implementation_paths(
    user_content: str,
    intent: WorkspaceIntent,
    required_paths: list[str],
) -> list[str]:
    """
    Return required write paths whose on-disk Python is not runnable.

    :param user_content: Original user request
    :param intent: Parsed workspace intent
    :param required_paths: Canonical paths that must be written
    :return: Paths containing non-runnable Python deliverables
    """
    if not is_substantive_software_creation(user_content, intent):
        return []

    broken: list[str] = []
    for path in required_paths:
        if not path.endswith(".py"):
            continue
        if not file_exists_in_workspace(path):
            continue
        success, content = read_workspace_file(path)
        if success and not is_runnable_python_content(
            content,
            path,
            user_content=user_content,
        ):
            broken.append(path)
    return broken


def sync_requirements_txt(relative_dir: str, third_party_packages: list[str]) -> str | None:
    """
    Create or update requirements.txt with third-party packages.

    :param relative_dir: Workspace-relative directory containing deliverables
    :param third_party_packages: Pip package names to ensure are listed
    :return: Workspace-relative requirements path when written, else None
    """
    packages = sorted({item.strip() for item in third_party_packages if item.strip()})
    if not packages:
        return None

    normalized_dir = normalize_workspace_relative_path(relative_dir.rstrip("/") or ".")
    req_relative = f"{normalized_dir}/requirements.txt" if normalized_dir != "." else "requirements.txt"
    existing_lines: list[str] = []
    if file_exists_in_workspace(req_relative):
        success, content = read_workspace_file(req_relative)
        if success:
            existing_lines = [
                line.strip()
                for line in content.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]

    merged: list[str] = []
    seen_lower: set[str] = set()
    for line in existing_lines:
        key = line.split("==")[0].split(">=")[0].split("[")[0].strip().lower()
        if not key or key in seen_lower:
            continue
        seen_lower.add(key)
        merged.append(line)
    for package in packages:
        key = package.split("==")[0].split(">=")[0].split("[")[0].strip().lower()
        if not key or key in seen_lower:
            continue
        seen_lower.add(key)
        merged.append(package)

    body = "\n".join(merged) + "\n"
    target = (settings.workspace_root.resolve() / req_relative).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return req_relative


_THIRD_PARTY_LIBRARY_NAMES = re.compile(
    r"\b("
    r"pyglet|pygame|matplotlib|numpy|pandas|flask|django|fastapi|requests|"
    r"pillow|opencv|torch|tensorflow|sklearn|scipy|streamlit|gradio|vpython"
    r")\b",
    re.IGNORECASE,
)

_STANDALONE_PYTHON_REQUEST = re.compile(
    r"\b("
    r"python\s+programm|python\s+script|python\s+tool|python\s+3d|"
    r"3d\s+animation|standalone|einfach(?:es|en|er)?\s+python"
    r")\b",
    re.IGNORECASE,
)


def prefers_python_stdlib_only(user_content: str) -> bool:
    """
    Decide whether a request should use stdlib-only Python unless a library is named.

    :param user_content: Original user request
    :return: True when third-party libraries were not requested explicitly
    """
    text = user_content or ""
    if _THIRD_PARTY_LIBRARY_NAMES.search(text):
        return False
    return bool(_STANDALONE_PYTHON_REQUEST.search(text))


def python_stdlib_delivery_rules(user_content: str) -> str:
    """
    Build optional prompt rules steering simple Python tools toward stdlib.

    :param user_content: Original user request
    :return: Extra prompt lines or an empty string
    """
    if not prefers_python_stdlib_only(user_content):
        return ""
    return (
        "- Prefer Python stdlib only (tkinter, math, time, cmath) for this "
        "standalone script unless the user named a specific library.\n"
        "- Do not use pyglet, pygame, matplotlib, or OpenGL unless explicitly "
        "requested.\n"
        "- Do not create virtual environments; dependencies are installed "
        "automatically via requirements.txt.\n"
    )


async def install_python_requirements(relative_dir: str) -> tuple[bool, str]:
    """
    Install pip packages listed in a workspace project's requirements.txt.

    :param relative_dir: Workspace-relative directory containing requirements.txt
    :return: Tuple of success flag and status message
    """
    normalized_dir = normalize_workspace_relative_path(relative_dir.rstrip("/") or ".")
    req_relative = (
        f"{normalized_dir}/requirements.txt" if normalized_dir != "." else "requirements.txt"
    )
    if not file_exists_in_workspace(req_relative):
        return False, "No requirements.txt"

    cwd = settings.workspace_root.resolve()
    if normalized_dir != ".":
        cwd = (cwd / normalized_dir).resolve()

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        "requirements.txt",
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = (stdout.decode() + stderr.decode()).strip()
    if proc.returncode == 0:
        return True, f"Installed dependencies from {req_relative}"
    detail = output[:500] if output else f"exit code {proc.returncode}"
    return False, f"pip install failed: {detail}"


def _python_compile_error(content: str, relative_path: str) -> str | None:
    if not relative_path.endswith(".py"):
        return None
    return analyze_python_file_details(content, relative_path).compile_error


def _content_is_strict_subset(candidate: str, existing: str) -> bool:
    candidate_lines = {line.strip() for line in candidate.splitlines() if line.strip()}
    existing_lines = {line.strip() for line in existing.splitlines() if line.strip()}
    return candidate_lines <= existing_lines and candidate.strip() != existing.strip()


def should_skip_redundant_write(relative_path: str, new_content: str) -> bool:
    """
    Skip duplicate writes or attempts to replace real code with placeholders.

    :param relative_path: Workspace-relative file path
    :param new_content: Candidate file body
    :return: True when the write should not be performed
    """
    if not file_exists_in_workspace(relative_path):
        return False
    success, existing = read_workspace_file(relative_path)
    if not success:
        return False
    if existing.strip() == new_content.strip():
        return True
    if (
        not is_placeholder_content(existing, relative_path)
        and is_placeholder_content(new_content, relative_path)
    ):
        return True

    if relative_path.endswith(".py"):
        try:
            existing_error = _python_compile_error(existing, relative_path)
            new_error = _python_compile_error(new_content, relative_path)
        except OSError:
            return False
        if existing_error and new_error and existing_error == new_error:
            return True
        if (
            not is_runnable_python_content(existing, relative_path)
            and not is_runnable_python_content(new_content, relative_path)
            and _content_is_strict_subset(new_content, existing)
        ):
            return True
    return False


def extract_literal_text_content(user_content: str) -> str | None:
    """
    Extract literal file body text from quoted phrases in the user request.

    :param user_content: User message text
    :return: Literal text or None
    """
    match = LITERAL_TEXT.search(user_content or "")
    if not match:
        return None
    text = match.group(1).strip()
    return text or None


def extract_explicit_filename_from_clause(clause: str) -> str | None:
    """
    Extract an explicit bare filename such as ``1.txt`` from one clause.

    :param clause: Prompt clause text
    :return: Filename including extension or None
    """
    match = EXPLICIT_BARE_FILENAME.search(clause or "")
    if not match:
        return None
    filename = match.group(1).strip()
    return filename or None


def extract_content_source_tag(clause: str) -> str | None:
    """
    Extract an HTML tag whose text should populate a write target.

    :param clause: Prompt clause text
    :return: Tag name such as h1, p, or div, or None
    """
    match = CONTENT_SOURCE_TAG.search(clause or "")
    if not match:
        return None
    return match.group(1).lower()


def extract_content_source_heading(clause: str) -> str | None:
    """
    Extract an HTML tag whose text should populate a write target.

    :param clause: Prompt clause text
    :return: Tag name such as h1 or None
    """
    return extract_content_source_tag(clause)


def resolve_write_path(
    filename: str,
    primary_dir: str | None,
    primary_file: str | None,
) -> str:
    """
    Resolve a bare filename against the current workflow directory context.

    :param filename: Bare filename or workspace-relative path
    :param primary_dir: Workspace-relative directory from the plan
    :param primary_file: Primary deliverable file path
    :return: Workspace-relative write path
    """
    if "/" in filename:
        return filename
    if primary_dir:
        return f"{primary_dir}/{filename}"
    if primary_file:
        parent = str(Path(primary_file).parent)
        if parent and parent not in {"", "."}:
            return f"{parent}/{filename}"
    return filename


def list_available_headings(html_content: str) -> list[str]:
    """
    List heading tag names (h1-h6) that contain extractable text in HTML.

    :param html_content: HTML document body
    :return: Ordered unique heading tag names present in the document
    """
    tags: list[str] = []
    seen: set[str] = set()
    for level in range(1, 7):
        tag = f"h{level}"
        if extract_tag_text_from_html(html_content, tag) and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def plan_write_body_from_html_source(html_content: str, tag_name: str) -> str | None:
    """
    Build a text file body from one HTML element inside HTML content.

    :param html_content: HTML document body
    :param tag_name: HTML tag name such as h1, p, or strong
    :return: Plain-text body with trailing newline or None
    """
    text = extract_tag_text_from_html(html_content, tag_name)
    if not text:
        return None
    return text if text.endswith("\n") else f"{text}\n"


def extract_literal_tag_text_from_request(user_content: str) -> str | None:
    """
    Extract literal element body text requested in natural language.

    :param user_content: User message text
    :return: Literal text or None
    """
    text = user_content or ""
    match = TAG_LITERAL_IN_REQUEST.search(text)
    if match:
        body = match.group(1).strip()
        return body or None
    if parse_tag_reference(text) or re.search(r"\büberschrift\b|\bheading\b", text, re.IGNORECASE):
        literal = extract_literal_text_content(text)
        if literal:
            return literal
    return None


def extract_h1_text_from_request(user_content: str) -> str | None:
    """
    Extract literal H1 body text requested in natural language.

    :param user_content: User message text
    :return: H1 text or None
    """
    return extract_literal_tag_text_from_request(user_content)


def extract_hN_text(html: str, level: int) -> str | None:
    """
    Extract the first HN element text from HTML content.

    :param html: HTML document body
    :param level: Heading level 1-6
    :return: Plain heading text or None
    """
    if level < 1 or level > 6:
        return None
    return extract_tag_text_from_html(html, f"h{level}")


def extract_h1_text(html: str) -> str | None:
    """
    Extract the first H1 element text from HTML content.

    :param html: HTML document body
    :return: Plain H1 text or None
    """
    return extract_tag_text_from_html(html, "h1")


def sanitize_filename_from_text(text: str) -> str:
    """
    Build a filesystem-safe basename from visible text.

    :param text: Source text such as an H1 label
    :return: Sanitized basename without extension
    """
    cleaned = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    if not cleaned:
        return "output"
    return cleaned[:120]


def plan_derived_txt_from_tag(
    relative_html_path: str,
    html_content: str,
    naming_source: str = "h1",
) -> tuple[str, str] | None:
    """
    Plan a .txt deliverable named after text inside an HTML element.

    :param relative_html_path: Workspace-relative HTML path
    :param html_content: HTML file body
    :param naming_source: HTML tag name such as h1, p, or strong
    :return: Tuple of (relative_txt_path, txt_body) or None
    """
    source = (naming_source or "h1").lower()
    element_text = extract_tag_text_from_html(html_content, source)
    if not element_text:
        return None
    parent = str(Path(relative_html_path).parent)
    basename = sanitize_filename_from_text(element_text)
    relative_txt = f"{parent}/{basename}.txt" if parent not in {"", "."} else f"{basename}.txt"
    body = element_text if element_text.endswith("\n") else f"{element_text}\n"
    return relative_txt, body


def plan_derived_txt_from_heading(
    relative_html_path: str,
    html_content: str,
    naming_source: str = "h1",
) -> tuple[str, str] | None:
    """
    Plan a .txt deliverable named after text inside an HTML element.

    :param relative_html_path: Workspace-relative HTML path
    :param html_content: HTML file body
    :param naming_source: HTML tag name such as h1, h2, or p
    :return: Tuple of (relative_txt_path, txt_body) or None
    """
    return plan_derived_txt_from_tag(relative_html_path, html_content, naming_source)


def plan_derived_txt_from_h1(relative_html_path: str, html_content: str) -> tuple[str, str] | None:
    """
    Plan a .txt deliverable named after the first H1 in HTML content.

    :param relative_html_path: Workspace-relative HTML path
    :param html_content: HTML file body
    :return: Tuple of (relative_txt_path, txt_body) or None
    """
    return plan_derived_txt_from_heading(relative_html_path, html_content, naming_source="h1")


def extract_html_tag_insert_from_clause(
    clause_text: str,
) -> tuple[str, str, str] | tuple[int, int, str] | None:
    """
    Extract an HTML element insertion request from one clause.

    :param clause_text: Prompt clause text
    :return: Tuple of (after_tag, insert_tag, text) or None
    """
    parsed = extract_tag_insert_from_clause(clause_text)
    if not parsed:
        return None
    after_tag, insert_tag, text = parsed
    if after_tag.startswith("h") and after_tag[1:].isdigit() and insert_tag.startswith("h") and insert_tag[1:].isdigit():
        return int(after_tag[1:]), int(insert_tag[1:]), text
    return after_tag, insert_tag, text


def insert_heading_after_heading(
    html: str,
    after_level: int,
    insert_level: int,
    text: str,
) -> str:
    """
    Insert a new heading element immediately after an existing heading.

    :param html: HTML document body
    :param after_level: Existing heading level to insert after
    :param insert_level: New heading level to insert
    :param text: Visible heading text
    :return: Updated HTML document
    """
    return insert_tag_after_tag(html, f"h{after_level}", f"h{insert_level}", text)


async def apply_html_tag_insertion(
    relative_path: str,
    after_tag: str,
    insert_tag: str,
    text: str,
) -> tuple[bool, str]:
    """
    Insert an HTML element into a workspace HTML file and write the result back.

    :param relative_path: Workspace-relative file path
    :param after_tag: Existing element tag name to insert after
    :param insert_tag: New element tag name to insert
    :param text: Visible element text
    :return: Tuple of success flag and summary or error message
    """
    success, content = read_workspace_file(relative_path)
    if not success:
        return False, content
    existing = extract_tag_text_from_html(content, insert_tag)
    if existing == text:
        return True, f"Element <{insert_tag}> already present in {relative_path}"
    updated = insert_tag_after_tag(content, after_tag, insert_tag, text)
    if updated == content:
        return False, f"Could not find <{after_tag}> in {relative_path}"
    write_ok, output = await write_file_direct(relative_path, updated)
    if not write_ok:
        return False, output
    return True, (
        f'Inserted <{insert_tag}> "{text}" under <{after_tag}> in {relative_path}'
    )


async def apply_html_heading_insertion(
    relative_path: str,
    after_level: int,
    insert_level: int,
    text: str,
) -> tuple[bool, str]:
    """
    Insert a heading into a workspace HTML file and write the result back.

    :param relative_path: Workspace-relative file path
    :param after_level: Existing heading level to insert after
    :param insert_level: New heading level to insert
    :param text: Visible heading text
    :return: Tuple of success flag and summary or error message
    """
    return await apply_html_tag_insertion(
        relative_path,
        f"h{after_level}",
        f"h{insert_level}",
        text,
    )


def build_deliverable_status_summary(user_content: str, intent: WorkspaceIntent) -> str:
    """
    Build a verified on-disk summary for planned deliverables.

    :param user_content: Original user request
    :param intent: Parsed workspace intent
    :return: Human-readable status message or empty string
    """
    planned = plan_deliverable_files(user_content, intent)
    if not planned:
        return ""

    root = settings.workspace_root.resolve()
    created: list[str] = []
    missing: list[str] = []
    for relative in planned:
        absolute = root / relative
        if file_exists_in_workspace(relative):
            created.append(f"- {relative} → {absolute}")
        else:
            missing.append(relative)

    if missing:
        return (
            "Could not verify all requested files on disk.\n"
            f"Missing: {', '.join(missing)}\n"
            f"Workspace root: {root}"
        )
    return (
        "Created files on disk:\n"
        + "\n".join(created)
        + f"\n\nWorkspace root: {root}"
    )


def resolve_read_file_paths(user_content: str, intent: WorkspaceIntent) -> list[str]:
    """
    Resolve workspace-relative file paths for read requests.

    :param user_content: Original user request
    :param intent: Parsed workspace intent
    :return: Deduplicated workspace-relative file paths
    """
    paths: list[str] = []
    seen: set[str] = set()

    if intent.wants_file_creation:
        planned = plan_deliverable_files(user_content, intent)
        for relative in planned:
            if relative not in seen:
                seen.add(relative)
                paths.append(relative)
        if paths:
            return paths

    for relative in intent.target_paths:
        if Path(relative).suffix and relative not in seen:
            seen.add(relative)
            paths.append(relative)

    if paths:
        return paths

    inferred = infer_requested_files(user_content, intent)
    for relative in inferred:
        if relative not in seen:
            seen.add(relative)
            paths.append(relative)
    return paths


def read_workspace_file(relative_path: str) -> tuple[bool, str]:
    """
    Read one workspace file and return its text content.

    :param relative_path: Workspace-relative file path
    :return: Tuple of success flag and content or error message
    """
    try:
        absolute = _resolve_path(relative_path)
        if not absolute.is_file():
            return False, f"File not found: {absolute}"
        if is_document_path(absolute):
            try:
                content = read_document_text(absolute)
            except OptionalDependencyError as exc:
                return False, str(exc)
        else:
            content = absolute.read_text(encoding="utf-8", errors="replace")
        if len(content) > settings.max_output_chars:
            content = content[: settings.max_output_chars] + "\n... [truncated]"
        return True, content
    except (OSError, PermissionError, UnicodeError) as exc:
        return False, str(exc)


async def prefetch_read_file_contents(
    user_content: str,
    intent: WorkspaceIntent,
) -> dict[str, str]:
    """
    Pre-read target files for a read request.

    :param user_content: Original user request
    :param intent: Parsed workspace intent
    :return: Mapping of workspace-relative path to content or error text
    """
    from agentforge.services.command_audit import record_read_file

    contents: dict[str, str] = {}
    for relative_path in resolve_read_file_paths(user_content, intent):
        success, payload = read_workspace_file(relative_path)
        await record_read_file(
            relative_path,
            output=payload,
            success=success,
        )
        if success:
            contents[relative_path] = payload
        else:
            contents[relative_path] = f"[ERROR] {payload}"
    return contents


def build_read_context_block(contents: dict[str, str]) -> str:
    """
    Build verified file-content context for agent prompts.

    :param contents: Mapping of path to file content or error text
    :return: Prompt context block or empty string
    """
    if not contents:
        return ""

    lines = [
        "Verified file content from disk (use this in your response; do not invent text):",
    ]
    for relative_path, payload in contents.items():
        lines.append(f"\nFile `{relative_path}`:")
        if payload.startswith("[ERROR]"):
            lines.append(payload)
        else:
            lines.append("```")
            lines.append(payload)
            lines.append("```")
    return "\n".join(lines)


def build_read_task_summary(
    user_content: str,
    intent: WorkspaceIntent,
    prefetched: dict[str, str] | None = None,
) -> str:
    """
    Build the final user-facing response for read-file requests.

    :param user_content: Original user request
    :param intent: Parsed workspace intent
    :param prefetched: Pre-read path-to-content mapping
    :return: Human-readable file listing or empty string
    """
    contents = prefetched or {}
    if not contents:
        return ""

    blocks: list[str] = []
    for relative_path, payload in contents.items():
        if payload.startswith("[ERROR]"):
            blocks.append(f"**{relative_path}**\n{payload}")
            continue
        blocks.append(
            f"Datei `{relative_path}`:\n\n```\n{payload}\n```"
        )
    return "\n\n".join(blocks)


def _base_directory(intent: WorkspaceIntent) -> str | None:
    """
    Resolve the target directory for planned deliverables.

    :param intent: Parsed workspace intent
    :return: Workspace-relative directory or None
    """
    if intent.target_dirs:
        return intent.target_dirs[0]
    if intent.target_paths:
        candidate = Path(intent.target_paths[0])
        if candidate.suffix:
            return str(candidate.parent) if str(candidate.parent) != "." else None
        return intent.target_paths[0]
    return None


def infer_requested_files(user_content: str, intent: WorkspaceIntent) -> list[str]:
    """
    Infer workspace-relative file paths from the user message.

    :param user_content: User message text
    :param intent: Parsed workspace intent
    :return: Relative file paths inside the workspace
    """
    text = user_content or ""
    named_match = NAMED_FILE.search(text)
    if named_match:
        names = [named_match.group(1)]
    else:
        names = [match.group(1) for match in REQUESTED_FILE.finditer(text)]
    unique_names: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique_names.append(name)

    if not unique_names:
        return []

    file_targets = [
        relative
        for relative in intent.target_paths
        if Path(relative).suffix
    ]
    if file_targets:
        return file_targets

    for relative in intent.target_paths:
        path = Path(relative)
        if path.suffix.lower() in {".php", ".html", ".htm", ".css", ".js", ".ts", ".py"}:
            return [relative]

    base_dir = intent.target_dirs[0] if intent.target_dirs else None
    if not base_dir and intent.target_paths:
        candidate = Path(intent.target_paths[0])
        base_dir = str(candidate.parent) if candidate.suffix else intent.target_paths[0]
        if base_dir == ".":
            base_dir = intent.target_paths[0]

    if base_dir:
        named_folder = extract_named_folder(user_content)
        if named_folder and not (
            base_dir.endswith(f"/{named_folder}") or base_dir == named_folder
        ):
            return [f"{base_dir}/{named_folder}/{name}" for name in unique_names]
        return [f"{base_dir}/{name}" for name in unique_names]
    return unique_names


def plan_deliverable_files(user_content: str, intent: WorkspaceIntent) -> list[str]:
    """
    Plan workspace files that must exist to satisfy the user request.

    :param user_content: User message text
    :param intent: Parsed workspace intent
    :return: Workspace-relative deliverable paths
    """
    if intent.wants_file_read and not intent.wants_file_creation:
        return []

    explicit = infer_requested_files(user_content, intent)
    if explicit:
        return explicit
    if not intent.wants_file_creation:
        return []

    base_dir = _base_directory(intent)
    if not base_dir:
        return []

    text = user_content or ""
    matched_types = match_deliverable_file_types(text)
    if matched_types:
        planned = [f"{base_dir}/{spec.default_filename}" for spec in matched_types]
        unique: list[str] = []
        seen_paths: set[str] = set()
        for path in planned:
            key = path.lower()
            if key not in seen_paths:
                seen_paths.add(key)
                unique.append(path)
        return unique

    if HTML_STRUCTURE.search(text):
        return [f"{base_dir}/index.html"]

    return [f"{base_dir}/output.txt"]


def minimal_html_with_h1(h1_text: str) -> str:
    """
    Return a minimal HTML page containing one H1 element.

    :param h1_text: Visible H1 text
    :return: HTML document string
    """
    safe_title = h1_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
</head>
<body>
  <h1>{safe_title}</h1>
</body>
</html>
"""


def default_html_scaffold(
    css_href: str | None = None,
    js_src: str | None = None,
    main_text: str | None = None,
) -> str:
    """
    Return a minimal HTML scaffold with header, menu, content, and footer.

    :param css_href: Optional external stylesheet path
    :param js_src: Optional external JavaScript path
    :param main_text: Optional main content paragraph text
    :return: HTML document string
    """
    css_link = f'  <link rel="stylesheet" href="{css_href}">\n' if css_href else ""
    inline_style = "" if css_href else """  <style>
    body { font-family: sans-serif; margin: 0; }
    header, nav, main, footer { padding: 1rem; }
    header { background: #1f2937; color: #fff; }
    nav { background: #374151; }
    nav a { color: #fff; margin-right: 1rem; text-decoration: none; }
    main { min-height: 40vh; }
    footer { background: #e5e7eb; }
  </style>
"""
    js_script = f'  <script src="{js_src}"></script>\n' if js_src else ""
    browser_block = (
        '    <p id="browser-type"></p>\n'
        if js_src
        else ""
    )
    main_paragraph = (
        f"    <p>{main_text}</p>\n"
        if main_text
        else "    <p>Main content area.</p>\n"
    )
    return f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AgentForge Page</title>
{css_link}{inline_style}</head>
<body>
  <header>
    <h1>Header</h1>
  </header>
  <nav>
    <a href="#">Home</a>
    <a href="#">About</a>
    <a href="#">Contact</a>
  </nav>
  <main>
    <h2>Content</h2>
{main_paragraph}{browser_block}  </main>
  <footer>
    <p>Footer</p>
  </footer>
{js_script}</body>
</html>
"""


def default_css_scaffold() -> str:
    """
    Return a minimal CSS scaffold for HTML layout.

    :return: CSS stylesheet string
    """
    return """/* Layout stylesheet */
body {
  font-family: sans-serif;
  margin: 0;
}

header,
nav,
main,
footer {
  padding: 1rem;
}

header {
  background: #1f2937;
  color: #fff;
}

nav {
  background: #374151;
}

nav a {
  color: #fff;
  margin-right: 1rem;
  text-decoration: none;
}

main {
  min-height: 40vh;
}

footer {
  background: #e5e7eb;
}
"""


def default_python_3d_animation_main(duration_seconds: float = 30.0) -> str:
    """
    Return a stdlib tkinter 3D wireframe animation script.

    :param duration_seconds: How long the animation runs before exit
    :return: Complete Python source
    """
    return f'''#!/usr/bin/env python3
"""Display a rotating 3D wireframe cube, then exit."""

import math
import time
import tkinter as tk

VERTICES = [
    (-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1),
    (1, -1, -1), (1, -1, 1), (1, 1, -1), (1, 1, 1),
]
EDGES = [
    (0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3), (2, 6),
    (3, 7), (4, 5), (4, 6), (5, 7), (6, 7),
]
DURATION = {duration_seconds}


def rotate_y(point, angle):
    x, y, z = point
    c, s = math.cos(angle), math.sin(angle)
    return (x * c + z * s, y, -x * s + z * c)


def rotate_x(point, angle):
    x, y, z = point
    c, s = math.cos(angle), math.sin(angle)
    return (x, y * c - z * s, y * s + z * c)


def project(point, width, height, scale=120.0, distance=4.0):
    x, y, z = point
    factor = scale / (distance + z)
    return (width / 2 + x * factor, height / 2 - y * factor)


def main():
    root = tk.Tk()
    root.title("3D Animation")
    width, height = 640, 480
    canvas = tk.Canvas(root, width=width, height=height, bg="black")
    canvas.pack()
    start = time.time()
    angle = 0.0

    def frame():
        nonlocal angle
        elapsed = time.time() - start
        if elapsed >= DURATION:
            root.destroy()
            return
        canvas.delete("all")
        angle += 0.04
        points = [
            project(rotate_x(rotate_y(v, angle), angle * 0.7), width, height)
            for v in VERTICES
        ]
        for i, j in EDGES:
            canvas.create_line(*points[i], *points[j], fill="#00d4ff", width=2)
        canvas.create_text(
            10, 20, anchor="nw", fill="white",
            text=f"{{DURATION - elapsed:.1f}}s remaining",
        )
        root.after(33, frame)

    root.after(0, frame)
    root.mainloop()


if __name__ == "__main__":
    main()
'''


def _python_animation_duration_seconds(user_content: str) -> float:
    """
    Extract animation duration from a user request when present.

    :param user_content: Original user request
    :return: Duration in seconds (defaults to 30)
    """
    match = re.search(r"(\d+)\s*(?:sekunden|seconds|s)\b", user_content or "", re.IGNORECASE)
    if match:
        return float(match.group(1))
    return 30.0


def _requests_python_3d_animation(user_content: str) -> bool:
    """
    Return True when the user asks for a Python 3D animation program.

    :param user_content: Original user request
    :return: Whether the tkinter animation fallback applies
    """
    text = user_content or ""
    return bool(
        re.search(r"\b3d\s*animation\b", text, re.IGNORECASE)
        and re.search(r"\bpython\b", text, re.IGNORECASE)
    )


def default_js_scaffold() -> str:
    """
    Return JavaScript that outputs the browser type into the HTML page.

    :return: JavaScript source string
    """
    return """document.addEventListener('DOMContentLoaded', () => {
  const target = document.getElementById('browser-type');
  const browserInfo = navigator.userAgent;
  if (target) {
    target.textContent = browserInfo;
  } else {
    console.log('Browser type:', browserInfo);
  }
});
"""


def _html_scaffold_for_request(user_content: str) -> str:
    """
    Build an HTML scaffold linked to CSS/JS when the request mentions them.

    :param user_content: Original user request
    :return: HTML document string
    """
    h1_text = extract_h1_text_from_request(user_content)
    if h1_text:
        return minimal_html_with_h1(h1_text)
    matched = {spec.type_id for spec in match_deliverable_file_types(user_content or "")}
    css_href = "styles.css" if "css" in matched else None
    js_src = "app.js" if "javascript" in matched else None
    literal = extract_literal_text_content(user_content)
    return default_html_scaffold(
        css_href=css_href,
        js_src=js_src,
        main_text=literal,
    )


def default_php_scaffold() -> str:
    """
    Return a minimal PHP page scaffold.

    :return: PHP document string
    """
    return """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <title>AgentForge PHP Page</title>
</head>
<body>
  <header><h1>Header</h1></header>
  <nav><a href="#">Menu</a></nav>
  <main><p><?php echo 'Content'; ?></p></main>
  <footer><p>Footer</p></footer>
</body>
</html>
"""


def _planned_asset_name(planned_files: list[str], suffix: str) -> str | None:
    """
    Return the basename of a planned file for the given suffix.

    :param planned_files: All planned deliverable paths
    :param suffix: File extension including dot
    :return: Basename or None
    """
    for path in planned_files:
        if Path(path).suffix.lower() == suffix:
            return Path(path).name
    return None


def _looks_like_html(content: str) -> bool:
    """
    Detect whether content resembles an HTML document.

    :param content: File body
    :return: True when HTML markers are present
    """
    return bool(re.search(r"<!DOCTYPE|<html\b", content or "", re.IGNORECASE))


def _looks_like_css(content: str) -> bool:
    """
    Detect whether content is primarily CSS rather than another language.

    :param content: File body
    :return: True when CSS selectors dominate
    """
    text = (content or "").strip()
    if not text or _looks_like_html(text):
        return False
    css_rules = len(re.findall(r"[.#][\w-]+\s*\{", text))
    js_markers = len(re.findall(r"\b(function|const|let|var|document|console)\b", text))
    return css_rules >= 1 and css_rules >= js_markers


def normalize_deliverable_content(
    relative_path: str,
    content: str,
    user_content: str,
    planned_files: list[str] | None = None,
) -> str:
    """
    Normalize generated deliverables: relative asset paths and valid file bodies.

    :param relative_path: Target workspace-relative path
    :param content: Generated file body
    :param user_content: Original user request
    :param planned_files: All deliverables planned for this request
    :return: Sanitized file body
    """
    planned = planned_files or [relative_path]
    suffix = Path(relative_path).suffix.lower()
    text = (content or "").strip()
    css_name = _planned_asset_name(planned, ".css")
    js_name = _planned_asset_name(planned, ".js")

    if suffix in {".html", ".htm"}:
        if not _looks_like_html(text):
            return _html_scaffold_for_request(user_content)
        if css_name:
            text = STYLE_BLOCK.sub("", text)
        text = ABSOLUTE_ASSET.sub(
            lambda match: f'{match.group("attr")}="{Path(match.group("path")).name}"',
            text,
        )
        if css_name:
            text = re.sub(
                r'(<link[^>]+href=["\'])[^"\']*\.css(["\'])',
                rf"\1{css_name}\2",
                text,
                flags=re.IGNORECASE,
            )
        if js_name:
            text = EXTERNAL_SCRIPT.sub("", text)
            text = re.sub(
                r'(<script[^>]+src=["\'])[^"\']*\.js(["\'])',
                rf"\1{js_name}\2",
                text,
                flags=re.IGNORECASE,
            )
        if js_name and 'id="browser-type"' not in text and "browser-type" not in text:
            text = text.replace(
                "</main>",
                '    <p id="browser-type"></p>\n  </main>',
                1,
            )
        return text.strip() + "\n"

    if suffix == ".css":
        if not text or _looks_like_html(text) or not _looks_like_css(text):
            return default_css_scaffold()
        return text.strip() + "\n"

    if suffix == ".js":
        if not text or _looks_like_css(text):
            return default_js_scaffold()
        return text.strip() + "\n"

    return text


def prepare_deliverable_content(
    relative_path: str,
    raw_content: str,
    user_content: str,
    planned_files: list[str] | None = None,
) -> str:
    """
    Clean, validate, and normalize one deliverable before writing it to disk.

    :param relative_path: Target workspace-relative path
    :param raw_content: Raw generated content
    :param user_content: Original user request
    :param planned_files: All deliverables planned for this request
    :return: Final file body
    """
    body = strip_code_fences(raw_content or "")
    if not body.strip():
        body = fallback_file_content(relative_path, user_content)
    elif (
        relative_path.endswith(".py")
        and is_placeholder_content(body, relative_path)
        and _requests_python_3d_animation(user_content)
    ):
        body = fallback_file_content(relative_path, user_content)
    return normalize_deliverable_content(
        relative_path,
        body,
        user_content,
        planned_files,
    )


def build_materialization_prompt(
    user_content: str,
    relative_path: str,
    planned_files: list[str],
) -> str:
    """
    Build a strict LLM prompt for generating one deliverable file.

    :param user_content: Original user request
    :param relative_path: Target workspace-relative path
    :param planned_files: All deliverables for this request
    :return: Prompt text
    """
    siblings = [Path(item).name for item in planned_files if item != relative_path]
    sibling_line = ", ".join(siblings) if siblings else "none"
    stdlib_rules = python_stdlib_delivery_rules(user_content)
    return (
        f"Original request: {user_content}\n\n"
        f"Generate the complete contents for this file only: {relative_path}\n"
        f"Other project files: {sibling_line}\n\n"
        "Rules:\n"
        "- Output file content only. No markdown fences. No explanation.\n"
        "- Write complete, runnable code — never placeholders, TODO stubs, or "
        "comments that say 'implement here'.\n"
        "- For Python: include every import the code uses (stdlib and third-party).\n"
        f"{stdlib_rules}"
        "- For Python third-party libraries, also ensure requirements.txt exists "
        "in the same directory listing each pip package.\n"
        "- Verify Python mentally with py_compile rules: valid syntax, no undefined "
        "names from missing imports, correct library APIs (no hallucinated methods).\n"
        "- Use relative asset paths only (e.g. styles.css, app.js).\n"
        "- Never use absolute filesystem paths in href or src.\n"
        "- Put CSS rules only in .css files.\n"
        "- Put JavaScript only in .js files.\n"
        "- Do not put CSS inside the HTML file when a .css file exists.\n"
        "- Do not load external CDN libraries unless explicitly requested.\n"
        f"- Workspace root for context only: {settings.workspace_root}\n"
    )


def fallback_file_content(relative_path: str, user_content: str) -> str:
    """
    Build deterministic fallback content when the LLM fails to generate a file.

    :param relative_path: Workspace-relative file path
    :param user_content: Original user request
    :return: File body
    """
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".html", ".htm"}:
        return _html_scaffold_for_request(user_content)
    if suffix == ".php":
        return default_php_scaffold()
    if suffix == ".css":
        return default_css_scaffold()
    if suffix == ".js":
        return default_js_scaffold()
    if suffix == ".ts":
        return "// Generated TypeScript\nexport {};\n"
    if suffix == ".json":
        return "{\n  \"generated\": true\n}\n"
    if suffix == ".txt":
        literal = extract_literal_text_content(user_content)
        if literal is not None:
            return literal if literal.endswith("\n") else f"{literal}\n"
    if suffix == ".docx":
        literal = extract_literal_text_content(user_content)
        if literal is not None:
            return literal if literal.endswith("\n") else f"{literal}\n"
        return "Generated document.\n"
    if suffix == ".pdf":
        literal = extract_literal_text_content(user_content)
        if literal is not None:
            return literal if literal.endswith("\n") else f"{literal}\n"
        return "Generated PDF document.\n"
    if suffix == ".py" and _requests_python_3d_animation(user_content):
        return default_python_3d_animation_main(
            _python_animation_duration_seconds(user_content),
        )
    return f"Generated for request:\n{user_content.strip()}\n"


def file_exists_in_workspace(relative_path: str) -> bool:
    """
    Check whether a workspace-relative file already exists.

    :param relative_path: Path relative to workspace root
    :return: True when the file exists
    """
    try:
        normalized = normalize_workspace_relative_path(relative_path)
        target = (settings.workspace_root.resolve() / normalized).resolve()
        return target.is_file()
    except (OSError, PermissionError):
        return False


def missing_requested_files(user_content: str, intent: WorkspaceIntent) -> list[str]:
    """
    Return requested files that are not yet present in the workspace.

    :param user_content: User message text
    :param intent: Parsed workspace intent
    :return: Missing workspace-relative file paths
    """
    if not intent.wants_file_creation:
        return []
    requested = plan_deliverable_files(user_content, intent)
    return [path for path in requested if not file_exists_in_workspace(path)]


def strip_code_fences(content: str) -> str:
    """
    Remove markdown code fences from generated file content.

    :param content: Raw model output
    :return: Clean file body
    """
    text = (content or "").strip()
    match = CODE_FENCE.match(text)
    if match:
        return match.group(1).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def build_implementation_prompt(user_content: str, file_paths: list[str]) -> str:
    """
    Build a strict implementation prompt for file creation.

    :param user_content: Original user request
    :param file_paths: Workspace-relative target files
    :return: Prompt text for the developer agent
    """
    files_block = "\n".join(f"- {path}" for path in file_paths)
    basenames = ", ".join(Path(path).name for path in file_paths)
    stdlib_rules = python_stdlib_delivery_rules(user_content)
    return (
        f"User request: {user_content}\n\n"
        "Implementation task:\n"
        "Create the requested files on disk using write_file.\n"
        "Write complete, runnable implementations — no placeholders, TODO stubs, "
        "or 'implement here' comments.\n"
        "For Python deliverables:\n"
        "- Include every import used in the file (stdlib and third-party).\n"
        f"{stdlib_rules}"
        "- When using third-party packages, also write requirements.txt in the "
        "same folder with pip package names.\n"
        "- Use correct library APIs; do not invent methods or modules.\n"
        "- Do not create virtual environments; write requirements.txt instead.\n"
        "Do not answer with JSON, search results, or pasted code only.\n"
        "You must call write_file for every path below:\n"
        f"{files_block}\n\n"
        "Web project rules when HTML/CSS/JS are requested:\n"
        f"- Files to create: {basenames}\n"
        "- Link HTML to CSS/JS with relative filenames in the same folder.\n"
        "- Never use absolute filesystem paths in href or src.\n"
        "- Keep CSS out of the HTML file when a separate .css file exists.\n"
        "- Keep JavaScript out of the HTML file except via <script src=\"...\">.\n"
        "After writing, briefly confirm which files were created."
    )


async def apply_file_text_replacement(
    relative_path: str,
    replace_from: str,
    replace_to: str,
) -> tuple[bool, str]:
    """
    Replace text in a workspace file and write the result back to disk.

    :param relative_path: Workspace-relative file path
    :param replace_from: Text to replace
    :param replace_to: Replacement text
    :return: Tuple of success flag and summary or error message
    """
    success, content = read_workspace_file(relative_path)
    if not success:
        return False, content
    if replace_to in content and replace_from not in content:
        return True, f"Edit already applied in {relative_path}"
    if replace_from not in content:
        return False, f'Text not found in {relative_path}: "{replace_from}"'
    updated = content.replace(replace_from, replace_to)
    write_ok, output = await write_file_direct(relative_path, updated)
    if not write_ok:
        return False, output
    return True, f'Updated {relative_path}: "{replace_from}" -> "{replace_to}"'


async def write_file_direct(relative_path: str, content: str) -> tuple[bool, str]:
    """
    Write one file directly through the workspace tool.

    :param relative_path: Workspace-relative file path
    :param content: File contents
    :return: Tuple of success flag and tool output
    """
    relative_path, content = maybe_swap_write_file_arguments(relative_path, content)
    valid, reason = validate_workspace_relative_path(relative_path)
    if not valid:
        return False, f"Invalid file path: {reason}"
    if should_skip_redundant_write(relative_path, content):
        return True, f"Skipped redundant write: {relative_path}"
    tool = WriteFileTool()
    result = await tool.execute({"path": relative_path, "content": content})
    return result.success, result.output
