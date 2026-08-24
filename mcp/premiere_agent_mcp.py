#!/usr/bin/env python3
"""Minimal stdio MCP server for safe Premiere Agent operations.

This is deliberately not a live-Premiere mutator yet. It exposes the safe,
file-based capabilities that an orchestrator can call before/alongside a real
Premiere bridge: validate EDLs, render review MP4s, build social packages, and
plan batch exports.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "helpers"
if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))

from premiere_live_bridge import (  # type: ignore[import-not-found]
    PremiereLiveBridge,
    live_bridge_protocol_spec,
    plan_live_premiere_job,
)

SERVER_NAME = "premiere-agent"
SERVER_VERSION = "0.1.0"


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _json_text(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2, sort_keys=True)}]}


def _read_json(path: str | Path) -> Any:
    p = Path(path).expanduser().resolve()
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str | Path, data: Any) -> Path:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def _slug(value: str, fallback: str = "export") -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()).strip("._-")
    return s or fallback


def validate_edl(edl_path: str) -> dict[str, Any]:
    path = Path(edl_path).expanduser().resolve()
    edl = _read_json(path)
    errors: list[str] = []
    warnings: list[str] = []
    sources = edl.get("sources")
    ranges = edl.get("ranges")
    if not isinstance(sources, dict) or not sources:
        errors.append("EDL must contain non-empty object `sources`.")
        sources = {}
    if not isinstance(ranges, list) or not ranges:
        errors.append("EDL must contain non-empty array `ranges`.")
        ranges = []

    source_paths: dict[str, str] = {}
    for name, src in sources.items():
        if not isinstance(src, str):
            errors.append(f"source {name!r} path must be a string")
            continue
        sp = Path(src).expanduser()
        if not sp.is_absolute():
            sp = (path.parent / sp).resolve()
        else:
            sp = sp.resolve()
        source_paths[str(name)] = str(sp)
        if not sp.exists():
            warnings.append(f"source {name!r} does not exist on this machine: {sp}")

    total_s = 0.0
    beats: list[str] = []
    for i, r in enumerate(ranges):
        if not isinstance(r, dict):
            errors.append(f"range[{i}] must be an object")
            continue
        src = r.get("source")
        if src not in source_paths:
            errors.append(f"range[{i}] references unknown source {src!r}")
        raw_start = r.get("start")
        raw_end = r.get("end")
        if raw_start is None or raw_end is None:
            errors.append(f"range[{i}] start/end must be numeric")
            continue
        try:
            start = float(raw_start)
            end = float(raw_end)
        except Exception:
            errors.append(f"range[{i}] start/end must be numeric")
            continue
        if start < 0:
            errors.append(f"range[{i}] start is negative")
        if end <= start:
            errors.append(f"range[{i}] end must be greater than start")
        speed = float(r.get("speed") or 1.0)
        if speed <= 0:
            errors.append(f"range[{i}] speed must be positive")
            speed = 1.0
        if speed > 10:
            warnings.append(f"range[{i}] speed {speed} will be clamped to 10x by exporters")
        total_s += max(0.0, end - start) / speed
        beat = r.get("beat")
        if beat:
            beats.append(str(beat))
        for deferred in ("audio_lead", "video_tail", "transition_in"):
            if float(r.get(deferred) or 0.0) != 0.0:
                warnings.append(f"range[{i}] has non-zero {deferred}; current professional path expects hard cuts")

    return {
        "ok": not errors,
        "edl_path": str(path),
        "range_count": len(ranges),
        "source_count": len(sources),
        "estimated_output_duration_s": round(total_s, 3),
        "beats": beats,
        "errors": errors,
        "warnings": warnings,
        "source_paths": source_paths,
    }


def render_preview_tool(edl_path: str, output: str | None = None, burn_subtitles: str = "none") -> dict[str, Any]:
    from render_preview import render_preview  # type: ignore[import-not-found]

    edl = Path(edl_path).expanduser().resolve()
    out = Path(output).expanduser().resolve() if output else edl.parent / "review" / "main.mp4"
    report = render_preview(edl, out, burn_subtitles=burn_subtitles)
    return report


def build_social_package_tool(edl_path: str, output_dir: str | None = None, max_vertical_s: float = 60.0) -> dict[str, Any]:
    from build_social_package import build_social_package  # type: ignore[import-not-found]

    edl = Path(edl_path).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve() if output_dir else None
    return build_social_package(edl, output_dir=out, max_vertical_s=float(max_vertical_s))


def batch_export_plan(items: list[dict[str, Any]], output_dir: str, naming: str = "{index:02d}_{slug}.mp4") -> dict[str, Any]:
    outdir = Path(output_dir).expanduser().resolve()
    planned = []
    seen: set[str] = set()
    for idx, item in enumerate(items, start=1):
        title = str(item.get("title") or item.get("name") or item.get("sequence") or f"export_{idx:02d}")
        slug = _slug(title, fallback=f"export_{idx:02d}")
        filename = naming.format(index=idx, slug=slug, title=slug)
        filename = _slug(filename, fallback=f"export_{idx:02d}.mp4")
        if not filename.lower().endswith(".mp4"):
            filename += ".mp4"
        base = filename
        n = 2
        while filename in seen:
            stem = Path(base).stem
            filename = f"{stem}_{n}{Path(base).suffix}"
            n += 1
        seen.add(filename)
        planned.append({
            "index": idx,
            "title": title,
            "sequence": item.get("sequence"),
            "range": item.get("range"),
            "output": str(outdir / filename),
            "notes": item.get("notes"),
        })
    plan = {"output_dir": str(outdir), "count": len(planned), "items": planned}
    _write_json(outdir / "batch_export_plan.json", plan)
    return plan


def nle_safety_checklist() -> dict[str, Any]:
    return {
        "mandatory_before_live_mutation": [
            "Confirm the target Premiere project and sequence name.",
            "Duplicate/backup the sequence before any timeline mutation.",
            "Prefer markers/imports/captions/scale/position changes over destructive deletes.",
            "Record every mutation with sequence, track, time range, and object id/path when available.",
            "Verify via timeline snapshot, exported still/contact sheet, or rendered file before claiming success.",
        ],
        "requires_explicit_user_consent": [
            "Deleting clips or tracks from a live Premiere sequence.",
            "Overwriting existing exports or project files.",
            "Uploading source footage to paid/cloud/generative services.",
            "Using stored API keys or paid model credits.",
        ],
        "fallback": "If the bridge disconnects or live mutation is unsafe, return to edl.json -> cut.xml/cut.fcpxml + review MP4.",
    }


def live_bridge_status(bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).status(dry_run=dry_run)


def live_bridge_verify_connection(bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).verify_premiere_connection(dry_run=dry_run)


def live_bridge_get_sequence_structure(
    sequence_id: str | None = None,
    *,
    bridge_url: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_sequence_structure(sequence_id, dry_run=dry_run)


def live_bridge_list_markers(
    sequence_id: str | None = None,
    *,
    bridge_url: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).list_markers(sequence_id, dry_run=dry_run)


def live_bridge_plan(job_type: str, sequence_id: str, requested_outputs: list[str] | None = None) -> dict[str, Any]:
    return plan_live_premiere_job(job_type, sequence_id, requested_outputs=requested_outputs)  # type: ignore[arg-type]


def live_bridge_duplicate_sequence(
    sequence_id: str,
    backup_name: str | None = None,
    *,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).duplicate_sequence(
        sequence_id,
        backup_name,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_add_marker(
    sequence_id: str,
    time_s: float,
    label: str,
    *,
    color: str = "red",
    comment: str = "",
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).add_marker(
        sequence_id,
        time_s,
        label,
        color=color,
        comment=comment,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_add_editorial_markers(
    sequence_id: str,
    notes: list[dict[str, Any]],
    *,
    default_color: str = "red",
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).add_editorial_markers(
        sequence_id,
        notes,
        default_color=default_color,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_export_review_frames(
    sequence_id: str,
    output_dir: str,
    *,
    frame_count: int = 6,
    range_start_s: float | None = None,
    range_end_s: float | None = None,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).export_sequence_review_frames(
        sequence_id,
        output_dir,
        frame_count=frame_count,
        range_start_s=range_start_s,
        range_end_s=range_end_s,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_import_captions(
    sequence_id: str,
    caption_path: str,
    *,
    start_s: float = 0.0,
    caption_format: str = "subtitle",
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).import_captions(
        sequence_id,
        caption_path,
        start_s=start_s,
        caption_format=caption_format,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_queue_export(
    sequence_id: str,
    output_path: str,
    *,
    range_start_s: float | None = None,
    range_end_s: float | None = None,
    preset: str = "match_source_h264",
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).queue_export(
        sequence_id,
        output_path,
        range_start_s=range_start_s,
        range_end_s=range_end_s,
        preset=preset,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


TOOLS: dict[str, dict[str, Any]] = {
    "premiere_agent_validate_edl": {
        "description": "Validate a Premiere Agent edl.json before render/import/export.",
        "inputSchema": {"type": "object", "properties": {"edl_path": {"type": "string"}}, "required": ["edl_path"]},
        "handler": lambda a: validate_edl(a["edl_path"]),
    },
    "premiere_agent_render_preview": {
        "description": "Render a flattened MP4 review preview from edl.json.",
        "inputSchema": {"type": "object", "properties": {"edl_path": {"type": "string"}, "output": {"type": "string"}, "burn_subtitles": {"type": "string", "default": "none"}}, "required": ["edl_path"]},
        "handler": lambda a: render_preview_tool(a["edl_path"], a.get("output"), a.get("burn_subtitles", "none")),
    },
    "premiere_agent_build_social_package": {
        "description": "Build main/vertical/square social derivatives from an approved EDL.",
        "inputSchema": {"type": "object", "properties": {"edl_path": {"type": "string"}, "output_dir": {"type": "string"}, "max_vertical_s": {"type": "number", "default": 60}}, "required": ["edl_path"]},
        "handler": lambda a: build_social_package_tool(a["edl_path"], a.get("output_dir"), a.get("max_vertical_s", 60.0)),
    },
    "premiere_agent_batch_export_plan": {
        "description": "Create a naming-safe JSON plan for exporting many sequences/ranges.",
        "inputSchema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object"}}, "output_dir": {"type": "string"}, "naming": {"type": "string", "default": "{index:02d}_{slug}.mp4"}}, "required": ["items", "output_dir"]},
        "handler": lambda a: batch_export_plan(a["items"], a["output_dir"], a.get("naming", "{index:02d}_{slug}.mp4")),
    },
    "premiere_agent_nle_safety_checklist": {
        "description": "Return safety checklist for live Premiere/NLE automation.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda a: nle_safety_checklist(),
    },
    "premiere_agent_live_bridge_protocol_spec": {
        "description": "Return the JSON-RPC protocol contract expected from a live Premiere CEP/UXP bridge.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda a: live_bridge_protocol_spec(),
    },
    "premiere_agent_live_bridge_status": {
        "description": "Check the configured live Premiere bridge status; dry-run by default if no bridge is running.",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_status(a.get("bridge_url"), a.get("dry_run", True)),
    },
    "premiere_agent_verify_premiere_connection": {
        "description": "Run a safe, read-only first-run check of the Premiere panel, project, and active sequence without exposing project/media names.",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_verify_connection(a.get("bridge_url"), a.get("dry_run", True)),
    },
    "premiere_agent_get_sequence_structure": {
        "description": "Read the active Premiere sequence structure: tracks, clips, gaps, playhead, and safe host-snapshot verification data.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_get_sequence_structure(a.get("sequence_id"), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_list_markers": {
        "description": "List markers on a live Premiere sequence for readback verification after marker/editorial-note writes.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_list_markers(a.get("sequence_id"), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_plan_live_job": {
        "description": "Plan a safety-gated live Premiere job before mutating a sequence.",
        "inputSchema": {"type": "object", "properties": {"job_type": {"type": "string", "enum": ["talking_head", "batch_export", "motion_graphic", "caption_pass"]}, "sequence_id": {"type": "string"}, "requested_outputs": {"type": "array", "items": {"type": "string"}}}, "required": ["job_type", "sequence_id"]},
        "handler": lambda a: live_bridge_plan(a["job_type"], a["sequence_id"], a.get("requested_outputs")),
    },
    "premiere_agent_duplicate_sequence": {
        "description": "Duplicate/backup a live Premiere sequence. Requires confirm=true for any non-dry-run write.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "backup_name": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_duplicate_sequence(a["sequence_id"], a.get("backup_name"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_add_marker": {
        "description": "Add a marker to a live Premiere sequence. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "time_s": {"type": "number"}, "label": {"type": "string"}, "color": {"type": "string", "default": "red"}, "comment": {"type": "string"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "time_s", "label"]},
        "handler": lambda a: live_bridge_add_marker(a["sequence_id"], a["time_s"], a["label"], color=a.get("color", "red"), comment=a.get("comment", ""), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_add_editorial_markers": {
        "description": "Add a batch of AI editorial-note markers for filler, retakes, editor notes, or review regions. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "notes": {"type": "array", "items": {"type": "object"}}, "default_color": {"type": "string", "default": "red"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "notes"]},
        "handler": lambda a: live_bridge_add_editorial_markers(a["sequence_id"], a["notes"], default_color=a.get("default_color", "red"), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_export_review_frames": {
        "description": "Export evenly spaced review frames from a live Premiere sequence into a folder. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "output_dir": {"type": "string"}, "frame_count": {"type": "integer", "default": 6}, "range_start_s": {"type": "number"}, "range_end_s": {"type": "number"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "output_dir"]},
        "handler": lambda a: live_bridge_export_review_frames(a["sequence_id"], a["output_dir"], frame_count=a.get("frame_count", 6), range_start_s=a.get("range_start_s"), range_end_s=a.get("range_end_s"), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_import_captions": {
        "description": "Import an SRT/VTT caption file into a live Premiere project/sequence scaffold. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "caption_path": {"type": "string"}, "start_s": {"type": "number", "default": 0}, "caption_format": {"type": "string", "default": "subtitle"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "caption_path"]},
        "handler": lambda a: live_bridge_import_captions(a["sequence_id"], a["caption_path"], start_s=a.get("start_s", 0.0), caption_format=a.get("caption_format", "subtitle"), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_queue_export": {
        "description": "Queue a live Premiere sequence/range export. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "output_path": {"type": "string"}, "range_start_s": {"type": "number"}, "range_end_s": {"type": "number"}, "preset": {"type": "string", "default": "match_source_h264"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "output_path"]},
        "handler": lambda a: live_bridge_queue_export(a["sequence_id"], a["output_path"], range_start_s=a.get("range_start_s"), range_end_s=a.get("range_end_s"), preset=a.get("preset", "match_source_h264"), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
}


def handle(method: str, params: dict[str, Any] | None) -> Any:
    params = params or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}},
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"tools": [{"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]} for name, spec in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            raise McpError(-32602, f"unknown tool: {name}")
        args = params.get("arguments") or {}
        try:
            return _json_text(TOOLS[name]["handler"](args))
        except Exception as e:
            return {"isError": True, "content": [{"type": "text", "text": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"}]}
    raise McpError(-32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            result = handle(req.get("method"), req.get("params"))
            if "id" not in req or req.get("method", "").startswith("notifications/"):
                continue
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}) + "\n")
            sys.stdout.flush()
        except McpError as e:
            rid = locals().get("req", {}).get("id")
            if rid is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": e.code, "message": e.message}}) + "\n")
                sys.stdout.flush()
        except Exception as e:
            rid = locals().get("req", {}).get("id")
            if rid is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}) + "\n")
                sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
