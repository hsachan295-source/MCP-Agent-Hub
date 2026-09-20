"""Tests that talk to the server through the real MCP protocol.

Three styles are used, from fastest to most realistic:

1. In-memory client/server (the ``client`` fixture): no subprocess, but real MCP messages.
2. A real subprocess over STDIO, driven by the official ``stdio_client`` / ``ClientSession``.
3. A real subprocess driven with hand-written JSON-RPC lines, which proves that stdout
   carries nothing but protocol messages.
"""

import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from mcp import Client, ClientSession, MCPError, stdio_client
from mcp.types import INVALID_PARAMS, CallToolResult

from mcp_agent_hub import SERVER_NAME, __version__
from mcp_agent_hub.client import build_server_parameters

EXPECTED_TOOLS = {
    "add_numbers",
    "subtract_numbers",
    "multiply_numbers",
    "divide_numbers",
    "calculate_percentage",
    "word_count",
    "uppercase_text",
    "lowercase_text",
    "reverse_text",
    "get_text_statistics",
    "list_workspace_files",
    "read_workspace_file",
    "write_workspace_note",
}
EXPECTED_RESOURCES = {"app://about", "app://help", "workspace://example", "workspace://notes"}
EXPECTED_PROMPTS = {"explain_text", "summarize_text", "code_review", "beginner_teacher"}


def text_of(result: CallToolResult) -> str:
    """Join the text blocks of a tool result."""
    return "\n".join(block.text for block in result.content if hasattr(block, "text"))


# --------------------------------------------------------------------------------------
# Initialization
# --------------------------------------------------------------------------------------


async def test_handshake_reports_server_identity_and_capabilities(client: Client) -> None:
    assert client.server_info is not None
    assert client.server_info.name == SERVER_NAME == "MCP Agent Hub"
    assert client.server_info.version == __version__
    capabilities = client.server_capabilities
    assert capabilities.tools is not None
    assert capabilities.resources is not None
    assert capabilities.prompts is not None
    assert client.instructions  # the server explains itself to AI models


# --------------------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------------------


async def test_list_tools_exposes_every_tool(client: Client) -> None:
    listed = (await client.list_tools()).tools
    assert {tool.name for tool in listed} == EXPECTED_TOOLS
    assert all(tool.description for tool in listed)


async def test_tool_input_schemas_are_generated_from_type_hints(client: Client) -> None:
    schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
    assert schemas["add_numbers"]["required"] == ["a", "b"]
    assert schemas["add_numbers"]["properties"]["a"]["type"] == "number"
    assert schemas["read_workspace_file"]["properties"]["filename"]["type"] == "string"
    assert set(schemas["write_workspace_note"]["required"]) == {"filename", "content"}
    assert schemas["list_workspace_files"]["properties"] == {}


async def test_call_tool_returns_text_and_structured_content(client: Client) -> None:
    result = await client.call_tool("add_numbers", {"a": 10, "b": 20})
    assert not result.is_error
    assert text_of(result) == "30.0"
    assert result.structured_content == {"result": 30.0}


async def test_call_tool_with_a_structured_result_type(client: Client) -> None:
    text = "Model Context Protocol makes AI tools interoperable."
    result = await client.call_tool("word_count", {"text": text})
    assert result.structured_content == {"characters": len(text), "words": 7, "lines": 1}


async def test_text_tools(client: Client) -> None:
    assert text_of(await client.call_tool("uppercase_text", {"text": "hello mcp"})) == "HELLO MCP"
    assert text_of(await client.call_tool("lowercase_text", {"text": "HELLO"})) == "hello"
    assert text_of(await client.call_tool("reverse_text", {"text": "MCP"})) == "PCM"


async def test_a_failing_tool_returns_an_error_result_instead_of_raising(client: Client) -> None:
    result = await client.call_tool("divide_numbers", {"a": 1, "b": 0})
    assert result.is_error
    assert "Cannot divide by zero" in text_of(result)


async def test_invalid_arguments_are_rejected_by_the_schema(client: Client) -> None:
    wrong_type = await client.call_tool("add_numbers", {"a": "banana", "b": 2})
    assert wrong_type.is_error
    assert "valid number" in text_of(wrong_type)

    missing = await client.call_tool("add_numbers", {"a": 1})
    assert missing.is_error
    assert "Field required" in text_of(missing)


async def test_unknown_tool_is_reported(client: Client) -> None:
    result = await client.call_tool("no_such_tool", {})
    assert result.is_error
    assert "Unknown tool" in text_of(result)


async def test_workspace_tools_work_together(client: Client, workspace: Path) -> None:
    written = await client.call_tool("write_workspace_note", {"filename": "hello.txt", "content": "hi there"})
    assert not written.is_error
    assert (workspace / "hello.txt").read_text(encoding="utf-8") == "hi there"

    read = await client.call_tool("read_workspace_file", {"filename": "hello.txt"})
    assert text_of(read) == "hi there"

    listed = await client.call_tool("list_workspace_files", {})
    names = [entry["name"] for entry in listed.structured_content["result"]]
    assert names == ["example.txt", "hello.txt", "notes.txt"]


@pytest.mark.parametrize(
    "filename",
    ["../secret.txt", "../../secret.txt", "..\\secret.txt", "/etc/passwd", "C:\\Windows\\win.ini", "sub/notes.txt"],
)
async def test_path_traversal_is_blocked_through_the_protocol(
    client: Client, workspace: Path, secret_text: str, filename: str
) -> None:
    read = await client.call_tool("read_workspace_file", {"filename": filename})
    assert read.is_error
    assert secret_text not in text_of(read)

    write = await client.call_tool("write_workspace_note", {"filename": filename, "content": "malicious"})
    assert write.is_error
    assert sorted(path.name for path in workspace.parent.iterdir()) == ["secret.txt", "workspace"]
    assert (workspace.parent / "secret.txt").read_text(encoding="utf-8") == secret_text


# --------------------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------------------


async def test_list_resources_and_templates(client: Client) -> None:
    resources = (await client.list_resources()).resources
    assert {str(resource.uri) for resource in resources} == EXPECTED_RESOURCES
    templates = (await client.list_resource_templates()).resource_templates
    assert [template.uri_template for template in templates] == ["workspace://file/{filename}"]


async def test_read_app_about(client: Client) -> None:
    contents = (await client.read_resource("app://about")).contents[0]
    assert contents.mime_type == "application/json"
    assert json.loads(contents.text) == {
        "project": "MCP Agent Hub",
        "purpose": "A local, beginner-friendly MCP server for learning tools, resources, prompts and STDIO.",
        "version": __version__,
        "transport": "stdio",
    }


async def test_app_help_lists_every_tool_resource_and_prompt(client: Client) -> None:
    help_text = (await client.read_resource("app://help")).contents[0].text
    for name in EXPECTED_TOOLS | EXPECTED_PROMPTS | EXPECTED_RESOURCES | {"workspace://file/{filename}"}:
        assert name in help_text


async def test_read_workspace_resources(client: Client) -> None:
    example = (await client.read_resource("workspace://example")).contents[0].text
    assert example == "Welcome to the test workspace.\n"
    notes = (await client.read_resource("workspace://notes")).contents[0].text
    assert notes == "Test notes.\n"


async def test_read_resource_template(client: Client) -> None:
    contents = (await client.read_resource("workspace://file/notes.txt")).contents[0]
    assert contents.text == "Test notes.\n"
    assert contents.mime_type == "text/plain"


async def test_unknown_resource_raises_an_mcp_error(client: Client) -> None:
    with pytest.raises(MCPError, match="Unknown resource"):
        await client.read_resource("app://does-not-exist")


async def test_missing_workspace_file_resource(client: Client) -> None:
    with pytest.raises(MCPError, match="File not found"):
        await client.read_resource("workspace://file/missing.txt")


async def test_resource_template_blocks_path_traversal(client: Client, workspace: Path, secret_text: str) -> None:
    absolute_secret = quote(str(workspace.parent / "secret.txt"), safe="")
    attempts = [
        "workspace://file/../secret.txt",
        "workspace://file/..%2Fsecret.txt",
        "workspace://file/..%2F..%2Fsecret.txt",
        "workspace://file/%2e%2e%2fsecret.txt",
        "workspace://file/..%5Csecret.txt",
        "workspace://file/%2Fetc%2Fpasswd",
        "workspace://file/C%3A%5CWindows%5Cwin.ini",
        f"workspace://file/{absolute_secret}",
    ]
    for uri in attempts:
        with pytest.raises(MCPError) as error:
            await client.read_resource(uri)
        assert secret_text not in str(error.value), uri


# --------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------


async def test_list_prompts_and_their_arguments(client: Client) -> None:
    prompts = {prompt.name: prompt for prompt in (await client.list_prompts()).prompts}
    assert set(prompts) == EXPECTED_PROMPTS
    argument_names = {name: [argument.name for argument in prompt.arguments] for name, prompt in prompts.items()}
    assert argument_names == {
        "explain_text": ["text", "level"],
        "summarize_text": ["text"],
        "code_review": ["code", "language"],
        "beginner_teacher": ["topic"],
    }
    assert all(argument.required for prompt in prompts.values() for argument in prompt.arguments)


async def test_get_beginner_teacher_prompt(client: Client) -> None:
    result = await client.get_prompt("beginner_teacher", {"topic": "Model Context Protocol"})
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"
    assert "Model Context Protocol" in result.messages[0].content.text
    assert "complete beginner" in result.messages[0].content.text


async def test_get_explain_text_prompt(client: Client) -> None:
    result = await client.get_prompt("explain_text", {"text": "Recursion", "level": "Beginner"})
    text = result.messages[0].content.text
    assert "beginner level" in text  # the level is normalised to lowercase
    assert "Recursion" in text


async def test_get_summarize_text_prompt(client: Client) -> None:
    text = (await client.get_prompt("summarize_text", {"text": "A long article."})).messages[0].content.text
    assert "A long article." in text
    assert "Key points" in text


async def test_get_code_review_prompt(client: Client) -> None:
    result = await client.get_prompt("code_review", {"code": "print(1/0)", "language": "Python"})
    text = result.messages[0].content.text
    assert "print(1/0)" in text
    assert "```python" in text
    for heading in ("Bugs", "Readability", "Security", "Performance", "Suggested improvements"):
        assert heading in text


async def test_prompt_rejects_an_unsupported_level(client: Client) -> None:
    with pytest.raises(MCPError, match="Unsupported level") as error:
        await client.get_prompt("explain_text", {"text": "x", "level": "expert"})
    assert error.value.code == INVALID_PARAMS


@pytest.mark.parametrize(
    ("prompt", "arguments"),
    [
        ("summarize_text", {"text": "   "}),
        ("beginner_teacher", {"topic": ""}),
        ("code_review", {"code": "x = 1", "language": " "}),
    ],
)
async def test_prompt_rejects_blank_arguments(client: Client, prompt: str, arguments: dict[str, str]) -> None:
    with pytest.raises(MCPError, match="must not be empty") as error:
        await client.get_prompt(prompt, arguments)
    assert error.value.code == INVALID_PARAMS


async def test_unknown_prompt_raises_an_mcp_error(client: Client) -> None:
    with pytest.raises(MCPError):
        await client.get_prompt("no_such_prompt", {})


# --------------------------------------------------------------------------------------
# STDIO transport (a real server subprocess)
# --------------------------------------------------------------------------------------


async def test_end_to_end_over_real_stdio(workspace: Path, tmp_path: Path) -> None:
    """Launch the server exactly like the real client does and use it through ClientSession."""
    server_log = tmp_path / "server-stderr.log"
    async with asyncio.timeout(60):
        # Capture the server's stderr in a file so we can check that logs really go there.
        with server_log.open("w", encoding="utf-8") as errlog:
            async with stdio_client(build_server_parameters(), errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    initialization = await session.initialize()
                    assert initialization.server_info.name == SERVER_NAME

                    assert {tool.name for tool in (await session.list_tools()).tools} == EXPECTED_TOOLS
                    assert len((await session.list_resources()).resources) == 4
                    assert {prompt.name for prompt in (await session.list_prompts()).prompts} == EXPECTED_PROMPTS

                    added = await session.call_tool("add_numbers", {"a": 10, "b": 20})
                    assert added.structured_content == {"result": 30.0}

                    about = await session.read_resource("app://about")
                    assert json.loads(about.contents[0].text)["transport"] == "stdio"

                    prompt = await session.get_prompt("beginner_teacher", {"topic": "MCP"})
                    assert "MCP" in prompt.messages[0].content.text

                    blocked = await session.call_tool("read_workspace_file", {"filename": "../secret.txt"})
                    assert blocked.is_error

    log = server_log.read_text(encoding="utf-8")
    assert "Starting MCP Agent Hub" in log
    assert "tool requested: add_numbers" in log
    assert "Blocked unsafe workspace path" in log


def test_stdout_carries_only_json_rpc_and_logs_go_to_stderr(workspace: Path) -> None:
    """Speak raw JSON-RPC to the server and check that stdout never contains anything else."""
    parameters = build_server_parameters()
    process = subprocess.Popen(
        [parameters.command, *parameters.args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=os.environ | (parameters.env or {}),
    )
    # Safety net: if the server ever hangs, kill it so the test fails instead of freezing.
    watchdog = threading.Timer(60, process.kill)
    watchdog.start()

    def send(message: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def receive() -> dict[str, Any]:
        assert process.stdout is not None
        line = process.stdout.readline()
        assert line, "The server closed stdout unexpectedly"
        return json.loads(line)  # raises if the line is not JSON, i.e. if something polluted stdout

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "raw-json-test", "version": "0"},
                },
            }
        )
        initialize_response = receive()
        assert initialize_response["jsonrpc"] == "2.0"
        assert initialize_response["id"] == 1
        assert initialize_response["result"]["serverInfo"]["name"] == SERVER_NAME

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_response = receive()
        assert tools_response["id"] == 2
        assert {tool["name"] for tool in tools_response["result"]["tools"]} == EXPECTED_TOOLS

        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "add_numbers", "arguments": {"a": 1, "b": 2}},
            }
        )
        call_response = receive()
        assert call_response["id"] == 3
        assert call_response["result"]["structuredContent"] == {"result": 3.0}

        assert process.stdin is not None
        process.stdin.close()  # end of input: a well-behaved server shuts down
        process.wait(timeout=30)
        assert process.stdout is not None
        assert process.stdout.read().strip() == "", "Unexpected extra output on stdout"
        assert process.stderr is not None
        stderr = process.stderr.read()
    finally:
        watchdog.cancel()
        process.kill()

    assert "Starting MCP Agent Hub" in stderr
    assert "tool requested: add_numbers" in stderr
    assert process.returncode == 0
