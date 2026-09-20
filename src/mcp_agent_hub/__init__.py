"""MCP Agent Hub - a beginner-friendly, fully local Model Context Protocol project.

The package contains:

* ``server``    - the MCP server (exposes tools, resources and prompts)
* ``client``    - an MCP client that launches the server over STDIO
* ``tools``     - the functions exposed as MCP *tools*
* ``resources`` - the functions exposed as MCP *resources*
* ``prompts``   - the functions exposed as MCP *prompts*
* ``utils``     - shared helpers (project paths, safe file access, logging)
"""

# Single source of truth for the project's identity.
# The server announces these values to every client during the MCP handshake,
# and the ``app://about`` resource reports them too.
SERVER_NAME = "MCP Agent Hub"
__version__ = "0.1.0"
