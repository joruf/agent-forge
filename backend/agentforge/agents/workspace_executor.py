"""Execute workspace file creation when user intent is explicit."""

from __future__ import annotations

import re
from pathlib import Path

from agentforge.agents.deliverable_types import (
    HTML_STRUCTURE,
    match_deliverable_file_type,
    match_deliverable_file_types,
)
from agentforge.agents.workspace_intent import WorkspaceIntent, extract_named_folder
from agentforge.config import settings
from agentforge.tools.registry import WriteFileTool, _resolve_path

REQUESTED_FILE = re.compile(
    r"\b([\w.-]+\.(?:php|html|htm|css|js|ts|tsx|jsx|py|md|json|txt|vue|sql|xml|yaml|yml|sh))\b",
    re.IGNORECASE,
)
NAMED_FILE = re.compile(
    r"(?:datei|file)\s+mit\s+(?:dem\s+)?namen[\s.:,]+([\w.-]+\.\w+)",
    re.IGNORECASE,
)
CODE_FENCE = re.compile(r"^```[\w.-]*\n(.*?)```$", re.DOTALL | re.IGNORECASE)
LITERAL_TEXT = re.compile(
    r'(?:text|inhalt|content|schreib(?:e|en|st)?|write(?:s|ing)?)\s+["\«„]([^"\»""]+)["\»""]',
    re.IGNORECASE,
)
STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
ABSOLUTE_ASSET = re.compile(
    r"""(?P<attr>href|src)=["'](?P<path>/[^"']+\.(?:css|js))["']""",
    re.IGNORECASE,
)
EXTERNAL_SCRIPT = re.compile(
    r"""<script\b[^>]*\bsrc=["']https?://[^"']+["'][^>]*>\s*</script>""",
    re.IGNORECASE,
)


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


def default_html_scaffold(
    css_href: str | None = None,
    js_src: str | None = None,
) -> str:
    """
    Return a minimal HTML scaffold with header, menu, content, and footer.

    :param css_href: Optional external stylesheet path
    :param js_src: Optional external JavaScript path
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
    <p>Main content area.</p>
{browser_block}  </main>
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
    matched = {spec.type_id for spec in match_deliverable_file_types(user_content or "")}
    css_href = "styles.css" if "css" in matched else None
    js_src = "app.js" if "javascript" in matched else None
    return default_html_scaffold(css_href=css_href, js_src=js_src)


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
    return (
        f"Original request: {user_content}\n\n"
        f"Generate the complete contents for this file only: {relative_path}\n"
        f"Other project files: {sibling_line}\n\n"
        "Rules:\n"
        "- Output file content only. No markdown fences. No explanation.\n"
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
    return f"Generated for request:\n{user_content.strip()}\n"


def file_exists_in_workspace(relative_path: str) -> bool:
    """
    Check whether a workspace-relative file already exists.

    :param relative_path: Path relative to workspace root
    :return: True when the file exists
    """
    try:
        return _resolve_path(relative_path).is_file()
    except PermissionError:
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
    return (
        f"User request: {user_content}\n\n"
        "Implementation task:\n"
        "Create the requested files on disk using write_file.\n"
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
    tool = WriteFileTool()
    result = await tool.execute({"path": relative_path, "content": content})
    return result.success, result.output
