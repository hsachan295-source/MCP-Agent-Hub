"""MCP tools: the *actions* a client (or an AI model) can ask this server to run.

In MCP a **tool** is a function that the server publishes together with a JSON schema
describing its arguments. A client discovers tools with ``tools/list`` and runs one
with ``tools/call``.

How this file relates to the MCP SDK
------------------------------------
* Every tool is a plain Python function, so it is easy to read and to unit-test.
  ``server.py`` registers them later; nothing in here needs a running server.
* The **type hints** matter: the SDK reads them to generate the tool's input schema
  and validates incoming arguments against it, so ``{"a": "banana"}`` is rejected
  before your code even runs.
* The **docstring** becomes the tool's description. AI models read it to decide
  when the tool is useful, so it is written for the caller.
* Failures you *expect* (dividing by zero, a bad file name) raise ``ToolError``. The SDK
  turns that into a result with ``isError = true`` and passes your message on to the
  caller. Any *other* exception is treated as a bug and its details are hidden.
* A **return annotation** that is a dataclass makes the SDK publish an output schema and
  send *structured content* (machine-readable JSON) next to the human-readable text.
"""

import math
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from mcp_agent_hub.utils import (
    WorkspaceError,
    list_workspace_paths,
    read_workspace_text,
    write_workspace_text,
)

# --------------------------------------------------------------------------------------
# Structured result types
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WordCount:
    """Result of ``word_count``."""

    characters: int
    words: int
    lines: int


@dataclass(frozen=True)
class TextStatistics:
    """Result of ``get_text_statistics``."""

    character_count: int
    word_count: int
    line_count: int
    sentence_count: int


@dataclass(frozen=True)
class WorkspaceFile:
    """One entry in the result of ``list_workspace_files``."""

    name: str
    size_bytes: int


# --------------------------------------------------------------------------------------
# Calculator tools
# --------------------------------------------------------------------------------------


def _finite(value: float) -> float:
    """Return ``value`` if it is a normal number, otherwise raise a friendly ``ToolError``.

    ``inf`` and ``nan`` (for example from 1e308 * 10) cannot be sent as valid JSON,
    so they are reported as a clear error instead of breaking the response.
    """
    if not math.isfinite(value):
        raise ToolError("The result is not a finite number (the calculation overflowed or an input was invalid).")
    return value


def add_numbers(a: float, b: float) -> float:
    """Add two numbers and return a + b. Example: a=10, b=20 returns 30."""
    return _finite(a + b)


def subtract_numbers(a: float, b: float) -> float:
    """Subtract b from a and return a - b. Example: a=10, b=4 returns 6."""
    return _finite(a - b)


def multiply_numbers(a: float, b: float) -> float:
    """Multiply two numbers and return a * b. Example: a=7, b=8 returns 56."""
    return _finite(a * b)


def divide_numbers(a: float, b: float) -> float:
    """Divide a by b and return a / b. Dividing by zero returns an error instead of crashing."""
    if b == 0:
        raise ToolError("Cannot divide by zero. Please provide a non-zero value for 'b'.")
    return _finite(a / b)


def calculate_percentage(value: float, percentage: float) -> float:
    """Calculate what `percentage` percent of `value` is. Example: value=200, percentage=10 returns 20."""
    # Multiply first, then divide: 1000 * 7 / 100 is exactly 70, while 1000 * (7 / 100) is 70.00000000000001.
    return _finite(value * percentage / 100)


# --------------------------------------------------------------------------------------
# Text tools
# --------------------------------------------------------------------------------------

# A sentence ends at . ! or ? followed by whitespace. This is a simple heuristic: an
# abbreviation such as "e.g. this" is counted as a sentence break.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _count_sentences(text: str) -> int:
    """Count sentences, ignoring fragments that contain no letters or digits (like '...')."""
    fragments = _SENTENCE_BREAK.split(text.strip())
    return sum(1 for fragment in fragments if any(character.isalnum() for character in fragment))


def word_count(text: str) -> WordCount:
    """Count the characters, words and lines in a piece of text."""
    return WordCount(
        characters=len(text),
        words=len(text.split()),  # split() with no argument splits on any run of whitespace
        lines=len(text.splitlines()),
    )


def uppercase_text(text: str) -> str:
    """Convert text to UPPERCASE."""
    return text.upper()


def lowercase_text(text: str) -> str:
    """Convert text to lowercase."""
    return text.lower()


def reverse_text(text: str) -> str:
    """Reverse text character by character. Example: 'MCP' becomes 'PCM'."""
    return text[::-1]


def get_text_statistics(text: str) -> TextStatistics:
    """Return character, word, line and sentence counts for a piece of text."""
    counts = word_count(text)  # reuse word_count instead of repeating its logic
    return TextStatistics(
        character_count=counts.characters,
        word_count=counts.words,
        line_count=counts.lines,
        sentence_count=_count_sentences(text),
    )


# --------------------------------------------------------------------------------------
# Workspace (filesystem) tools
# --------------------------------------------------------------------------------------
# These are the only tools that touch the disk. All the safety logic (blocking "../",
# absolute paths, symlink escapes ...) lives in utils.py, so it exists exactly once.


@contextmanager
def _workspace_errors_as_tool_errors() -> Iterator[None]:
    """Translate workspace problems into ``ToolError`` so the caller sees a clear message."""
    try:
        yield
    except WorkspaceError as error:
        # Our own errors were written to be shown to the user, so pass the message on.
        raise ToolError(str(error)) from error
    except OSError as error:
        raise ToolError(f"Workspace file error: {error.strerror or error}") from error


def list_workspace_files() -> list[WorkspaceFile]:
    """List the text files available inside the project's workspace/ folder, with their sizes in bytes."""
    with _workspace_errors_as_tool_errors():
        return [WorkspaceFile(name=path.name, size_bytes=path.stat().st_size) for path in list_workspace_paths()]


def read_workspace_file(filename: str) -> str:
    """Read a text file from the workspace/ folder. Only plain names such as 'example.txt' are accepted;
    paths that try to leave the workspace ('../x.txt', 'C:\\x.txt', '/etc/passwd') are rejected."""
    with _workspace_errors_as_tool_errors():
        return read_workspace_text(filename)


def write_workspace_note(filename: str, content: str) -> str:
    """Create or overwrite a text note inside the workspace/ folder. Only plain names such as
    'ideas.txt' are accepted; paths that try to leave the workspace are rejected."""
    with _workspace_errors_as_tool_errors():
        written = write_workspace_text(filename, content)
    return f"Saved {written} bytes to workspace/{filename}."


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------

# server.py loops over this tuple to register the tools, and the app://help resource
# reads it too, so adding a tool means adding one function and one line here.
ALL_TOOLS: tuple[Callable[..., Any], ...] = (
    add_numbers,
    subtract_numbers,
    multiply_numbers,
    divide_numbers,
    calculate_percentage,
    word_count,
    uppercase_text,
    lowercase_text,
    reverse_text,
    get_text_statistics,
    list_workspace_files,
    read_workspace_file,
    write_workspace_note,
)
