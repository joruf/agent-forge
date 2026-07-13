"""Tests for deliverable content normalization."""

from agentforge.agents.workspace_executor import (
    normalize_deliverable_content,
    prepare_deliverable_content,
)

PLANNED = [
    "GitHub/Test2/index.html",
    "GitHub/Test2/styles.css",
    "GitHub/Test2/app.js",
]

BAD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>GitHub Test 2</title>
    <link rel="stylesheet" type="text/css" href="/home/joruf/Dokumente/GitHub/Test2/style.css">
    <script src="https://cdn.jsdelivr.net/npm/jquery@3.5.1/dist/jquery.min.js"></script>
    <style>
    #home { background-color: #333; }
    </style>
</head>
<body>
  <main id="app-content">
    <section id="home"><h1>Home</h1></section>
  </main>
  <script src="/home/joruf/Dokumente/GitHub/Test2/script.js"></script>
</body>
</html>
"""

CSS_ONLY = """#home {
    background-color: #333;
    color: #fff;
}
"""


def test_normalize_html_uses_relative_assets_and_removes_inline_css() -> None:
    """HTML assets are rewritten to planned relative filenames without inline CSS."""
    result = normalize_deliverable_content(
        "GitHub/Test2/index.html",
        BAD_HTML,
        "Create html css js project",
        PLANNED,
    )
    assert 'href="styles.css"' in result
    assert 'src="app.js"' in result
    assert "/home/joruf/Dokumente" not in result
    assert "<style" not in result.lower()
    assert "jquery" not in result.lower()


def test_css_only_content_does_not_become_html_file() -> None:
    """CSS-only bodies are rejected for HTML targets."""
    result = normalize_deliverable_content(
        "GitHub/Test2/index.html",
        CSS_ONLY,
        "Create html css js project",
        PLANNED,
    )
    assert "<!DOCTYPE html>" in result
    assert "#home {" not in result or 'href="styles.css"' in result


def test_css_file_rejects_html_and_accepts_valid_css() -> None:
    """CSS deliverables must contain CSS, not HTML."""
    valid = normalize_deliverable_content(
        "GitHub/Test2/styles.css",
        CSS_ONLY,
        "Create stylesheet",
        PLANNED,
    )
    assert "#home {" in valid

    fallback = normalize_deliverable_content(
        "GitHub/Test2/styles.css",
        BAD_HTML,
        "Create stylesheet",
        PLANNED,
    )
    assert "header," in fallback


def test_js_file_rejects_css_content() -> None:
    """JavaScript deliverables must not contain CSS selector blocks."""
    result = normalize_deliverable_content(
        "GitHub/Test2/app.js",
        CSS_ONLY,
        "Create js file",
        PLANNED,
    )
    assert "navigator.userAgent" in result


def test_prepare_deliverable_content_normalizes_llm_html() -> None:
    """Prepared HTML from raw LLM output is sanitized before writing."""
    result = prepare_deliverable_content(
        "GitHub/Test2/index.html",
        BAD_HTML,
        "Create html css js project",
        PLANNED,
    )
    assert 'href="styles.css"' in result
    assert 'src="app.js"' in result
