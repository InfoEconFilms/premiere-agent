#!/usr/bin/env python3
"""Local JSON-RPC bridge scaffold for live Premiere automation.

This is the host-side bridge process that `premiere_live_bridge.py` talks to.
Today it ships with a deterministic mock backend so the protocol, MCP tools,
and safety gates can be exercised without Premiere installed. A CEP/UXP panel
or ExtendScript adapter can later replace MockPremiereBackend while preserving
the same JSON-RPC method contract.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
PORT = 48791


class JsonRpcError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class MockPremiereBackend:
    """Small in-memory backend matching the bridge protocol.

    The object shape mirrors the data a CEP/UXP panel should eventually return:
    project id/name/path, active sequence id/name, markers, imports, transforms,
    Lumetri operations, and queued exports.
    """

    project_id: str = "mock_project"
    project_name: str = "Mock Premiere Project"
    project_path: str | None = None
    active_sequence_id: str = "seq_main"
    active_sequence_name: str = "Main Timeline"
    sequences: dict[str, dict[str, Any]] = field(default_factory=dict)
    exports: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.sequences:
            self.sequences[self.active_sequence_id] = {
                "id": self.active_sequence_id,
                "name": self.active_sequence_name,
                "duration_s": 0.0,
                "width": 1920,
                "height": 1080,
                "fps": 25,
                "markers": [],
                "imports": [],
                "transforms": [],
                "lumetri": [],
                "created_from": None,
            }

    def status(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "mock",
            "premiere_connected": False,
            "project_id": self.project_id,
            "active_sequence_id": self.active_sequence_id,
            "message": "Mock bridge running. Replace backend with CEP/UXP adapter for live Premiere control.",
        }

    def get_active_project(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "project": {
                "id": self.project_id,
                "name": self.project_name,
                "path": self.project_path,
                "sequence_count": len(self.sequences),
            },
        }

    def get_active_sequence(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(self.active_sequence_id)
        return {"ok": True, "sequence": self._public_sequence(seq)}

    def verify_premiere_connection(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "mock",
            "overall": "ready",
            "checks": {
                "bridge_reachable": True,
                "premiere_project_open": True,
                "active_sequence_open": True,
                "read_only_snapshot_ok": True,
                "duration_non_negative_or_null": True,
                "track_collections_readable": True,
            },
            "privacy": "Mock read-only check omits project/media details.",
            "mutates_project": False,
        }

    def get_sequence_structure(self, params: dict[str, Any]) -> dict[str, Any]:
        seq_id = params.get("sequence_id") or self.active_sequence_id
        seq = self._sequence(str(seq_id))
        video_clips = seq.get("clips") or []
        return {
            "ok": True,
            "sequence": self._public_sequence(seq),
            "playhead_s": 0.0,
            "total_clip_count": len(video_clips),
            "video_tracks": [{"type": "video", "index": 0, "name": "V1", "clip_count": len(video_clips), "muted": None, "locked": None, "clips": video_clips, "gaps": []}],
            "audio_tracks": [{"type": "audio", "index": 0, "name": "A1", "clip_count": 0, "muted": False, "locked": None, "clips": [], "gaps": []}],
            "verification": {"kind": "mock_sequence_structure", "boundary": "mock_snapshot", "mutates_project": False},
        }

    def snapshot_sequence(self, params: dict[str, Any]) -> dict[str, Any]:
        seq_id = params.get("sequence_id") or self.active_sequence_id
        seq = self._sequence(seq_id)
        return {
            "ok": True,
            "snapshot": self._public_sequence(seq),
            "verification": {
                "kind": "mock_snapshot",
                "captured_at": time.time(),
                "note": "Live bridge implementers should return timeline item ids, track layout, markers, selected range, and a still/contact-sheet path when available.",
            },
        }

    def duplicate_sequence(self, params: dict[str, Any]) -> dict[str, Any]:
        seq_id = str(params.get("sequence_id") or self.active_sequence_id)
        seq = self._sequence(seq_id)
        backup_name = str(params.get("backup_name") or f"{seq['name']}_AI_BACKUP")
        backup_id = _slug(f"{seq_id}_{backup_name}_{len(self.sequences)+1}")
        clone = json.loads(json.dumps(seq))
        clone.update({"id": backup_id, "name": backup_name, "created_from": seq_id})
        self.sequences[backup_id] = clone
        return {
            "ok": True,
            "sequence_id": seq_id,
            "backup_sequence_id": backup_id,
            "backup_name": backup_name,
            "message": "Backup sequence created before live mutation.",
        }

    def add_marker(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        marker = {
            "id": f"marker_{len(seq['markers'])+1:04d}",
            "time_s": float(params["time_s"]),
            "label": str(params.get("label") or "AI Marker"),
            "color": str(params.get("color") or "red"),
            "comment": str(params.get("comment") or ""),
            "backup_sequence_id": params.get("backup_sequence_id"),
        }
        seq["markers"].append(marker)
        return {"ok": True, "marker": marker, "sequence_id": seq["id"]}

    def list_markers(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        markers = list(seq.get("markers", []))
        return {
            "ok": True,
            "sequence": self._public_sequence(seq),
            "marker_count": len(markers),
            "markers": markers,
            "verification": {"kind": "mock_marker_list", "mutates_project": False},
        }

    def add_editorial_markers(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        notes = params.get("notes") or []
        if not isinstance(notes, list) or not notes:
            raise JsonRpcError(-32602, "notes must be a non-empty array")
        added: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for i, note in enumerate(notes):
            try:
                item = note if isinstance(note, dict) else {}
                raw_time = item.get("time_s", item.get("start_s"))
                if raw_time is None:
                    raise ValueError("time_s or start_s required")
                marker = {
                    "id": f"marker_{len(seq['markers'])+1:04d}",
                    "time_s": float(raw_time),
                    "label": str(item.get("label") or f"AI {item.get('kind', 'editorial_note')}"),
                    "color": str(item.get("color") or params.get("default_color") or "red"),
                    "comment": str(item.get("comment") or item.get("reason") or ""),
                    "kind": str(item.get("kind") or "editorial_note"),
                    "backup_sequence_id": params.get("backup_sequence_id"),
                }
                seq["markers"].append(marker)
                added.append(marker)
            except Exception as e:
                failures.append({"index": i, "error": f"{type(e).__name__}: {e}"})
        return {
            "ok": bool(added),
            "sequence_id": seq["id"],
            "added_count": len(added),
            "failed_count": len(failures),
            "markers": added,
            "failures": failures,
            "verification": {"kind": "mock_editorial_marker_pass", "mutates_project": True, "backup_sequence_id": params.get("backup_sequence_id")},
        }

    def export_sequence_review_frames(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        outdir = Path(str(params["output_dir"])).expanduser().resolve()
        outdir.mkdir(parents=True, exist_ok=True)
        count = max(2, min(24, int(params.get("frame_count") or 6)))
        start = float(params.get("range_start_s") or 0.0)
        end = float(params.get("range_end_s") or seq.get("duration_s") or 1.0)
        if end <= start:
            raise JsonRpcError(-32602, "range_end_s must be greater than range_start_s")
        frames = []
        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c4944415408d763f8ffff3f0005fe02fea73581e80000000049454e44ae426082")
        for i in range(count):
            at = start + ((end - start) * i / (count - 1))
            path = outdir / f"review_{i+1:03d}.png"
            path.write_bytes(png)
            frames.append({"index": i, "time_s": round(at, 3), "path": str(path), "method": "mock_png"})
        manifest = {"sequence_id": seq["id"], "frames": frames, "range": {"start_s": start, "end_s": end}}
        (outdir / "review_frames_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "sequence": self._public_sequence(seq),
            "output_dir": str(outdir),
            "requested_count": count,
            "exported_count": len(frames),
            "frames": frames,
            "manifest_path": str(outdir / "review_frames_manifest.json"),
            "contact_sheet_pending": True,
            "verification": {"kind": "mock_review_frames", "backup_sequence_id": params.get("backup_sequence_id")},
        }

    def import_captions(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        caption_path = str(Path(str(params["caption_path"])).expanduser().resolve())
        item = {
            "id": f"caption_{len(seq.get('captions', []))+1:04d}",
            "caption_path": caption_path,
            "start_s": float(params.get("start_s") or 0.0),
            "caption_format": params.get("caption_format") or "subtitle",
            "backup_sequence_id": params.get("backup_sequence_id"),
            "caption_track_created": True,
        }
        seq.setdefault("captions", []).append(item)
        return {"ok": True, "sequence_id": seq["id"], "caption": item, "verification": {"kind": "mock_caption_import", "mutates_project": True}}

    def import_media(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        media_path = str(Path(str(params["media_path"])).expanduser().resolve())
        item = {
            "id": f"import_{len(seq['imports'])+1:04d}",
            "media_path": media_path,
            "time_s": params.get("time_s"),
            "track": params.get("track"),
            "backup_sequence_id": params.get("backup_sequence_id"),
        }
        seq["imports"].append(item)
        return {"ok": True, "imported": item, "sequence_id": seq["id"]}

    def queue_export(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        output_path = str(Path(str(params["output_path"])).expanduser().resolve())
        export = {
            "id": f"export_{len(self.exports)+1:04d}",
            "sequence_id": seq["id"],
            "output_path": output_path,
            "range_start_s": params.get("range_start_s"),
            "range_end_s": params.get("range_end_s"),
            "preset": params.get("preset") or "match_source_h264",
            "status": "queued_mock",
            "backup_sequence_id": params.get("backup_sequence_id"),
        }
        self.exports.append(export)
        return {"ok": True, "export": export}

    def apply_basic_lumetri(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        op = {
            "id": f"lumetri_{len(seq['lumetri'])+1:04d}",
            "look": params.get("look") or "subtle_professional",
            "intensity": float(params.get("intensity") or 0.25),
            "target": params.get("target") or "selected_or_all_clips",
            "backup_sequence_id": params.get("backup_sequence_id"),
        }
        seq["lumetri"].append(op)
        return {"ok": True, "lumetri": op, "sequence_id": seq["id"]}

    def set_clip_transform(self, params: dict[str, Any]) -> dict[str, Any]:
        seq = self._sequence(str(params.get("sequence_id") or self.active_sequence_id))
        _require_backup(params)
        op = {
            "id": f"transform_{len(seq['transforms'])+1:04d}",
            "clip_id": params.get("clip_id"),
            "range_start_s": params.get("range_start_s"),
            "range_end_s": params.get("range_end_s"),
            "scale": params.get("scale"),
            "position": params.get("position"),
            "backup_sequence_id": params.get("backup_sequence_id"),
        }
        seq["transforms"].append(op)
        return {"ok": True, "transform": op, "sequence_id": seq["id"]}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method.startswith("rpc."):
            method = method.split(".", 1)[1]
        fn = getattr(self, method, None)
        if fn is None or method.startswith("_"):
            raise JsonRpcError(-32601, f"method not found: {method}")
        return fn(params or {})

    def _sequence(self, seq_id: str) -> dict[str, Any]:
        if seq_id not in self.sequences:
            raise JsonRpcError(-32602, f"unknown sequence_id: {seq_id}")
        return self.sequences[seq_id]

    def _public_sequence(self, seq: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": seq["id"],
            "name": seq["name"],
            "duration_s": seq.get("duration_s"),
            "width": seq.get("width"),
            "height": seq.get("height"),
            "fps": seq.get("fps"),
            "created_from": seq.get("created_from"),
            "marker_count": len(seq.get("markers", [])),
            "import_count": len(seq.get("imports", [])),
            "transform_count": len(seq.get("transforms", [])),
            "lumetri_count": len(seq.get("lumetri", [])),
            "markers": seq.get("markers", []),
            "imports": seq.get("imports", []),
            "transforms": seq.get("transforms", []),
            "lumetri": seq.get("lumetri", []),
        }


def _require_backup(params: dict[str, Any]) -> None:
    if not params.get("backup_sequence_id"):
        raise JsonRpcError(-32602, "backup_sequence_id is required for this live-Premiere operation")


def _slug(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("._-")
    return safe or "sequence_backup"


def handle_jsonrpc(backend: MockPremiereBackend, payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("jsonrpc") != "2.0":
        return _error(payload.get("id"), -32600, "jsonrpc must be '2.0'")
    method = payload.get("method")
    if not method:
        return _error(payload.get("id"), -32600, "method is required")
    if method.startswith("notifications/"):
        return None
    try:
        result = backend.dispatch(str(method), payload.get("params") or {})
        return {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
    except JsonRpcError as e:
        return _error(payload.get("id"), e.code, e.message)
    except Exception as e:
        return _error(payload.get("id"), -32603, f"{type(e).__name__}: {e}")


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class BridgeHandler(BaseHTTPRequestHandler):
    backend: MockPremiereBackend

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback
        if self.path in ("/health", "/status"):
            self._send(200, {"ok": True, "service": "premiere-agent-bridge", "backend": "mock"})
        else:
            self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw)
        except Exception as e:
            self._send(400, _error(None, -32700, f"parse error: {e}"))
            return
        response = handle_jsonrpc(self.backend, payload)
        if response is None:
            self._send(204, {})
        else:
            self._send(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep stdout clean for agent harnesses; use explicit startup line only.
        return

    def _send(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, indent=2, sort_keys=True).encode("utf-8") if data else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def serve(host: str = HOST, port: int = PORT, backend: MockPremiereBackend | None = None) -> ThreadingHTTPServer:
    backend = backend or MockPremiereBackend()

    class Handler(BridgeHandler):
        pass

    Handler.backend = backend
    server = ThreadingHTTPServer((host, int(port)), Handler)
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the Premiere Agent local JSON-RPC bridge scaffold")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--project-name", default="Mock Premiere Project")
    ap.add_argument("--sequence-name", default="Main Timeline")
    args = ap.parse_args(argv)

    backend = MockPremiereBackend(project_name=args.project_name, active_sequence_name=args.sequence_name)
    server = serve(args.host, args.port, backend)
    print(f"premiere-agent bridge listening on http://{args.host}:{args.port}/jsonrpc (mock backend)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbridge stopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
