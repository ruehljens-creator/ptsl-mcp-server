"""Thin, safe PTSL client.

- one Engine connection (reconnects after a gRPC failure)
- a hard gRPC deadline on every call (py-ptsl does not pass one through, so a
  Pro Tools dialog would otherwise block forever)
- calls are serialized with a lock
- every path handed to Pro Tools goes through config.to_remote()

Errors are raised as PTSLError; a deadline hit is PTSLTimeout.
"""
import logging
import os
import threading
import time
from typing import List, Optional

import grpc
import ptsl
import ptsl.ops as ops
import ptsl.PTSL_pb2 as pt

from . import config
from .config import CONFIG, to_remote

log = logging.getLogger("ptsl_mcp.client")


class PTSLError(RuntimeError):
    pass


class PTSLTimeout(PTSLError):
    pass


_engine = None
_engine_lock = threading.Lock()
_call_lock = threading.Lock()
_deadline_tls = threading.local()


def _install_deadline(engine):
    raw = engine.client.raw_client
    orig = raw.SendGrpcRequest
    if getattr(orig, "_ptsl_mcp_wrapped", False):
        return

    def _with_deadline(request, *args, **kwargs):
        kwargs.setdefault("timeout", getattr(_deadline_tls, "value", CONFIG.timeout))
        return orig(request, *args, **kwargs)

    _with_deadline._ptsl_mcp_wrapped = True
    raw.SendGrpcRequest = _with_deadline


def get_engine():
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            eng = ptsl.Engine(company_name=CONFIG.company, application_name=CONFIG.app,
                              address=CONFIG.address)
        except Exception as e:
            raise PTSLError(f"Could not connect to PTSL at {CONFIG.address}: {e}") from e
        _install_deadline(eng)
        _engine = eng
        log.info("PTSL connected (%s)", CONFIG.address)
        return eng


def reset_engine():
    global _engine
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.close()
            except Exception:
                pass
            _engine = None


def call(fn, *args, label: str = "", timeout: Optional[float] = None, **kwargs):
    """Serialized PTSL call with a hard deadline."""
    timeout = CONFIG.timeout if timeout is None else timeout
    label = label or getattr(fn, "__name__", "ptsl")
    if not _call_lock.acquire(timeout=timeout + 5):
        raise PTSLTimeout(f"[{label}] PTSL lock not acquired; a previous command is stuck")
    _deadline_tls.value = timeout
    t0 = time.monotonic()
    try:
        return fn(*args, **kwargs)
    except grpc.RpcError as e:
        reset_engine()
        if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            raise PTSLTimeout(
                f"[{label}] no answer from Pro Tools after {timeout:.0f}s "
                f"(is Pro Tools showing a dialog?)") from e
        raise PTSLError(f"[{label}] gRPC error: {e.code().name} {e.details()}") from e
    except PTSLError:
        raise
    except Exception as e:
        raise PTSLError(f"[{label}] {type(e).__name__}: {e}") from e
    finally:
        _deadline_tls.value = CONFIG.timeout
        _call_lock.release()
        log.debug("[%s] %.2fs", label, time.monotonic() - t0)


def run_op(op, label: str, timeout: Optional[float] = None):
    eng = get_engine()
    return call(eng.client.run, op, label=label, timeout=timeout)


# ── read ───────────────────────────────────────────────────────────────────

def host_ready(timeout: float = 5.0) -> bool:
    call(get_engine().host_ready_check, label="host_ready_check", timeout=timeout)
    return True


def ptsl_version(timeout: float = 5.0) -> int:
    op = ops.CId_GetPTSLVersion()
    run_op(op, "get_ptsl_version", timeout)
    return int(op.response.version)


def session_info(timeout: float = 8.0) -> dict:
    eng = get_engine()
    out = {}
    for key, name in (("name", "session_name"), ("path", "session_path"),
                      ("sample_rate", "session_sample_rate"),
                      ("bit_depth", "session_bit_depth"),
                      ("start_time", "session_start_time"),
                      ("timecode_rate", "session_timecode_rate"),
                      ("audio_format", "session_audio_format"),
                      ("interleaved", "session_interleaved_state")):
        try:
            out[key] = call(getattr(eng, name), label=name, timeout=timeout)
        except PTSLError as e:
            out[key] = f"<error: {e}>"
    return out


def has_open_session(timeout: float = 8.0) -> bool:
    try:
        return bool(call(get_engine().session_name, label="session_name", timeout=timeout))
    except PTSLTimeout:
        raise
    except PTSLError:
        return False


def track_list(timeout: float = 10.0) -> List[dict]:
    res = call(get_engine().track_list, label="track_list", timeout=timeout)
    tracks = []
    for t in res or []:
        tracks.append({
            "name": getattr(t, "name", "?"),
            "type": pt.TrackType.Name(t.type) if hasattr(t, "type") else "?",
            "format": pt.TrackFormat.Name(t.format) if hasattr(t, "format") else "?",
            "id": getattr(t, "id", ""),
            "index": getattr(t, "index", -1),
        })
    return tracks


# ── write ──────────────────────────────────────────────────────────────────

_BIT_DEPTH = {16: pt.Bit16, 24: pt.Bit24, 32: pt.Bit32Float}
_SAMPLE_RATE = {44100: pt.SR_44100, 48000: pt.SR_48000, 88200: pt.SR_88200,
                96000: pt.SR_96000, 176400: pt.SR_176400, 192000: pt.SR_192000}
_AUDIO_FMT = {"wave": pt.SAF_WAVE, "wav": pt.SAF_WAVE, "aiff": pt.SAF_AIFF}
_TRACK_FMT = {"mono": pt.TF_Mono, "stereo": pt.TF_Stereo, "lcr": pt.TF_LCR,
              "5.1": pt.TF_5_1}
_TRACK_TYPE = {"audio": pt.TT_Audio, "aux": pt.TT_Aux, "midi": pt.TT_Midi,
               "master": pt.TT_Master, "vca": pt.TT_Vca}
_TIMEBASE = {"samples": pt.TTB_Samples, "ticks": pt.TTB_Ticks}
_AUDIO_OP = {"add": pt.AddAudio, "copy": pt.CopyAudio, "convert": pt.ConvertAudio,
             "default": pt.Default}
_AUDIO_DEST = {"new_track": pt.MD_NewTrack, "clip_list": pt.MD_ClipList}
_AUDIO_LOC = {"session_start": pt.ML_SessionStart, "selection": pt.ML_Selection,
              "spot": pt.ML_Spot, "none": pt.ML_None}
_TIME_OPTS = {"samples": pt.Samples, "timecode": pt.TimeCode,
              "min:sec": pt.MinSecs, "bars+beats": pt.BarsBeats,
              "feet+frames": pt.FeetFrames}


def _guard_write():
    if CONFIG.read_only:
        raise PTSLError("Refused: server runs in read-only mode (PTSL_MCP_READ_ONLY=1)")


def create_session(name: str, location: str, sample_rate: int = 48000,
                   bit_depth: int = 24, audio_format: str = "wave",
                   interleaved: bool = False, timeout: float = 60.0) -> str:
    """Create a session. `location` is the local folder; Pro Tools creates
    `<name>/<name>.ptx` inside it. Returns the expected local .ptx path.

    The py-ptsl CreateSessionBuilder is not used on purpose: it pprints the
    request to stdout, which corrupts a stdio MCP transport."""
    _guard_write()
    if sample_rate not in _SAMPLE_RATE:
        raise ValueError(f"Unsupported sample rate: {sample_rate}")
    if bit_depth not in _BIT_DEPTH:
        raise ValueError(f"Unsupported bit depth: {bit_depth} (16/24/32)")
    if audio_format not in _AUDIO_FMT:
        raise ValueError(f"Unsupported audio format: {audio_format}")
    if not CONFIG.maps_paths or os.path.isabs(location):
        os.makedirs(location, exist_ok=True)
    op = ops.CId_CreateSession(
        session_name=name,
        file_type=_AUDIO_FMT[audio_format],
        sample_rate=_SAMPLE_RATE[sample_rate],
        input_output_settings=pt.IO_Last,
        is_interleaved=interleaved,
        session_location=to_remote(location),
        bit_depth=_BIT_DEPTH[bit_depth],
        is_cloud_project=False,
        create_from_template=False,
    )
    run_op(op, "create_session", timeout)
    return os.path.join(location, name, name + ".ptx")


def open_session(ptx_path: str, timeout: float = 60.0) -> None:
    _guard_write()
    if CONFIG.maps_paths and not os.path.isfile(ptx_path):
        raise PTSLError(f"Session file not found locally: {ptx_path}")
    run_op(ops.CId_OpenSession(session_path=to_remote(ptx_path)), "open_session", timeout)


def save_session(timeout: float = 60.0) -> None:
    _guard_write()
    run_op(ops.CId_SaveSession(), "save_session", timeout)


def save_session_as(name: str, location: str, timeout: float = 60.0) -> str:
    _guard_write()
    op = ops.CId_SaveSessionAs(session_name=name, session_location=to_remote(location))
    run_op(op, "save_session_as", timeout)
    return os.path.join(location, name, name + ".ptx")


def close_session(save: bool = False, timeout: float = 60.0) -> None:
    _guard_write()
    run_op(ops.CId_CloseSession(save_on_close=save), "close_session", timeout)


def create_tracks(count: int = 1, name: Optional[str] = None,
                  track_format: str = "mono", track_type: str = "audio",
                  timebase: str = "samples", timeout: float = 30.0) -> None:
    _guard_write()
    op = ops.CId_CreateNewTracks(
        number_of_tracks=int(count),
        track_name=name or "",
        track_format=_TRACK_FMT[track_format],
        track_type=_TRACK_TYPE[track_type],
        track_timebase=_TIMEBASE[timebase],
    )
    run_op(op, "create_new_tracks", timeout)


def import_audio(files: List[str], operation: str = "add",
                 destination: str = "new_track", location: str = "session_start",
                 spot_value: Optional[str] = None, spot_format: str = "samples",
                 destination_folder: Optional[str] = None,
                 timeout: float = 120.0) -> None:
    """Import audio files into the open session. Note: py-ptsl's own
    import_audio sends import_type=Session; this sends Audio."""
    _guard_write()
    if CONFIG.maps_paths:
        for f in files:
            if not os.path.isfile(f):
                raise PTSLError(f"Audio file not found locally: {f}")
    location_data = pt.SpotLocationData(
        location_type=pt.Start,
        location_options=_TIME_OPTS[spot_format],
        location_value=spot_value or "",
    )
    audio_data = pt.AudioData(
        file_list=[to_remote(f) for f in files],
        destination_path=to_remote(destination_folder) if destination_folder else "",
        audio_operations=_AUDIO_OP[operation],
        audio_destination=_AUDIO_DEST[destination],
        audio_location=_AUDIO_LOC[location],
        location_data=location_data,
    )
    run_op(ops.CId_Import(import_type=pt.Audio, audio_data=audio_data), "import_audio", timeout)


def export_session_text(time_type: str = "samples", include_clip_list: bool = True,
                        include_file_list: bool = True, include_track_edls: bool = True,
                        include_markers: bool = False, include_plugins: bool = False,
                        show_sub_frames: bool = False, include_user_timestamps: bool = False,
                        timeout: float = 60.0) -> str:
    """Session Info as Text, returned as a string (no file dialog)."""
    op = ops.CId_ExportSessionInfoAsText(
        include_clip_list=include_clip_list,
        include_file_list=include_file_list,
        include_markers=include_markers,
        include_plugin_list=include_plugins,
        include_track_edls=include_track_edls,
        show_sub_frames=show_sub_frames,
        track_list_type=pt.AllTracks,
        include_user_timestamps=include_user_timestamps,
        fade_handling_type=pt.DontShowCrossfades,
        track_offset_options=_TIME_OPTS[time_type],
        text_as_file_format=pt.UTF8,
        output_type=pt.ESI_String,
        output_path="",
    )
    run_op(op, "export_session_text", timeout)
    return op.response.session_info


def set_session_start_time(timecode: str, maintain_relative: bool = True,
                           timeout: float = 30.0) -> None:
    _guard_write()
    op = ops.CId_SetSessionStartTime(session_start_time=timecode,
                                     track_offset_opts=pt.TimeCode,
                                     maintain_relative_position=maintain_relative)
    run_op(op, "set_session_start_time", timeout)
