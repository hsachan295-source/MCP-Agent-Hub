"""Shared helpers for MCP Agent Hub.

Nothing in this file talks the MCP protocol. It holds the plain-Python building
blocks that the tools, resources and prompts all share:

* finding the project root and the ``workspace/`` folder
* validating file names so nobody can escape ``workspace/`` (path-traversal defence)
* small, safe helpers to read, write and list workspace files
* logging configuration (to **stderr**, never stdout) and a decorator that logs MCP requests
"""

import functools
import inspect
import logging
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

# A separate logger just for "a client asked for X" lines, so they are easy to spot in the log output.
request_logger = logging.getLogger("mcp_agent_hub.requests")

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

# If this environment variable is set, it replaces the default ``<project>/workspace``
# folder. The tests use it to work in a throw-away temporary folder so they never touch
# your real workspace files.
WORKSPACE_ENV_VAR = "MCP_AGENT_HUB_WORKSPACE"

# Only plain-text formats may be read or written. This keeps the "notes" tools from
# being used to drop scripts or binaries (.bat, .exe, .py ...) onto the disk.
ALLOWED_EXTENSIONS = frozenset({".txt", ".md", ".json", ".csv", ".log"})

MAX_FILENAME_LENGTH = 100
MAX_FILE_BYTES = 1_000_000  # 1 MB limit for reading and for writing

# Windows treats these names as *devices*, not files: opening "CON.txt" reads from the
# console and "NUL.txt" silently discards data. They must never be used as file names.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)

# A strict allow-list: it is easier to be safe by describing what IS allowed than by
# trying to list everything that is dangerous (slashes, colons, wildcards, ...).
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


# --------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------


class WorkspaceError(Exception):
    """A workspace problem whose message is safe and helpful to show to the user."""


class UnsafePathError(WorkspaceError):
    """The requested file name is unsafe (path traversal, absolute path, bad name...)."""


class WorkspaceFileNotFoundError(WorkspaceError):
    """The requested file does not exist inside the workspace."""


# --------------------------------------------------------------------------------------
# Project layout
# --------------------------------------------------------------------------------------


def find_project_root(start: Path | None = None) -> Path:
    """Return the closest parent folder (of ``start`` or of this file) holding ``pyproject.toml``."""
    origin = (start or Path(__file__)).resolve()
    # ``origin.parents`` walks upwards: parent, grandparent, ... up to the drive root.
    for candidate in (origin, *origin.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        "Could not find the project root (no pyproject.toml above "
        f"{origin}). Set the {WORKSPACE_ENV_VAR} environment variable to choose a workspace folder."
    )


def get_workspace_dir() -> Path:
    """Return the absolute workspace folder, creating it when it does not exist yet."""
    override = os.environ.get(WORKSPACE_ENV_VAR)
    workspace = Path(override) if override else find_project_root() / "workspace"
    # resolve() makes the path absolute and follows symlinks, so later comparisons are reliable.
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


# --------------------------------------------------------------------------------------
# Safe file names and paths
# --------------------------------------------------------------------------------------


def validate_filename(filename: str) -> str:
    """Check a file name against every safety rule and return it unchanged if it passes.

    Only *plain* file names are accepted: ``notes.txt`` is fine, but ``../notes.txt``,
    ``C:\\notes.txt``, ``/etc/passwd`` or ``folder/notes.txt`` are all rejected.
    The checks are ordered so the error message names the most specific problem.

    Raises:
        UnsafePathError: If the name breaks any rule.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise UnsafePathError("Filename must be a non-empty string such as 'notes.txt'.")

    if "\x00" in filename:
        # Null bytes can truncate a path inside the operating system: "a.txt\0.exe".
        raise UnsafePathError("Filename contains a null character, which is not allowed.")

    # ``anchor`` is the drive/root part of a path: 'C:\\', '/', '\\\\server\\share'...
    # It is empty for a relative name. We test with Windows *and* POSIX rules so the
    # check behaves the same on every operating system.
    if PureWindowsPath(filename).anchor or PurePosixPath(filename).anchor:
        raise UnsafePathError(
            f"Absolute paths are not allowed ({filename!r}). Use a plain file name inside the workspace."
        )

    # Split on BOTH separators, because '..\\secret.txt' is dangerous on Windows.
    if ".." in re.split(r"[\\/]", filename):
        raise UnsafePathError(f"Path traversal is not allowed ({filename!r}): '..' would leave the workspace.")

    if "/" in filename or "\\" in filename:
        raise UnsafePathError(f"Sub-folders are not supported ({filename!r}). Use a plain name like 'notes.txt'.")

    if len(filename) > MAX_FILENAME_LENGTH:
        raise UnsafePathError(f"Filename is too long (maximum {MAX_FILENAME_LENGTH} characters).")

    if not _SAFE_FILENAME.fullmatch(filename):
        # This also blocks ':' (which on Windows can address hidden "alternate data streams").
        raise UnsafePathError(
            f"Invalid filename {filename!r}. Use only letters, digits, '_', '-' and '.', "
            "and start with a letter, digit or '_'."
        )

    if filename.split(".", 1)[0].lower() in _WINDOWS_RESERVED_NAMES:
        raise UnsafePathError(f"{filename!r} uses a name that Windows reserves for devices (CON, NUL, COM1 ...).")

    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise UnsafePathError(f"Unsupported file type for {filename!r}. Allowed extensions: {allowed}.")

    return filename


def is_valid_filename(filename: str) -> bool:
    """Return True if ``filename`` passes :func:`validate_filename`."""
    try:
        validate_filename(filename)
    except UnsafePathError:
        return False
    return True


def resolve_safe_path(filename: str, workspace_dir: Path | None = None) -> Path:
    """Turn a user-supplied file name into an absolute path that is guaranteed to be inside the workspace.

    Two independent layers protect the workspace:

    1. :func:`validate_filename` rejects suspicious *names* before touching the disk.
    2. The resolved path is compared with the workspace folder. ``resolve()`` follows
       symlinks, so a link such as ``workspace/link.txt -> ../secret.txt`` is caught here.

    Raises:
        UnsafePathError: If the name is unsafe or the final path leaves the workspace.
    """
    try:
        validate_filename(filename)
    except UnsafePathError as error:
        # A security event deserves a log line. %r escapes control characters so a
        # malicious name cannot forge fake log entries.
        logger.warning("Blocked unsafe workspace path %r: %s", filename, error)
        raise

    root = (workspace_dir if workspace_dir is not None else get_workspace_dir()).resolve()
    candidate = (root / filename).resolve()

    # Files must live *directly* in the workspace, so the candidate's parent has to be the root.
    if candidate.parent != root:
        logger.warning("Blocked workspace path %r: it resolves outside the workspace (%s)", filename, candidate)
        raise UnsafePathError(f"{filename!r} resolves to a location outside the workspace.")
    return candidate


# --------------------------------------------------------------------------------------
# Safe workspace file operations
# --------------------------------------------------------------------------------------


def list_workspace_paths() -> list[Path]:
    """Return the readable files directly inside the workspace, sorted by name."""
    return sorted(
        (
            path
            for path in get_workspace_dir().iterdir()
            # Only list what read/write would also accept, so every listed file is usable.
            if path.is_file() and not path.is_symlink() and is_valid_filename(path.name)
        ),
        key=lambda path: path.name.lower(),
    )


def read_workspace_text(filename: str) -> str:
    """Read a UTF-8 text file from the workspace.

    Raises:
        UnsafePathError: If the file name is unsafe.
        WorkspaceFileNotFoundError: If the file does not exist.
        WorkspaceError: If the file is too large, not UTF-8 text, or cannot be read.
    """
    path = resolve_safe_path(filename)
    if not path.is_file():
        raise WorkspaceFileNotFoundError(
            f"File not found in the workspace: {filename!r}. Use list_workspace_files to see what is available."
        )
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise WorkspaceError(f"{filename!r} is too large to read ({size} bytes; the limit is {MAX_FILE_BYTES}).")
        # "utf-8-sig" behaves like "utf-8" but also strips the invisible BOM that some
        # Windows editors (older Notepad) add at the start of a file.
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise WorkspaceError(f"{filename!r} is not a valid UTF-8 text file.") from error
    except OSError as error:
        raise WorkspaceError(f"Could not read {filename!r}: {error.strerror or error}") from error


def write_workspace_text(filename: str, content: str) -> int:
    """Create or overwrite a UTF-8 text file inside the workspace and return the bytes written.

    Raises:
        UnsafePathError: If the file name is unsafe.
        WorkspaceError: If the content is too large, cannot be encoded, or cannot be written.
    """
    path = resolve_safe_path(filename)
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise WorkspaceError("The content contains characters that cannot be stored as UTF-8.") from error
    if len(data) > MAX_FILE_BYTES:
        raise WorkspaceError(f"The content is too large ({len(data)} bytes; the limit is {MAX_FILE_BYTES}).")
    try:
        # write_bytes avoids Windows newline translation, so what you send is what is stored.
        path.write_bytes(data)
    except OSError as error:
        raise WorkspaceError(f"Could not write {filename!r}: {error.strerror or error}") from error
    return len(data)


# --------------------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------------------

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


def configure_logging(level: int | str = logging.INFO) -> None:
    """Send all log output to **stderr**.

    WHY STDERR? An MCP server that uses the STDIO transport talks to its client through
    its *stdout* and *stdin* pipes: every line on stdout must be a valid JSON-RPC
    message. One stray ``print("hello")`` (or a log line on stdout) corrupts the protocol
    and the client fails. stderr is a separate channel, so logs are safe there, and the
    client simply forwards them to your terminal.

    ``basicConfig`` does nothing if logging is already configured, which makes this
    function safe to call more than once (and friendly to pytest).
    """
    logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stderr)


F = TypeVar("F", bound=Callable[..., Any])


def _shorten(value: object, limit: int = 60) -> str:
    """Return ``repr(value)`` cut to ``limit`` characters so long texts do not flood the log."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def log_call(kind: str) -> Callable[[F], F]:
    """Decorator factory: log every request to a tool / resource / prompt at INFO level.

    ``functools.wraps`` copies the wrapped function's name, docstring and type hints
    onto the wrapper. That matters: the MCP SDK builds each tool's name, description
    and JSON input schema from the function it is given.
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):
            # A sync wrapper around an async function would hand back an un-awaited coroutine.
            raise TypeError(f"log_call supports regular functions only, not async def {func.__name__}")

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            arguments = ", ".join(f"{name}={_shorten(value)}" for name, value in kwargs.items())
            request_logger.info("%s requested: %s(%s)", kind, func.__name__, arguments)
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator
