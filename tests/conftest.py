"""Fixtures shared by all tests."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from mcp import Client

from mcp_agent_hub.server import create_server
from mcp_agent_hub.utils import WORKSPACE_ENV_VAR


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a throw-away workspace and point the whole project at it.

    A file called ``secret.txt`` is created just OUTSIDE the workspace. Because it really
    exists, a test that fails to read ``../secret.txt`` proves the file is protected -
    not merely that the path happened to be missing.

    Setting the environment variable also reaches a server started as a subprocess,
    because ``client.build_server_parameters()`` forwards it.
    """
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "example.txt").write_text("Welcome to the test workspace.\n", encoding="utf-8")
    (workspace_dir / "notes.txt").write_text("Test notes.\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET-MARKER: never readable through MCP", encoding="utf-8")
    monkeypatch.setenv(WORKSPACE_ENV_VAR, str(workspace_dir))
    return workspace_dir


@pytest.fixture
def secret_text(workspace: Path) -> str:
    """The content of the secret file that lives outside the workspace."""
    return (workspace.parent / "secret.txt").read_text(encoding="utf-8")


@pytest.fixture
async def client(workspace: Path) -> AsyncIterator[Client]:
    """A client connected to a fresh server *inside the same process*.

    No subprocess and no pipes are involved, but every request still travels through the
    real MCP protocol layers (initialize handshake, JSON-RPC messages, schema validation),
    so the tests exercise exactly what a real client would see - only faster.
    """
    connected: asyncio.Future[Client] = asyncio.get_running_loop().create_future()
    release = asyncio.Event()

    async def hold_connection_open() -> None:
        # The MCP client is built on anyio task groups, and anyio requires that a task group
        # is exited by the SAME task that entered it. pytest-asyncio runs a fixture's setup
        # and teardown in different tasks, so a plain `async with Client(...): yield` fails
        # at teardown. Solution: one dedicated task opens AND closes the connection, and the
        # fixture just tells it when to let go.
        async with Client(create_server()) as opened:
            connected.set_result(opened)
            await release.wait()

    holder = asyncio.create_task(hold_connection_open())
    # Wait until the client is connected, or until the task dies (then its error is raised below).
    await asyncio.wait({holder, connected}, return_when=asyncio.FIRST_COMPLETED)
    if not connected.done():
        await holder
    try:
        yield connected.result()
    finally:
        release.set()
        await holder
