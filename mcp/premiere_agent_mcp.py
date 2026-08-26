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
from orchestrator_schema import (  # type: ignore[import-not-found]
    JOB_TYPES,
    SAFETY_POLICY,
    VERIFICATION_METHODS,
    create_orchestrator_job,
    verify_orchestrator_job,
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


def live_bridge_move_clip(
    sequence_id: str,
    track_type: str,
    from_track_index: int,
    clip_index: int,
    to_track_index: int,
    *,
    start_s: float | None = None,
    remove_original: bool = False,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).move_clip(
        sequence_id,
        track_type,
        from_track_index,
        clip_index,
        to_track_index,
        start_s=start_s,
        remove_original=remove_original,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_remove_clip(
    sequence_id: str,
    track_type: str,
    track_index: int,
    clip_index: int,
    *,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).remove_clip(
        sequence_id,
        track_type,
        track_index,
        clip_index,
        backup_sequence_id=backup_sequence_id,
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


def live_bridge_import_media(
    sequence_id: str,
    media_path: str,
    *,
    time_s: float | None = None,
    track: str | None = None,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).import_media(
        sequence_id,
        media_path,
        time_s=time_s,
        track=track,
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


def create_orchestrator_job_tool(
    job_type: str,
    sequence_id: str,
    output_path: str,
    *,
    requested_outputs: list[str] | None = None,
    notes: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    return create_orchestrator_job(
        job_type,
        sequence_id,
        output_path,
        requested_outputs=requested_outputs,
        notes=notes,
        overwrite=overwrite,
    )


def verify_orchestrator_job_tool(manifest_path: str) -> dict[str, Any]:
    return verify_orchestrator_job(manifest_path)


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
    "premiere_agent_move_clip": {
        "description": "Copy a clip onto a different track (e.g. onto a separate camera track for multicam layout) via Track.insertClip. By default the original is left in place (remove_original=false) since TrackItem removal is unproven on this Premiere build; only set remove_original=true after confirming the copy looks correct. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "from_track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "to_track_index": {"type": "integer"}, "start_s": {"type": "number"}, "remove_original": {"type": "boolean", "default": False}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "from_track_index", "clip_index", "to_track_index"]},
        "handler": lambda a: live_bridge_move_clip(a["sequence_id"], a["track_type"], a["from_track_index"], a["clip_index"], a["to_track_index"], start_s=a.get("start_s"), remove_original=a.get("remove_original", False), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_remove_clip": {
        "description": "Remove a single clip from a track via TrackItem.remove() (undocumented ExtendScript API, unproven across Premiere builds). Use to clean up a redundant copy left behind by move_clip. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index"]},
        "handler": lambda a: live_bridge_remove_clip(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
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
    "premiere_agent_import_media": {
        "description": "Import a media file (e.g. a rendered motion graphic) into a live Premiere project/sequence at an optional time/track. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "media_path": {"type": "string"}, "time_s": {"type": "number"}, "track": {"type": "string"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "media_path"]},
        "handler": lambda a: live_bridge_import_media(a["sequence_id"], a["media_path"], time_s=a.get("time_s"), track=a.get("track"), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_queue_export": {
        "description": "Queue a live Premiere sequence/range export. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "output_path": {"type": "string"}, "range_start_s": {"type": "number"}, "range_end_s": {"type": "number"}, "preset": {"type": "string", "default": "match_source_h264"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "output_path"]},
        "handler": lambda a: live_bridge_queue_export(a["sequence_id"], a["output_path"], range_start_s=a.get("range_start_s"), range_end_s=a.get("range_end_s"), preset=a.get("preset", "match_source_h264"), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_create_orchestrator_job": {
        "description": "Write a JSON orchestrator job manifest (plan only; never touches Premiere). Claude Desktop reads this back to decide which bounded tools to call, in what order, with which safety gates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_type": {"type": "string", "enum": JOB_TYPES},
                "sequence_id": {"type": "string"},
                "output_path": {"type": "string", "description": "Path where the JSON orchestrator manifest should be written; this is not a Premiere export path."},
                "requested_outputs": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["job_type", "sequence_id", "output_path"],
        },
        "handler": lambda a: create_orchestrator_job_tool(
            a["job_type"],
            a["sequence_id"],
            a["output_path"],
            requested_outputs=a.get("requested_outputs"),
            notes=a.get("notes"),
            overwrite=a.get("overwrite", False),
        ),
    },
    "premiere_agent_verify_orchestrator_job": {
        "description": "Read-only check that an orchestrator job manifest requires a sequence backup first, lists no destructive actions, and has requires_backup + a verification placeholder on every live-write task.",
        "inputSchema": {"type": "object", "properties": {"manifest_path": {"type": "string"}}, "required": ["manifest_path"]},
        "handler": lambda a: verify_orchestrator_job_tool(a["manifest_path"]),
    },
}


def _resource_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, sort_keys=True)


def _safety_policy_resource() -> dict[str, Any]:
    return {
        "policy": SAFETY_POLICY,
        "verification_required": VERIFICATION_METHODS,
        "live_write_tools_require": ["confirm=true", "backup_sequence_id (for timeline-affecting writes)"],
        "duplicate_sequence_first": "Every timeline-affecting write must be preceded by premiere_agent_duplicate_sequence(confirm=true) and must carry the returned backup_sequence_id on the write call.",
        "verify_after_every_write": "Every write must be followed by a readback: premiere_agent_get_sequence_structure, premiere_agent_list_markers, premiere_agent_export_review_frames, or a rendered/exported file check.",
        "destructive_operations": "Not supported by any exposed tool. delete_clip, delete_track, and overwrite_sequence are explicitly unsupported.",
        "professional_fallback": "edl.json -> cut.xml/cut.fcpxml + master.srt remains the source-of-truth path if the live bridge is unavailable.",
    }


def _live_bridge_protocol_resource() -> dict[str, Any]:
    return live_bridge_protocol_spec()


def _orchestrator_contract_resource() -> dict[str, Any]:
    return {
        "roles": {
            "orchestrator": "Claude Desktop (or another host agent) is the orchestrator. It decides what to do, sequences tool calls, and confirms intent with the user.",
            "premiere_agent_mcp": "This MCP server is a bounded tool/resource/prompt surface. It performs only the specific action a tool call asks for; it never plans or chains actions on its own.",
            "cep_uxp_bridge": "The CEP/UXP Premiere panel is a local transport only. It executes the single bridge action it is sent and returns the result; it does not decide what to do next.",
        },
        "non_negotiables": [
            "The CEP/UXP panel must never become the decision-making orchestrator.",
            "Live writes are dry-run by default; a write only executes with confirm=true.",
            "Timeline-affecting writes require backup_sequence_id from premiere_agent_duplicate_sequence.",
            "Every live write must be followed by verification: snapshot, marker readback, review frames/contact sheet, or a rendered/exported file check.",
            "No destructive Premiere operations are exposed.",
            "edl.json -> cut.xml/cut.fcpxml + master.srt remains the professional fallback/source-of-truth path.",
        ],
        "how_to_orchestrate": [
            "Discover repeatable workflows via prompts/list and prompts/get (see config://premiere-agent/workflow-skills).",
            "Read config://premiere-agent/safety-policy and config://premiere-agent/live-bridge-protocol before planning writes.",
            "Optionally call premiere_agent_create_orchestrator_job to record a plan-only JSON manifest before touching Premiere.",
            "Call bounded tools directly for every actual mutation; never assume a tool call implies any follow-up action.",
        ],
    }


def _workflow_skills_resource() -> dict[str, Any]:
    return {
        "prompts": [
            {
                "name": name,
                "description": spec["description"],
                "arguments": [a["name"] for a in spec["arguments"]],
            }
            for name, spec in PROMPTS.items()
        ],
        "job_types": JOB_TYPES,
        "manifest_tool": "premiere_agent_create_orchestrator_job",
        "manifest_verify_tool": "premiere_agent_verify_orchestrator_job",
        "note": "Prompts package a repeatable orchestration sequence for Claude Desktop; they do not perform any action themselves. Actual mutations always go through the bounded premiere_agent_* tools.",
    }


RESOURCES: dict[str, dict[str, Any]] = {
    "config://premiere-agent/safety-policy": {
        "name": "safety-policy",
        "description": "Static safety policy: dry-run defaults, confirm/backup requirements, verification requirements, and the file-based fallback path.",
        "mimeType": "application/json",
        "handler": _safety_policy_resource,
    },
    "config://premiere-agent/live-bridge-protocol": {
        "name": "live-bridge-protocol",
        "description": "JSON-RPC protocol contract a CEP/UXP Premiere bridge implements: read/write/destructive action lists and write policy.",
        "mimeType": "application/json",
        "handler": _live_bridge_protocol_resource,
    },
    "config://premiere-agent/orchestrator-contract": {
        "name": "orchestrator-contract",
        "description": "Who is the orchestrator (Claude Desktop), what this MCP server is (bounded tool surface), and what the CEP/UXP panel is (local transport only).",
        "mimeType": "application/json",
        "handler": _orchestrator_contract_resource,
    },
    "config://premiere-agent/workflow-skills": {
        "name": "workflow-skills",
        "description": "Index of the repeatable orchestration prompts this server exposes, and the orchestrator job manifest tools.",
        "mimeType": "application/json",
        "handler": _workflow_skills_resource,
    },
}


def _orchestrator_directives() -> str:
    return (
        "You are the orchestrator for this task. The premiere-agent MCP server is a bounded tool/resource/prompt "
        "surface, not a decision-maker: it only performs the exact action each tool call asks for. The CEP/UXP "
        "Premiere panel is a local transport only. Claude Desktop (you) must:\n"
        "1. Call premiere_agent_verify_premiere_connection (read-only) to confirm the Premiere bridge is reachable "
        "before doing anything else.\n"
        "2. Call premiere_agent_get_sequence_structure (and premiere_agent_list_markers if relevant) to inspect the "
        "active/target sequence.\n"
        "3. State the exact project/sequence and the intended operation to the user and get explicit confirmation "
        "before any write.\n"
        "4. Call premiere_agent_duplicate_sequence with confirm=true to back up the sequence before any "
        "timeline-affecting mutation, and capture the returned backup_sequence_id.\n"
        "5. Perform only the specific bounded tool calls listed for this job, passing backup_sequence_id and "
        "confirm=true on every live write.\n"
        "6. After every live write, call a verification tool (premiere_agent_get_sequence_structure, "
        "premiere_agent_list_markers, premiere_agent_export_review_frames, or check the rendered/exported file) to "
        "confirm the write actually happened.\n"
        "7. Report back exact sequence ids, marker ids, file paths, time ranges, and the verification evidence you "
        "collected. Never claim success without it.\n"
        "8. If premiere_agent_verify_premiere_connection or any bridge call fails or the bridge is unavailable, fall "
        "back to the file-based path: edl.json -> cut.xml/cut.fcpxml + master.srt."
    )


def _prompt_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": {"type": "text", "text": text}}


def _talking_head_prompt(args: dict[str, Any]) -> list[dict[str, Any]]:
    sequence_id = args.get("sequence_id") or "<TARGET_SEQUENCE_ID>"
    notes = args.get("notes") or ""
    text = (
        f"Task: run a talking-head editorial pass on Premiere sequence {sequence_id}.\n\n"
        + _orchestrator_directives()
        + "\n\nJob-specific bounded tool sequence:\n"
        "- premiere_agent_verify_premiere_connection\n"
        "- premiere_agent_get_sequence_structure(sequence_id)\n"
        "- premiere_agent_duplicate_sequence(sequence_id, confirm=true) -> backup_sequence_id\n"
        "- premiere_agent_add_editorial_markers(sequence_id, notes, backup_sequence_id, confirm=true) for filler/retake/review notes\n"
        "- premiere_agent_import_captions(sequence_id, caption_path, backup_sequence_id, confirm=true) if captions are requested\n"
        "- premiere_agent_export_review_frames(sequence_id, output_dir, backup_sequence_id, confirm=true) to produce a contact sheet\n"
        "- premiere_agent_queue_export(sequence_id, output_path, backup_sequence_id, confirm=true) for the final deliverable\n"
        "- premiere_agent_list_markers(sequence_id) and/or premiere_agent_get_sequence_structure(sequence_id) to verify readback\n\n"
        "Optionally call premiere_agent_create_orchestrator_job(job_type=\"talking_head\", ...) first to record the plan as a manifest.\n"
        f"Additional notes from the user: {notes}"
    )
    return [_prompt_message(text)]


def _batch_export_prompt(args: dict[str, Any]) -> list[dict[str, Any]]:
    sequence_id = args.get("sequence_id") or "<TARGET_SEQUENCE_ID>"
    output_dir = args.get("output_dir") or "<OUTPUT_DIR>"
    text = (
        f"Task: batch-export shorts/ranges from Premiere sequence {sequence_id} into {output_dir}.\n\n"
        + _orchestrator_directives()
        + "\n\nJob-specific bounded tool sequence:\n"
        "- premiere_agent_verify_premiere_connection\n"
        "- premiere_agent_get_sequence_structure(sequence_id)\n"
        "- premiere_agent_batch_export_plan(items, output_dir) to produce a naming-safe export manifest\n"
        "- premiere_agent_duplicate_sequence(sequence_id, confirm=true) -> backup_sequence_id\n"
        "- premiere_agent_queue_export(sequence_id, output_path, backup_sequence_id, confirm=true) for each planned item\n"
        "- verify every exported file exists at its reported output path before reporting success\n\n"
        "Optionally call premiere_agent_create_orchestrator_job(job_type=\"batch_export\", ...) first to record the plan as a manifest."
    )
    return [_prompt_message(text)]


def _motion_graphic_prompt(args: dict[str, Any]) -> list[dict[str, Any]]:
    sequence_id = args.get("sequence_id") or "<TARGET_SEQUENCE_ID>"
    range_start_s = args.get("range_start_s", "<RANGE_START_S>")
    range_end_s = args.get("range_end_s", "<RANGE_END_S>")
    text = (
        f"Task: plan/produce a motion graphic for sequence {sequence_id}, range {range_start_s}s-{range_end_s}s.\n\n"
        + _orchestrator_directives()
        + "\n\nJob-specific bounded tool sequence:\n"
        "- premiere_agent_verify_premiere_connection\n"
        "- premiere_agent_get_sequence_structure(sequence_id)\n"
        "- premiere_agent_duplicate_sequence(sequence_id, confirm=true) -> backup_sequence_id\n"
        "- premiere_agent_export_review_frames(sequence_id, output_dir, range_start_s, range_end_s, backup_sequence_id, confirm=true) "
        "for reference stills of the target range\n"
        "- render the graphic asset offline (Remotion/Hyperframes/Manim/etc); this is not a bounded Premiere tool call\n"
        "- premiere_agent_import_media(sequence_id, media_path, time_s, track, backup_sequence_id, confirm=true) to bring the "
        "rendered graphic into the sequence at the target range\n"
        "- verify with premiere_agent_get_sequence_structure/premiere_agent_export_review_frames that the graphic is present at "
        "the intended range\n\n"
        "Optionally call premiere_agent_create_orchestrator_job(job_type=\"motion_graphic\", ...) first to record the plan as a manifest."
    )
    return [_prompt_message(text)]


def _caption_pass_prompt(args: dict[str, Any]) -> list[dict[str, Any]]:
    sequence_id = args.get("sequence_id") or "<TARGET_SEQUENCE_ID>"
    caption_path = args.get("caption_path") or "<CAPTION_PATH.srt>"
    text = (
        f"Task: import captions from {caption_path} into Premiere sequence {sequence_id}.\n\n"
        + _orchestrator_directives()
        + "\n\nJob-specific bounded tool sequence:\n"
        "- premiere_agent_verify_premiere_connection\n"
        "- premiere_agent_get_sequence_structure(sequence_id)\n"
        "- premiere_agent_duplicate_sequence(sequence_id, confirm=true) -> backup_sequence_id\n"
        "- premiere_agent_import_captions(sequence_id, caption_path, backup_sequence_id, confirm=true)\n"
        "- premiere_agent_export_review_frames(sequence_id, output_dir, backup_sequence_id, confirm=true) to visually confirm "
        "burned-in/attached captions\n"
        "- premiere_agent_list_markers/premiere_agent_get_sequence_structure to verify readback\n\n"
        "Optionally call premiere_agent_create_orchestrator_job(job_type=\"caption_pass\", ...) first to record the plan as a manifest."
    )
    return [_prompt_message(text)]


def _sequence_qa_prompt(args: dict[str, Any]) -> list[dict[str, Any]]:
    sequence_id = args.get("sequence_id") or "<TARGET_SEQUENCE_ID>"
    text = (
        f"Task: run a read-only QA pass on Premiere sequence {sequence_id}. This job performs no timeline mutation.\n\n"
        + _orchestrator_directives()
        + "\n\nJob-specific bounded tool sequence:\n"
        "- premiere_agent_verify_premiere_connection\n"
        "- premiere_agent_get_sequence_structure(sequence_id)\n"
        "- premiere_agent_list_markers(sequence_id)\n"
        "- premiere_agent_export_review_frames(sequence_id, output_dir, backup_sequence_id, confirm=true) for an optional spot-check "
        "contact sheet (still a live write: duplicate the sequence first if you use it)\n"
        "- report findings only; do not mutate the sequence in this job\n\n"
        "Optionally call premiere_agent_create_orchestrator_job(job_type=\"sequence_qa\", ...) first to record the plan as a manifest."
    )
    return [_prompt_message(text)]


PROMPTS: dict[str, dict[str, Any]] = {
    "premiere_orchestrator_talking_head": {
        "description": "Orchestrate a talking-head editorial pass: inspect, backup, editorial markers, captions, review frames, final export.",
        "arguments": [
            {"name": "sequence_id", "description": "Target Premiere sequence id, if already known.", "required": False},
            {"name": "notes", "description": "Any extra editorial notes/instructions from the user.", "required": False},
        ],
        "handler": _talking_head_prompt,
    },
    "premiere_orchestrator_batch_export": {
        "description": "Orchestrate a batch export of shorts/ranges from a sequence into an output directory.",
        "arguments": [
            {"name": "sequence_id", "description": "Target Premiere sequence id, if already known.", "required": False},
            {"name": "output_dir", "description": "Directory to export batch items into.", "required": False},
        ],
        "handler": _batch_export_prompt,
    },
    "premiere_orchestrator_motion_graphic_for_range": {
        "description": "Orchestrate producing a motion graphic for a specific in/out range on a sequence.",
        "arguments": [
            {"name": "sequence_id", "description": "Target Premiere sequence id, if already known.", "required": False},
            {"name": "range_start_s", "description": "Range start in seconds.", "required": False},
            {"name": "range_end_s", "description": "Range end in seconds.", "required": False},
        ],
        "handler": _motion_graphic_prompt,
    },
    "premiere_orchestrator_caption_pass": {
        "description": "Orchestrate importing an SRT/VTT caption file into a sequence and verifying it visually.",
        "arguments": [
            {"name": "sequence_id", "description": "Target Premiere sequence id, if already known.", "required": False},
            {"name": "caption_path", "description": "Path to the .srt/.vtt caption file.", "required": False},
        ],
        "handler": _caption_pass_prompt,
    },
    "premiere_orchestrator_sequence_qa": {
        "description": "Orchestrate a read-only QA pass over a sequence: structure, markers, optional spot-check frames.",
        "arguments": [
            {"name": "sequence_id", "description": "Target Premiere sequence id, if already known.", "required": False},
        ],
        "handler": _sequence_qa_prompt,
    },
}


def handle(method: str, params: dict[str, Any] | None) -> Any:
    params = params or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
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
    if method == "resources/list":
        return {
            "resources": [
                {"uri": uri, "name": spec["name"], "description": spec["description"], "mimeType": spec["mimeType"]}
                for uri, spec in RESOURCES.items()
            ]
        }
    if method == "resources/read":
        uri = params.get("uri")
        if uri not in RESOURCES:
            raise McpError(-32602, f"unknown resource: {uri}")
        spec = RESOURCES[uri]
        text = _resource_text(spec["handler"]())
        return {"contents": [{"uri": uri, "mimeType": spec["mimeType"], "text": text}]}
    if method == "prompts/list":
        return {
            "prompts": [
                {"name": name, "description": spec["description"], "arguments": spec["arguments"]}
                for name, spec in PROMPTS.items()
            ]
        }
    if method == "prompts/get":
        name = params.get("name")
        if name not in PROMPTS:
            raise McpError(-32602, f"unknown prompt: {name}")
        spec = PROMPTS[name]
        args = params.get("arguments") or {}
        return {"description": spec["description"], "messages": spec["handler"](args)}
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
