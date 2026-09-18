"""Generic access to EVERY PTSL command.

The protobuf definition shipped with py-ptsl (ptsl.PTSL_pb2) is the source of
truth: each CommandId entry is a command, <Name>RequestBody / <Name>ResponseBody
describe its parameters and result. py-ptsl wraps only part of them as classes
in ptsl.ops; the rest are created on the fly from the same base class, so the
JSON round-trip is identical.

Parameters arrive as a JSON object and are parsed with json_format.ParseDict:
enums by name ("TF_Mono") or number, nested messages as objects, repeated
fields as lists. Path-like fields are translated to the Pro Tools view.
"""
from typing import Any, Dict, List, Optional

from google.protobuf import json_format
from google.protobuf.descriptor import FieldDescriptor as FD
import ptsl.ops as ops
import ptsl.PTSL_pb2 as pt

from . import client
from .config import CONFIG, to_remote

PATH_FIELDS = {
    "session_path", "session_location", "output_path", "destination_path",
    "file_list", "path_to_aaf", "file_path", "path", "export_path",
    "template_path", "aaf_path", "omf_path", "files_list",
}

# Commands that never change a session. Everything else counts as a write
# and is refused in read-only mode.
_READ_PREFIXES = ("CId_Get", "CId_HostReadyCheck", "CId_ExportSessionInfoAsText",
                  "CId_RegisterConnection", "CId_AuthorizeConnection")

_TYPE_NAMES = {
    FD.TYPE_STRING: "string", FD.TYPE_BOOL: "bool", FD.TYPE_INT32: "int32",
    FD.TYPE_INT64: "int64", FD.TYPE_UINT32: "uint32", FD.TYPE_UINT64: "uint64",
    FD.TYPE_DOUBLE: "double", FD.TYPE_FLOAT: "float", FD.TYPE_ENUM: "enum",
    FD.TYPE_MESSAGE: "message", FD.TYPE_BYTES: "bytes",
}


def normalize(command: str) -> str:
    return command if command.startswith("CId_") else "CId_" + command


def is_read_only(command: str) -> bool:
    return normalize(command).startswith(_READ_PREFIXES)


def command_names() -> List[str]:
    """Unique command names (CId_*); enum aliases without the prefix are dropped."""
    by_num: Dict[int, str] = {}
    for name, num in pt.CommandId.items():
        if name.startswith("CId_") or num not in by_num:
            by_num[num] = name
    return [by_num[n] for n in sorted(by_num)]


def _op_class(command: str):
    command = normalize(command)
    try:
        pt.CommandId.Value(command)
    except ValueError:
        raise ValueError(f"Unknown PTSL command: {command!r}")
    cls = getattr(ops, command, None)
    if cls is None or not isinstance(cls, type):
        cls = type(command, (ops.Operation,), {})
    return cls


def _field_info(f, depth: int = 0) -> Dict[str, Any]:
    info: Dict[str, Any] = {"name": f.name, "type": _TYPE_NAMES.get(f.type, str(f.type))}
    if f.label == FD.LABEL_REPEATED:
        info["repeated"] = True
    if f.type == FD.TYPE_ENUM:
        info["enum"] = f.enum_type.name
        vals, seen = [], set()
        for v in f.enum_type.values:
            if v.number in seen:
                continue
            seen.add(v.number)
            vals.append(f"{v.name}={v.number}")
        info["values"] = vals
    elif f.type == FD.TYPE_MESSAGE:
        info["message"] = f.message_type.name
        if depth < 3:
            info["fields"] = [_field_info(sf, depth + 1) for sf in f.message_type.fields]
    return info


def describe(command: str) -> Dict[str, Any]:
    command = normalize(command)
    cls = _op_class(command)
    rq, rs = cls.request_body(), cls.response_body()
    return {
        "command": command,
        "id": int(pt.CommandId.Value(command)),
        "read_only": is_read_only(command),
        "wrapped_by_py_ptsl": hasattr(ops, command),
        "request": [_field_info(f) for f in rq.DESCRIPTOR.fields] if rq else [],
        "response": [_field_info(f) for f in rs.DESCRIPTOR.fields] if rs else [],
    }


def list_commands(filter_text: str = "") -> List[Dict[str, Any]]:
    out, ft = [], filter_text.lower()
    for name in command_names():
        if ft and ft not in name.lower():
            continue
        cls = _op_class(name)
        rq, rs = cls.request_body(), cls.response_body()
        out.append({
            "command": name,
            "read_only": is_read_only(name),
            "request_fields": [f.name for f in rq.DESCRIPTOR.fields] if rq else [],
            "response_fields": [f.name for f in rs.DESCRIPTOR.fields] if rs else [],
        })
    return out


def _map_paths(obj: Any, key: Optional[str] = None) -> Any:
    if isinstance(obj, dict):
        return {k: _map_paths(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_map_paths(v, key) for v in obj]
    if isinstance(obj, str) and key in PATH_FIELDS and obj:
        return to_remote(obj)
    return obj


def run(command: str, params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None, map_paths: bool = True) -> Dict[str, Any]:
    """Run any PTSL command. Returns request, response (as dict) and status."""
    command = normalize(command)
    if CONFIG.read_only and not is_read_only(command):
        raise client.PTSLError(
            f"Refused: {command} can modify the session and the server is read-only")
    cls = _op_class(command)
    params = params or {}
    if map_paths and CONFIG.maps_paths:
        params = _map_paths(params)
    op = cls()
    rq = cls.request_body()
    if rq is not None:
        op.request = json_format.ParseDict(params, rq(), ignore_unknown_fields=False)
    elif params:
        raise ValueError(f"{command} takes no parameters")
    client.run_op(op, command, timeout)
    resp = None
    if op.response is not None:
        resp = json_format.MessageToDict(op.response, preserving_proto_field_name=True)
    try:
        status = pt.TaskStatus.Name(op.status) if op.status is not None else None
    except Exception:
        status = str(op.status)
    return {
        "command": command,
        "request": json_format.MessageToDict(op.request, preserving_proto_field_name=True) if rq else {},
        "response": resp,
        "status": status,
    }
