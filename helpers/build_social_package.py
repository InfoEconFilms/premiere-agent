"""Build a local social derivative package from an approved EDL.

This helper is deliberately additive: it does not modify the source EDL and it
does not replace the XML/FCPXML/SRT professional handoff. It creates reviewable
MP4 derivatives and editor-facing metadata docs under `<edit>/social/`.

First-pass preset: `youtube_shorts`
  - main_captioned.mp4: 1920x1080 review render
  - vertical_60s.mp4: 1080x1920 center-crop derivative from main render
  - square.mp4: 1080x1080 center-crop derivative from main render
  - derivative EDL JSONs for traceability
  - chapters/titles/description/thumbnail prompt markdowns
  - package_report.json
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from render_preview import render_preview, _ffprobe_json

PRESETS = {"youtube_shorts"}


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)


def _duration_from_report(report: dict[str, Any]) -> float:
    try:
        return float(report.get("output_duration_s") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _load_edl(edl_path: Path) -> dict[str, Any]:
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    if not (edl.get("ranges") or edl.get("edl")):
        raise ValueError("EDL has no ranges")
    if not edl.get("sources"):
        raise ValueError("EDL has no sources map")
    return edl


def _ranges(edl: dict[str, Any]) -> list[dict[str, Any]]:
    return list(edl.get("ranges") or edl.get("edl") or [])


def _range_output_duration(r: dict[str, Any]) -> float:
    start = float(r.get("start", 0.0))
    end = float(r.get("end", 0.0))
    try:
        speed = float(r.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    speed = min(10.0, max(1.0, speed))
    return max(0.0, end - start) / speed


def _make_derivative_edl(edl: dict[str, Any], *, name_suffix: str, max_duration_s: float | None = None) -> dict[str, Any]:
    out = copy.deepcopy(edl)
    out["name"] = f"{edl.get('name') or 'premiere_agent_cut'}_{name_suffix}"
    out["derivative"] = {
        "kind": name_suffix,
        "source_edl_name": edl.get("name"),
        "max_duration_s": max_duration_s,
        "note": "Derivative EDL for social package traceability; source EDL is unchanged.",
    }
    if max_duration_s is not None:
        kept: list[dict[str, Any]] = []
        remaining = max_duration_s
        for r in _ranges(edl):
            dur = _range_output_duration(r)
            if dur <= 0:
                continue
            if dur <= remaining + 1e-6:
                kept.append(copy.deepcopy(r))
                remaining -= dur
            else:
                # Trim the final range at source-time equivalent so output lands
                # at the requested duration. For timelapses, source span consumed
                # is output_remaining * speed.
                nr = copy.deepcopy(r)
                try:
                    speed = float(nr.get("speed") or 1.0)
                except (TypeError, ValueError):
                    speed = 1.0
                speed = min(10.0, max(1.0, speed))
                start = float(nr.get("start", 0.0))
                nr["end"] = min(float(nr.get("end", start)), start + remaining * speed)
                nr["reason"] = (nr.get("reason", "") + " Trimmed for social duration cap.").strip()
                if _range_output_duration(nr) > 0.05:
                    kept.append(nr)
                break
        out["ranges"] = kept
        out.pop("edl", None)
    return out


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _crop_derivative(src: Path, out: Path, *, width: int, height: int, fps: str = "30") -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Scale to cover the target, then center crop. This is the safe baseline
    # before smarter face/object reframe hints exist.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p"
    )
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ])
    meta = _ffprobe_json(out)
    video = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
    return {
        "output": str(out),
        "width": video.get("width"),
        "height": video.get("height"),
        "avg_frame_rate": video.get("avg_frame_rate"),
        "duration_s": float((meta.get("format") or {}).get("duration") or 0.0),
    }


def _beat_rows(edl: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    cursor = 0.0
    for i, r in enumerate(_ranges(edl)):
        dur = _range_output_duration(r)
        rows.append({
            "index": i,
            "output_start_s": cursor,
            "output_end_s": cursor + dur,
            "beat": r.get("beat") or f"Beat {i + 1}",
            "quote": r.get("quote"),
            "reason": r.get("reason"),
            "source": r.get("source"),
        })
        cursor += dur
    return rows


def _fmt_time(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _write_metadata_docs(edl: dict[str, Any], social_dir: Path) -> dict[str, str]:
    rows = _beat_rows(edl)
    title_base = edl.get("name") or "Premiere Agent cut"
    first_quote = next((r.get("quote") for r in _ranges(edl) if r.get("quote")), None)
    first_beat = rows[0]["beat"] if rows else title_base

    chapters = ["# Chapters", ""]
    for row in rows:
        chapters.append(f"- {_fmt_time(row['output_start_s'])} — {row['beat']}")
    chapters.append("")

    titles = [
        "# Title ideas", "",
        f"1. {first_beat}",
        f"2. {title_base}: the clearest version",
        f"3. What this clip really says about {first_beat}",
    ]
    if first_quote:
        titles.append(f"4. \"{first_quote[:80]}{'…' if len(first_quote) > 80 else ''}\"")
    titles.append("")

    description = [
        "# Description", "",
        f"Social derivative package generated from `{title_base}`.", "",
        "## Beat notes", "",
    ]
    for row in rows:
        description.append(f"- **{row['beat']}** ({_fmt_time(row['output_start_s'])}): source `{row['source']}`")
        if row.get("quote"):
            description.append(f"  - Quote: {row['quote']}")
        if row.get("reason"):
            description.append(f"  - Reason: {row['reason']}")
    description.append("")

    thumbnails = [
        "# Thumbnail prompts", "",
        f"- Clean editorial thumbnail for `{title_base}`; bold readable 3–5 word headline based on `{first_beat}`; high contrast; no fake UI.",
        f"- Vertical shorts cover frame: subject/action from `{first_beat}`, large kinetic text, safe center composition.",
        "- Alternative: use the strongest emotional/visual beat from the contact sheet, with minimal text and brand-safe colors.",
        "",
    ]

    docs = {
        "chapters": social_dir / "chapters.md",
        "titles": social_dir / "titles.md",
        "description": social_dir / "description.md",
        "thumbnail_prompts": social_dir / "thumbnail_prompts.md",
    }
    docs["chapters"].write_text("\n".join(chapters), encoding="utf-8")
    docs["titles"].write_text("\n".join(titles), encoding="utf-8")
    docs["description"].write_text("\n".join(description), encoding="utf-8")
    docs["thumbnail_prompts"].write_text("\n".join(thumbnails), encoding="utf-8")
    return {k: str(v) for k, v in docs.items()}


def build_social_package(
    edl_path: Path,
    *,
    preset: str = "youtube_shorts",
    output_dir: Path | None = None,
    max_vertical_s: float = 60.0,
) -> dict[str, Any]:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; valid: {sorted(PRESETS)}")
    edl_path = edl_path.resolve()
    edl = _load_edl(edl_path)
    social_dir = (output_dir or (edl_path.parent / "social")).resolve()
    social_dir.mkdir(parents=True, exist_ok=True)

    main_edl = _make_derivative_edl(edl, name_suffix="main_captioned")
    vertical_edl = _make_derivative_edl(edl, name_suffix="vertical_60s", max_duration_s=max_vertical_s)
    square_edl = _make_derivative_edl(edl, name_suffix="square")

    main_edl_path = social_dir / "main_captioned.edl.json"
    vertical_edl_path = social_dir / "vertical_60s.edl.json"
    square_edl_path = social_dir / "square.edl.json"
    _write_json(main_edl_path, main_edl)
    _write_json(vertical_edl_path, vertical_edl)
    _write_json(square_edl_path, square_edl)

    srt = edl_path.parent / "master.srt"
    burn_preset = "standard" if srt.exists() else "none"

    main_mp4 = social_dir / "main_captioned.mp4"
    main_report = render_preview(
        main_edl_path,
        main_mp4,
        resolution=(1920, 1080),
        fps="30",
        burn_subtitles=burn_preset,
        subtitles=srt if srt.exists() else None,
        contact_sheet=social_dir / "main_contact_sheet.jpg",
        report_path=social_dir / "main_captioned.render_report.json",
    )

    # Render vertical from the duration-trimmed derivative EDL first, then
    # center-crop into 9:16. This keeps vertical_60s duration honest.
    vertical_base = social_dir / "vertical_60s_base.mp4"
    vertical_base_report = render_preview(
        vertical_edl_path,
        vertical_base,
        resolution=(1920, 1080),
        fps="30",
        burn_subtitles=burn_preset,
        subtitles=srt if srt.exists() else None,
        contact_sheet=social_dir / "vertical_60s_contact_sheet.jpg",
        report_path=social_dir / "vertical_60s_base.render_report.json",
    )
    vertical = _crop_derivative(vertical_base, social_dir / "vertical_60s.mp4", width=1080, height=1920)
    try:
        vertical_base.unlink()
    except OSError:
        pass

    square = _crop_derivative(main_mp4, social_dir / "square.mp4", width=1080, height=1080)
    docs = _write_metadata_docs(edl, social_dir)

    report = {
        "preset": preset,
        "source_edl": str(edl_path),
        "output_dir": str(social_dir),
        "derivative_edls": {
            "main_captioned": str(main_edl_path),
            "vertical_60s": str(vertical_edl_path),
            "square": str(square_edl_path),
        },
        "outputs": {
            "main_captioned": {
                "output": str(main_mp4),
                "duration_s": _duration_from_report(main_report),
                "width": (main_report.get("video") or {}).get("width"),
                "height": (main_report.get("video") or {}).get("height"),
                "contact_sheet": main_report.get("contact_sheet"),
            },
            "vertical_60s": vertical,
            "square": square,
        },
        "subtitles": {
            "burn_preset": burn_preset,
            "srt": str(srt) if srt.exists() else None,
            "note": "Burned into social MP4s when master.srt exists; SRT remains canonical for NLE import.",
        },
        "intermediate_reports": {
            "main_captioned": str(social_dir / "main_captioned.render_report.json"),
            "vertical_base": str(social_dir / "vertical_60s_base.render_report.json"),
            "vertical_base_duration_s": _duration_from_report(vertical_base_report),
        },
        "docs": docs,
        "beat_count": len(_ranges(edl)),
    }
    report_path = social_dir / "package_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build social derivative outputs from a Premiere Agent EDL")
    ap.add_argument("edl", type=Path, help="Path to approved edl.json")
    ap.add_argument("--preset", default="youtube_shorts", choices=sorted(PRESETS))
    ap.add_argument("-o", "--output-dir", type=Path, default=None, help="Output directory (default: <edl_dir>/social)")
    ap.add_argument("--max-vertical-seconds", type=float, default=60.0)
    args = ap.parse_args(argv)

    try:
        report = build_social_package(
            args.edl,
            preset=args.preset,
            output_dir=args.output_dir,
            max_vertical_s=args.max_vertical_seconds,
        )
    except Exception as e:
        print(f"[build_social_package] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"[build_social_package] wrote {report['output_dir']}")
    print(f"[build_social_package] report: {Path(report['output_dir']) / 'package_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
