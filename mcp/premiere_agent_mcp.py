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


def live_bridge_execute_extendscript(
    code: str,
    *,
    timeout_s: float | None = None,
    bridge_url: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).execute_extendscript(code, timeout_s=timeout_s, dry_run=dry_run)


def live_bridge_evaluate_expression(
    expression: str,
    *,
    bridge_url: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).evaluate_expression(expression, dry_run=dry_run)


def live_bridge_inspect_dom_object(
    object_path: str,
    *,
    max_depth: int = 1,
    bridge_url: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).inspect_dom_object(object_path, max_depth=max_depth, dry_run=dry_run)


def live_bridge_list_clip_effects(
    sequence_id: str, track_type: str, track_index: int, clip_index: int, *, bridge_url: str | None = None, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).list_clip_effects(sequence_id, track_type, track_index, clip_index, dry_run=dry_run)


def live_bridge_get_effect_properties(
    sequence_id: str, track_type: str, track_index: int, clip_index: int, effect_name: str, *, bridge_url: str | None = None, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_effect_properties(sequence_id, track_type, track_index, clip_index, effect_name, dry_run=dry_run)


def live_bridge_set_effect_property(
    sequence_id: str,
    track_type: str,
    track_index: int,
    clip_index: int,
    effect_name: str,
    property_name: str,
    value: float | str | bool,
    *,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_effect_property(
        sequence_id, track_type, track_index, clip_index, effect_name, property_name, value,
        backup_sequence_id=backup_sequence_id, confirm=confirm, dry_run=dry_run,
    )


def live_bridge_get_keyframes(
    sequence_id: str, track_type: str, track_index: int, clip_index: int, effect_name: str, property_name: str,
    *, bridge_url: str | None = None, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_keyframes(sequence_id, track_type, track_index, clip_index, effect_name, property_name, dry_run=dry_run)


def live_bridge_add_keyframe(
    sequence_id: str,
    track_type: str,
    track_index: int,
    clip_index: int,
    effect_name: str,
    property_name: str,
    time_s: float,
    value: float,
    *,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).add_keyframe(
        sequence_id, track_type, track_index, clip_index, effect_name, property_name, time_s, value,
        backup_sequence_id=backup_sequence_id, confirm=confirm, dry_run=dry_run,
    )


def live_bridge_remove_keyframe(
    sequence_id: str,
    track_type: str,
    track_index: int,
    clip_index: int,
    effect_name: str,
    property_name: str,
    time_s: float,
    *,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).remove_keyframe(
        sequence_id, track_type, track_index, clip_index, effect_name, property_name, time_s,
        backup_sequence_id=backup_sequence_id, confirm=confirm, dry_run=dry_run,
    )


def live_bridge_select_clips_by_name(
    sequence_id: str, name: str, *, track_type: str = "both", track_index: int | None = None,
    add_to_selection: bool = False, bridge_url: str | None = None, dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).select_clips_by_name(sequence_id, name, track_type=track_type, track_index=track_index, add_to_selection=add_to_selection, dry_run=dry_run)


def live_bridge_select_all_clips(
    sequence_id: str, *, track_type: str = "both", track_index: int | None = None, bridge_url: str | None = None, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).select_all_clips(sequence_id, track_type=track_type, track_index=track_index, dry_run=dry_run)


def live_bridge_deselect_all_clips(sequence_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).deselect_all_clips(sequence_id, dry_run=dry_run)


def live_bridge_select_clips_in_range(
    sequence_id: str, start_s: float, end_s: float, *, track_type: str = "both", track_index: int | None = None,
    bridge_url: str | None = None, dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).select_clips_in_range(sequence_id, start_s, end_s, track_type=track_type, track_index=track_index, dry_run=dry_run)


def live_bridge_select_clips_by_color(sequence_id: str, color_index: int, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).select_clips_by_color(sequence_id, color_index, dry_run=dry_run)


def live_bridge_invert_selection(sequence_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).invert_selection(sequence_id, dry_run=dry_run)


def live_bridge_select_disabled_clips(sequence_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).select_disabled_clips(sequence_id, dry_run=dry_run)


def live_bridge_copy_effect_values(
    sequence_id: str,
    source_track_type: str, source_track_index: int, source_clip_index: int,
    target_track_type: str, target_track_index: int, target_clip_index: int,
    effect_name: str,
    *,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).copy_effect_values(
        sequence_id, source_track_type, source_track_index, source_clip_index,
        target_track_type, target_track_index, target_clip_index, effect_name,
        backup_sequence_id=backup_sequence_id, confirm=confirm, dry_run=dry_run,
    )


def live_bridge_remove_effect_by_name(
    sequence_id: str, track_type: str, track_index: int, clip_index: int, effect_name: str,
    *, backup_sequence_id: str | None = None, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).remove_effect_by_name(sequence_id, track_type, track_index, clip_index, effect_name, backup_sequence_id=backup_sequence_id, confirm=confirm, dry_run=dry_run)


def live_bridge_set_blend_mode(
    sequence_id: str, track_type: str, track_index: int, clip_index: int, blend_mode: str,
    *, backup_sequence_id: str | None = None, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_blend_mode(sequence_id, track_type, track_index, clip_index, blend_mode, backup_sequence_id=backup_sequence_id, confirm=confirm, dry_run=dry_run)


def live_bridge_save_project(*, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).save_project(confirm=confirm, dry_run=dry_run)


def live_bridge_undo(*, count: int = 1, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).undo(count=count, confirm=confirm, dry_run=dry_run)


def live_bridge_set_active_sequence(sequence_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_active_sequence(sequence_id, dry_run=dry_run)


def live_bridge_create_bin(
    name: str, *, parent_bin: str | None = None, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).create_bin(name, parent_bin=parent_bin, confirm=confirm, dry_run=dry_run)


def live_bridge_delete_bin(bin_id: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).delete_bin(bin_id, confirm=confirm, dry_run=dry_run)


def live_bridge_rename_bin(
    bin_id: str, new_name: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).rename_bin(bin_id, new_name, confirm=confirm, dry_run=dry_run)


def live_bridge_move_item_to_bin(
    item_id: str, target_bin: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).move_item_to_bin(item_id, target_bin, confirm=confirm, dry_run=dry_run)


def live_bridge_get_item_info(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_item_info(item_id, dry_run=dry_run)


def live_bridge_select_item(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).select_item(item_id, dry_run=dry_run)


def live_bridge_check_offline_media(*, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).check_offline_media(dry_run=dry_run)


def live_bridge_get_metadata(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_metadata(item_id, dry_run=dry_run)


def live_bridge_set_metadata(
    item_id: str, field_name: str, value: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_metadata(item_id, field_name, value, confirm=confirm, dry_run=dry_run)


def live_bridge_set_color_label(
    item_id: str, color_index: int, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_color_label(item_id, color_index, confirm=confirm, dry_run=dry_run)


def live_bridge_get_color_label(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_color_label(item_id, dry_run=dry_run)


def live_bridge_get_footage_interpretation(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_footage_interpretation(item_id, dry_run=dry_run)


def live_bridge_set_footage_interpretation(
    item_id: str,
    *,
    frame_rate: float | None = None,
    pixel_aspect_ratio: float | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_footage_interpretation(
        item_id, frame_rate=frame_rate, pixel_aspect_ratio=pixel_aspect_ratio, confirm=confirm, dry_run=dry_run
    )


def live_bridge_get_xmp_metadata(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_xmp_metadata(item_id, dry_run=dry_run)


def live_bridge_set_xmp_metadata(
    item_id: str, xmp_xml: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_xmp_metadata(item_id, xmp_xml, confirm=confirm, dry_run=dry_run)


def live_bridge_get_color_space(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_color_space(item_id, dry_run=dry_run)


def live_bridge_import_media_files(
    file_paths: list[str],
    *,
    target_bin: str | None = None,
    suppress_ui: bool = True,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).import_media_files(
        file_paths, target_bin=target_bin, suppress_ui=suppress_ui, confirm=confirm, dry_run=dry_run
    )


def live_bridge_import_folder(
    folder_path: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).import_folder(folder_path, confirm=confirm, dry_run=dry_run)


def live_bridge_relink_media(
    item_id: str, new_path: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).relink_media(item_id, new_path, confirm=confirm, dry_run=dry_run)


def live_bridge_refresh_media(
    item_id: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).refresh_media(item_id, confirm=confirm, dry_run=dry_run)


def live_bridge_set_offline(
    item_id: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_offline(item_id, confirm=confirm, dry_run=dry_run)


def live_bridge_has_proxy(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).has_proxy(item_id, dry_run=dry_run)


def live_bridge_detach_proxy(
    item_id: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).detach_proxy(item_id, confirm=confirm, dry_run=dry_run)


def live_bridge_set_override_frame_rate(
    item_id: str, frame_rate: float, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_override_frame_rate(item_id, frame_rate, confirm=confirm, dry_run=dry_run)


def live_bridge_set_override_pixel_aspect_ratio(
    item_id: str,
    numerator: float,
    denominator: float,
    *,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_override_pixel_aspect_ratio(
        item_id, numerator, denominator, confirm=confirm, dry_run=dry_run
    )


def live_bridge_set_scale_to_frame_size(
    item_id: str, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_scale_to_frame_size(item_id, confirm=confirm, dry_run=dry_run)


def live_bridge_set_start_time(
    item_id: str, start_seconds: float, *, bridge_url: str | None = None, confirm: bool = False, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_start_time(item_id, start_seconds, confirm=confirm, dry_run=dry_run)


def live_bridge_open_in_source(item_id: str, *, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).open_in_source(item_id, dry_run=dry_run)


def live_bridge_close_source_monitor(*, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).close_source_monitor(dry_run=dry_run)


def live_bridge_close_all_source_clips(*, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).close_all_source_clips(dry_run=dry_run)


def live_bridge_set_source_in_out(
    *, in_seconds: float | None = None, out_seconds: float | None = None, bridge_url: str | None = None, dry_run: bool = True
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).set_source_in_out(in_seconds=in_seconds, out_seconds=out_seconds, dry_run=dry_run)


def live_bridge_insert_from_source(
    sequence_id: str,
    *,
    video_track_index: int = 0,
    audio_track_index: int = 0,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).insert_from_source(
        sequence_id,
        video_track_index=video_track_index,
        audio_track_index=audio_track_index,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_overwrite_from_source(
    sequence_id: str,
    *,
    video_track_index: int = 0,
    audio_track_index: int = 0,
    backup_sequence_id: str | None = None,
    bridge_url: str | None = None,
    confirm: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).overwrite_from_source(
        sequence_id,
        video_track_index=video_track_index,
        audio_track_index=audio_track_index,
        backup_sequence_id=backup_sequence_id,
        confirm=confirm,
        dry_run=dry_run,
    )


def live_bridge_get_source_monitor_info(*, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    return PremiereLiveBridge(bridge_url).get_source_monitor_info(dry_run=dry_run)


_RECOVERY_UNSAVED_CHANGES_REASON = "Premiere's documented scripting APIs do not expose the current dirty/unsaved flag."
_RECOVERY_AUTO_SAVE_DIR_NAMES = ("Adobe Premiere Pro Auto-Save", "Premiere Pro Auto-Save")


def live_bridge_inspect_project_recovery(*, bridge_url: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    """Read-only: list candidate .prproj auto-save/backup files for the open project.

    Never opens or restores anything -- see automatic_restore_supported below.
    """
    if dry_run:
        return {"ok": True, "dry_run": True}
    path_resp = PremiereLiveBridge(bridge_url).evaluate_expression("app.project.path", dry_run=False)
    inner = path_resp.get("response", {})
    if not path_resp.get("ok") or not inner.get("ok"):
        return {"ok": False, "error": inner.get("error") or "Could not read app.project.path"}
    project_path_str = inner.get("value")
    if not project_path_str:
        return {"ok": False, "error": "No project is currently open"}
    project_path = Path(project_path_str)
    stem = project_path.stem
    project_dir = project_path.parent
    candidate_dirs = [project_dir] + [project_dir / name for name in _RECOVERY_AUTO_SAVE_DIR_NAMES]
    candidates: list[dict[str, Any]] = []
    for directory in candidate_dirs:
        if not directory.is_dir():
            continue
        for f in directory.glob("*.prproj"):
            if not f.is_file() or not f.name.startswith(stem):
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            candidates.append({"path": str(f), "directory": str(directory), "modified_epoch_s": stat.st_mtime, "size_bytes": stat.st_size})
    candidates.sort(key=lambda c: c["modified_epoch_s"], reverse=True)
    return {
        "ok": True,
        "project_path": str(project_path),
        "candidates": candidates,
        "unsaved_changes": {"reason": _RECOVERY_UNSAVED_CHANGES_REASON},
        "recovery": {
            "automatic_restore_supported": False,
            "guidance": "This tool only lists candidate files, newest first. To restore, use Premiere's File > Open Recent or File > Open manually.",
        },
    }


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
    "premiere_agent_execute_extendscript": {
        "description": "Escape hatch: run arbitrary ES3 ExtendScript in Premiere's already-loaded engine for anything not covered by a fixed tool. Code runs as the body of a function and may `return` a plain value (JSON-serialized back). Has full access to every paXxx helper already defined in extendscript_bridge.jsx (paFindSequence, paSequenceId, paClipSummary, etc.) since it evals in that same file's scope. DANGEROUS: carries the same risk as hand-editing that file — a try/catch here only protects against thrown JS exceptions, NOT against passing a wrong native object type into a method with a fixed signature, which can crash the whole Premiere process (this happened once during development of move_clip). Never guess at native method argument types; prefer read-only property access when exploring; there is no confirm/backup_sequence_id gate here because arbitrary code can't be statically classified as safe or not — the caller is fully responsible for that judgment on every call, the same as if editing the .jsx directly.",
        "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}, "timeout_s": {"type": "number"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["code"]},
        "handler": lambda a: live_bridge_execute_extendscript(a["code"], timeout_s=a.get("timeout_s"), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_evaluate_expression": {
        "description": "Evaluate a single read-focused ExtendScript expression (e.g. 'app.version', 'app.project.activeSequence.videoTracks.numTracks') and return its value. For a multi-statement script, use execute_extendscript instead.",
        "inputSchema": {"type": "object", "properties": {"expression": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["expression"]},
        "handler": lambda a: live_bridge_evaluate_expression(a["expression"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_inspect_dom_object": {
        "description": "Read-only: list a Premiere DOM object's properties (never calls methods) for API exploration/debugging, e.g. object_path='app.project.activeSequence.videoTracks[0].clips[0]'.",
        "inputSchema": {"type": "object", "properties": {"object_path": {"type": "string"}, "max_depth": {"type": "integer", "default": 1}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["object_path"]},
        "handler": lambda a: live_bridge_inspect_dom_object(a["object_path"], max_depth=a.get("max_depth", 1), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_list_clip_effects": {
        "description": "Read-only: list the effect components already applied to a clip (index + display/match name). Uses the public Component DOM, not QE.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index"]},
        "handler": lambda a: live_bridge_list_clip_effects(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_effect_properties": {
        "description": "Read-only: list an effect's properties (display name, current value, whether keyframes are supported/enabled) by effect display or match name (e.g. 'Motion', 'Opacity', 'Lumetri Color').",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "effect_name": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index", "effect_name"]},
        "handler": lambda a: live_bridge_get_effect_properties(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], a["effect_name"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_effect_property": {
        "description": "Set a value on an existing effect property (e.g. Opacity, Scale, Position on a clip's default Motion/Opacity components, or a Lumetri Color parameter). Only works on effects already present on the clip — this does not add a new effect. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "effect_name": {"type": "string"}, "property_name": {"type": "string"}, "value": {"type": ["number", "string", "boolean"]}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index", "effect_name", "property_name", "value"]},
        "handler": lambda a: live_bridge_set_effect_property(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], a["effect_name"], a["property_name"], a["value"], backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_keyframes": {
        "description": "Read-only: list keyframes (time_s + value) for an effect property, if it's time-varying.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "effect_name": {"type": "string"}, "property_name": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index", "effect_name", "property_name"]},
        "handler": lambda a: live_bridge_get_keyframes(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], a["effect_name"], a["property_name"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_add_keyframe": {
        "description": "Add a keyframe at time_s with the given value on an effect property, enabling time-varying if needed. Verifies via readback only (not render/playback). Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "effect_name": {"type": "string"}, "property_name": {"type": "string"}, "time_s": {"type": "number"}, "value": {"type": "number"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index", "effect_name", "property_name", "time_s", "value"]},
        "handler": lambda a: live_bridge_add_keyframe(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], a["effect_name"], a["property_name"], a["time_s"], a["value"], backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_remove_keyframe": {
        "description": "Remove the keyframe at time_s on an effect property. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "effect_name": {"type": "string"}, "property_name": {"type": "string"}, "time_s": {"type": "number"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index", "effect_name", "property_name", "time_s"]},
        "handler": lambda a: live_bridge_remove_keyframe(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], a["effect_name"], a["property_name"], a["time_s"], backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_select_clips_by_name": {
        "description": "Select all clips whose name contains the given substring (case-insensitive). Selection is UI-only editing state, not a structural mutation, so no confirm/backup gate.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "name": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio", "both"], "default": "both"}, "track_index": {"type": "integer"}, "add_to_selection": {"type": "boolean", "default": False}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "name"]},
        "handler": lambda a: live_bridge_select_clips_by_name(a["sequence_id"], a["name"], track_type=a.get("track_type", "both"), track_index=a.get("track_index"), add_to_selection=a.get("add_to_selection", False), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_select_all_clips": {
        "description": "Select all clips in the sequence, or all clips on one track.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio", "both"], "default": "both"}, "track_index": {"type": "integer"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_select_all_clips(a["sequence_id"], track_type=a.get("track_type", "both"), track_index=a.get("track_index"), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_deselect_all_clips": {
        "description": "Deselect all clips in the sequence.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_deselect_all_clips(a["sequence_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_select_clips_in_range": {
        "description": "Select all clips overlapping a time range.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "start_s": {"type": "number"}, "end_s": {"type": "number"}, "track_type": {"type": "string", "enum": ["video", "audio", "both"], "default": "both"}, "track_index": {"type": "integer"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "start_s", "end_s"]},
        "handler": lambda a: live_bridge_select_clips_in_range(a["sequence_id"], a["start_s"], a["end_s"], track_type=a.get("track_type", "both"), track_index=a.get("track_index"), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_select_clips_by_color": {
        "description": "Select all clips whose source project item has the given color label index (0=Violet .. 15=Yellow).",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "color_index": {"type": "integer"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "color_index"]},
        "handler": lambda a: live_bridge_select_clips_by_color(a["sequence_id"], a["color_index"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_invert_selection": {
        "description": "Invert the current clip selection.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_invert_selection(a["sequence_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_select_disabled_clips": {
        "description": "Select all disabled (unchecked) clips in the sequence.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_select_disabled_clips(a["sequence_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_copy_effect_values": {
        "description": "Copy an effect's property values from one clip to another (both clips must already have that effect). Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "source_track_type": {"type": "string", "enum": ["video", "audio"]}, "source_track_index": {"type": "integer"}, "source_clip_index": {"type": "integer"}, "target_track_type": {"type": "string", "enum": ["video", "audio"]}, "target_track_index": {"type": "integer"}, "target_clip_index": {"type": "integer"}, "effect_name": {"type": "string"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "source_track_type", "source_track_index", "source_clip_index", "target_track_type", "target_track_index", "target_clip_index", "effect_name"]},
        "handler": lambda a: live_bridge_copy_effect_values(a["sequence_id"], a["source_track_type"], a["source_track_index"], a["source_clip_index"], a["target_track_type"], a["target_track_index"], a["target_clip_index"], a["effect_name"], backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_remove_effect_by_name": {
        "description": "Remove all instances of an effect from a clip by display name, via Component.remove() — preflights every match first so a component this Premiere build can't remove causes no partial removal. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "effect_name": {"type": "string"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index", "effect_name"]},
        "handler": lambda a: live_bridge_remove_effect_by_name(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], a["effect_name"], backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_blend_mode": {
        "description": "UNVERIFIED on Premiere 26.3.2: setting 'Multiply' was confirmed live to visually show as 'Darker Color' instead, and this build exposes two same-named 'Blend Mode' properties on Opacity, only the first of which is written. Do not trust the name->value mapping; check Effect Controls after calling. Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "track_type": {"type": "string", "enum": ["video", "audio"]}, "track_index": {"type": "integer"}, "clip_index": {"type": "integer"}, "blend_mode": {"type": "string", "enum": ["Normal", "Dissolve", "Darken", "Multiply", "Color Burn", "Linear Burn", "Darker Color", "Lighten", "Screen", "Color Dodge", "Linear Dodge", "Lighter Color", "Overlay", "Soft Light", "Hard Light", "Vivid Light", "Linear Light", "Pin Light", "Hard Mix", "Difference", "Exclusion", "Subtract", "Divide", "Hue", "Saturation", "Color", "Luminosity"]}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "track_type", "track_index", "clip_index", "blend_mode"]},
        "handler": lambda a: live_bridge_set_blend_mode(a["sequence_id"], a["track_type"], a["track_index"], a["clip_index"], a["blend_mode"], backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_save_project": {
        "description": "Save the current Premiere project to disk. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_save_project(bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_undo": {
        "description": "Undo the last action(s) in Premiere. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"count": {"type": "integer", "default": 1}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_undo(count=a.get("count", 1), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_active_sequence": {
        "description": "Make a sequence active/focused. UI focus state, not a structural mutation, so no confirm gate.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_set_active_sequence(a["sequence_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_create_bin": {
        "description": "Create a new bin (folder) in the Project panel. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "parent_bin": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["name"]},
        "handler": lambda a: live_bridge_create_bin(a["name"], parent_bin=a.get("parent_bin"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_delete_bin": {
        "description": "Delete a bin from the Project panel by name or node ID. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"bin_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["bin_id"]},
        "handler": lambda a: live_bridge_delete_bin(a["bin_id"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_rename_bin": {
        "description": "Rename a bin. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"bin_id": {"type": "string"}, "new_name": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["bin_id", "new_name"]},
        "handler": lambda a: live_bridge_rename_bin(a["bin_id"], a["new_name"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_move_item_to_bin": {
        "description": "Move a project item into a different bin. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "target_bin": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "target_bin"]},
        "handler": lambda a: live_bridge_move_item_to_bin(a["item_id"], a["target_bin"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_item_info": {
        "description": "Read-only: type/status info about a project item (sequence/multicam/merged-clip/offline/proxy/media path).",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_get_item_info(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_select_item": {
        "description": "Select a project item in the Project panel. UI state, not a structural mutation.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_select_item(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_check_offline_media": {
        "description": "Read-only: recursively scan the project for offline (missing) media items.",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_check_offline_media(bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_metadata": {
        "description": "Read-only: get project and XMP metadata for a project item.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_get_metadata(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_metadata": {
        "description": "Set a project metadata field (e.g. 'Column.Intrinsic.Description') on a project item. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "field_name": {"type": "string"}, "value": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "field_name", "value"]},
        "handler": lambda a: live_bridge_set_metadata(a["item_id"], a["field_name"], a["value"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_color_label": {
        "description": "Set the Project panel color label on an item (0=Violet, 1=Iris, 2=Caribbean, 3=Lavender, 4=Cerulean, 5=Forest, 6=Rose, 7=Mango, 8=Purple, 9=Blue, 10=Teal, 11=Magenta, 12=Tan, 13=Green, 14=Brown, 15=Yellow). Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "color_index": {"type": "integer", "minimum": 0, "maximum": 15}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "color_index"]},
        "handler": lambda a: live_bridge_set_color_label(a["item_id"], a["color_index"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_color_label": {
        "description": "Read-only: get a project item's color label index.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_get_color_label(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_footage_interpretation": {
        "description": "Read-only: get footage interpretation (frame rate, pixel aspect ratio, alpha, field type) for a project item.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_get_footage_interpretation(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_footage_interpretation": {
        "description": "Override footage interpretation (frame rate and/or pixel aspect ratio) for a project item. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "frame_rate": {"type": "number"}, "pixel_aspect_ratio": {"type": "number"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_set_footage_interpretation(a["item_id"], frame_rate=a.get("frame_rate"), pixel_aspect_ratio=a.get("pixel_aspect_ratio"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_xmp_metadata": {
        "description": "Read-only: get the raw XMP metadata blob (EXIF/IPTC/Dublin Core, etc.) for a project item.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_get_xmp_metadata(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_xmp_metadata": {
        "description": "Overwrite the entire XMP metadata blob on a project item with a complete XMP XML string. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "xmp_xml": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "xmp_xml"]},
        "handler": lambda a: live_bridge_set_xmp_metadata(a["item_id"], a["xmp_xml"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_color_space": {
        "description": "Read-only: get color space, original color space, and embedded/input LUT IDs for a project item.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_get_color_space(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_import_media_files": {
        "description": "Bulk-import one or more media files into the project (a bin, not the timeline). Distinct from premiere_agent_import_media, which inserts a single file into a sequence. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"file_paths": {"type": "array", "items": {"type": "string"}}, "target_bin": {"type": "string"}, "suppress_ui": {"type": "boolean", "default": True}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["file_paths"]},
        "handler": lambda a: live_bridge_import_media_files(a["file_paths"], target_bin=a.get("target_bin"), suppress_ui=a.get("suppress_ui", True), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_import_folder": {
        "description": "Import every file in a folder into the project root bin. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"folder_path": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["folder_path"]},
        "handler": lambda a: live_bridge_import_folder(a["folder_path"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_relink_media": {
        "description": "Relink an offline project item to a new file path. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "new_path": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "new_path"]},
        "handler": lambda a: live_bridge_relink_media(a["item_id"], a["new_path"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_refresh_media": {
        "description": "Refresh a project item to pick up changes to its source file on disk. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_refresh_media(a["item_id"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_offline": {
        "description": "Force a project item to offline status (unlinks its media). Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_set_offline(a["item_id"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_has_proxy": {
        "description": "Read-only: whether a project item has/can have a proxy, and its proxy path.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_has_proxy(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_detach_proxy": {
        "description": "Detach/remove the proxy from a project item. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_detach_proxy(a["item_id"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_override_frame_rate": {
        "description": "Override the interpreted frame rate of a project item (e.g. for image sequences or misinterpreted media). Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "frame_rate": {"type": "number"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "frame_rate"]},
        "handler": lambda a: live_bridge_set_override_frame_rate(a["item_id"], a["frame_rate"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_override_pixel_aspect_ratio": {
        "description": "Override the pixel aspect ratio of a project item (1:1 = square pixels). Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "numerator": {"type": "number"}, "denominator": {"type": "number"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "numerator", "denominator"]},
        "handler": lambda a: live_bridge_set_override_pixel_aspect_ratio(a["item_id"], a["numerator"], a["denominator"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_scale_to_frame_size": {
        "description": "Enable 'Scale to Frame Size' on a project item so it fills the sequence frame. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_set_scale_to_frame_size(a["item_id"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_start_time": {
        "description": "Set the start-time (timecode offset) of a project item. Requires confirm=true.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "start_seconds": {"type": "number"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id", "start_seconds"]},
        "handler": lambda a: live_bridge_set_start_time(a["item_id"], a["start_seconds"], bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_open_in_source": {
        "description": "Open a project item in the Source Monitor for preview/trimming. UI state, not a structural mutation.",
        "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}, "required": ["item_id"]},
        "handler": lambda a: live_bridge_open_in_source(a["item_id"], bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_close_source_monitor": {
        "description": "Close the clip currently open in the Source Monitor. UI state, not a structural mutation.",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_close_source_monitor(bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_close_all_source_clips": {
        "description": "Close all clips in the Source Monitor. UI state, not a structural mutation.",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_close_all_source_clips(bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_set_source_in_out": {
        "description": "Set in and/or out points on the clip currently open in the Source Monitor. UI state, not a structural mutation.",
        "inputSchema": {"type": "object", "properties": {"in_seconds": {"type": "number"}, "out_seconds": {"type": "number"}, "bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_set_source_in_out(in_seconds=a.get("in_seconds"), out_seconds=a.get("out_seconds"), bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_insert_from_source": {
        "description": "Insert edit: insert the clip currently open in the Source Monitor into a sequence at the playhead (shifts existing clips). Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "video_track_index": {"type": "integer", "default": 0}, "audio_track_index": {"type": "integer", "default": 0}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_insert_from_source(a["sequence_id"], video_track_index=a.get("video_track_index", 0), audio_track_index=a.get("audio_track_index", 0), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_overwrite_from_source": {
        "description": "Overwrite edit: overwrite into a sequence at the playhead with the clip currently open in the Source Monitor (replaces existing clips, does not shift). Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "video_track_index": {"type": "integer", "default": 0}, "audio_track_index": {"type": "integer", "default": 0}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id"]},
        "handler": lambda a: live_bridge_overwrite_from_source(a["sequence_id"], video_track_index=a.get("video_track_index", 0), audio_track_index=a.get("audio_track_index", 0), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_get_source_monitor_info": {
        "description": "Read-only: info about the clip currently loaded in the Source Monitor (name, media path, in/out points).",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_get_source_monitor_info(bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_inspect_project_recovery": {
        "description": "Read-only: list candidate .prproj auto-save/backup files for the currently open project (project directory + Adobe/Premiere Pro Auto-Save subfolders), newest first. Cannot report unsaved-changes state (not exposed by Premiere's scripting API) and never restores anything automatically -- restoring is always a manual File > Open in Premiere.",
        "inputSchema": {"type": "object", "properties": {"bridge_url": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}}},
        "handler": lambda a: live_bridge_inspect_project_recovery(bridge_url=a.get("bridge_url"), dry_run=a.get("dry_run", True)),
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
        "description": "Import a media file (e.g. a rendered motion graphic) into the project bin, then insert it into the sequence via Track.insertClip at `time_s` (default 0) on `track` (e.g. \"V1\", \"A2\"; plain track type + 1-based index, default video track 1). Requires confirm=true and backup_sequence_id.",
        "inputSchema": {"type": "object", "properties": {"sequence_id": {"type": "string"}, "media_path": {"type": "string"}, "time_s": {"type": "number"}, "track": {"type": "string"}, "backup_sequence_id": {"type": "string"}, "bridge_url": {"type": "string"}, "confirm": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": True}}, "required": ["sequence_id", "media_path"]},
        "handler": lambda a: live_bridge_import_media(a["sequence_id"], a["media_path"], time_s=a.get("time_s"), track=a.get("track"), backup_sequence_id=a.get("backup_sequence_id"), bridge_url=a.get("bridge_url"), confirm=a.get("confirm", False), dry_run=a.get("dry_run", True)),
    },
    "premiere_agent_queue_export": {
        "description": "Export a live Premiere sequence (or range) to a file via sequence.exportAsMediaDirect, auto-selecting a matching system preset (H.264 by default, or a name/format hint via `preset`). Synchronous: blocks until Premiere finishes writing the file or the render fails, and reports the real written path — not queued to Adobe Media Encoder despite the tool name. Requires confirm=true and backup_sequence_id.",
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
