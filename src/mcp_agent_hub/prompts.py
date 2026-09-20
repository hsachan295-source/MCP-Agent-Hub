"""MCP prompts: reusable, parameterised *message templates* the server offers to clients.

A **prompt** is the third MCP building block (next to tools and resources). It is not
run by the server: the server only fills in a template and hands back a list of
messages. The client (a chat app, an IDE, an agent) then decides to send those messages
to whichever AI model it uses. That is why this project needs no AI model or API key.

A client discovers prompts with ``prompts/list`` and fetches one with ``prompts/get``,
passing the arguments as strings.

If a prompt function returns a plain ``str``, the SDK wraps it in a single
message with the role ``user``.
"""

from collections.abc import Callable
from typing import Any

from mcp import MCPError
from mcp.types import INVALID_PARAMS

# Guidance that changes with the `level` argument of the explain_text prompt.
_LEVEL_GUIDANCE = {
    "beginner": "Use everyday words, avoid jargon (or define it immediately), and add one simple analogy.",
    "intermediate": "Assume basic familiarity with the subject, explain the reasoning, and mention common pitfalls.",
    "advanced": "Be precise and technical, cover edge cases and trade-offs, and skip introductory explanations.",
}


def _require_text(argument: str, value: str) -> str:
    """Return the value stripped of surrounding whitespace, or raise a clear protocol error if it is blank.

    ``MCPError`` with the code ``INVALID_PARAMS`` (-32602) is the standard way to tell a
    client "one of your arguments is wrong". The SDK passes its message through unchanged.
    """
    cleaned = value.strip()
    if not cleaned:
        raise MCPError(INVALID_PARAMS, f"The '{argument}' argument must not be empty.")
    return cleaned


def explain_text(text: str, level: str) -> str:
    """Ask an AI assistant to explain a piece of text at a chosen level: beginner, intermediate or advanced."""
    chosen_level = _require_text("level", level).lower()
    if chosen_level not in _LEVEL_GUIDANCE:
        allowed = ", ".join(_LEVEL_GUIDANCE)
        raise MCPError(INVALID_PARAMS, f"Unsupported level {level!r}. Choose one of: {allowed}.")
    source = _require_text("text", text)
    return (
        f"Explain the following text at a {chosen_level} level.\n\n"
        f"Level guidance: {_LEVEL_GUIDANCE[chosen_level]}\n\n"
        f"Text:\n\"\"\"\n{source}\n\"\"\"\n\n"
        "Structure your answer like this:\n"
        "1. One-sentence summary\n"
        "2. Explanation\n"
        "3. A concrete example\n"
        "4. Key terms and their meanings"
    )


def summarize_text(text: str) -> str:
    """Ask an AI assistant to summarize a piece of text in a fixed, structured format."""
    source = _require_text("text", text)
    return (
        "Summarize the following text.\n\n"
        f"Text:\n\"\"\"\n{source}\n\"\"\"\n\n"
        "Use exactly this structure:\n"
        "- One-line summary\n"
        "- Key points (3 to 5 bullet points)\n"
        "- Important names, numbers or dates mentioned\n"
        "- Anything that is unclear or missing in the text"
    )


def code_review(code: str, language: str) -> str:
    """Ask an AI assistant to review source code for bugs, readability, security and performance problems."""
    source = _require_text("code", code)
    chosen_language = _require_text("language", language)
    return (
        f"Please review the following {chosen_language} code.\n\n"
        f"```{chosen_language.lower()}\n{source}\n```\n\n"
        "Report your findings under these headings:\n"
        "1. Bugs - anything that is wrong or will fail\n"
        "2. Readability problems - naming, structure, missing comments\n"
        "3. Security issues - unsafe input handling, secrets, injection risks\n"
        "4. Performance issues - wasted work, slow patterns\n"
        "5. Suggested improvements - concrete changes, with short code examples where helpful"
    )


def beginner_teacher(topic: str) -> str:
    """Ask an AI assistant to teach a topic simply, with examples, as if to a complete beginner."""
    subject = _require_text("topic", topic)
    return (
        f"Act as a friendly teacher and teach me about: {subject}\n\n"
        "I am a complete beginner, so please:\n"
        "- Start with a one-paragraph explanation in plain language\n"
        "- Compare it to something from everyday life\n"
        "- Give two small, concrete examples\n"
        "- List the three most important ideas to remember\n"
        "- End with two practice questions I can try to answer"
    )


# server.py registers everything listed here, and the app://help resource reads it too.
ALL_PROMPTS: tuple[Callable[..., Any], ...] = (
    explain_text,
    summarize_text,
    code_review,
    beginner_teacher,
)
