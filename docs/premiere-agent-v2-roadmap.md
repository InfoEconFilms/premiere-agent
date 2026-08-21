# Premiere Agent v2 Roadmap

## Goal

Upgrade Premiere Agent from an XML-first editorial assistant into a broader agentic video-production assistant while preserving the current professional NLE handoff.

The current XML/FCPXML/SRT path remains authoritative. New render and motion-graphics features are additive review/delivery lanes, not replacements.

## Phase 1 — Rendered review lane

### Deliverables

- `helpers/render_preview.py <edit>/edl.json -o <edit>/review/main.mp4`
- Contact sheet: `<edit>/review/contact_sheet.jpg`
- Render report: `<edit>/review/render_report.json`
- Optional flags:
  - `--burn-subtitles {none,standard,bold,minimal}`
  - `--resolution 1920x1080`
  - `--fps auto|24|25|29.97|30`
  - `--watermark-timecode`

### Requirements

- Reads the same `edl.json` as `export_fcpxml.py`.
- Supports normal ranges and existing `speed` / `audio_strategy` timelapse fields.
- Uses ffmpeg only for the first pass.
- Does not alter XML export behavior.
- Validates output with `ffprobe`: duration, resolution, frame rate, audio presence.

### Acceptance tests

- Render a tiny synthetic two-clip EDL.
- Verify MP4 exists and has plausible non-zero duration.
- Verify SRT burn mode does not break render when `master.srt` exists.
- Verify `speed` field creates shorter output than source duration.

## Phase 2 — Social derivative package

### Deliverables

- `helpers/build_social_package.py <edit>/edl.json --preset youtube_shorts`
- Outputs under `<edit>/social/`:
  - `vertical_60s.mp4`
  - `square.mp4`
  - `main_captioned.mp4`
  - `chapters.md`
  - `titles.md`
  - `description.md`
  - `thumbnail_prompts.md`
  - `package_report.json`

### Requirements

- Uses an approved main EDL as the source of truth.
- Makes derivative EDLs rather than destructively changing the main EDL.
- For vertical crops, starts with safe center-crop and supports optional EDL `reframe` hints.
- Keeps professional NLE export available unchanged.

### Acceptance tests

- Build package from fixture EDL.
- Confirm generated clips meet expected aspect ratios.
- Confirm metadata files exist and include source beat references.

## Phase 3 — Styled captions and overlays

### Deliverables

- Shared caption styling module.
- Presets: `standard`, `minimal`, `bold_social`, `hormozi`.
- Optional EDL overlay directives:
  - `zoom`: punch-in/out with timing.
  - `text_overlay`: phrase, position, start/end.
  - `marker`: note for NLE/editor.

### Requirements

- SRT sidecar remains canonical for NLE caption import.
- Burned captions are for review/social MP4 outputs only.
- No mandatory cloud dependency.

## Phase 4 — Remotion motion-graphics lane

### Deliverables

- `helpers/motion_graphics_plan.py` to create scene-plan JSON.
- `helpers/remotion_bootstrap.py` to create `<edit>/motion_graphics/` project.
- `helpers/render_motion_graphics.py` for low-res preview and final render.
- Scene plan schema:
  - `brief`
  - `brand_sources`
  - `assets`
  - `scenes[]`
  - `voiceover/music/sfx`
  - `provenance`

### Requirements

- Remotion is the default for kinetic text, product UI, launch videos, social explainers.
- Manim remains specialist for mathematical/diagrammatic animations.
- All generated code/assets live in `<videos_dir>/edit/motion_graphics/`, never inside the skill repo.
- User approval required before paid/cloud asset generation or footage upload.

## Phase 5 — NLE and generative-tool integration

### Deliverables

- Optional Premiere/Resolve automation hooks.
- Marker/memo export for AI decisions.
- Shot repair candidates list for tools such as Runway, Adobe, Resolve.

### Requirements

- Ask before uploading source footage or spending money.
- Preserve provenance for generated/modified assets.

## First implementation task

Build Phase 1 minimally:

1. Inspect `export_fcpxml.py` EDL parsing assumptions.
2. Create a tiny fixture source video/audio with ffmpeg.
3. Write `helpers/render_preview.py` with basic concat support.
4. Add tests to `tests.py` or a new lightweight test file.
5. Verify by rendering a sample MP4 and running `ffprobe`.

## Notes

This roadmap was created after reviewing Si's supplied YouTube references and the Aug 2026 frontier scan in `references/agentic-video-editing-frontier-2026.md`.
