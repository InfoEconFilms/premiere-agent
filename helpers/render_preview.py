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

Optional subtitle burn-in uses an existing output-timeline SRT (`master.srt` by
default). The SRT remains canonical for NLE import; burn-in is only for review
and social MP4s.

The renderer intentionally starts conservative: hard cuts, normalized H.264/AAC
segments, concat demuxer, optional contact sheet + ffprobe-backed report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MIN_SPEED = 1.0
MAX_SPEED = 10.0
SUBTITLE_PRESETS = {"none", "standard", "minimal", "bold_social", "hormozi"}

SUBTITLE_FORCE_STYLES = {
    "standard": "FontName=Helvetica,FontSize=28,PrimaryColour=&H00FFFFFF,OutlineColour=&HAA000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=70",
    "minimal": "FontName=Helvetica,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&HAA000000,BorderStyle=1,Outline=1,Shadow=0,Alignment=2,MarginV=55",
    "bold_social": "FontName=Arial,FontSize=42,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&HCC000000,BorderStyle=1,Outline=4,Shadow=1,Alignment=2,MarginV=110",
    "hormozi": "FontName=Arial,FontSize=48,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&HCC000000,BorderStyle=1,Outline=5,Shadow=1,Alignment=2,MarginV=120",
}

DRAW_TEXT_STYLES = {
    "standard": {"fontsize": 28, "fontcolor": "white", "boxcolor": "black@0.45", "boxborderw": 12, "y": "h-th-70"},
    "minimal": {"fontsize": 24, "fontcolor": "white", "boxcolor": "black@0.30", "boxborderw": 8, "y": "h-th-55"},
    "bold_social": {"fontsize": 42, "fontcolor": "white", "boxcolor": "black@0.65", "boxborderw": 18, "y": "h-th-110"},
    "hormozi": {"fontsize": 48, "fontcolor": "yellow", "boxcolor": "black@0.70", "boxborderw": 20, "y": "h-th-120"},
}


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


def _parse_subtitle_preset(value: str) -> str:
    preset = (value or "none").strip().lower()
    if preset not in SUBTITLE_PRESETS:
        raise argparse.ArgumentTypeError(
            f"subtitle preset must be one of: {', '.join(sorted(SUBTITLE_PRESETS))}"
        )
    return preset


def _escape_subtitles_path(path: Path) -> str:
    """Escape a path for ffmpeg's subtitles filter argument.

    The filter parser treats backslash, colon, comma, and apostrophe as special
    inside option values. Escaping here keeps spaces and macOS `/var/...` paths
    safe without invoking a shell.
    """
    s = str(path)
    return (
        s.replace("\\", "\\\\")
         .replace(":", "\\:")
         .replace(",", "\\,")
         .replace("'", "\\'")
    )


def _escape_force_style(style: str) -> str:
    return style.replace("\\", "\\\\").replace("'", "\\'")


def _srt_time_to_seconds(value: str) -> float:
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not m:
        raise ValueError(f"bad SRT timestamp: {value!r}")
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    entries: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if lines[0].isdigit():
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in lines[0].split("-->", 1)]
        cue_text = " ".join(lines[1:]).strip()
        if not cue_text:
            continue
        entries.append((_srt_time_to_seconds(start_raw), _srt_time_to_seconds(end_raw), cue_text))
    return entries


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
             .replace(":", "\\:")
             .replace("'", "\\'")
             .replace("%", "\\%")
             .replace("\n", " ")
    )


def _burn_subtitles_with_drawtext(src: Path, out: Path, srt: Path, preset: str) -> None:
    cues = _parse_srt(srt)
    if not cues:
        raise ValueError(f"no subtitle cues found in {srt}")
    style = DRAW_TEXT_STYLES[preset]
    filters: list[str] = []
    for start, end, text in cues:
        filters.append(
            "drawtext="
            f"text='{_escape_drawtext(text)}':"
            f"fontsize={style['fontsize']}:"
            f"fontcolor={style['fontcolor']}:"
            "box=1:"
            f"boxcolor={style['boxcolor']}:"
            f"boxborderw={style['boxborderw']}:"
            "x=(w-text_w)/2:"
            f"y={style['y']}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy", "-movflags", "+faststart", str(out),
    ])


def _load_caption_font(size: int):
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        p = Path(c)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap_caption(text: str, draw, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur: list[str] = []
    for word in words:
        trial = " ".join([*cur, word])
        box = draw.textbbox((0, 0), trial, font=font, stroke_width=2)
        if cur and (box[2] - box[0]) > max_width:
            lines.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))
    return lines[:3]


def _burn_subtitles_with_image_overlay(src: Path, out: Path, srt: Path, preset: str) -> None:
    """Portable burn-in fallback for ffmpeg builds without text filters.

    Generates a transparent PNG overlay stream with PIL, then uses ffmpeg's
    widely available `overlay` filter to composite it over the preview.
    """
    from PIL import Image, ImageDraw

    cues = _parse_srt(srt)
    if not cues:
        raise ValueError(f"no subtitle cues found in {srt}")
    meta = _ffprobe_json(src)
    video = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
    width = int(video.get("width") or 1920)
    height = int(video.get("height") or 1080)
    duration = _duration(src)
    fps = 10.0  # enough for caption timing, much cheaper than full-rate PNGs
    frame_count = max(1, int(math.ceil(duration * fps)))
    style = DRAW_TEXT_STYLES[preset]
    font = _load_caption_font(int(style["fontsize"]))
    tmp_frames = Path(tempfile.mkdtemp(prefix="premiere-agent-captions-"))
    try:
        for i in range(frame_count):
            t = i / fps
            active = [text for start, end, text in cues if start <= t <= end]
            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            if active:
                draw = ImageDraw.Draw(img)
                text = active[-1]
                lines = _wrap_caption(text, draw, font, int(width * 0.86))
                line_boxes = [draw.textbbox((0, 0), ln, font=font, stroke_width=2) for ln in lines]
                line_h = max((b[3] - b[1] for b in line_boxes), default=int(style["fontsize"]))
                gap = max(6, int(line_h * 0.18))
                block_h = len(lines) * line_h + max(0, len(lines) - 1) * gap
                margin_v = int(str(style["y"]).split("-")[-1]) if "-" in str(style["y"]) else 80
                y = max(0, height - block_h - margin_v)
                for ln, box in zip(lines, line_boxes):
                    tw = box[2] - box[0]
                    th = box[3] - box[1]
                    x = (width - tw) // 2
                    pad_x = int(style["boxborderw"])
                    pad_y = max(6, pad_x // 2)
                    draw.rounded_rectangle(
                        [x - pad_x, y - pad_y, x + tw + pad_x, y + th + pad_y],
                        radius=10,
                        fill=(0, 0, 0, 170),
                    )
                    fill = (255, 255, 0, 255) if style["fontcolor"] == "yellow" else (255, 255, 255, 255)
                    draw.text((x, y), ln, font=font, fill=fill, stroke_width=2, stroke_fill=(0, 0, 0, 255))
                    y += line_h + gap
            img.save(tmp_frames / f"frame_{i:06d}.png")

        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-framerate", str(fps), "-i", str(tmp_frames / "frame_%06d.png"),
            "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[v]",
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(out),
        ])
    finally:
        import shutil
        shutil.rmtree(tmp_frames, ignore_errors=True)


def _burn_subtitles(src: Path, out: Path, srt: Path, preset: str) -> None:
    # Prefer libass/subtitles when ffmpeg was built with it; fall back to a
    # portable drawtext renderer for Homebrew/static builds without libass.
    style = SUBTITLE_FORCE_STYLES[preset]
    vf = (
        f"subtitles=filename='{_escape_subtitles_path(srt)}'"
        f":force_style='{_escape_force_style(style)}'"
    )
    try:
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy", "-movflags", "+faststart", str(out),
        ])
    except subprocess.CalledProcessError as e:
        if "No such filter" not in (e.stderr or "") and "Filter not found" not in (e.stderr or ""):
            raise
        try:
            _burn_subtitles_with_drawtext(src, out, srt, preset)
        except subprocess.CalledProcessError as e2:
            if "No such filter" not in (e2.stderr or "") and "Filter not found" not in (e2.stderr or ""):
                raise
            _burn_subtitles_with_image_overlay(src, out, srt, preset)


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
    burn_subtitles: str = "none",
    subtitles: Path | None = None,
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
    burn_subtitles = _parse_subtitle_preset(burn_subtitles)

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = report_path or output.with_suffix(".render_report.json")
    contact_sheet = contact_sheet or output.with_name(output.stem + "_contact_sheet.jpg")
    subtitles = (subtitles.resolve() if subtitles is not None else edl_path.parent / "master.srt")

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

        subtitle_report: dict[str, Any] = {"preset": burn_subtitles, "path": None, "burned": False}
        if burn_subtitles != "none":
            subtitle_report["path"] = str(subtitles)
            if not subtitles.exists():
                raise FileNotFoundError(
                    f"subtitle burn requested ({burn_subtitles}) but SRT not found: {subtitles}"
                )
            burned = tmp / "burned_subtitles.mp4"
            _burn_subtitles(output, burned, subtitles, burn_subtitles)
            burned.replace(output)
            subtitle_report["burned"] = True

        if contact_sheet:
            try:
                _run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(output),
                    "-vf", "fps=1/5,scale=320:-1,tile=5x3,format=yuvj420p",
                    "-frames:v", "1", "-q:v", "3", str(contact_sheet),
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
            "subtitles": subtitle_report,
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
    ap.add_argument(
        "--burn-subtitles", type=_parse_subtitle_preset, default="none",
        help="Burn an SRT into the review MP4 with a preset: none, standard, minimal, bold_social, hormozi",
    )
    ap.add_argument("--subtitles", type=Path, default=None, help="SRT path for burn-in (default: <edl_dir>/master.srt)")
    ap.add_argument("--keep-segments", action="store_true", help="Keep normalized segment MP4s next to output")
    ap.add_argument("--contact-sheet", type=Path, default=None, help="Override contact sheet path")
    ap.add_argument("--report", type=Path, default=None, help="Override JSON report path")
    args = ap.parse_args(argv)

    try:
        report = render_preview(
            args.edl, args.output,
            resolution=args.resolution,
            fps=args.fps,
            burn_subtitles=args.burn_subtitles,
            subtitles=args.subtitles,
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
