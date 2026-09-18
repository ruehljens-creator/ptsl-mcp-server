"""Configuration via environment variables.

PTSL_ADDRESS            gRPC address of Pro Tools' PTSL server (default localhost:31416)
PTSL_MCP_TIMEOUT        default per-command deadline in seconds (default 20)
PTSL_MCP_LOCAL_ROOT     root of the shared volume as seen by this machine
PTSL_MCP_REMOTE_ROOT    the same root as seen by Pro Tools
PTSL_MCP_READ_ONLY      "1" to refuse every command that can change a session
PTSL_MCP_COMPANY/APP    names announced to Pro Tools when registering

If LOCAL_ROOT and REMOTE_ROOT are both unset, paths are passed through
unchanged (Pro Tools on the same machine). If both are set, every path field
is translated in both directions and paths outside LOCAL_ROOT are rejected.
"""
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Config:
    address: str
    timeout: float
    local_root: Optional[str]
    remote_root: Optional[str]
    read_only: bool
    company: str
    app: str

    @property
    def maps_paths(self) -> bool:
        return bool(self.local_root and self.remote_root)


def _root(var: str) -> Optional[str]:
    v = os.environ.get(var, "").strip()
    if not v:
        return None
    return v.rstrip("/\\") or v


def load() -> Config:
    lr, rr = _root("PTSL_MCP_LOCAL_ROOT"), _root("PTSL_MCP_REMOTE_ROOT")
    if bool(lr) != bool(rr):
        raise RuntimeError("Set both PTSL_MCP_LOCAL_ROOT and PTSL_MCP_REMOTE_ROOT, or neither")
    return Config(
        address=os.environ.get("PTSL_ADDRESS", "localhost:31416"),
        timeout=float(os.environ.get("PTSL_MCP_TIMEOUT", "20")),
        local_root=lr,
        remote_root=rr,
        read_only=os.environ.get("PTSL_MCP_READ_ONLY", "").strip() in ("1", "true", "yes"),
        company=os.environ.get("PTSL_MCP_COMPANY", "ptsl-mcp-server"),
        app=os.environ.get("PTSL_MCP_APP", "ptsl-mcp-server"),
    )


CONFIG = load()


def to_remote(local_path: str) -> str:
    """Local absolute path -> path as Pro Tools sees it."""
    if not CONFIG.maps_paths:
        return local_path
    if not os.path.isabs(local_path):
        raise ValueError(f"Path must be absolute: {local_path!r}")
    local_path = os.path.normpath(local_path)
    root = CONFIG.local_root
    if local_path != root and not local_path.startswith(root + os.sep):
        raise ValueError(f"Path is outside PTSL_MCP_LOCAL_ROOT ({root}): {local_path!r}")
    rel = local_path[len(root):]
    remote = CONFIG.remote_root
    if "\\" in remote:
        return remote + rel.replace("/", "\\")
    return remote + rel


def to_local(remote_path: str) -> str:
    """Path as Pro Tools reports it -> local absolute path."""
    if not CONFIG.maps_paths:
        return remote_path
    remote = CONFIG.remote_root
    if not remote_path.startswith(remote):
        raise ValueError(f"Path is outside PTSL_MCP_REMOTE_ROOT ({remote}): {remote_path!r}")
    rel = remote_path[len(remote):]
    if "\\" in remote:
        rel = rel.replace("\\", "/")
    return os.path.normpath(CONFIG.local_root + rel)
