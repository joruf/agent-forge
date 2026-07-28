"""Small, safe repairs for near-miss JSON produced by weak local LLMs."""

from __future__ import annotations

import re

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_CLOSERS = {"{": "}", "[": "]"}


def repair_json(text: str) -> str:
    """
    Apply minimal, safe fixes for common malformed-JSON patterns.

    Handles the two mistakes small local models make most often: a trailing
    comma before a closing bracket, and truncated output that never closes
    its braces/brackets (e.g. cut off by a max_tokens limit). Does not
    attempt riskier fixes like quote-style conversion, which can corrupt
    otherwise-valid string content.

    :param text: Candidate JSON text that failed to parse as-is
    :return: Text with trailing commas removed and open brackets closed
    """
    repaired = _TRAILING_COMMA.sub(r"\1", text)

    stack: list[str] = []
    in_string = False
    escape = False
    for char in repaired:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in _CLOSERS:
            stack.append(_CLOSERS[char])
        elif stack and char == stack[-1]:
            stack.pop()

    if stack:
        repaired += "".join(reversed(stack))
    return repaired
