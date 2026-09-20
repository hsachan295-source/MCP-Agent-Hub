"""The MCP Agent Hub server.

Run it directly with ``python -m mcp_agent_hub.server``. Normally you never need to:
the client (``python main.py``) launches the server for you as a child process and
talks to it over **STDIO** - the client writes JSON-RPC messages to the server's
stdin, and the server answers on its stdout.

IMPORTANT STDIO RULE
--------------------
stdout belongs to the MCP protocol. Never call ``print()`` in code that runs inside
this server, because extra text on stdout corrupts the conversation. All diagnostics
go through ``logging``, which ``configure_logging()`` sends to **stderr**.
(Newer SDK versions also try to divert stray ``print()`` output to stderr as a safety net,
but that is best-effort: do not rely on it.)
"""

import logging

# In mcp 1.x this class was called FastMCP (mcp.server.fastmcp). In mcp 2.x it was
# renamed to MCPServer. It is the same idea: a high-level helper that turns ordinary
# Python functions into MCP tools, resources and prompts.
from mcp.server.mcpserver import MCPServer

from mcp_agent_hub import SERVER_NAME, __version__, prompts, resources, tools
from mcp_agent_hub.utils import configure_logging, log_call

# An explicit name (instead of __name__) because __name__ is "__main__" when the server is started
# with `python -m mcp_agent_hub.server`, which would make the log lines confusing.
logger = logging.getLogger("mcp_agent_hub.server")

# Shown to clients during the handshake: a short "how to use this server" note that
# an AI model can read to understand what is on offer.
INSTRUCTIONS = (
    "MCP Agent Hub is a local learning server. Use its tools for calculations, text analysis and safe "
    "access to the workspace/ folder; read app://help for a full overview; fetch its prompts for ready-made "
    "explain / summarize / code-review / teaching requests."
)


def create_server() -> MCPServer:
    """Build a fully configured MCP server (tools, resources and prompts registered).

    This is a factory function so tests can create a fresh, independent server
    whenever they need one.
    """
    # Configure logging BEFORE creating the server: the SDK also calls basicConfig(),
    # and only the first call wins, so this makes sure our stderr format is used.
    configure_logging()

    # The name and version are sent to every client in the initialize handshake.
    server = MCPServer(name=SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)

    # TOOLS ---------------------------------------------------------------------------
    # add_tool() is what the @server.tool() decorator calls under the hood. The SDK inspects
    # each function's name, docstring and type hints to build the tool's schema.
    for tool in tools.ALL_TOOLS:
        server.add_tool(log_call("tool")(tool))

    # RESOURCES -----------------------------------------------------------------------
    # A URI without {placeholders} becomes a static resource; one with {filename}
    # becomes a resource template. The decorator form is used because the SDK decides
    # between "static" and "template" based on the URI.
    for spec in resources.RESOURCES:
        server.resource(spec.uri, mime_type=spec.mime_type)(log_call("resource")(spec.handler))

    # PROMPTS -------------------------------------------------------------------------
    # Each function's parameters become the prompt's arguments; its return value becomes the messages.
    for prompt in prompts.ALL_PROMPTS:
        server.prompt()(log_call("prompt")(prompt))

    return server


def main() -> None:
    """Start the server and serve requests over STDIO until the client disconnects."""
    server = create_server()
    logger.info("Starting %s v%s (transport: stdio)", SERVER_NAME, __version__)
    try:
        # run() blocks: it reads JSON-RPC requests from stdin and writes responses to stdout
        # until the client closes the connection.
        server.run(transport="stdio")
    except KeyboardInterrupt:
        # Ctrl+C in the terminal also reaches this child process; exit quietly.
        logger.info("Interrupted; shutting down.")
    logger.info("%s stopped.", SERVER_NAME)


# This block runs for `python -m mcp_agent_hub.server` (which is exactly how the client starts us).
if __name__ == "__main__":
    main()
