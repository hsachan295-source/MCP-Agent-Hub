"""The MCP Agent Hub client.

What an MCP client does
-----------------------
An MCP *client* is the program that talks to an MCP *server*. Here the conversation
is: our client starts the server as a child process and exchanges JSON-RPC messages
with it through the child's stdin/stdout pipes (the **STDIO transport**).

    client (this file)  --- writes requests to the server's stdin  --->  server
    client (this file)  <--- reads responses from the server's stdout ---  server

The lifecycle, step by step:

1. ``stdio_client`` launches the server process and connects the pipes.
2. ``ClientSession`` speaks the MCP protocol over those pipes.
3. ``session.initialize()`` performs the handshake (see ``run_client``).
4. We then use the session: ``list_tools`` / ``call_tool``, ``list_resources`` /
   ``read_resource`` and ``list_prompts`` / ``get_prompt``.
5. Leaving the ``async with`` blocks closes the pipes and stops the server.

Everything below talks to the server ONLY through the session. The client never
imports ``tools.py`` and never calls a tool function directly.

Note on ``print``: the rule "never print in the server" does not apply here. In the
*client*, stdout is simply your terminal, so printing is exactly what we want.
"""

import argparse
import asyncio
import json
import math
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# The three building blocks of an MCP client:
#   StdioServerParameters - describes HOW to start a server process
#   stdio_client          - starts that process and connects to it
#   ClientSession         - speaks the MCP protocol on top of that connection
from mcp import ClientSession, MCPError, StdioServerParameters, stdio_client
from mcp.types import CallToolResult, InitializeResult, TextContent

from mcp_agent_hub.utils import WORKSPACE_ENV_VAR, configure_logging

SERVER_MODULE = "mcp_agent_hub.server"
INITIALIZE_TIMEOUT_SECONDS = 30  # give up if the server never answers the handshake


# --------------------------------------------------------------------------------------
# Connecting to the server
# --------------------------------------------------------------------------------------


def build_server_parameters() -> StdioServerParameters:
    """Describe how to launch the MCP server subprocess."""
    # The folder that contains the `mcp_agent_hub` package (the project's "src" folder).
    # Adding it to PYTHONPATH lets the server subprocess import our package even if you
    # have not installed the project with `pip install -e .`.
    source_folder = str(Path(__file__).resolve().parents[1])
    environment = {"PYTHONPATH": os.pathsep.join(filter(None, [source_folder, os.environ.get("PYTHONPATH")]))}

    # The SDK deliberately passes only a small safe set of environment variables to the
    # server. If you chose a different workspace folder, forward that one variable explicitly.
    if WORKSPACE_ENV_VAR in os.environ:
        environment[WORKSPACE_ENV_VAR] = os.environ[WORKSPACE_ENV_VAR]

    return StdioServerParameters(
        # sys.executable is the full path of the Python interpreter running THIS program.
        # Using it (instead of the text "python") guarantees the server runs in the same
        # virtual environment as the client, with the same installed packages.
        command=sys.executable,
        # "-m mcp_agent_hub.server" runs our server as a module, exactly like
        # `python -m mcp_agent_hub.server` typed in a terminal.
        args=["-m", SERVER_MODULE],
        env=environment,
    )


def flatten_exceptions(error: BaseException) -> list[BaseException]:
    """Unwrap (possibly nested) ExceptionGroups into the individual errors inside them.

    The SDK runs its background work in task groups, so a failure often arrives wrapped
    as "unhandled errors in a TaskGroup". The useful message is one level deeper.
    """
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in flatten_exceptions(inner)]
    return [error]


def describe_failure(error: BaseException) -> str:
    """Turn any exception into one readable line."""
    parts = []
    for leaf in flatten_exceptions(error):
        if isinstance(leaf, MCPError):
            parts.append(f"{leaf.message} (MCP error code {leaf.code})")
        elif isinstance(leaf, TimeoutError):
            parts.append(f"The server did not answer within {INITIALIZE_TIMEOUT_SECONDS} seconds.")
        else:
            parts.append(f"{type(leaf).__name__}: {leaf}")
    return "; ".join(parts)


# --------------------------------------------------------------------------------------
# Output helpers - turn protocol objects into readable text
# --------------------------------------------------------------------------------------


def print_banner(title: str) -> None:
    print("=" * 40)
    print(title)
    print("=" * 40)


def print_section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def print_block(label: str, text: str) -> None:
    """Print ``label: text``; long or multi-line text goes on indented lines below the label."""
    lines = text.splitlines() or [""]
    if len(lines) == 1:
        print(f"  {label}: {lines[0]}")
        return
    print(f"  {label}:")
    for line in lines:
        print(f"    {line}")


def describe_schema(schema: dict[str, Any] | None) -> str:
    """Summarise a JSON Schema as a compact signature, e.g. ``a: number, b: number``."""
    schema = schema or {}
    required = set(schema.get("required", []))
    parts = []
    for name, details in schema.get("properties", {}).items():
        kind = details.get("type") or "any"
        kind = "|".join(kind) if isinstance(kind, list) else kind
        parts.append(f"{name}: {kind}" + ("" if name in required else " (optional)"))
    return ", ".join(parts) or "no arguments"


def format_arguments(arguments: dict[str, Any]) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in arguments.items())


def render_tool_result(result: CallToolResult) -> str:
    """Return the value of a tool result as clean text.

    A tool result carries the same answer in two forms: ``content`` (blocks meant for
    humans and language models) and, when the tool declares a return type,
    ``structured_content`` (machine-readable JSON). We prefer the structured form
    because a list result is split over several text blocks in ``content``.
    """
    structured = result.structured_content
    if structured is not None:
        # The SDK wraps plain values (a float, a str, a list) as {"result": value}; unwrap them.
        value = structured["result"] if list(structured) == ["result"] else structured
        return value if isinstance(value, str) else json.dumps(value, indent=2)
    blocks = [
        block.text if isinstance(block, TextContent) else f"<{block.type} content>" for block in result.content
    ]
    return "\n".join(blocks)


def show_tool_result(result: CallToolResult, *, show_structured: bool = False) -> None:
    # A failing tool does NOT raise an exception: the call succeeds and the result is flagged is_error.
    print_block("ERROR" if result.is_error else "result", render_tool_result(result))
    if show_structured and result.structured_content is not None:
        print_block("structured content", json.dumps(result.structured_content))


# --------------------------------------------------------------------------------------
# The six MCP requests we use, each wrapped so it prints its result
# --------------------------------------------------------------------------------------


async def call_tool(
    session: ClientSession, name: str, arguments: dict[str, Any], *, show_structured: bool = False
) -> CallToolResult:
    """Run a tool on the server (``tools/call``) and print the outcome."""
    print(f"\n> {name}({format_arguments(arguments)})")
    result = await session.call_tool(name, arguments)
    show_tool_result(result, show_structured=show_structured)
    return result


async def read_resource(session: ClientSession, uri: str) -> None:
    """Load a resource (``resources/read``) and print its content."""
    print(f"\n> read_resource({uri!r})")
    try:
        result = await session.read_resource(uri)
    except MCPError as error:
        # Unlike tools, a failing resources/read comes back as a JSON-RPC error,
        # which the SDK raises as MCPError.
        print_block("ERROR", f"{error.message} (MCP error code {error.code})")
        return
    for item in result.contents:
        print(f"  uri: {item.uri}   type: {item.mime_type}")
        # A resource holds either text or binary data (base64-encoded in `blob`).
        print_block("content", getattr(item, "text", None) or f"<binary data, {len(item.blob)} base64 characters>")


async def get_prompt(session: ClientSession, name: str, arguments: dict[str, str]) -> None:
    """Fetch a filled-in prompt (``prompts/get``) and print its messages."""
    print(f"\n> get_prompt({name!r}, {format_arguments(arguments)})")
    try:
        result = await session.get_prompt(name, arguments)
    except MCPError as error:
        print_block("ERROR", f"{error.message} (MCP error code {error.code})")
        return
    if result.description:
        print(f"  description: {result.description}")
    for message in result.messages:
        # Prompt messages have a role ("user" or "assistant") and content blocks like tool results.
        text = message.content.text if isinstance(message.content, TextContent) else f"<{message.content.type}>"
        print_block(f"message from {message.role}", text)


# --------------------------------------------------------------------------------------
# Part 1: the automatic demonstration
# --------------------------------------------------------------------------------------


def show_server_info(initialization: InitializeResult) -> None:
    """Print what the server told us about itself during the handshake."""
    server = initialization.server_info
    offered = [
        feature
        for feature in ("tools", "resources", "prompts")
        if getattr(initialization.capabilities, feature, None) is not None
    ]
    print(f"Server name      : {server.name}")
    print(f"Server version   : {server.version}")
    print(f"Protocol version : {initialization.protocol_version}")
    print(f"Capabilities     : {', '.join(offered) or 'none'}")
    if initialization.instructions:
        print(f"Instructions     : {initialization.instructions}")


async def list_everything(session: ClientSession) -> None:
    """Ask the server what it offers: tools/list, resources/list, prompts/list."""
    print_section("TOOLS AVAILABLE")
    for tool in (await session.list_tools()).tools:
        print(f"* {tool.name}({describe_schema(tool.input_schema)})")
        print(f"    {' '.join((tool.description or '').split())}")

    print_section("RESOURCES AVAILABLE")
    for resource in (await session.list_resources()).resources:
        print(f"* {resource.uri}   [{resource.mime_type}]")
        print(f"    {' '.join((resource.description or '').split())}")
    # Templates are "resources with a placeholder"; they are listed by a separate request.
    for template in (await session.list_resource_templates()).resource_templates:
        print(f"* {template.uri_template}   [{template.mime_type}]   (template)")
        print(f"    {' '.join((template.description or '').split())}")

    print_section("PROMPTS AVAILABLE")
    for prompt in (await session.list_prompts()).prompts:
        arguments = ", ".join(argument.name for argument in prompt.arguments or [])
        print(f"* {prompt.name}({arguments})")
        print(f"    {' '.join((prompt.description or '').split())}")


async def run_demo(session: ClientSession, initialization: InitializeResult) -> None:
    print_banner("MCP SERVER CONNECTED")
    show_server_info(initialization)
    await list_everything(session)

    print_section("TOOL DEMO")
    await call_tool(session, "add_numbers", {"a": 10, "b": 20}, show_structured=True)
    await call_tool(session, "multiply_numbers", {"a": 7, "b": 8}, show_structured=True)
    await call_tool(
        session,
        "word_count",
        {"text": "Model Context Protocol makes AI tools interoperable."},
        show_structured=True,
    )
    await call_tool(session, "uppercase_text", {"text": "hello mcp"}, show_structured=True)

    print_section("RESOURCE DEMO")
    await read_resource(session, "app://about")
    await read_resource(session, "workspace://example")

    print_section("PROMPT DEMO")
    await get_prompt(session, "beginner_teacher", {"topic": "Model Context Protocol"})

    print_section("ERROR HANDLING DEMO")
    print("Tool failures return a normal result flagged as an error, so the client keeps running:")
    await call_tool(session, "divide_numbers", {"a": 1, "b": 0})
    await call_tool(session, "read_workspace_file", {"filename": "example.txt"})
    await call_tool(session, "read_workspace_file", {"filename": "../secret.txt"})
    await call_tool(session, "read_workspace_file", {"filename": "C:\\Windows\\win.ini"})
    print("\nResource and prompt failures arrive as MCP errors:")
    await read_resource(session, "app://does-not-exist")
    await get_prompt(session, "explain_text", {"text": "MCP", "level": "expert"})


# --------------------------------------------------------------------------------------
# Part 2: the interactive menu
# --------------------------------------------------------------------------------------
# input() blocks the whole event loop while it waits for you to type. That is fine here:
# the server only speaks when spoken to, so nothing else needs to run meanwhile.


def ask(prompt: str) -> str:
    """Read one line of input. Raises EOFError when there is no more input (e.g. piped or closed stdin)."""
    return input(prompt).strip()


def ask_text(prompt: str, *, required: bool = True) -> str:
    """Ask until the user types something (or, if not required, accept an empty answer)."""
    while True:
        answer = ask(prompt)
        if answer or not required:
            return answer
        print("  This value is required.")


def ask_multiline(label: str) -> str:
    """Read several lines; an empty line ends the input."""
    print(f"  {label} (finish with an empty line):")
    lines = []
    while True:
        try:
            line = input("  | ")
        except EOFError:
            break  # treat the end of piped input like an empty line
        if not line:
            break
        lines.append(line)
    return "\n".join(lines)


def ask_number(label: str) -> float:
    """Ask until the user types a valid, finite number."""
    while True:
        try:
            number = float(ask(f"  {label} = "))
        except ValueError:
            print("  Please type a number, for example 12 or 3.5.")
            continue
        if math.isfinite(number):
            return number
        print("  Please type a normal number (not 'nan' or 'inf').")


def choose(prompt: str, options: list[str]) -> str:
    """Show a numbered list and return the option chosen by number; typed text is returned as-is."""
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    answer = ask(prompt)
    return options[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(options) else answer


# Menu option -> (label, tool name, the tool's argument names)
CALCULATOR_OPERATIONS = {
    "1": ("Add", "add_numbers", ("a", "b")),
    "2": ("Subtract", "subtract_numbers", ("a", "b")),
    "3": ("Multiply", "multiply_numbers", ("a", "b")),
    "4": ("Divide", "divide_numbers", ("a", "b")),
    "5": ("Percentage (x% of a value)", "calculate_percentage", ("value", "percentage")),
}


async def menu_list_tools(session: ClientSession) -> None:
    for tool in (await session.list_tools()).tools:
        print(f"* {tool.name}({describe_schema(tool.input_schema)})")
        print(f"    {' '.join((tool.description or '').split())}")


async def menu_calculator(session: ClientSession) -> None:
    for key, (label, _, _) in CALCULATOR_OPERATIONS.items():
        print(f"  {key}. {label}")
    choice = ask("Operation: ")
    if choice not in CALCULATOR_OPERATIONS:
        print("  Unknown operation.")
        return
    _, tool_name, parameters = CALCULATOR_OPERATIONS[choice]
    arguments = {parameter: ask_number(parameter) for parameter in parameters}
    await call_tool(session, tool_name, arguments)


async def menu_analyze_text(session: ClientSession) -> None:
    text = ask_multiline("Text to analyze")
    await call_tool(session, "get_text_statistics", {"text": text})


async def menu_list_workspace_files(session: ClientSession) -> None:
    result = await session.call_tool("list_workspace_files", {})
    if result.is_error:
        show_tool_result(result)
        return
    files = (result.structured_content or {}).get("result", [])
    if not files:
        print("  The workspace is empty.")
    for file in files:
        print(f"  {file['name']:<30} {file['size_bytes']:>8} bytes")


async def menu_read_workspace_file(session: ClientSession) -> None:
    filename = ask_text("File name (for example example.txt): ")
    await call_tool(session, "read_workspace_file", {"filename": filename})


async def menu_write_workspace_note(session: ClientSession) -> None:
    filename = ask_text("File name to write (for example ideas.txt): ")
    content = ask_multiline("Note text")
    await call_tool(session, "write_workspace_note", {"filename": filename, "content": content})


async def menu_list_resources(session: ClientSession) -> None:
    for resource in (await session.list_resources()).resources:
        print(f"* {resource.uri}   [{resource.mime_type}]")
    for template in (await session.list_resource_templates()).resource_templates:
        print(f"* {template.uri_template}   [{template.mime_type}]   (template)")


async def menu_read_resource(session: ClientSession) -> None:
    uris = [resource.uri for resource in (await session.list_resources()).resources]
    uris += [template.uri_template for template in (await session.list_resource_templates()).resource_templates]
    uri = choose("Resource (number, or type a URI such as workspace://file/notes.txt): ", uris)
    await read_resource(session, uri)


async def menu_list_prompts(session: ClientSession) -> None:
    for prompt in (await session.list_prompts()).prompts:
        arguments = ", ".join(
            f"{argument.name}{'' if argument.required else '?'}" for argument in prompt.arguments or []
        )
        print(f"* {prompt.name}({arguments})")
        print(f"    {' '.join((prompt.description or '').split())}")


async def menu_get_prompt(session: ClientSession) -> None:
    prompts = (await session.list_prompts()).prompts
    name = choose("Prompt (number or name): ", [prompt.name for prompt in prompts])
    prompt = next((candidate for candidate in prompts if candidate.name == name), None)
    if prompt is None:
        print(f"  There is no prompt called {name!r}.")
        return
    # Ask for every argument the server declared. Checking "required" here means we never send
    # an incomplete request (the server would answer it with an unhelpful generic error).
    arguments: dict[str, str] = {}
    for argument in prompt.arguments or []:
        suffix = " (required)" if argument.required else " (optional, press Enter to skip)"
        value = ask_text(f"  {argument.name}{suffix}: ", required=bool(argument.required))
        if value:
            arguments[argument.name] = value
    await get_prompt(session, prompt.name, arguments)


MenuAction = Callable[[ClientSession], Awaitable[None]]

MENU: dict[str, tuple[str, MenuAction]] = {
    "1": ("List Tools", menu_list_tools),
    "2": ("Call Calculator Tool", menu_calculator),
    "3": ("Analyze Text", menu_analyze_text),
    "4": ("List Workspace Files", menu_list_workspace_files),
    "5": ("Read Workspace File", menu_read_workspace_file),
    "6": ("Write Workspace Note", menu_write_workspace_note),
    "7": ("List Resources", menu_list_resources),
    "8": ("Read Resource", menu_read_resource),
    "9": ("List Prompts", menu_list_prompts),
    "10": ("Get Prompt", menu_get_prompt),
}


async def run_menu(session: ClientSession) -> None:
    """Let the user explore the server interactively. Every action goes through the MCP session."""
    print()
    print_banner("INTERACTIVE MENU")
    while True:
        print()
        for key, (label, _) in MENU.items():
            print(f"  {key:>2}. {label}")
        print("   0. Exit")
        try:
            choice = ask("\nChoose an option: ")
            if choice == "0":
                print("Goodbye!")
                return
            if choice not in MENU:
                print(f"  '{choice}' is not a menu option. Type a number from 0 to {len(MENU)}.")
                continue
            await MENU[choice][1](session)
        except EOFError:
            print("\nNo more input; leaving the menu.")
            return
        except MCPError as error:
            # Protocol-level failure (tool failures are reported as is_error results, not raised).
            print(f"  MCP error {error.code}: {error.message}")


# --------------------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------------------


async def run_client(*, interactive: bool = True) -> int:
    """Start the server, connect, run the demonstration (and the menu). Returns the exit code."""
    server_parameters = build_server_parameters()
    print(f"Starting MCP server: {server_parameters.command} -m {SERVER_MODULE}\n")

    connected = False
    try:
        # stdio_client launches the server process and gives us two streams:
        #   read  -> messages coming from the MCP server
        #   write -> messages we send to the MCP server
        async with stdio_client(server_parameters) as (read, write):
            # ClientSession manages the MCP protocol on top of those streams: it numbers
            # requests, matches responses to them and validates the messages.
            async with ClientSession(read, write) as session:
                # initialize() performs the MCP handshake. Nothing else is allowed before it:
                #   1. the client sends "initialize" with its protocol version and capabilities;
                #   2. the server replies with ITS version, name and capabilities (tools? resources?);
                #   3. the client confirms with an "initialized" notification.
                # After that both sides know what the other supports and normal requests can begin.
                async with asyncio.timeout(INITIALIZE_TIMEOUT_SECONDS):
                    initialization = await session.initialize()
                connected = True

                await run_demo(session, initialization)
                if interactive:
                    await run_menu(session)
        # Leaving the `async with` blocks closed the pipes, and the SDK stopped the server process.
        print("\nDisconnected. The MCP server process has been stopped.")
        return 0
    except Exception as error:
        stage = "The MCP session failed" if connected else "Could not connect to the MCP server"
        print(f"\n{stage}: {describe_failure(error)}", file=sys.stderr)
        if not connected:
            print(
                "Check the server messages above (they are printed on stderr), and make sure the project's "
                "dependencies are installed (see the README).",
                file=sys.stderr,
            )
        return 1


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="MCP Agent Hub client: starts the MCP server, demonstrates it, then opens an interactive menu.",
    )
    parser.add_argument(
        "--demo-only",
        action="store_true",
        help="run the automatic demonstration and exit, without the interactive menu",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point used by main.py."""
    arguments = parse_arguments(argv)
    configure_logging()
    # If output is redirected to a file, Windows may use a legacy encoding that cannot show
    # every character; replace such characters instead of crashing.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        return asyncio.run(run_client(interactive=not arguments.demo_only))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
