"""Entry point: ``python main.py`` starts the MCP client, which launches the MCP server for you.

You do NOT need a second terminal. The client starts the server as a child process
and talks to it over STDIO (see src/mcp_agent_hub/client.py).
"""

import sys
from pathlib import Path

# Make `import mcp_agent_hub` work even if the project has not been installed with
# `pip install -e .`: the package lives in the "src" folder next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mcp_agent_hub.client import main  # noqa: E402  (must come after the sys.path line above)

if __name__ == "__main__":
    raise SystemExit(main())
