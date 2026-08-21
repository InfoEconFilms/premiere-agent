"""Render a flattened MP4 review preview from a Premiere Agent EDL.

This is an ADDITIVE review lane. `helpers/export_fcpxml.py` remains the
professional NLE handoff path; this helper exists so users can quickly watch a
cut without importing XML into Premiere / Resolve / Final Cut.

Supported EDL fields match the core exporter where possible:

  - sources: {source_id: "/abs/path/to/media"}
  - ranges[] or edl[]:
      - source: source_id
      - start, end: source seconds
      - speed: optional timelapse multiplier, clamped to [1, 10]
      - audio_strategy: "keep" or "drop". Defaults to keep at 1x, drop when
        speed != 1x, matching export_fcpxml.py.

The renderer intentionally starts conservative: hard cuts, normalized H.264/AAC
segments, concat demuxer, optional contact sheet + ffprobe-backed report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MIN_SPEED = 1.0
MAX_SPEED = 10.0


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=check)


def _ffprobe_json(path: Path) -> dict[str, Any]:
    proc = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    return json.loads(proc.stdout or "{}")


def _has_audio(path: Path) -> bool:
    try:
        meta = _ffprobe_json(path)
    except Exception:
        return False
    return any(s.get("codec_type") == "audio" for s in meta.get("streams", []))


def _duration(path: Path) -> float:
    meta = _ffprobe_json(path)
    raw = (meta.get("format") or {}).get("duration")
    try:
        if raw is None:
            return 0.0
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _read_speed(r: dict[str, Any], idx: int) -> float:
    raw = r.get("speed")
    if raw is None:
        return 1.0
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        print(f"warn: range[{idx}] has non-numeric speed={raw!r}; using 1.0", file=sys.stderr)
        return 1.0
    if speed < MIN_SPEED:
        print(f"warn: range[{idx}] speed={speed:g} < {MIN_SPEED:g}; clamped to 1.0", file=sys.stderr)
        return MIN_SPEED
    if speed > MAX_SPEED:
        print(f"warn: range[{idx}] speed={speed:g} > {MAX_SPEED:g}; clamped to {MAX_SPEED:g}", file=sys.stderr)
        return MAX_SPEED
    return speed


def _audio_strategy(r: dict[str, Any], speed: float, idx: int) -> str:
    raw = r.get("audio_strategy")
    default = "keep" if abs(speed - 1.0) < 1e-9 else "drop"
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"keep", "drop"}:
        return value
    print(f"warn: range[{idx}] has unknown audio_strategy={raw!r}; using {default!r}", file=sys.stderr)
    return default


def _atempo_filter(speed: float) -> str:
    """Return an ffmpeg atempo chain for the requested speed.

    atempo historically accepted 0.5..2.0; newer ffmpeg accepts wider ranges,
    but chaining keeps this portable and works for our 1..10 clamp.
    """
    if speed <= 1.000001:
        return "anull"
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    parts.append(f"atempo={remaining:.8g}")
    return ",".join(parts)


def _parse_resolution(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        w, h = value.lower().split("x", 1)
        width, height = int(w), int(h)
        if width <= 0 or height <= 0:
            raise ValueError
        return width, height
    except Exception as e:
        raise argparse.ArgumentTypeError("resolution must look like 1920x1080") from e


def _segment_filter(
    *,
    src: Path,
    out: Path,
    start: float,
    end: float,
    speed: float,
    audio_strategy: str,
    resolution: tuple[int, int] | None,
    fps: str | None,
    idx: int,
) -> dict[str, Any]:
    src_duration = max(0.0, end - start)
    if src_duration <= 0:
        raise ValueError(f"range[{idx}] has non-positive duration: start={start}, end={end}")
    out_duration = src_duration / speed
    has_audio = _has_audio(src)

    vchain = f"[0:v]trim=start={start:.6f}:end={end:.6f},setpts=(PTS-STARTPTS)/{speed:.8g}"
    if resolution:
        w, h = resolution
        # Contain inside target resolution, preserving source aspect, then pad.
        vchain += f",scale=w={w}:h={h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    if fps and fps != "auto":
        vchain += f",fps={fps}"
    vchain += ",format=yuv420p[v]"

    filters = [vchain]
    maps = ["-map", "[v]"]

    if audio_strategy == "keep" and has_audio:
        achain = (
            f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
            f"asetpts=PTS-STARTPTS,{_atempo_filter(speed)}[a]"
        )
        filters.append(achain)
        maps += ["-map", "[a]"]
    else:
        # Keep every segment audio-shaped so concat is reliable even when
        # source has no audio or timelapse audio is intentionally dropped.
        filters.append(
            f"anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=duration={out_duration:.6f},asetpts=PTS-STARTPTS[a]"
        )
        maps += ["-map", "[a]"]

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-filter_complex", ";".join(filters),
        *maps,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]
    _run(cmd)
    return {
        "index": idx,
        "source": str(src),
        "start": start,
        "end": end,
        "source_duration_s": src_duration,
        "speed": speed,
        "audio_strategy": audio_strategy,
        "expected_output_duration_s": out_duration,
        "rendered_segment": str(out),
    }


def render_preview(
    edl_path: Path,
    output: Path,
    *,
    resolution: tuple[int, int] | None = None,
    fps: str | None = None,
    keep_segments: bool = False,
    contact_sheet: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    edl_path = edl_path.resolve()
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    sources = edl.get("sources") or {}
    ranges = edl.get("ranges") or edl.get("edl") or []
    if not ranges:
        raise ValueError("EDL has no ranges")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = report_path or output.with_suffix(".render_report.json")
    contact_sheet = contact_sheet or output.with_name(output.stem + "_contact_sheet.jpg")

    tmp_owner = tempfile.TemporaryDirectory(prefix="premiere-agent-render-")
    tmp = Path(tmp_owner.name)
    segment_dir = output.parent / "segments" if keep_segments else tmp
    segment_dir.mkdir(parents=True, exist_ok=True)

    rendered_ranges: list[dict[str, Any]] = []
    segment_paths: list[Path] = []

    try:
        for idx, r in enumerate(ranges):
            src_key = r.get("source")
            if src_key not in sources:
                raise KeyError(f"range[{idx}].source {src_key!r} is not in EDL sources")
            src = Path(sources[src_key]).expanduser().resolve()
            if not src.exists():
                raise FileNotFoundError(f"range[{idx}] source missing: {src}")
            start = float(r.get("start", 0.0))
            end = float(r.get("end", 0.0))
            speed = _read_speed(r, idx)
            strategy = _audio_strategy(r, speed, idx)
            seg = segment_dir / f"segment_{idx:04d}.mp4"
            rendered = _segment_filter(
                src=src, out=seg, start=start, end=end, speed=speed,
                audio_strategy=strategy, resolution=resolution, fps=fps, idx=idx,
            )
            rendered["source_id"] = src_key
            rendered["beat"] = r.get("beat")
            segment_paths.append(seg)
            rendered_ranges.append(rendered)

        concat_file = tmp / "concat.txt"
        concat_file.write_text(
            "".join(f"file {shlex.quote(str(p))}\n" for p in segment_paths),
            encoding="utf-8",
        )
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", "-movflags", "+faststart", str(output),
        ])

        if contact_sheet:
            try:
                _run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(output),
                    "-vf", "fps=1/5,scale=320:-1,tile=5x3",
                    "-frames:v", "1", str(contact_sheet),
                ])
            except subprocess.CalledProcessError as e:
                print(f"warn: contact sheet failed: {e.stderr.strip()}", file=sys.stderr)

        meta = _ffprobe_json(output)
        out_duration = _duration(output)
        video = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
        audio = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), {})
        report = {
            "edl": str(edl_path),
            "output": str(output),
            "output_duration_s": out_duration,
            "range_count": len(rendered_ranges),
            "ranges": rendered_ranges,
            "video": {
                "codec": video.get("codec_name"),
                "width": video.get("width"),
                "height": video.get("height"),
                "avg_frame_rate": video.get("avg_frame_rate"),
            },
            "audio": {
                "codec": audio.get("codec_name"),
                "channels": audio.get("channels"),
                "sample_rate": audio.get("sample_rate"),
            },
            "contact_sheet": str(contact_sheet) if contact_sheet and contact_sheet.exists() else None,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        if keep_segments:
            tmp_owner.cleanup()
        else:
            tmp_owner.cleanup()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a review MP4 from a Premiere Agent EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output review MP4")
    ap.add_argument("--resolution", type=_parse_resolution, default=None, help="Target resolution, e.g. 1920x1080")
    ap.add_argument("--fps", default="auto", help="Output fps: auto, 24, 25, 29.97, 30, etc.")
    ap.add_argument("--keep-segments", action="store_true", help="Keep normalized segment MP4s next to output")
    ap.add_argument("--contact-sheet", type=Path, default=None, help="Override contact sheet path")
    ap.add_argument("--report", type=Path, default=None, help="Override JSON report path")
    args = ap.parse_args(argv)

    try:
        report = render_preview(
            args.edl, args.output,
            resolution=args.resolution,
            fps=args.fps,
            keep_segments=args.keep_segments,
            contact_sheet=args.contact_sheet,
            report_path=args.report,
        )
    except Exception as e:
        print(f"[render_preview] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"[render_preview] wrote {report['output']} ({report['output_duration_s']:.2f}s)")
    if report.get("contact_sheet"):
        print(f"[render_preview] contact sheet: {report['contact_sheet']}")
    print(f"[render_preview] report: {args.report or args.output.with_suffix('.render_report.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
