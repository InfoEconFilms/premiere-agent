"""Lightweight orchestrator job manifest schema for Claude Desktop / host agents.

This module never touches Premiere. It only plans and records intent as a
JSON manifest that the orchestrator (Claude Desktop, not this MCP server and
not the CEP/UXP panel) reads back to decide which bounded MCP tools to call
next, in what order, with which safety gates.

The manifest is a plan, not a lock: it records `backup_sequence_id: null`
until the orchestrator actually calls `premiere_agent_duplicate_sequence`
and updates its own bookkeeping. This module does not mutate manifests after
creation; `verify_orchestrator_job` only reads them.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from premiere_live_bridge import DESTRUCTIVE_ACTIONS  # type: ignore[import-not-found]

MANIFEST_VERSION = "1.0"

JOB_TYPES = ["talking_head", "batch_export", "motion_graphic", "caption_pass", "sequence_qa"]

LIVE_WRITE_SIDE_EFFECTS = {"write", "render"}

SAFETY_POLICY: dict[str, Any] = {
    "orchestrator": "Claude Desktop / host agent decides what to do; this MCP server only exposes bounded tools.",
    "dry_run_default": True,
    "live_writes_require_confirm": True,
    "must_duplicate_sequence_first": True,
    "timeline_writes_require_backup_sequence_id": True,
    "verification_required_after_every_write": True,
    "destructive_actions_supported": False,
    "fallback": "If the live bridge is unavailable, fall back to edl.json -> cut.xml/cut.fcpxml + master.srt.",
}

VERIFICATION_METHODS = [
    "sequence_snapshot_or_get_sequence_structure_readback",
    "marker_readback_via_list_markers",
    "review_frames_or_contact_sheet",
    "rendered_or_exported_file_check",
]


def _task(
    step: str,
    tool: str | None,
    side_effect: str,
    *,
    is_backup_step: bool = False,
    requires_confirm: bool = False,
    requires_backup: bool = False,
    verification_required: bool = False,
    verification_method: str | None = None,
    args: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "tool": tool,
        "side_effect": side_effect,
        "is_backup_step": is_backup_step,
        "requires_confirm": requires_confirm,
        "requires_backup": requires_backup,
        "verification": {
            "required": verification_required,
            "method": verification_method,
            "evidence": None,
        },
        "args": args or {},
        "note": note,
    }


def _preamble(sequence_id: str) -> list[dict[str, Any]]:
    return [
        _task(
            "verify_connection",
            "premiere_agent_verify_premiere_connection",
            "read",
        ),
        _task(
            "inspect_sequence",
            "premiere_agent_get_sequence_structure",
            "read",
            args={"sequence_id": sequence_id},
        ),
    ]


def _backup_task(sequence_id: str) -> dict[str, Any]:
    return _task(
        "backup_sequence",
        "premiere_agent_duplicate_sequence",
        "backup",
        is_backup_step=True,
        requires_confirm=True,
        verification_required=True,
        verification_method="get_sequence_structure/list_markers readback confirming the backup sequence id exists",
        args={"sequence_id": sequence_id},
    )


def _build_talking_head_tasks(sequence_id: str, manifest_path: str, requested_outputs: list[str]) -> list[dict[str, Any]]:
    tasks = _preamble(sequence_id) + [_backup_task(sequence_id)]
    tasks.append(_task(
        "add_editorial_markers",
        "premiere_agent_add_editorial_markers",
        "write",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="premiere_agent_list_markers readback of the added marker ids",
        args={"sequence_id": sequence_id},
    ))
    if "captions" in requested_outputs:
        tasks.append(_task(
            "import_captions",
            "premiere_agent_import_captions",
            "write",
            requires_confirm=True,
            requires_backup=True,
            verification_required=True,
            verification_method="list_markers/get_sequence_structure readback or an exported review frame showing burned-in captions",
            args={"sequence_id": sequence_id},
        ))
    tasks.append(_task(
        "export_review_frames",
        "premiere_agent_export_review_frames",
        "render",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="confirm the frame files exist on disk at the reported paths",
        args={"sequence_id": sequence_id},
    ))
    tasks.append(_task(
        "final_export",
        "premiere_agent_queue_export",
        "render",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="confirm the exported file exists at output_path",
        args={"sequence_id": sequence_id, "output_path": "<EXPORT_OUTPUT_PATH>"},
    ))
    return tasks


def _build_batch_export_tasks(sequence_id: str, manifest_path: str, requested_outputs: list[str]) -> list[dict[str, Any]]:
    tasks = _preamble(sequence_id)
    tasks.append(_task(
        "build_export_manifest",
        "premiere_agent_batch_export_plan",
        "file-write",
        args={"output_dir": "<BATCH_EXPORT_OUTPUT_DIR>"},
    ))
    tasks.append(_backup_task(sequence_id))
    tasks.append(_task(
        "queue_exports",
        "premiere_agent_queue_export",
        "render",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="confirm every exported file exists at its reported output path",
        args={"sequence_id": sequence_id, "output_path": "<EXPORT_OUTPUT_PATH_FROM_BATCH_PLAN>"},
    ))
    return tasks


def _build_motion_graphic_tasks(sequence_id: str, manifest_path: str, requested_outputs: list[str]) -> list[dict[str, Any]]:
    tasks = _preamble(sequence_id) + [_backup_task(sequence_id)]
    tasks.append(_task(
        "export_reference_frames_for_range",
        "premiere_agent_export_review_frames",
        "render",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="confirm exported reference frames for the target range exist on disk",
        args={"sequence_id": sequence_id},
    ))
    tasks.append(_task(
        "render_graphic_asset_offline",
        None,
        "file-write",
        note="Rendered locally (Remotion/Hyperframes/Manim/etc); not a live Premiere action and not a bounded MCP tool.",
        args={"output_path": "<GRAPHIC_ASSET_OUTPUT_PATH>"},
    ))
    tasks.append(_task(
        "import_graphic_into_sequence",
        "premiere_agent_import_media",
        "write",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="get_sequence_structure/export_review_frames confirming the graphic is present at the intended range",
        args={"sequence_id": sequence_id, "media_path": "<GRAPHIC_ASSET_OUTPUT_PATH>"},
    ))
    return tasks


def _build_caption_pass_tasks(sequence_id: str, manifest_path: str, requested_outputs: list[str]) -> list[dict[str, Any]]:
    tasks = _preamble(sequence_id) + [_backup_task(sequence_id)]
    tasks.append(_task(
        "import_captions",
        "premiere_agent_import_captions",
        "write",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="list_markers/get_sequence_structure readback or an exported review frame showing burned-in captions",
        args={"sequence_id": sequence_id, "caption_path": "<CAPTION_PATH.srt>"},
    ))
    tasks.append(_task(
        "export_review_frames",
        "premiere_agent_export_review_frames",
        "render",
        requires_confirm=True,
        requires_backup=True,
        verification_required=True,
        verification_method="visually confirm burned-in/attached captions in the exported frames",
        args={"sequence_id": sequence_id},
    ))
    return tasks


def _build_sequence_qa_tasks(sequence_id: str, manifest_path: str, requested_outputs: list[str]) -> list[dict[str, Any]]:
    tasks = _preamble(sequence_id)
    tasks.append(_task(
        "list_markers",
        "premiere_agent_list_markers",
        "read",
        args={"sequence_id": sequence_id},
    ))
    tasks.append(_task(
        "optional_review_frames_note",
        None,
        "read",
        note="This default QA manifest is read-only. If visual spot-check frames are needed, the orchestrator must first duplicate the sequence and then call premiere_agent_export_review_frames as a separate confirmed render step with backup_sequence_id.",
        args={"sequence_id": sequence_id, "output_dir": "<REVIEW_FRAME_OUTPUT_DIR>"},
    ))
    tasks.append(_task(
        "report_findings",
        None,
        "read",
        note="No mutation. Report structure/marker findings back to the user. If a separate confirmed review-frame pass was run, include those frame paths as verification evidence.",
        args={"manifest_path": manifest_path},
    ))
    return tasks


_TASK_BUILDERS = {
    "talking_head": _build_talking_head_tasks,
    "batch_export": _build_batch_export_tasks,
    "motion_graphic": _build_motion_graphic_tasks,
    "caption_pass": _build_caption_pass_tasks,
    "sequence_qa": _build_sequence_qa_tasks,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_orchestrator_job(
    job_type: str,
    sequence_id: str,
    output_path: str,
    *,
    requested_outputs: list[str] | None = None,
    notes: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise ValueError(f"job_type must be one of {JOB_TYPES}, got {job_type!r}")
    if not sequence_id:
        raise ValueError("sequence_id is required")
    if not output_path:
        raise ValueError("output_path is required")

    requested_outputs = list(requested_outputs or [])
    manifest_path = Path(output_path).expanduser().resolve()
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(
            f"orchestrator job manifest already exists at {manifest_path}; pass overwrite=true to replace it."
        )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    tasks = _TASK_BUILDERS[job_type](sequence_id, str(manifest_path), requested_outputs)

    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "job_id": f"job_{uuid.uuid4().hex[:12]}",
        "created_at": _now_iso(),
        "job_type": job_type,
        "target_sequence_id": sequence_id,
        "backup_sequence_id": None,
        "status": "planned",
        "requested_outputs": requested_outputs,
        "notes": notes or "",
        "tasks": tasks,
        "policy": SAFETY_POLICY,
        "verification_required": VERIFICATION_METHODS,
    }

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "summary": (
            f"Created {job_type} orchestrator job {manifest['job_id']} for sequence {sequence_id!r} "
            f"at {manifest_path} (status=planned, backup_sequence_id=null). This manifest is a plan only; "
            "no Premiere state was touched."
        ),
    }


def verify_orchestrator_job(manifest_path: str) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))

    errors: list[str] = []
    checks_passed: list[str] = []

    policy = manifest.get("policy") or {}
    if policy.get("must_duplicate_sequence_first"):
        checks_passed.append("policy.must_duplicate_sequence_first is true")
    else:
        errors.append("policy.must_duplicate_sequence_first must be true")

    if policy.get("destructive_actions_supported") is False:
        checks_passed.append("policy.destructive_actions_supported is false")
    else:
        errors.append("policy.destructive_actions_supported must be false")

    tasks = manifest.get("tasks") or []
    destructive_found = [
        t.get("step") for t in tasks
        if (t.get("tool") or "") in DESTRUCTIVE_ACTIONS or (t.get("step") or "") in DESTRUCTIVE_ACTIONS
    ]
    if destructive_found:
        errors.append(f"manifest lists destructive actions: {destructive_found}")
    else:
        checks_passed.append("no destructive actions listed in tasks")

    for t in tasks:
        step = t.get("step", "<unknown>")
        if t.get("side_effect") in LIVE_WRITE_SIDE_EFFECTS and not t.get("is_backup_step"):
            if not t.get("requires_backup"):
                errors.append(f"task {step!r} is a live write/render but requires_backup is not true")
            verification = t.get("verification") or {}
            if not verification.get("required") or not verification.get("method"):
                errors.append(f"task {step!r} is a live write/render but has no verification method placeholder")

    if not errors:
        checks_passed.append("every live-write/render task requires backup and has a verification placeholder")

    return {
        "ok": not errors,
        "manifest_path": str(path),
        "job_id": manifest.get("job_id"),
        "job_type": manifest.get("job_type"),
        "checks_passed": checks_passed,
        "errors": errors,
    }
