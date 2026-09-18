#!/usr/bin/env python3
"""MCP server exposing Pro Tools' PTSL API.

Two layers:
- convenience tools for the common session workflow (create/open/save/close,
  tracks, audio import, Session Info as Text)
- generic tools (list_commands / describe_command / run_command) that cover
  every command in the PTSL protobuf definition, including the ones py-ptsl
  has no wrapper for.

Every call has a hard deadline; a stuck Pro Tools (modal dialog) is reported
as a timeout instead of hanging the assistant.
"""
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

from mcp.server.fastmcp import FastMCP  # noqa: E402

from . import client as pc  # noqa: E402
from . import generic as pg  # noqa: E402
from .config import CONFIG, to_local  # noqa: E402

mcp = FastMCP("ptsl")


def _err(e: Exception) -> str:
    kind = "TIMEOUT" if isinstance(e, pc.PTSLTimeout) else "ERROR"
    return f"{kind}: {e}"


# ── diagnostics ────────────────────────────────────────────────────────────

@mcp.tool()
def ptsl_ping() -> str:
    """Check that Pro Tools answers over PTSL. Returns the PTSL version, the
    address, the path mapping and whether the server is read-only."""
    try:
        pc.host_ready()
        ver = pc.ptsl_version()
    except Exception as e:
        return _err(e)
    mapping = (f"{CONFIG.local_root}  ->  {CONFIG.remote_root}"
               if CONFIG.maps_paths else "none (paths passed through)")
    return (f"PTSL ok, version {ver}\naddress: {CONFIG.address}\n"
            f"path mapping: {mapping}\nread-only: {CONFIG.read_only}")


@mcp.tool()
def session_info() -> str:
    """Name, path (as Pro Tools reports it and translated to a local path),
    sample rate, bit depth, start time, timecode rate of the open session."""
    try:
        info = pc.session_info()
    except Exception as e:
        return _err(e)
    if not info.get("name"):
        return "No session open."
    try:
        info["path_local"] = to_local(str(info.get("path", "")))
    except Exception:
        info["path_local"] = "<outside path mapping>"
    return "\n".join(f"{k}: {v}" for k, v in info.items())


@mcp.tool()
def list_tracks() -> str:
    """Tracks of the open session (name, type, format)."""
    try:
        tracks = pc.track_list()
    except Exception as e:
        return _err(e)
    if not tracks:
        return "No tracks."
    return "\n".join(f"- {t['name']} ({t['type']}, {t['format']})" for t in tracks)


# ── session workflow ───────────────────────────────────────────────────────

@mcp.tool()
def create_session(name: str, location: str, sample_rate: int = 48000,
                   bit_depth: int = 24, audio_format: str = "wave",
                   interleaved: bool = False) -> str:
    """Create a new session. `location` is the folder in which Pro Tools
    creates `<name>/<name>.ptx`. bit_depth: 16, 24 or 32 (float).
    Pro Tools closes any session that is already open, so save it first."""
    try:
        ptx = pc.create_session(name, location, sample_rate, bit_depth,
                                audio_format, interleaved)
    except Exception as e:
        return _err(e)
    return f"Session created: {ptx}\nvisible locally: {os.path.isfile(ptx)}"


@mcp.tool()
def open_session(ptx_path: str) -> str:
    """Open a session file (.ptx or .ptf)."""
    try:
        pc.open_session(ptx_path)
    except Exception as e:
        return _err(e)
    return f"Opened: {ptx_path}"


@mcp.tool()
def save_session() -> str:
    """Save the open session."""
    try:
        pc.save_session()
    except Exception as e:
        return _err(e)
    return "Saved."


@mcp.tool()
def save_session_as(name: str, location: str) -> str:
    """Save the open session under a new name into `location`."""
    try:
        ptx = pc.save_session_as(name, location)
    except Exception as e:
        return _err(e)
    return f"Saved as: {ptx}"


@mcp.tool()
def close_session(save: bool = False) -> str:
    """Close the open session, optionally saving it first."""
    try:
        pc.close_session(save)
    except Exception as e:
        return _err(e)
    return "Closed."


@mcp.tool()
def create_tracks(count: int = 1, name: str = "", track_format: str = "mono",
                  track_type: str = "audio", timebase: str = "samples") -> str:
    """Create tracks. track_format: mono|stereo|lcr|5.1; track_type:
    audio|aux|midi|master|vca; timebase: samples|ticks."""
    try:
        pc.create_tracks(count, name or None, track_format, track_type, timebase)
    except Exception as e:
        return _err(e)
    return f"{count} track(s) created."


@mcp.tool()
def import_audio(files: list[str], operation: str = "add",
                 destination: str = "new_track", location: str = "session_start",
                 spot_value: str = "", spot_format: str = "samples") -> str:
    """Import audio files. operation: add|copy|convert; destination:
    new_track|clip_list; location: session_start|spot (spot_value in
    spot_format samples|timecode)."""
    try:
        pc.import_audio(files, operation, destination, location,
                        spot_value or None, spot_format)
    except Exception as e:
        return _err(e)
    return f"{len(files)} file(s) imported."


@mcp.tool()
def export_session_text(output_path: str = "", time_type: str = "samples",
                        include_clip_list: bool = True,
                        include_file_list: bool = True,
                        include_track_edls: bool = True,
                        include_markers: bool = False) -> str:
    """Session Info as Text. Without output_path the text is returned;
    otherwise it is written to that local file. time_type: samples|timecode|
    min:sec|bars+beats|feet+frames."""
    kw = dict(time_type=time_type, include_clip_list=include_clip_list,
              include_file_list=include_file_list,
              include_track_edls=include_track_edls,
              include_markers=include_markers)
    try:
        text = pc.export_session_text(**kw)
        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            return f"Export written: {output_path} ({len(text)} chars)"
        return text
    except Exception as e:
        return _err(e)


@mcp.tool()
def set_session_start_time(timecode: str, maintain_relative: bool = True) -> str:
    """Set the session start timecode (e.g. 01:00:00:00)."""
    try:
        pc.set_session_start_time(timecode, maintain_relative)
    except Exception as e:
        return _err(e)
    return f"Session start: {timecode}"


# ── generic: every PTSL command ────────────────────────────────────────────

@mcp.tool()
def list_commands(filter_text: str = "") -> str:
    """List ALL PTSL commands from the protobuf definition, optionally
    filtered by substring (e.g. "Session", "Clip"). Shows request and
    response field names and whether a command is read-only."""
    rows = pg.list_commands(filter_text)
    lines = [f"{len(rows)} commands"]
    for r in rows:
        req = ", ".join(r["request_fields"]) or "-"
        res = ", ".join(r["response_fields"]) or "-"
        tag = "r" if r["read_only"] else "W"
        lines.append(f"[{tag}] {r['command']}  req[{req}]  res[{res}]")
    return "\n".join(lines)


@mcp.tool()
def describe_command(command: str) -> str:
    """Describe one PTSL command: request fields with type, enum values and
    nested fields, plus response fields. The CId_ prefix is optional."""
    try:
        return json.dumps(pg.describe(command), indent=1, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def run_command(command: str, params: dict | None = None,
                timeout_s: float = 20.0) -> str:
    """Run any PTSL command. params = request fields as JSON (enums by name
    or number, nested messages as objects, repeated fields as lists; see
    describe_command). Path fields (session_path, session_location,
    file_list, output_path, ...) are translated to the Pro Tools view.
    Returns request, response and task status as JSON. Commands marked [W]
    in list_commands modify the open session."""
    try:
        res = pg.run(command, params or {}, timeout=timeout_s)
    except Exception as e:
        return _err(e)
    return json.dumps(res, indent=1, ensure_ascii=False)


def registered_tool_names() -> list[str]:
    return sorted(t.name for t in mcp._tool_manager.list_tools())


def main():
    if "--list-tools" in sys.argv:
        print(json.dumps(registered_tool_names(), indent=1))
        return
    if "--list-commands" in sys.argv:
        print("\n".join(pg.command_names()))
        return
    mcp.run()


if __name__ == "__main__":
    main()
