"""Unit tests for the tool functions in tools.py (no MCP server involved).

Failures that a tool *expects* are raised as ``ToolError``; that is what most tests below check for.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_agent_hub import tools
from mcp_agent_hub.utils import (
    MAX_FILE_BYTES,
    WORKSPACE_ENV_VAR,
    UnsafePathError,
    find_project_root,
    get_workspace_dir,
    resolve_safe_path,
)

# --------------------------------------------------------------------------------------
# Calculator tools
# --------------------------------------------------------------------------------------


def test_add_numbers() -> None:
    assert tools.add_numbers(10, 20) == 30
    assert tools.add_numbers(-5, 2.5) == -2.5


def test_subtract_numbers() -> None:
    assert tools.subtract_numbers(10, 4) == 6
    assert tools.subtract_numbers(1, 3) == -2


def test_multiply_numbers() -> None:
    assert tools.multiply_numbers(7, 8) == 56
    assert tools.multiply_numbers(-2, 3.5) == -7


def test_divide_numbers() -> None:
    assert tools.divide_numbers(10, 4) == 2.5
    assert tools.divide_numbers(-9, 3) == -3


@pytest.mark.parametrize("zero", [0, 0.0, -0.0])
def test_divide_by_zero_raises_a_friendly_error(zero: float) -> None:
    with pytest.raises(ToolError, match="Cannot divide by zero"):
        tools.divide_numbers(1, zero)


def test_calculate_percentage() -> None:
    assert tools.calculate_percentage(200, 10) == 20
    assert tools.calculate_percentage(50, 200) == 100
    assert tools.calculate_percentage(0, 10) == 0
    # Multiplying before dividing avoids the 70.00000000000001 rounding artefact.
    assert tools.calculate_percentage(1000, 7) == 70


def test_overflow_is_reported_instead_of_returning_infinity() -> None:
    with pytest.raises(ToolError, match="not a finite number"):
        tools.multiply_numbers(1e308, 10)


# --------------------------------------------------------------------------------------
# Text tools
# --------------------------------------------------------------------------------------


def test_word_count() -> None:
    text = "Hello MCP world\nsecond line"
    assert tools.word_count(text) == tools.WordCount(characters=len(text), words=5, lines=2)


def test_word_count_of_empty_text() -> None:
    assert tools.word_count("") == tools.WordCount(characters=0, words=0, lines=0)


def test_uppercase_text() -> None:
    assert tools.uppercase_text("hello mcp") == "HELLO MCP"


def test_lowercase_text() -> None:
    assert tools.lowercase_text("Hello MCP") == "hello mcp"


def test_reverse_text() -> None:
    assert tools.reverse_text("MCP") == "PCM"
    assert tools.reverse_text("") == ""


def test_get_text_statistics() -> None:
    text = "Hello world. How are you? I am fine!\nA new line."
    assert tools.get_text_statistics(text) == tools.TextStatistics(
        character_count=len(text), word_count=11, line_count=2, sentence_count=4
    )


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("", 0),
        ("   ", 0),
        ("...", 0),  # punctuation alone is not a sentence
        ("no punctuation at all", 1),
        ("Wait... what?", 2),
        ("Pi is 3.14 today.", 1),  # the dot inside 3.14 is not followed by a space
    ],
)
def test_sentence_counting(text: str, sentences: int) -> None:
    assert tools.get_text_statistics(text).sentence_count == sentences


# --------------------------------------------------------------------------------------
# Workspace tools: normal use
# --------------------------------------------------------------------------------------


def test_list_workspace_files(workspace: Path) -> None:
    listed = tools.list_workspace_files()
    assert [file.name for file in listed] == ["example.txt", "notes.txt"]
    assert listed[0].size_bytes == (workspace / "example.txt").stat().st_size


def test_list_workspace_files_hides_unusable_entries(workspace: Path) -> None:
    (workspace / "program.exe").write_bytes(b"MZ")  # extension is not allowed
    (workspace / "folder.txt").mkdir()  # a directory, not a file
    (workspace / "has space.txt").write_text("x", encoding="utf-8")  # name could not be read back
    assert [file.name for file in tools.list_workspace_files()] == ["example.txt", "notes.txt"]


def test_read_workspace_file(workspace: Path) -> None:
    assert tools.read_workspace_file("example.txt") == "Welcome to the test workspace.\n"


def test_read_missing_file(workspace: Path) -> None:
    with pytest.raises(ToolError, match="File not found"):
        tools.read_workspace_file("missing.txt")


def test_read_binary_file_is_reported(workspace: Path) -> None:
    (workspace / "binary.txt").write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(ToolError, match="not a valid UTF-8"):
        tools.read_workspace_file("binary.txt")


def test_read_file_larger_than_the_limit(workspace: Path) -> None:
    (workspace / "big.txt").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(ToolError, match="too large"):
        tools.read_workspace_file("big.txt")


def test_the_real_project_workspace_is_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The example files shipped with the project are found without any override."""
    monkeypatch.delenv(WORKSPACE_ENV_VAR, raising=False)
    assert get_workspace_dir() == find_project_root() / "workspace"
    assert "MCP Agent Hub" in tools.read_workspace_file("example.txt")
    assert "notes" in tools.read_workspace_file("notes.txt").lower()


def test_write_workspace_note(workspace: Path) -> None:
    message = tools.write_workspace_note("ideas.txt", "line one\nline two\n")
    assert "ideas.txt" in message
    # Bytes on disk must match exactly (no Windows "\r\n" translation).
    assert (workspace / "ideas.txt").read_bytes() == b"line one\nline two\n"
    assert tools.read_workspace_file("ideas.txt") == "line one\nline two\n"


def test_write_supports_unicode_and_overwrites(workspace: Path) -> None:
    tools.write_workspace_note("greeting.md", "first")
    tools.write_workspace_note("greeting.md", "héllo wörld ✓")
    assert tools.read_workspace_file("greeting.md") == "héllo wörld ✓"


def test_write_rejects_unsupported_file_type(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Unsupported file type"):
        tools.write_workspace_note("run.bat", "echo hi")
    assert not (workspace / "run.bat").exists()


def test_write_rejects_content_over_the_limit(workspace: Path) -> None:
    with pytest.raises(ToolError, match="too large"):
        tools.write_workspace_note("huge.txt", "x" * (MAX_FILE_BYTES + 1))
    assert not (workspace / "huge.txt").exists()


def test_write_to_a_directory_name_is_reported(workspace: Path) -> None:
    (workspace / "folder.txt").mkdir()
    with pytest.raises(ToolError):
        tools.write_workspace_note("folder.txt", "content")


# --------------------------------------------------------------------------------------
# Workspace security: path traversal and other unsafe names
# --------------------------------------------------------------------------------------

UNSAFE_FILENAMES = [
    "../secret.txt",  # the classic traversal
    "../../secret.txt",  # two levels up
    "..\\secret.txt",  # the same, with Windows separators
    "..\\..\\secret.txt",
    "notes/../../secret.txt",  # traversal hidden after a legitimate part
    "..",
    "/etc/passwd",  # POSIX absolute path
    "C:\\Windows\\win.ini",  # Windows absolute path
    "C:/Windows/win.ini",
    "C:secret.txt",  # drive-relative path
    "\\\\server\\share\\secret.txt",  # UNC network path
    "sub/notes.txt",  # sub-folders are not supported
    "sub\\notes.txt",
    "./notes.txt",
    "notes.txt:hidden",  # NTFS alternate data stream
    "CON.txt",  # Windows device names
    "nul.txt",
    "COM1.log",
    ".hidden.txt",  # must start with a letter, digit or underscore
    "notes",  # no extension
    "malware.exe",  # extension not allowed
    "script.bat",
    "",  # empty
    "   ",
    "notes.txt\x00.exe",  # embedded null byte
    "a" * 200 + ".txt",  # far too long
]


@pytest.mark.parametrize("filename", UNSAFE_FILENAMES)
def test_unsafe_names_cannot_be_read(workspace: Path, secret_text: str, filename: str) -> None:
    with pytest.raises(ToolError) as error:
        tools.read_workspace_file(filename)
    assert secret_text not in str(error.value)


@pytest.mark.parametrize("filename", UNSAFE_FILENAMES)
def test_unsafe_names_cannot_be_written(workspace: Path, filename: str) -> None:
    with pytest.raises(ToolError):
        tools.write_workspace_note(filename, "malicious content")
    # Nothing new may appear anywhere near the workspace, inside or outside it.
    assert sorted(path.name for path in workspace.iterdir()) == ["example.txt", "notes.txt"]
    assert sorted(path.name for path in workspace.parent.iterdir()) == ["secret.txt", "workspace"]


def test_error_messages_name_the_specific_problem(workspace: Path) -> None:
    with pytest.raises(ToolError, match="Path traversal"):
        tools.read_workspace_file("../secret.txt")
    with pytest.raises(ToolError, match="Path traversal"):
        tools.read_workspace_file("../../secret.txt")
    with pytest.raises(ToolError, match="Absolute paths"):
        tools.read_workspace_file("C:\\anything")
    with pytest.raises(ToolError, match="Absolute paths"):
        tools.read_workspace_file("/etc/passwd")


def test_absolute_path_to_a_real_file_is_blocked(workspace: Path, secret_text: str) -> None:
    real_absolute_path = str(workspace.parent / "secret.txt")
    assert os.path.isabs(real_absolute_path)
    with pytest.raises(ToolError, match="Absolute paths") as error:
        tools.read_workspace_file(real_absolute_path)
    assert secret_text not in str(error.value)


def test_symlink_pointing_outside_the_workspace_is_blocked(workspace: Path, secret_text: str) -> None:
    link = workspace / "link.txt"
    try:
        link.symlink_to(workspace.parent / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("Creating symlinks is not permitted on this system (on Windows it needs Developer Mode).")
    # The name "link.txt" is perfectly innocent; only resolving the link reveals the escape.
    with pytest.raises(ToolError, match="outside the workspace") as error:
        tools.read_workspace_file("link.txt")
    assert secret_text not in str(error.value)
    with pytest.raises(ToolError, match="outside the workspace"):
        tools.write_workspace_note("link.txt", "overwrite attempt")
    assert (workspace.parent / "secret.txt").read_text(encoding="utf-8") == secret_text
    assert "link.txt" not in [file.name for file in tools.list_workspace_files()]


def test_directory_link_pointing_outside_the_workspace_is_blocked(workspace: Path, tmp_path: Path) -> None:
    """Same escape route as the symlink test, but runnable on Windows without special rights.

    A directory *junction* (``mklink /J``) needs no administrator rights. Like a symlink,
    it makes an innocent-looking name in the workspace lead somewhere else.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("Could not create a directory symlink on this system.")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)

    with pytest.raises(ToolError, match="outside the workspace"):
        tools.read_workspace_file("linked.txt")
    with pytest.raises(ToolError, match="outside the workspace"):
        tools.write_workspace_note("linked.txt", "escape attempt")
    assert list(outside.iterdir()) == []  # nothing was written through the link


def test_resolve_safe_path_returns_a_path_inside_the_workspace(workspace: Path) -> None:
    resolved = resolve_safe_path("example.txt")
    assert resolved == workspace.resolve() / "example.txt"
    assert resolved.parent == workspace.resolve()


def test_resolve_safe_path_accepts_an_explicit_workspace(tmp_path: Path) -> None:
    assert resolve_safe_path("a.md", tmp_path) == tmp_path.resolve() / "a.md"
    with pytest.raises(UnsafePathError):
        resolve_safe_path("../a.md", tmp_path)


def test_blocked_attempts_are_logged_as_warnings(workspace: Path, caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(ToolError):
        tools.read_workspace_file("../secret.txt")
    assert any(
        record.levelname == "WARNING" and "Blocked unsafe workspace path" in record.getMessage()
        for record in caplog.records
    )


# --------------------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------------------


def test_registry_lists_all_thirteen_tools_with_descriptions() -> None:
    names = [tool.__name__ for tool in tools.ALL_TOOLS]
    assert len(names) == len(set(names)) == 13
    assert all(tool.__doc__ for tool in tools.ALL_TOOLS)  # the docstring becomes the tool description
