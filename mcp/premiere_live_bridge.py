"""Adapter for a future live Premiere Pro bridge.

The adapter speaks a small JSON-over-HTTP protocol that a CEP/UXP Premiere
panel, local bridge app, or third-party Premiere MCP wrapper can implement.
It is safety-first: write operations are blocked unless the caller explicitly
confirms the side effect and either supplies a backup sequence id or asks the
adapter to create one first.

No Adobe APIs are imported here, so the module is testable on machines without
Premiere installed. With no bridge URL it can return dry-run payloads for
planning and orchestration tests.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DEFAULT_TIMEOUT_S = 15.0
DEFAULT_BRIDGE_ENV = "PREMIERE_AGENT_BRIDGE_URL"

READ_ACTIONS = {
    "status",
    "verify_premiere_connection",
    "get_active_project",
    "get_active_sequence",
    "snapshot_sequence",
    "get_sequence_structure",
    "list_markers",
}
WRITE_ACTIONS = {
    "duplicate_sequence",
    "add_marker",
    "add_editorial_markers",
    "import_media",
    "import_captions",
    "export_sequence_review_frames",
    "queue_export",
    "apply_basic_lumetri",
    "set_clip_transform",
}
DESTRUCTIVE_ACTIONS = {
    "delete_clip",
    "delete_track",
    "overwrite_sequence",
}


class BridgeError(RuntimeError):
    """Raised when the live bridge refuses or cannot complete a request."""


@dataclass(frozen=True)
class BridgeRequest:
    action: str
    payload: dict[str, Any]
    dry_run: bool = False


@dataclass(frozen=True)
class BridgeResponse:
    ok: bool
    action: str
    dry_run: bool
    request: dict[str, Any]
    response: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "dry_run": self.dry_run,
            "request": self.request,
            "response": self.response,
        }


class PremiereLiveBridge:
    """Small safety-gated client for a live Premiere bridge endpoint."""

    def __init__(self, url: str | None = None, *, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.url = (url or os.getenv(DEFAULT_BRIDGE_ENV) or "").rstrip("/")
        self.timeout_s = float(timeout_s)

    @property
    def connected(self) -> bool:
        return bool(self.url)

    def _request(self, req: BridgeRequest) -> BridgeResponse:
        body = {
            "jsonrpc": "2.0",
            "id": f"premiere-agent-{int(time.time() * 1000)}",
            "method": req.action,
            "params": req.payload,
        }
        if req.dry_run or not self.connected:
            return BridgeResponse(
                ok=True,
                action=req.action,
                dry_run=True,
                request=body,
                response={
                    "dry_run": True,
                    "bridge_url": self.url or None,
                    "message": "No live request sent. Set PREMIERE_AGENT_BRIDGE_URL or pass bridge_url to execute.",
                },
            )
        data = json.dumps(body).encode("utf-8")
        http_req = urllib.request.Request(
            self.url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            raise BridgeError(f"Premiere bridge request failed: {e}") from e
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise BridgeError(f"Premiere bridge returned non-JSON response: {raw[:500]}") from e
        if parsed.get("error"):
            raise BridgeError(f"Premiere bridge error: {parsed['error']}")
        result = parsed.get("result", parsed)
        # The transport succeeded (no JSON-RPC error), but the ExtendScript handler
        # itself may report ok=false (e.g. unsupported on this Premiere version) —
        # promote that so callers checking only the top-level ok don't miss it.
        result_ok = result.get("ok", True) if isinstance(result, dict) else True
        return BridgeResponse(ok=bool(result_ok), action=req.action, dry_run=False, request=body, response=result)

    def status(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("status", {}, dry_run=dry_run)).to_dict()

    def verify_premiere_connection(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("verify_premiere_connection", {}, dry_run=dry_run)).to_dict()

    def get_active_sequence(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_active_sequence", {}, dry_run=dry_run)).to_dict()

    def get_sequence_structure(self, sequence_id: str | None = None, *, dry_run: bool = False) -> dict[str, Any]:
        payload = {"sequence_id": sequence_id} if sequence_id else {}
        return self._request(BridgeRequest("get_sequence_structure", payload, dry_run=dry_run)).to_dict()

    def list_markers(self, sequence_id: str | None = None, *, dry_run: bool = False) -> dict[str, Any]:
        payload = {"sequence_id": sequence_id} if sequence_id else {}
        return self._request(BridgeRequest("list_markers", payload, dry_run=dry_run)).to_dict()

    def duplicate_sequence(self, sequence_id: str, backup_name: str | None = None, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("duplicate_sequence", confirm)
        payload = {"sequence_id": sequence_id, "backup_name": backup_name or f"{sequence_id}_AI_BACKUP"}
        return self._request(BridgeRequest("duplicate_sequence", payload, dry_run=dry_run)).to_dict()

    def add_marker(
        self,
        sequence_id: str,
        time_s: float,
        label: str,
        *,
        color: str = "red",
        comment: str = "",
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("add_marker", confirm)
            _require_backup("add_marker", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "time_s": float(time_s),
            "label": label,
            "color": color,
            "comment": comment,
        }
        return self._request(BridgeRequest("add_marker", payload, dry_run=dry_run)).to_dict()

    def add_editorial_markers(
        self,
        sequence_id: str,
        notes: list[dict[str, Any]],
        *,
        default_color: str = "red",
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("add_editorial_markers", confirm)
            _require_backup("add_editorial_markers", backup_sequence_id)
        if not isinstance(notes, list) or not notes:
            raise BridgeError("add_editorial_markers requires a non-empty notes list")
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "default_color": default_color,
            "notes": notes,
        }
        return self._request(BridgeRequest("add_editorial_markers", payload, dry_run=dry_run)).to_dict()

    def export_sequence_review_frames(
        self,
        sequence_id: str,
        output_dir: str,
        *,
        frame_count: int = 6,
        range_start_s: float | None = None,
        range_end_s: float | None = None,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("export_sequence_review_frames", confirm)
            _require_backup("export_sequence_review_frames", backup_sequence_id)
        out = Path(output_dir).expanduser().resolve()
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "output_dir": str(out),
            "frame_count": int(frame_count),
            "range_start_s": range_start_s,
            "range_end_s": range_end_s,
        }
        return self._request(BridgeRequest("export_sequence_review_frames", payload, dry_run=dry_run)).to_dict()

    def import_captions(
        self,
        sequence_id: str,
        caption_path: str,
        *,
        start_s: float = 0.0,
        caption_format: str = "subtitle",
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("import_captions", confirm)
            _require_backup("import_captions", backup_sequence_id)
        captions = Path(caption_path).expanduser().resolve()
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "caption_path": str(captions),
            "start_s": float(start_s),
            "caption_format": caption_format,
        }
        return self._request(BridgeRequest("import_captions", payload, dry_run=dry_run)).to_dict()

    def move_clip(
        self,
        sequence_id: str,
        track_type: str,
        from_track_index: int,
        clip_index: int,
        to_track_index: int,
        *,
        start_s: float | None = None,
        remove_original: bool = False,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("move_clip", confirm)
            _require_backup("move_clip", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "track_type": track_type,
            "from_track_index": int(from_track_index),
            "to_track_index": int(to_track_index),
            "clip_index": int(clip_index),
            "start_s": start_s,
            "remove_original": bool(remove_original),
        }
        return self._request(BridgeRequest("move_clip", payload, dry_run=dry_run)).to_dict()

    def remove_clip(
        self,
        sequence_id: str,
        track_type: str,
        track_index: int,
        clip_index: int,
        *,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("remove_clip", confirm)
            _require_backup("remove_clip", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "track_type": track_type,
            "track_index": int(track_index),
            "clip_index": int(clip_index),
        }
        return self._request(BridgeRequest("remove_clip", payload, dry_run=dry_run)).to_dict()

    def execute_extendscript(self, code: str, *, timeout_s: float | None = None, dry_run: bool = False) -> dict[str, Any]:
        payload = {"code": code}
        req = BridgeRequest("execute_extendscript", payload, dry_run=dry_run)
        if timeout_s is not None:
            self.timeout_s = float(timeout_s)
        return self._request(req).to_dict()

    def evaluate_expression(self, expression: str, *, dry_run: bool = False) -> dict[str, Any]:
        payload = {"expression": expression}
        return self._request(BridgeRequest("evaluate_expression", payload, dry_run=dry_run)).to_dict()

    def inspect_dom_object(self, object_path: str, *, max_depth: int = 1, dry_run: bool = False) -> dict[str, Any]:
        payload = {"object_path": object_path, "max_depth": int(max_depth)}
        return self._request(BridgeRequest("inspect_dom_object", payload, dry_run=dry_run)).to_dict()

    def list_clip_effects(
        self, sequence_id: str, track_type: str, track_index: int, clip_index: int, *, dry_run: bool = False
    ) -> dict[str, Any]:
        payload = {"sequence_id": sequence_id, "track_type": track_type, "track_index": int(track_index), "clip_index": int(clip_index)}
        return self._request(BridgeRequest("list_clip_effects", payload, dry_run=dry_run)).to_dict()

    def get_effect_properties(
        self, sequence_id: str, track_type: str, track_index: int, clip_index: int, effect_name: str, *, dry_run: bool = False
    ) -> dict[str, Any]:
        payload = {
            "sequence_id": sequence_id, "track_type": track_type, "track_index": int(track_index),
            "clip_index": int(clip_index), "effect_name": effect_name,
        }
        return self._request(BridgeRequest("get_effect_properties", payload, dry_run=dry_run)).to_dict()

    def set_effect_property(
        self,
        sequence_id: str,
        track_type: str,
        track_index: int,
        clip_index: int,
        effect_name: str,
        property_name: str,
        value: float | str | bool,
        *,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_effect_property", confirm)
            _require_backup("set_effect_property", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id, "track_type": track_type,
            "track_index": int(track_index), "clip_index": int(clip_index), "effect_name": effect_name,
            "property_name": property_name, "value": value,
        }
        return self._request(BridgeRequest("set_effect_property", payload, dry_run=dry_run)).to_dict()

    def get_keyframes(
        self, sequence_id: str, track_type: str, track_index: int, clip_index: int, effect_name: str, property_name: str, *, dry_run: bool = False
    ) -> dict[str, Any]:
        payload = {
            "sequence_id": sequence_id, "track_type": track_type, "track_index": int(track_index),
            "clip_index": int(clip_index), "effect_name": effect_name, "property_name": property_name,
        }
        return self._request(BridgeRequest("get_keyframes", payload, dry_run=dry_run)).to_dict()

    def add_keyframe(
        self,
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
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("add_keyframe", confirm)
            _require_backup("add_keyframe", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id, "track_type": track_type,
            "track_index": int(track_index), "clip_index": int(clip_index), "effect_name": effect_name,
            "property_name": property_name, "time_s": float(time_s), "value": value,
        }
        return self._request(BridgeRequest("add_keyframe", payload, dry_run=dry_run)).to_dict()

    def remove_keyframe(
        self,
        sequence_id: str,
        track_type: str,
        track_index: int,
        clip_index: int,
        effect_name: str,
        property_name: str,
        time_s: float,
        *,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("remove_keyframe", confirm)
            _require_backup("remove_keyframe", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id, "track_type": track_type,
            "track_index": int(track_index), "clip_index": int(clip_index), "effect_name": effect_name,
            "property_name": property_name, "time_s": float(time_s),
        }
        return self._request(BridgeRequest("remove_keyframe", payload, dry_run=dry_run)).to_dict()

    def select_clips_by_name(
        self, sequence_id: str, name: str, *, track_type: str = "both", track_index: int | None = None,
        add_to_selection: bool = False, dry_run: bool = False,
    ) -> dict[str, Any]:
        payload = {"sequence_id": sequence_id, "name": name, "track_type": track_type, "track_index": track_index, "add_to_selection": add_to_selection}
        return self._request(BridgeRequest("select_clips_by_name", payload, dry_run=dry_run)).to_dict()

    def select_all_clips(
        self, sequence_id: str, *, track_type: str = "both", track_index: int | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        payload = {"sequence_id": sequence_id, "track_type": track_type, "track_index": track_index}
        return self._request(BridgeRequest("select_all_clips", payload, dry_run=dry_run)).to_dict()

    def deselect_all_clips(self, sequence_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("deselect_all_clips", {"sequence_id": sequence_id}, dry_run=dry_run)).to_dict()

    def select_clips_in_range(
        self, sequence_id: str, start_s: float, end_s: float, *, track_type: str = "both", track_index: int | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        payload = {"sequence_id": sequence_id, "start_s": float(start_s), "end_s": float(end_s), "track_type": track_type, "track_index": track_index}
        return self._request(BridgeRequest("select_clips_in_range", payload, dry_run=dry_run)).to_dict()

    def select_clips_by_color(self, sequence_id: str, color_index: int, *, dry_run: bool = False) -> dict[str, Any]:
        payload = {"sequence_id": sequence_id, "color_index": int(color_index)}
        return self._request(BridgeRequest("select_clips_by_color", payload, dry_run=dry_run)).to_dict()

    def invert_selection(self, sequence_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("invert_selection", {"sequence_id": sequence_id}, dry_run=dry_run)).to_dict()

    def select_disabled_clips(self, sequence_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("select_disabled_clips", {"sequence_id": sequence_id}, dry_run=dry_run)).to_dict()

    def copy_effect_values(
        self,
        sequence_id: str,
        source_track_type: str, source_track_index: int, source_clip_index: int,
        target_track_type: str, target_track_index: int, target_clip_index: int,
        effect_name: str,
        *,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("copy_effect_values", confirm)
            _require_backup("copy_effect_values", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id,
            "source_track_type": source_track_type, "source_track_index": int(source_track_index), "source_clip_index": int(source_clip_index),
            "target_track_type": target_track_type, "target_track_index": int(target_track_index), "target_clip_index": int(target_clip_index),
            "effect_name": effect_name,
        }
        return self._request(BridgeRequest("copy_effect_values", payload, dry_run=dry_run)).to_dict()

    def remove_effect_by_name(
        self, sequence_id: str, track_type: str, track_index: int, clip_index: int, effect_name: str,
        *, backup_sequence_id: str | None = None, confirm: bool = False, dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("remove_effect_by_name", confirm)
            _require_backup("remove_effect_by_name", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id, "track_type": track_type,
            "track_index": int(track_index), "clip_index": int(clip_index), "effect_name": effect_name,
        }
        return self._request(BridgeRequest("remove_effect_by_name", payload, dry_run=dry_run)).to_dict()

    def set_blend_mode(
        self, sequence_id: str, track_type: str, track_index: int, clip_index: int, blend_mode: str,
        *, backup_sequence_id: str | None = None, confirm: bool = False, dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_blend_mode", confirm)
            _require_backup("set_blend_mode", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id, "track_type": track_type,
            "track_index": int(track_index), "clip_index": int(clip_index), "blend_mode": blend_mode,
        }
        return self._request(BridgeRequest("set_blend_mode", payload, dry_run=dry_run)).to_dict()

    def set_clip_transform(
        self,
        sequence_id: str,
        track_type: str,
        track_index: int,
        clip_index: int,
        *,
        scale: float | None = None,
        position: list[float] | None = None,
        rotation: float | None = None,
        range_start_s: float | None = None,
        range_end_s: float | None = None,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_clip_transform", confirm)
            _require_backup("set_clip_transform", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id, "track_type": track_type,
            "track_index": int(track_index), "clip_index": int(clip_index),
            "scale": scale, "position": position, "rotation": rotation,
            "range_start_s": range_start_s, "range_end_s": range_end_s,
        }
        return self._request(BridgeRequest("set_clip_transform", payload, dry_run=dry_run)).to_dict()

    def apply_basic_lumetri(
        self,
        sequence_id: str,
        track_type: str,
        track_index: int,
        clip_index: int,
        *,
        look: str = "subtle_professional",
        intensity: float = 0.25,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("apply_basic_lumetri", confirm)
            _require_backup("apply_basic_lumetri", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id, "track_type": track_type,
            "track_index": int(track_index), "clip_index": int(clip_index),
            "look": look, "intensity": float(intensity),
        }
        return self._request(BridgeRequest("apply_basic_lumetri", payload, dry_run=dry_run)).to_dict()

    def save_project(self, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("save_project", confirm)
        return self._request(BridgeRequest("save_project", {}, dry_run=dry_run)).to_dict()

    def undo(self, *, count: int = 1, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("undo", confirm)
        return self._request(BridgeRequest("undo", {"count": int(count)}, dry_run=dry_run)).to_dict()

    def set_active_sequence(self, sequence_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("set_active_sequence", {"sequence_id": sequence_id}, dry_run=dry_run)).to_dict()

    def create_bin(self, name: str, *, parent_bin: str | None = None, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("create_bin", confirm)
        return self._request(BridgeRequest("create_bin", {"name": name, "parent_bin": parent_bin}, dry_run=dry_run)).to_dict()

    def delete_bin(self, bin_id: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("delete_bin", confirm)
        return self._request(BridgeRequest("delete_bin", {"bin_id": bin_id}, dry_run=dry_run)).to_dict()

    def rename_bin(self, bin_id: str, new_name: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("rename_bin", confirm)
        return self._request(BridgeRequest("rename_bin", {"bin_id": bin_id, "new_name": new_name}, dry_run=dry_run)).to_dict()

    def move_item_to_bin(self, item_id: str, target_bin: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("move_item_to_bin", confirm)
        return self._request(BridgeRequest("move_item_to_bin", {"item_id": item_id, "target_bin": target_bin}, dry_run=dry_run)).to_dict()

    def get_item_info(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_item_info", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def select_item(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("select_item", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def check_offline_media(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("check_offline_media", {}, dry_run=dry_run)).to_dict()

    def get_metadata(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_metadata", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def set_metadata(
        self, item_id: str, field_name: str, value: str, *, confirm: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_metadata", confirm)
        payload = {"item_id": item_id, "field_name": field_name, "value": value}
        return self._request(BridgeRequest("set_metadata", payload, dry_run=dry_run)).to_dict()

    def set_color_label(
        self, item_id: str, color_index: int, *, confirm: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_color_label", confirm)
        payload = {"item_id": item_id, "color_index": int(color_index)}
        return self._request(BridgeRequest("set_color_label", payload, dry_run=dry_run)).to_dict()

    def get_color_label(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_color_label", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def get_footage_interpretation(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_footage_interpretation", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def set_footage_interpretation(
        self,
        item_id: str,
        *,
        frame_rate: float | None = None,
        pixel_aspect_ratio: float | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_footage_interpretation", confirm)
        payload = {"item_id": item_id, "frame_rate": frame_rate, "pixel_aspect_ratio": pixel_aspect_ratio}
        return self._request(BridgeRequest("set_footage_interpretation", payload, dry_run=dry_run)).to_dict()

    def get_xmp_metadata(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_xmp_metadata", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def set_xmp_metadata(
        self, item_id: str, xmp_xml: str, *, confirm: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_xmp_metadata", confirm)
        payload = {"item_id": item_id, "xmp_xml": xmp_xml}
        return self._request(BridgeRequest("set_xmp_metadata", payload, dry_run=dry_run)).to_dict()

    def get_color_space(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_color_space", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def import_media_files(
        self,
        file_paths: list[str],
        *,
        target_bin: str | None = None,
        suppress_ui: bool = True,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("import_media_files", confirm)
        payload = {"file_paths": list(file_paths), "target_bin": target_bin, "suppress_ui": suppress_ui}
        return self._request(BridgeRequest("import_media_files", payload, dry_run=dry_run)).to_dict()

    def import_folder(self, folder_path: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("import_folder", confirm)
        return self._request(BridgeRequest("import_folder", {"folder_path": folder_path}, dry_run=dry_run)).to_dict()

    def relink_media(self, item_id: str, new_path: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("relink_media", confirm)
        payload = {"item_id": item_id, "new_path": new_path}
        return self._request(BridgeRequest("relink_media", payload, dry_run=dry_run)).to_dict()

    def refresh_media(self, item_id: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("refresh_media", confirm)
        return self._request(BridgeRequest("refresh_media", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def set_offline(self, item_id: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_offline", confirm)
        return self._request(BridgeRequest("set_offline", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def has_proxy(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("has_proxy", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def detach_proxy(self, item_id: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("detach_proxy", confirm)
        return self._request(BridgeRequest("detach_proxy", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def set_override_frame_rate(
        self, item_id: str, frame_rate: float, *, confirm: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_override_frame_rate", confirm)
        payload = {"item_id": item_id, "frame_rate": frame_rate}
        return self._request(BridgeRequest("set_override_frame_rate", payload, dry_run=dry_run)).to_dict()

    def set_override_pixel_aspect_ratio(
        self, item_id: str, numerator: float, denominator: float, *, confirm: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_override_pixel_aspect_ratio", confirm)
        payload = {"item_id": item_id, "numerator": numerator, "denominator": denominator}
        return self._request(BridgeRequest("set_override_pixel_aspect_ratio", payload, dry_run=dry_run)).to_dict()

    def set_scale_to_frame_size(self, item_id: str, *, confirm: bool = False, dry_run: bool = False) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_scale_to_frame_size", confirm)
        return self._request(BridgeRequest("set_scale_to_frame_size", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def set_start_time(
        self, item_id: str, start_seconds: float, *, confirm: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("set_start_time", confirm)
        payload = {"item_id": item_id, "start_seconds": start_seconds}
        return self._request(BridgeRequest("set_start_time", payload, dry_run=dry_run)).to_dict()

    def open_in_source(self, item_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("open_in_source", {"item_id": item_id}, dry_run=dry_run)).to_dict()

    def close_source_monitor(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("close_source_monitor", {}, dry_run=dry_run)).to_dict()

    def close_all_source_clips(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("close_all_source_clips", {}, dry_run=dry_run)).to_dict()

    def set_source_in_out(
        self, *, in_seconds: float | None = None, out_seconds: float | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        payload = {"in_seconds": in_seconds, "out_seconds": out_seconds}
        return self._request(BridgeRequest("set_source_in_out", payload, dry_run=dry_run)).to_dict()

    def insert_from_source(
        self,
        sequence_id: str,
        *,
        video_track_index: int = 0,
        audio_track_index: int = 0,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("insert_from_source", confirm)
            _require_backup("insert_from_source", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id,
            "video_track_index": int(video_track_index), "audio_track_index": int(audio_track_index),
        }
        return self._request(BridgeRequest("insert_from_source", payload, dry_run=dry_run)).to_dict()

    def overwrite_from_source(
        self,
        sequence_id: str,
        *,
        video_track_index: int = 0,
        audio_track_index: int = 0,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("overwrite_from_source", confirm)
            _require_backup("overwrite_from_source", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id, "backup_sequence_id": backup_sequence_id,
            "video_track_index": int(video_track_index), "audio_track_index": int(audio_track_index),
        }
        return self._request(BridgeRequest("overwrite_from_source", payload, dry_run=dry_run)).to_dict()

    def get_source_monitor_info(self, *, dry_run: bool = False) -> dict[str, Any]:
        return self._request(BridgeRequest("get_source_monitor_info", {}, dry_run=dry_run)).to_dict()

    def import_media(
        self,
        sequence_id: str,
        media_path: str,
        *,
        time_s: float | None = None,
        track: str | None = None,
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("import_media", confirm)
            _require_backup("import_media", backup_sequence_id)
        media = Path(media_path).expanduser().resolve()
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "media_path": str(media),
            "time_s": time_s,
            "track": track,
        }
        return self._request(BridgeRequest("import_media", payload, dry_run=dry_run)).to_dict()

    def queue_export(
        self,
        sequence_id: str,
        output_path: str,
        *,
        range_start_s: float | None = None,
        range_end_s: float | None = None,
        preset: str = "match_source_h264",
        backup_sequence_id: str | None = None,
        confirm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run:
            _require_confirm("queue_export", confirm)
            # Export is not timeline-destructive, but a backup id proves the caller
            # has consciously identified a safe sequence state before rendering.
            _require_backup("queue_export", backup_sequence_id)
        payload = {
            "sequence_id": sequence_id,
            "backup_sequence_id": backup_sequence_id,
            "output_path": str(Path(output_path).expanduser().resolve()),
            "range_start_s": range_start_s,
            "range_end_s": range_end_s,
            "preset": preset,
        }
        return self._request(BridgeRequest("queue_export", payload, dry_run=dry_run)).to_dict()


def _require_confirm(action: str, confirm: bool) -> None:
    if not confirm:
        raise BridgeError(f"{action} is a live-Premiere write action; pass confirm=True after explicit user intent.")


def _require_backup(action: str, backup_sequence_id: str | None) -> None:
    if not backup_sequence_id:
        raise BridgeError(f"{action} requires backup_sequence_id from duplicate_sequence before mutating or exporting a live timeline.")


def plan_live_premiere_job(
    job_type: Literal["talking_head", "batch_export", "motion_graphic", "caption_pass"],
    sequence_id: str,
    *,
    requested_outputs: list[str] | None = None,
) -> dict[str, Any]:
    """Return an orchestrator-safe live-Premiere execution plan."""
    requested_outputs = requested_outputs or []
    base = [
        {"step": "inspect", "tool": "get_active_sequence", "side_effect": "read"},
        {"step": "backup", "tool": "duplicate_sequence", "side_effect": "write", "requires_confirm": True},
    ]
    if job_type == "talking_head":
        base += [
            {"step": "mark_editor_notes", "tool": "add_marker", "side_effect": "write", "requires_backup": True},
            {"step": "caption", "tool": "import_media/add_caption_track", "side_effect": "write", "requires_backup": True},
            {"step": "polish", "tool": "set_clip_transform/apply_basic_lumetri", "side_effect": "write", "requires_backup": True},
        ]
    elif job_type == "batch_export":
        base += [
            {"step": "build_export_manifest", "tool": "premiere_agent_batch_export_plan", "side_effect": "file-write"},
            {"step": "queue_each_export", "tool": "queue_export", "side_effect": "render", "requires_backup": True},
        ]
    elif job_type == "motion_graphic":
        base += [
            {"step": "extract_selected_transcript", "tool": "snapshot_sequence", "side_effect": "read"},
            {"step": "render_graphic", "tool": "Remotion/Hyperframes/Manim", "side_effect": "file-write"},
            {"step": "import_graphic", "tool": "import_media", "side_effect": "write", "requires_backup": True},
        ]
    elif job_type == "caption_pass":
        base += [
            {"step": "import_or_create_captions", "tool": "import_media/add_caption_track", "side_effect": "write", "requires_backup": True},
            {"step": "verify_captions", "tool": "snapshot_sequence/export_still", "side_effect": "read/render"},
        ]
    else:
        raise BridgeError(f"unsupported live Premiere job_type: {job_type}")
    return {
        "job_type": job_type,
        "sequence_id": sequence_id,
        "requested_outputs": requested_outputs,
        "policy": {
            "must_duplicate_sequence_first": True,
            "live_writes_require_confirm": True,
            "destructive_actions_supported": False,
            "fallback": "Use edl.json -> cut.xml/cut.fcpxml + render_preview.py if the bridge is unavailable.",
        },
        "steps": base,
    }


def live_bridge_protocol_spec() -> dict[str, Any]:
    """Machine-readable protocol contract for a CEP/UXP bridge implementer."""
    return {
        "transport": "JSON-RPC 2.0 over local HTTP POST",
        "env": DEFAULT_BRIDGE_ENV,
        "required_response_shape": {"jsonrpc": "2.0", "id": "same as request", "result": {"ok": True, "...": "..."}},
        "read_actions": sorted(READ_ACTIONS),
        "write_actions": sorted(WRITE_ACTIONS),
        "unsupported_destructive_actions": sorted(DESTRUCTIVE_ACTIONS),
        "write_policy": {
            "confirm_required": True,
            "backup_sequence_id_required_after_duplicate": True,
            "verify_after_write": "timeline snapshot, exported still/contact sheet, or rendered file",
        },
        "example_request": {
            "jsonrpc": "2.0",
            "id": "premiere-agent-1",
            "method": "add_marker",
            "params": {
                "sequence_id": "seq_123",
                "backup_sequence_id": "seq_123_AI_BACKUP",
                "time_s": 42.0,
                "label": "EDITOR NOTE",
                "color": "red",
                "comment": "Review this in-clip editor note before cutting.",
            },
        },
    }
