"""Tests for the client: the automatic demo, the interactive menu and connection-failure handling."""

import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from mcp_agent_hub import client as client_module
from mcp_agent_hub.client import describe_failure, describe_schema, flatten_exceptions, run_client, run_menu


class ScriptedInput:
    """Stands in for the keyboard: hands out prepared answers, then behaves like closed input."""

    def __init__(self, *answers: str) -> None:
        self._answers = iter(answers)

    def __call__(self, prompt: str = "") -> str:
        try:
            return next(self._answers)
        except StopIteration:
            raise EOFError from None


# --------------------------------------------------------------------------------------
# The automatic demonstration (a real server subprocess, launched over STDIO)
# --------------------------------------------------------------------------------------


async def test_demo_runs_end_to_end(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = await run_client(interactive=False)
    output = capsys.readouterr().out

    assert exit_code == 0
    for heading in (
        "MCP SERVER CONNECTED",
        "TOOLS AVAILABLE",
        "RESOURCES AVAILABLE",
        "PROMPTS AVAILABLE",
        "TOOL DEMO",
        "RESOURCE DEMO",
        "PROMPT DEMO",
        "ERROR HANDLING DEMO",
    ):
        assert heading in output
    assert "Server name      : MCP Agent Hub" in output
    assert "result: 30.0" in output  # add_numbers(10, 20)
    assert "result: 56.0" in output  # multiply_numbers(7, 8)
    assert "HELLO MCP" in output  # uppercase_text
    assert '"project": "MCP Agent Hub"' in output  # app://about
    assert "Welcome to the test workspace." in output  # workspace://example
    assert "teach me about: Model Context Protocol" in output  # beginner_teacher prompt
    assert "Cannot divide by zero" in output
    assert "Path traversal is not allowed" in output
    assert "Absolute paths are not allowed" in output
    assert "Disconnected" in output


# --------------------------------------------------------------------------------------
# Connection failures
# --------------------------------------------------------------------------------------


async def test_client_explains_a_server_that_cannot_be_started(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        client_module,
        "build_server_parameters",
        lambda: StdioServerParameters(command="definitely-not-a-real-program-xyz"),
    )
    assert await run_client(interactive=False) == 1
    assert "Could not connect to the MCP server" in capsys.readouterr().err


async def test_client_explains_a_server_that_exits_immediately(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        client_module,
        "build_server_parameters",
        lambda: StdioServerParameters(command=sys.executable, args=["-c", "pass"]),
    )
    assert await run_client(interactive=False) == 1
    error_output = capsys.readouterr().err
    assert "Could not connect to the MCP server" in error_output
    assert "Connection closed" in error_output


def test_nested_exception_groups_are_flattened() -> None:
    error = ExceptionGroup("outer", [ExceptionGroup("inner", [ValueError("boom")]), KeyError("k")])
    assert [type(leaf) for leaf in flatten_exceptions(error)] == [ValueError, KeyError]
    assert describe_failure(error) == "ValueError: boom; KeyError: 'k'"


def test_describe_schema_is_readable() -> None:
    schema = {
        "properties": {"a": {"type": "number"}, "note": {"anyOf": []}, "tags": {"type": ["string", "null"]}},
        "required": ["a"],
    }
    assert describe_schema(schema) == "a: number, note: any (optional), tags: string|null (optional)"
    assert describe_schema({"properties": {}}) == "no arguments"
    assert describe_schema(None) == "no arguments"


# --------------------------------------------------------------------------------------
# The interactive menu (driven by scripted keyboard input, in-memory server)
# --------------------------------------------------------------------------------------


async def test_menu_actions_go_through_mcp(
    client: Client, workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "builtins.input",
        ScriptedInput(
            "1",  # List Tools
            "2", "1", "2", "3",  # Calculator: Add, a=2, b=3
            "3", "Hello world. Bye.", "",  # Analyze Text (an empty line ends the text)
            "4",  # List Workspace Files
            "5", "example.txt",  # Read Workspace File
            "6", "menu-note.txt", "written from the menu", "",  # Write Workspace Note
            "7",  # List Resources
            "8", "1",  # Read Resource number 1
            "9",  # List Prompts
            "10", "4", "MCP",  # Get Prompt number 4 (beginner_teacher), topic=MCP
            "abc",  # not a menu option
            "0",  # Exit
        ),
    )  # fmt: skip

    await run_menu(client.session)
    output = capsys.readouterr().out

    assert "* add_numbers(a: number, b: number)" in output  # 1
    assert "result: 5.0" in output  # 2
    assert '"sentence_count": 2' in output  # 3
    assert "example.txt" in output and "notes.txt" in output  # 4
    assert "Welcome to the test workspace." in output  # 5
    assert (workspace / "menu-note.txt").read_text(encoding="utf-8") == "written from the menu"  # 6
    assert "Saved 21 bytes to workspace/menu-note.txt." in output
    assert "* app://about   [application/json]" in output  # 7
    assert '"transport": "stdio"' in output  # 8
    assert "* beginner_teacher(topic)" in output  # 9
    assert "Act as a friendly teacher and teach me about: MCP" in output  # 10
    assert "'abc' is not a menu option" in output
    assert "Goodbye!" in output


async def test_menu_handles_bad_input_and_tool_errors(
    client: Client, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "builtins.input",
        ScriptedInput(
            "2", "4", "not-a-number", "10", "0",  # Divide: a is retyped after a bad value, then b=0
            "5", "../secret.txt",  # Read Workspace File: path traversal attempt
            "10", "explain_text", "some text", "expert",  # Get Prompt with an unsupported level
            "0",
        ),
    )  # fmt: skip

    await run_menu(client.session)
    output = capsys.readouterr().out

    assert "Please type a number" in output
    assert "Cannot divide by zero" in output
    assert "Path traversal is not allowed" in output
    assert "Unsupported level 'expert'" in output
    assert "Goodbye!" in output


async def test_menu_leaves_cleanly_when_input_ends(
    client: Client, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", ScriptedInput())  # no answers at all: immediate end of input
    await run_menu(client.session)
    assert "No more input" in capsys.readouterr().out
