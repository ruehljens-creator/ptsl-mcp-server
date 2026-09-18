# ptsl-mcp-server

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants (Claude Code, Claude Desktop, any MCP client) drive **Avid Pro Tools** through the **PTSL** scripting API.

It differs from other Pro Tools MCP servers in one point: it knows **every PTSL command**. The command list, parameters and enum values are read from the protobuf definition that ships with [py-ptsl](https://github.com/iluvcapra/py-ptsl), so any command Pro Tools understands can be described and executed, including the ones py-ptsl has no Python wrapper for.

> Experimental. Write commands modify the open session. Use the read-only mode if you only want to inspect.

## Features

- **Generic access to all PTSL commands**: `list_commands`, `describe_command`, `run_command` cover the whole `CommandId` enum (159 commands with PTSL 2026). Parameters are plain JSON; enums by name or number; nested messages as objects.
- **Convenience tools** for the common session loop: create, open, save, save as, close; create tracks; import audio; Session Info as Text as a string (no file dialog); set the session start timecode.
- **Hard timeouts on every call.** py-ptsl does not pass a gRPC deadline, so a modal dialog in Pro Tools (missing plug-in, playback engine, sample-rate mismatch) would block forever. This server injects a deadline and reports a stuck Pro Tools as `TIMEOUT` instead of hanging the assistant.
- **Serialized calls and automatic reconnect** after a gRPC failure.
- **Path mapping** for the setup where Pro Tools runs on another machine and the shared volume is mounted under different paths on each side. Every path field is translated in both directions; paths outside the configured root are rejected.
- **Read-only mode** (`PTSL_MCP_READ_ONLY=1`) refuses every command that can change a session, in the convenience tools and in `run_command`.
- **stdio-safe**: the py-ptsl `CreateSessionBuilder` prints its request to stdout, which corrupts a stdio MCP transport. The server builds the request itself. It also fixes py-ptsl's audio import, which sends the wrong import type.

## Requirements

- A Pro Tools version that ships the PTSL scripting server (introduced with Pro Tools 2022.9). Tested with Pro Tools 2026.4 (PTSL version 2026).
- Python 3.10+
- `py-ptsl`, `mcp`, `grpcio`, `protobuf` (installed automatically)

## Installation

```bash
pip install git+https://github.com/ruehljens-creator/ptsl-mcp-server
```

or from a clone:

```bash
git clone https://github.com/ruehljens-creator/ptsl-mcp-server
cd ptsl-mcp-server
pip install -e .
```

Check that the package is installed and see the registered tools:

```bash
ptsl-mcp-server --list-tools
```

If the `ptsl-mcp-server` script is not on your `PATH` (common with framework Python installs on macOS), use `python3 -m ptsl_mcp.server` everywhere the examples say `ptsl-mcp-server`.

## Configuration

Everything is set through environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `PTSL_ADDRESS` | `localhost:31416` | gRPC address of Pro Tools' PTSL server |
| `PTSL_MCP_TIMEOUT` | `20` | default deadline per command, seconds |
| `PTSL_MCP_LOCAL_ROOT` | unset | shared volume root as seen by this machine |
| `PTSL_MCP_REMOTE_ROOT` | unset | the same root as seen by Pro Tools |
| `PTSL_MCP_READ_ONLY` | `0` | `1` refuses all session-modifying commands |
| `PTSL_MCP_COMPANY`, `PTSL_MCP_APP` | `ptsl-mcp-server` | names registered with Pro Tools |

If both roots are unset, paths are passed through unchanged (Pro Tools on the same machine). If both are set, every path is translated; setting only one is an error.

### Claude Code

Add to `.mcp.json` in your project (see `examples/`):

```json
{
  "mcpServers": {
    "ptsl": {
      "command": "ptsl-mcp-server",
      "env": { "PTSL_ADDRESS": "localhost:31416" }
    }
  }
}
```

Or from the command line:

```bash
claude mcp add ptsl -- ptsl-mcp-server
```

### Claude Desktop

Add the same block to `claude_desktop_config.json`.

## Tools

### Diagnostics

| Tool | Description |
|---|---|
| `ptsl_ping` | Checks the connection; returns PTSL version, address, path mapping, read-only state. |
| `session_info` | Name, path (Pro Tools view and local view), sample rate, bit depth, start time, timecode rate. |
| `list_tracks` | Tracks with type and format. |

### Session workflow

| Tool | Description |
|---|---|
| `create_session(name, location, sample_rate=48000, bit_depth=24, audio_format="wave", interleaved=False)` | Creates `<location>/<name>/<name>.ptx`. |
| `open_session(ptx_path)` | Opens a session file. |
| `save_session()` | Saves the open session. |
| `save_session_as(name, location)` | Saves under a new name. |
| `close_session(save=False)` | Closes the open session. |
| `create_tracks(count=1, name="", track_format="mono", track_type="audio", timebase="samples")` | Creates tracks. |
| `import_audio(files, operation="add", destination="new_track", location="session_start", spot_value="", spot_format="samples")` | Imports audio files, optionally spotted to a sample or timecode position. |
| `export_session_text(output_path="", time_type="samples", include_clip_list=True, include_file_list=True, include_track_edls=True, include_markers=False)` | Session Info as Text, returned or written to a file. |
| `set_session_start_time(timecode, maintain_relative=True)` | Sets the session start timecode. |

### Generic

| Tool | Description |
|---|---|
| `list_commands(filter_text="")` | All PTSL commands with request/response field names, marked `[r]` read-only or `[W]` write. |
| `describe_command(command)` | Full request/response schema of one command: field types, enum values, nested fields. |
| `run_command(command, params={}, timeout_s=20)` | Runs any command. Returns request, response and task status as JSON. |

Example, run from an assistant:

```
describe_command("CreateNewTracks")
run_command("CreateNewTracks", {"number_of_tracks": 2, "track_name": "Dialog",
            "track_format": "TF_Mono", "track_type": "TT_Audio",
            "track_timebase": "TTB_Samples"})
run_command("GetSessionSampleRate")
```

## Safety notes

- Pro Tools shows modal dialogs on session open (missing plug-ins, playback engine, sample-rate mismatch) that are invisible over PTSL. The server reports these as timeouts; someone has to dismiss the dialog in Pro Tools.
- `create_session` closes the currently open session without asking. Save it first.
- The read-only classification in `run_command` is by command name (`Get*`, `HostReadyCheck`, `ExportSessionInfoAsText`, connection commands). Everything else is treated as a write.

## Development

```bash
ptsl-mcp-server --list-tools      # registered MCP tools
ptsl-mcp-server --list-commands   # all PTSL command names
```

## License

MIT, see `LICENSE`.
