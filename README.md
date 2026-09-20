# MCP Agent Hub

A beginner-friendly Python implementation of the Model Context Protocol featuring MCP servers, clients, tools, resources, prompts, and STDIO communication.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [What is MCP?](#2-what-is-mcp)
3. [Architecture](#3-architecture)
4. [Project Structure](#4-project-structure)
5. [Features](#5-features)
6. [MCP Server](#6-mcp-server)
7. [MCP Client](#7-mcp-client)
8. [MCP Tools](#8-mcp-tools)
9. [MCP Resources](#9-mcp-resources)
10. [MCP Prompts](#10-mcp-prompts)
11. [STDIO Transport](#11-stdio-transport)
12. [Installation](#12-installation)
13. [Running the Project](#13-running-the-project)
14. [Running Tests](#14-running-tests)
15. [Example Output](#15-example-output)
16. [Security](#16-security)
17. [Future Improvements](#17-future-improvements)
18. [Learning Notes](#18-learning-notes)

---

## 1. Project Overview

**MCP Agent Hub** is a small but complete MCP project made for learning. It contains

* an **MCP server** that offers 13 tools, 5 resources and 4 prompts, and
* an **MCP client** that starts that server by itself, talks to it, and then lets you explore it from an interactive menu.

Everything runs **locally**. There is **no AI model, no API key, no network access and no account** involved. The goal of this first version is to understand the *protocol itself* before any LLM is connected in later phases.

Quick start (from the `MCP-Agent-Hub` folder):

```powershell
uv sync
uv run python main.py
```

You do not need a second terminal: the client launches the server for you.

---

## 2. What is MCP?

The **Model Context Protocol (MCP)** is an open standard that lets AI applications talk to external programs in one uniform way. Think of it as *USB-C for AI*: instead of every AI app inventing its own way to call your code, your program speaks MCP once and works with every MCP-capable client.

Two roles exist:

| Role | What it is | In this project |
|---|---|---|
| **MCP server** | A program that *offers* capabilities | `src/mcp_agent_hub/server.py` |
| **MCP client** | A program that *uses* those capabilities | `src/mcp_agent_hub/client.py` |

A server can offer three kinds of capabilities ("primitives"):

| Primitive | Meaning | Who decides to use it | Example here |
|---|---|---|---|
| **Tool** | An *action* the server performs | Usually the AI model | `add_numbers`, `write_workspace_note` |
| **Resource** | *Content* the server can hand over | Usually the application | `app://about`, `workspace://example` |
| **Prompt** | A reusable *message template* | Usually the user | `beginner_teacher`, `code_review` |

Messages are exchanged as **JSON-RPC 2.0** and travel over a **transport**. This project uses the **STDIO** transport (see [section 11](#11-stdio-transport)).

---

## 3. Architecture

```
+----------------------+
|   Python MCP Client  |
+----------+-----------+
           |
           | MCP / STDIO
           v
+----------------------+
|    MCP Agent Hub     |
|      MCP Server      |
+----------+-----------+
           |
    +------+----------------+
    |      |                |
    v      v                v
  Tools  Resources        Prompts
    |
    v
Local Workspace
```

The client and server are **two separate processes**. The client starts the server as a child process and the two exchange messages through the child's stdin and stdout pipes.

The requests the client can send, in the order a session uses them:

```
MCP Client
    |
    | initialize()
    v
MCP Server
    |
    +-- list_tools()
    +-- call_tool()
    +-- list_resources()
    +-- read_resource()
    +-- list_prompts()
    +-- get_prompt()
```

`initialize()` always comes first: it is the handshake in which both sides announce their protocol version and what they support. (`list_resource_templates()` is the companion of `list_resources()` for URIs with a `{placeholder}`.)

---

## 4. Project Structure

```
MCP-Agent-Hub/
│
├── .gitignore
├── .python-version              Python version used by uv
├── pyproject.toml               dependencies, build and pytest settings
├── uv.lock                      exact dependency versions (created by uv)
├── README.md
├── main.py                      entry point: `python main.py`
│
├── src/
│   └── mcp_agent_hub/
│       ├── __init__.py          server name and version
│       ├── server.py            builds and runs the MCP server
│       ├── client.py            MCP client: demo + interactive menu
│       ├── tools.py             the 13 tool functions
│       ├── resources.py         the resource functions
│       ├── prompts.py           the 4 prompt templates
│       └── utils.py             paths, safe file access, logging
│
├── workspace/                   the only folder the file tools may touch
│   ├── example.txt
│   └── notes.txt
│
└── tests/
    ├── __init__.py
    ├── conftest.py              shared fixtures (temporary workspace, in-memory client)
    ├── test_tools.py            unit tests + path-traversal security tests
    ├── test_server.py           protocol tests: in-memory, real STDIO and raw JSON-RPC
    └── test_client.py           demo, scripted menu, connection-failure tests
```

---

## 5. Features

* Official **MCP Python SDK** (`mcp` 2.x), **STDIO** transport
* **13 tools**: calculator, text utilities and safe workspace file access
* **5 resources**: 4 static ones plus a `workspace://file/{filename}` template
* **4 prompts**: explain, summarize, code review, beginner teacher
* **Structured tool output** (typed JSON next to human-readable text)
* **Async client** using `ClientSession`, `StdioServerParameters` and `stdio_client`
* Client **demo** plus an **interactive menu** that goes through MCP only
* **Path-traversal protection** in two independent layers
* **Clean error handling**: friendly messages, no tracebacks for expected problems
* **Logging to stderr** so stdout stays reserved for the protocol
* **137 tests**, no API keys required
* Heavily commented code written for learning

---

## 6. MCP Server

File: `src/mcp_agent_hub/server.py`

The server is built with the SDK's high-level `MCPServer` class, which turns ordinary Python functions into MCP tools, resources and prompts.

> **Note for readers of older tutorials:** in `mcp` 1.x this class was called `FastMCP` (`from mcp.server.fastmcp import FastMCP`). In `mcp` 2.x it was renamed to `MCPServer` (`from mcp.server.mcpserver import MCPServer`). The idea is identical. Importing the old path on version 2 raises a `ModuleNotFoundError` that points to the migration guide.

`create_server()` does four things:

```python
configure_logging()                                   # 1. logs -> stderr
server = MCPServer(name=SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)   # 2. identity for the handshake

for tool in tools.ALL_TOOLS:                          # 3. register everything
    server.add_tool(log_call("tool")(tool))
for spec in resources.RESOURCES:
    server.resource(spec.uri, mime_type=spec.mime_type)(log_call("resource")(spec.handler))
for prompt in prompts.ALL_PROMPTS:
    server.prompt()(log_call("prompt")(prompt))
```

How the SDK uses your functions:

* **Function name** becomes the tool / prompt name.
* **Docstring** becomes the description (what an AI model reads).
* **Type hints** become the JSON input schema, and incoming arguments are validated against it before your code runs.
* **Return type**: a dataclass return type also produces an *output schema* and *structured content*.
* `tools.py`, `resources.py` and `prompts.py` each export a registry (`ALL_TOOLS`, `RESOURCES`, `ALL_PROMPTS`). The server and the `app://help` resource both read those registries, so the help text can never go out of date.

You normally never start the server yourself, but you can: `python -m mcp_agent_hub.server` (it then waits for JSON-RPC on stdin; press `Ctrl+C` to stop it).

---

## 7. MCP Client

File: `src/mcp_agent_hub/client.py`

The client flow is the heart of MCP:

```python
server_parameters = StdioServerParameters(
    command=sys.executable,                  # the same Python that runs this client
    args=["-m", "mcp_agent_hub.server"],     # start our server as a module
)

async with stdio_client(server_parameters) as (read, write):
    # stdio_client launches the server process and gives us two streams:
    #   read  -> messages coming from the MCP server
    #   write -> messages we send to the MCP server
    async with ClientSession(read, write) as session:
        # ClientSession speaks the MCP protocol over those streams.
        await session.initialize()           # the handshake
        tools = await session.list_tools()
        result = await session.call_tool("add_numbers", {"a": 10, "b": 20})
```

| Piece | What it does |
|---|---|
| `sys.executable` | Full path of the Python running the client. Using it (not the text `"python"`) guarantees the server runs in the **same virtual environment**. |
| `StdioServerParameters` | Describes *how to start* the server: command, arguments, environment. |
| `stdio_client` | Starts that process and connects to its stdin/stdout. |
| `ClientSession` | Speaks MCP on top of the connection: numbers requests, matches responses, validates messages. |
| `initialize()` | The handshake: the client sends its protocol version, the server replies with its name, version and capabilities, the client confirms. Nothing else is allowed before it. |

When it runs, the client

1. starts the server and calls `initialize()`,
2. lists tools, resources and prompts,
3. runs a **demo** (tool calls, resource reads, a prompt, and an *error handling* section),
4. opens an **interactive menu**:

| Option | Action | MCP request used |
|---|---|---|
| 1 | List Tools | `tools/list` |
| 2 | Call Calculator Tool | `tools/call` |
| 3 | Analyze Text | `tools/call` |
| 4 | List Workspace Files | `tools/call` |
| 5 | Read Workspace File | `tools/call` |
| 6 | Write Workspace Note | `tools/call` |
| 7 | List Resources | `resources/list`, `resources/templates/list` |
| 8 | Read Resource | `resources/read` |
| 9 | List Prompts | `prompts/list` |
| 10 | Get Prompt | `prompts/get` |
| 0 | Exit | — |

The client never imports `tools.py`: every action goes through the session. If the server cannot start, or dies during the handshake, the client explains what happened instead of crashing.

---

## 8. MCP Tools

Implemented in `src/mcp_agent_hub/tools.py`.

| Tool | Arguments | Returns |
|---|---|---|
| `add_numbers` | `a`, `b` (numbers) | `a + b` |
| `subtract_numbers` | `a`, `b` | `a - b` |
| `multiply_numbers` | `a`, `b` | `a * b` |
| `divide_numbers` | `a`, `b` | `a / b` — an error message if `b` is `0` |
| `calculate_percentage` | `value`, `percentage` | `value * percentage / 100` (200, 10 → 20) |
| `word_count` | `text` | `{characters, words, lines}` |
| `uppercase_text` | `text` | the text in UPPERCASE |
| `lowercase_text` | `text` | the text in lowercase |
| `reverse_text` | `text` | the text reversed |
| `get_text_statistics` | `text` | `{character_count, word_count, line_count, sentence_count}` |
| `list_workspace_files` | — | list of `{name, size_bytes}` for files in `workspace/` |
| `read_workspace_file` | `filename` | the file's text (workspace only) |
| `write_workspace_note` | `filename`, `content` | confirmation message (workspace only; overwrites an existing file) |

**How tool errors work.** A tool signals an *expected* problem by raising `ToolError("message")`. The call itself still succeeds, but the result is flagged `is_error = true` and carries the message, so the client (and an AI model) can read it and react. Any other exception is treated as a bug and its details are hidden from the client.

---

## 9. MCP Resources

Implemented in `src/mcp_agent_hub/resources.py`.

| URI | MIME type | Content |
|---|---|---|
| `app://about` | `application/json` | project name, purpose, version, transport |
| `app://help` | `text/markdown` | every tool, resource and prompt (generated from the code) |
| `workspace://example` | `text/plain` | `workspace/example.txt` |
| `workspace://notes` | `text/plain` | `workspace/notes.txt` |
| `workspace://file/{filename}` | `text/plain` | any allowed text file in `workspace/` (a **resource template**) |

The resource template is supported cleanly by the installed SDK: a URI containing `{filename}` is registered as a template, and the SDK passes the placeholder value to the function. The SDK additionally rejects `..`, absolute paths and null bytes in template values, and this project's own path check runs as a second layer.

Unlike tool failures, a failing `resources/read` (unknown URI, missing file, blocked path) comes back as a JSON-RPC error, which the client SDK raises as `MCPError`.

---

## 10. MCP Prompts

Implemented in `src/mcp_agent_hub/prompts.py`.

| Prompt | Arguments | Purpose |
|---|---|---|
| `explain_text` | `text`, `level` (`beginner`, `intermediate` or `advanced`) | Explain a text at a chosen level |
| `summarize_text` | `text` | Summarize a text in a fixed structure |
| `code_review` | `code`, `language` | Review code for bugs, readability, security, performance; suggest improvements |
| `beginner_teacher` | `topic` | Teach a topic simply, with examples |

A prompt is **not executed** by the server. The server only fills in a template and returns messages (`role: user`). The *client* decides which AI model, if any, receives them. That is why no AI model or API key is needed here.

Arguments are validated: a blank value or an unsupported `level` returns an `INVALID_PARAMS` (-32602) error with a clear message.

---

## 11. STDIO Transport

With the STDIO transport the client starts the server as a child process and they talk through **standard streams**:

```
+-----------+    server's stdin  (requests)      +-----------+
|  Client   | ---------------------------------> |  Server   |
| (main.py) | <--------------------------------- | (child    |
+-----------+    server's stdout (responses)     |  process) |
      ^                                            +-----+-----+
      |             server's stderr (logs)               |
      +--------------------------------------------------+
```

Each message is one line of JSON-RPC 2.0. This is the **real** traffic of a short session (captured from this project):

```text
client -> server  {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"raw-example","version":"1.0"}}}
server -> client  {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"prompts":{...},"resources":{...},"tools":{...}},"instructions":"MCP Agent Hub is a local learning server. ...","protocolVersion":"2025-11-25","serverInfo":{"name":"MCP Agent Hub","version":"0.1.0"}}}
client -> server  {"jsonrpc":"2.0","method":"notifications/initialized"}
client -> server  {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add_numbers","arguments":{"a":10,"b":20}}}
server -> client  {"jsonrpc":"2.0","id":2,"result":{"content":[{"text":"30.0","type":"text"}],"isError":false,"structuredContent":{"result":30.0}}}
```

### The golden rule: stdout belongs to the protocol

Because every line on the server's stdout must be valid JSON-RPC, **nothing else may be written there**. One stray `print("hello")` in server code corrupts the conversation. Therefore:

* the server **never uses `print()`**;
* `configure_logging()` in `utils.py` sends all logs to **stderr**;
* the client forwards the server's stderr to your terminal, which is why you see log lines such as `tool requested: add_numbers(a=10.0, b=20.0)` while the demo runs.

A test (`test_stdout_carries_only_json_rpc_and_logs_go_to_stderr`) speaks raw JSON-RPC to the server and fails if a single stdout line is not valid JSON.

### Lifecycle

1. `stdio_client` starts `python -m mcp_agent_hub.server`.
2. `initialize()` performs the handshake.
3. Requests and responses flow.
4. Leaving the `async with` blocks closes the server's stdin; the server sees end-of-input and exits.

---

## 12. Installation

**Requirements:** Python **3.11 or newer**. [uv](https://docs.astral.sh/uv/) is optional but recommended. Tested with `mcp` 2.2.0 on Python 3.11 and 3.13 (Windows 11).

All commands below are run from inside the `MCP-Agent-Hub` folder.

### Option A: uv (recommended)

```powershell
cd MCP-Agent-Hub
uv sync
```

`uv sync` creates `.venv` and installs the project and its test tools.

### Option B: standard virtual environment (pip)

```powershell
cd MCP-Agent-Hub
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

`pip install -e .` installs the project (and `mcp`); the `[dev]` extra also installs `pytest` and `pytest-asyncio`, which you need for `pytest`.

If PowerShell refuses to run the activation script, allow it for the current window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

---

## 13. Running the Project

**With uv:**

```powershell
uv run python main.py
```

**With an activated virtual environment:**

```powershell
python main.py
```

The client starts the server, prints the demo and then opens the interactive menu. Type a number and press Enter; type `0` to quit.

Useful variations:

```powershell
python main.py --demo-only                      # demo only, no menu (good for scripts)
python main.py 2>$null                          # hide the server's log lines (stderr)
$env:MCP_AGENT_HUB_WORKSPACE = "C:\my\notes"    # use another workspace folder
python -m mcp_agent_hub.server                  # start just the server (Ctrl+C to stop)
```

**Troubleshooting:** if uv prints ``VIRTUAL_ENV=... does not match the project environment path `.venv` and will be ignored``, another virtual environment is activated in your shell. The warning is harmless; `deactivate` removes it.

---

## 14. Running Tests

**With uv:**

```powershell
uv run pytest
```

**With an activated virtual environment:**

```powershell
pytest
```

The suite has **137 tests** and needs no API key or network access. It never modifies your real `workspace/` folder: tests write only to a temporary workspace (one test just *reads* the shipped `example.txt` and `notes.txt`).

| File | What it checks |
|---|---|
| `tests/test_tools.py` | every tool (add, subtract, multiply, divide, divide by zero, word count, uppercase, lowercase, reverse, percentage ...) and the **path-traversal security matrix** |
| `tests/test_server.py` | the server *through the MCP protocol*: an **in-memory** client/server pair, a **real STDIO subprocess**, and **raw JSON-RPC** lines |
| `tests/test_client.py` | the client demo, the interactive menu (scripted keyboard input) and connection failures |

The symlink-escape test is skipped on Windows unless Developer Mode is enabled (creating symlinks needs it). A companion test using a directory *junction*, which needs no special rights, covers the same protection and always runs.

---

## 15. Example Output

Output of `python main.py --demo-only` (trimmed with `...`; the server's log lines appear on stderr between these lines):

```text
Starting MCP server: D:\...\MCP-Agent-Hub\.venv\Scripts\python.exe -m mcp_agent_hub.server

========================================
MCP SERVER CONNECTED
========================================
Server name      : MCP Agent Hub
Server version   : 0.1.0
Protocol version : 2025-11-25
Capabilities     : tools, resources, prompts

TOOLS AVAILABLE
---------------
* add_numbers(a: number, b: number)
    Add two numbers and return a + b. Example: a=10, b=20 returns 30.
* subtract_numbers(a: number, b: number)
    Subtract b from a and return a - b. Example: a=10, b=4 returns 6.
...
* write_workspace_note(filename: string, content: string)
    Create or overwrite a text note inside the workspace/ folder. ...

RESOURCES AVAILABLE
-------------------
* app://about   [application/json]
    Basic facts about this MCP server: project name, purpose, version and transport.
* app://help   [text/markdown]
    A guide to the tools, resources and prompts this server offers.
* workspace://example   [text/plain]
    The contents of workspace/example.txt.
* workspace://notes   [text/plain]
    The contents of workspace/notes.txt.
* workspace://file/{filename}   [text/plain]   (template)
    The contents of any text file in the workspace, chosen by name, e.g. workspace://file/notes.txt.

PROMPTS AVAILABLE
-----------------
* explain_text(text, level)
* summarize_text(text)
* code_review(code, language)
* beginner_teacher(topic)

TOOL DEMO
---------

> add_numbers(a=10, b=20)
  result: 30.0
  structured content: {"result": 30.0}

> multiply_numbers(a=7, b=8)
  result: 56.0
  structured content: {"result": 56.0}

> word_count(text='Model Context Protocol makes AI tools interoperable.')
  result:
    {
      "characters": 52,
      "words": 7,
      "lines": 1
    }
  structured content: {"characters": 52, "words": 7, "lines": 1}

> uppercase_text(text='hello mcp')
  result: HELLO MCP
  structured content: {"result": "HELLO MCP"}

RESOURCE DEMO
-------------

> read_resource('app://about')
  uri: app://about   type: application/json
  content:
    {
      "project": "MCP Agent Hub",
      "purpose": "A local, beginner-friendly MCP server for learning tools, resources, prompts and STDIO.",
      "version": "0.1.0",
      "transport": "stdio"
    }

> read_resource('workspace://example')
  uri: workspace://example   type: text/plain
  content:
    Welcome to MCP Agent Hub.

    This file is exposed safely through an MCP resource and MCP tool.

PROMPT DEMO
-----------

> get_prompt('beginner_teacher', topic='Model Context Protocol')
  description: Ask an AI assistant to teach a topic simply, with examples, as if to a complete beginner.
  message from user:
    Act as a friendly teacher and teach me about: Model Context Protocol
    ...

ERROR HANDLING DEMO
-------------------
Tool failures return a normal result flagged as an error, so the client keeps running:

> divide_numbers(a=1, b=0)
  ERROR: Error executing tool divide_numbers: Cannot divide by zero. Please provide a non-zero value for 'b'.

> read_workspace_file(filename='../secret.txt')
  ERROR: Error executing tool read_workspace_file: Path traversal is not allowed ('../secret.txt'): '..' would leave the workspace.

> read_workspace_file(filename='C:\\Windows\\win.ini')
  ERROR: Error executing tool read_workspace_file: Absolute paths are not allowed ('C:\\Windows\\win.ini'). Use a plain file name inside the workspace.

Resource and prompt failures arrive as MCP errors:

> read_resource('app://does-not-exist')
  ERROR: Unknown resource: app://does-not-exist (MCP error code -32602)

> get_prompt('explain_text', text='MCP', level='expert')
  ERROR: Unsupported level 'expert'. Choose one of: beginner, intermediate, advanced. (MCP error code -32602)

Disconnected. The MCP server process has been stopped.
```

Sample of the server's log lines (stderr), including a blocked attack:

```text
2026-09-20 13:28:31,425 INFO    [mcp_agent_hub.requests] tool requested: add_numbers(a=10.0, b=20.0)
2026-09-20 13:28:31,513 INFO    [mcp_agent_hub.requests] tool requested: read_workspace_file(filename='../secret.txt')
2026-09-20 13:28:31,513 WARNING [mcp_agent_hub.utils] Blocked unsafe workspace path '../secret.txt': Path traversal is not allowed ('../secret.txt'): '..' would leave the workspace.
```

---

## 16. Security

The workspace tools are the only code that touches the disk, and they can reach **only** the `workspace/` folder. All the logic lives in one place, `utils.py`, in two independent layers:

1. **Name validation** (`validate_filename`) rejects a name *before* the disk is touched.
2. **Containment check** (`resolve_safe_path`) resolves the final path (following symlinks) and verifies it sits directly inside the workspace.

| Attempt | Result |
|---|---|
| `../secret.txt`, `../../secret.txt`, `..\secret.txt` | blocked: path traversal |
| `C:\anything`, `C:/x`, `C:x`, `\\server\share\x`, `/etc/passwd` | blocked: absolute path |
| `sub/notes.txt` | blocked: sub-folders are not supported |
| `notes.txt:hidden` (Windows alternate data stream) | blocked: invalid character |
| `CON.txt`, `nul.txt`, `COM1.log` (Windows device names) | blocked: reserved name |
| `run.bat`, `tool.exe`, a name with no extension | blocked: only `.txt .md .json .csv .log` are allowed |
| a name with a null byte, an empty name, a name over 100 characters | blocked |
| a symlink or junction inside `workspace/` that points elsewhere | blocked by the containment check |
| files over 1 MB (read or write) | rejected |

Other measures:

* Only plain file names are accepted, and only from a strict character allow-list (letters, digits, `_`, `-`, `.`).
* Every blocked attempt is logged as a `WARNING` on stderr.
* The `workspace://file/{filename}` resource is protected by the SDK's own template security **and** by the checks above.
* Unexpected exceptions never leak details to the client (the SDK reports only `Error executing tool <name>`).

**Honest limits.** This is a *local learning project*, not a hardened service: there is no authentication (a STDIO server trusts the process that launched it), `write_workspace_note` overwrites existing files without asking, and the symlink check is a point-in-time check rather than protection against a concurrent attacker who can already modify the workspace folder.

---

## 17. Future Improvements

This first version is deliberately small and API-free. Later phases can build on it:

* Connect an **LLM** and let it choose which tools to call (an agent loop over `list_tools` / `call_tool`).
* Try other **transports**, such as Streamable HTTP, so the server can run separately from the client.
* Add **progress reporting and logging notifications** through the SDK's `Context` object.
* Add **tool annotations** (read-only / destructive hints) so clients can ask for confirmation before writing.
* Add **async tools** that call external services, and persistent storage beyond the workspace folder.
* Publish a **console command** (for example `mcp-agent-hub`) through `pyproject.toml`.
* Later: retrieval, databases, authentication, containers and deployment.

---

## 18. Learning Notes

* **Tool vs resource vs prompt.** Tools *do* things, resources *provide* content, prompts *template* messages. If a client only needs to read data, use a resource; if something must happen, use a tool.
* **Two kinds of errors.** A failing *tool* returns a normal result with `is_error = true` (the call worked, the tool did not). A failing *resource* or *prompt* returns a JSON-RPC error, raised by the client SDK as `MCPError`. `client.py` shows both.
* **Structured content.** A tool result carries the answer twice: `content` (text for humans and models) and `structured_content` (JSON for programs). Give a tool a dataclass return type and the SDK publishes an output schema as well. Plain values (a number, a string, a list) are wrapped as `{"result": ...}`.
* **Type hints are the contract.** The tool's JSON schema is generated from your annotations, and bad arguments are rejected before your code runs. Try `add_numbers` with `"banana"` in the tests to see it.
* **Never print in a STDIO server.** stdout is the protocol channel; log to stderr.
* **Why `sys.executable`?** So the server always runs in the same environment as the client.
* **`FastMCP` is now `MCPServer`.** Most online examples target `mcp` 1.x. On 2.x, use `from mcp.server.mcpserver import MCPServer`.
* **Testing async MCP code.** The client is built on `anyio` task groups, which must be entered and exited by the *same task*. A plain `async with Client(...): yield` pytest fixture breaks at teardown under `pytest-asyncio`; `tests/conftest.py` shows the fix (one dedicated task owns the connection).

Exercises to try next:

1. Add a `celsius_to_fahrenheit` tool: write the function in `tools.py` and add it to `ALL_TOOLS`. Restart the client and it shows up under TOOLS AVAILABLE and in `app://help` automatically. Then run `pytest`: the tests that expect exactly 13 tools fail, which is a good way to learn what they protect. Update the expected lists to fix them.
2. Add a `workspace://readme` resource in `resources.py`.
3. Add a `translate_text` prompt in `prompts.py`.
4. Change `write_workspace_note` to append instead of overwrite, and update its test.
