"""MCP resources: read-only *content* a client can load into an AI model's context.

Where a **tool** is an action ("do something"), a **resource** is data ("here is some
content"). Every resource has a URI such as ``app://about``; a client finds them with
``resources/list`` and loads one with ``resources/read``.

This project uses two kinds:

* **static resources** - a fixed URI, like ``app://about``
* **resource templates** - a URI with a ``{placeholder}``, like ``workspace://file/{filename}``.
  The client fills in the placeholder and the SDK passes it to the function as an argument.
"""

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from mcp_agent_hub import SERVER_NAME, __version__, prompts, tools
from mcp_agent_hub.utils import WorkspaceError, WorkspaceFileNotFoundError, read_workspace_text


def _summary(function: Callable[..., object]) -> str:
    """Return the first line of a function's docstring (used to build the help text)."""
    documentation = inspect.getdoc(function) or ""
    return documentation.splitlines()[0] if documentation else "(no description)"


def _read_workspace_resource(filename: str) -> str:
    """Read a workspace file for a resource, using resource-style errors.

    ``ResourceNotFoundError`` and ``ResourceError`` are the resource equivalents of
    ``ToolError``: the SDK sends their message to the client as a protocol error.
    """
    try:
        return read_workspace_text(filename)
    except WorkspaceFileNotFoundError as error:
        raise ResourceNotFoundError(str(error)) from error
    except WorkspaceError as error:
        raise ResourceError(str(error)) from error


# --------------------------------------------------------------------------------------
# Resource handlers
# --------------------------------------------------------------------------------------


def about() -> str:
    """Basic facts about this MCP server: project name, purpose, version and transport."""
    return json.dumps(
        {
            "project": SERVER_NAME,
            "purpose": "A local, beginner-friendly MCP server for learning tools, resources, prompts and STDIO.",
            "version": __version__,
            "transport": "stdio",
        },
        indent=2,
    )


def help_guide() -> str:
    """A guide to the tools, resources and prompts this server offers."""
    lines = [
        f"# {SERVER_NAME} - help",
        "",
        "This guide is generated from the code, so it always matches what the server really offers.",
        "",
        "## Tools (actions - run them with tools/call)",
        *(f"- `{tool.__name__}` - {_summary(tool)}" for tool in tools.ALL_TOOLS),
        "",
        "## Resources (content - load them with resources/read)",
        *(f"- `{spec.uri}` - {_summary(spec.handler)}" for spec in RESOURCES),
        "",
        "## Prompts (message templates - fetch them with prompts/get)",
        *(f"- `{prompt.__name__}` - {_summary(prompt)}" for prompt in prompts.ALL_PROMPTS),
    ]
    return "\n".join(lines)


def example_file() -> str:
    """The contents of workspace/example.txt."""
    return _read_workspace_resource("example.txt")


def notes_file() -> str:
    """The contents of workspace/notes.txt."""
    return _read_workspace_resource("notes.txt")


def workspace_file(filename: str) -> str:
    """The contents of any text file in the workspace, chosen by name, e.g. workspace://file/notes.txt."""
    # The SDK already refuses '..' and absolute paths in template values; the shared safe-path
    # check inside read_workspace_text is a second, independent layer of defence.
    return _read_workspace_resource(filename)


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceSpec:
    """Everything server.py needs to register one resource."""

    uri: str  # the address clients use; a {placeholder} makes it a template
    handler: Callable[..., str]  # the function that produces the content
    mime_type: str  # tells the client how to interpret the content


RESOURCES: tuple[ResourceSpec, ...] = (
    ResourceSpec("app://about", about, "application/json"),
    ResourceSpec("app://help", help_guide, "text/markdown"),
    ResourceSpec("workspace://example", example_file, "text/plain"),
    ResourceSpec("workspace://notes", notes_file, "text/plain"),
    ResourceSpec("workspace://file/{filename}", workspace_file, "text/plain"),
)
