# Premiere MCP lane — starter architecture

This lane is the bridge from Premiere Agent's current file-based workflow into an agentic Premiere Pro control loop.

## Goal

Keep the existing XML/FCPXML/SRT workflow as the safe source of truth, then add an optional MCP path for jobs where the user wants the agent to operate around a live Premiere project: markers, captions, zooms, motion-graphic imports, sequence duplication, and batch exports.

## Principles

1. **XML-first stays safe.** `edl.json`, `cut.xml`, `cut.fcpxml`, and `master.srt` remain the professional fallback and interchange path.
2. **Direct NLE control is high side-effect.** Any live-Premiere mutation must duplicate/backup the target sequence first and require explicit user intent.
3. **Orchestrator owns taste and safety.** Worker agents can trim, caption, render, or plan graphics, but the orchestrator verifies outputs before handoff.
4. **Everything is provenance-backed.** Every rendered/exported asset records source EDL, source clips, prompt/tool, output path, duration, and verification result.
5. **Start with safe file tools.** Before controlling Premiere directly, expose local MCP tools that validate EDLs, render review MP4s, build social packages, and generate batch-export plans.

## Starter MCP server

The first server is local and stdio-based:

```bash
python mcp/premiere_agent_mcp.py
```

It exposes safe tools plus dry-run live-bridge planning tools:

| Tool | Side effect | Purpose |
| --- | --- | --- |
| `premiere_agent_validate_edl` | read-only | Validate an `edl.json` before render/import/export. |
| `premiere_agent_render_preview` | writes MP4/report | Render a flattened review MP4 using `helpers/render_preview.py`. |
| `premiere_agent_build_social_package` | writes social package | Build main/vertical/square derivative outputs. |
| `premiere_agent_batch_export_plan` | writes JSON plan | Turn ranges/sequences into a naming-safe export manifest. |
| `premiere_agent_nle_safety_checklist` | read-only | Return the mandatory safety steps before live-NLE mutation. |
| `premiere_agent_live_bridge_protocol_spec` | read-only | Return the JSON-RPC contract a CEP/UXP bridge must implement. |
| `premiere_agent_live_bridge_status` | read-only/dry-run | Check bridge connectivity or produce the request shape when no bridge is configured. |
| `premiere_agent_verify_premiere_connection` | read-only/dry-run | First-run safety probe: bridge reachable, project open, active sequence readable, no names/paths/media details returned. |
| `premiere_agent_get_sequence_structure` | read-only/dry-run | Read tracks, clips, gaps, playhead, and host-snapshot verification for the active/target sequence. |
| `premiere_agent_list_markers` | read-only/dry-run | Read back sequence markers by time/name/comment for verification after marker writes. |
| `premiere_agent_plan_live_job` | read-only | Build an orchestrator plan for talking-head, batch-export, motion-graphic, or caption jobs. |
| `premiere_agent_duplicate_sequence` | write, dry-run default | Duplicate/backup a sequence; requires `confirm=true` for live execution. |
| `premiere_agent_add_marker` | write, dry-run default | Add a sequence marker; requires `confirm=true` and `backup_sequence_id`. |
| `premiere_agent_add_editorial_markers` | write, dry-run default | Batch-add AI editorial markers for filler, retakes, in-clip editor notes, or review regions; requires `confirm=true` and `backup_sequence_id`. |
| `premiere_agent_export_review_frames` | render/file write, dry-run default | Export evenly spaced review frames from a live sequence. CEP first tries stills; on Premiere builds with no still preset it may return short H.264 proof clips, which the local side can frame-extract into a contact sheet. |
| `premiere_agent_import_captions` | write, dry-run default | Import an `.srt`/`.vtt` caption file and scaffold caption-track creation where Premiere exposes it. |
| `premiere_agent_queue_export` | render/write, dry-run default | Queue a sequence/range export; requires `confirm=true` and `backup_sequence_id`. |

## Hermes registration

Once the server is ready, add it to Hermes as a stdio MCP server:

```bash
hermes mcp add premiere-agent \
  --command python \
  --args /Users/sivmacstudio/Premiere_AI/premiere-agent/mcp/premiere_agent_mcp.py
hermes mcp test premiere-agent
```

Then restart the session or run `/reload-mcp` in a Hermes CLI session.

## Live bridge protocol

The tool adapter lives in:

```text
mcp/premiere_live_bridge.py
```

It expects a local bridge URL in:

```bash
export PREMIERE_AGENT_BRIDGE_URL=http://127.0.0.1:48791/jsonrpc
```

A runnable mock bridge scaffold now ships in:

```bash
python mcp/premiere_bridge_server.py
```

An installable CEP panel scaffold lives in:

```text
premiere_bridge/
```

Install it for local Premiere testing with:

```bash
python scripts/install_premiere_bridge.py
```

That installs a symlink into the Adobe CEP extensions folder and enables unsigned CEP debug mode on macOS. Restart Premiere, then open `Window → Extensions → Premiere Agent Bridge`.

A UXP frontend scaffold now lives in:

```text
premiere_uxp/
```

Side-load it during development with Adobe UXP Developer Tool, then open `Window → UXP Plugins → Premiere Agent`. If UXP Developer Tool reports `Host Application specified is not available` even while Premiere is running, install/register the panel directly with:

```bash
python3 scripts/install_premiere_uxp.py
```

That copies `premiere_uxp/` into Adobe's local External UXP plugin folder and updates `~/Library/Application Support/Adobe/UXP/PluginsInfo/v1/premierepro.json` with a timestamped backup. Restart Premiere after registration.

The UXP panel is the preferred user-facing frontend for documented Premiere APIs and status UX. It currently uses a hybrid architecture: UXP reads active project/sequence state directly through Premiere's documented UXP API, while the proven CEP localhost JSON-RPC bridge remains the compatibility transport/fallback for ExtendScript/QE methods and the already-verified live tools. The UXP scaffold adapts MIT-licensed UI patterns from `leancoderkavy/premiere-pro-mcp`; attribution is recorded in `premiere_uxp/THIRD_PARTY_NOTICES.md`.

The mock server implements every initial JSON-RPC method against an in-memory mock Premiere project, so the MCP live tools can be tested end-to-end without Premiere. The CEP panel now exposes the same endpoint from inside Premiere when opened. It loads `extendscript_bridge.jsx`, which implements real read probes (`status`, sanitized `verify_premiere_connection`, active project/sequence, sequence snapshots, richer `get_sequence_structure`, `list_markers`) and guarded write attempts for backup, markers, editorial-marker batches, caption import, review-frame export, media import, and export handoff where Premiere exposes the needed APIs.

The bridge should accept JSON-RPC 2.0 POST requests:

```json
{
  "jsonrpc": "2.0",
  "id": "premiere-agent-1",
  "method": "add_marker",
  "params": {
    "sequence_id": "seq_123",
    "backup_sequence_id": "seq_123_AI_BACKUP",
    "time_s": 42.0,
    "label": "EDITOR NOTE",
    "color": "red",
    "comment": "Review before cutting."
  }
}
```

Required read methods:

- `status`
- `verify_premiere_connection`
- `get_active_project`
- `get_active_sequence`
- `snapshot_sequence`
- `get_sequence_structure`
- `list_markers`

Initial write methods:

- `duplicate_sequence`
- `add_marker`
- `add_editorial_markers`
- `import_media`
- `import_captions`
- `export_sequence_review_frames`
- `queue_export`
- `apply_basic_lumetri`
- `set_clip_transform`

Unsupported/destructive until later:

- `delete_clip`
- `delete_track`
- `overwrite_sequence`

Write policy enforced by the adapter:

1. Write tools require `confirm=true`.
2. Timeline-affecting writes require `backup_sequence_id` from `duplicate_sequence`.
3. Tools default to `dry_run=true`, so they return the request payload without touching Premiere.
4. Every live write must be followed by a verification action: snapshot, exported still/contact sheet, or rendered file.

## Future live-Premiere bridge

The next phase should introduce a separate Premiere-specific bridge rather than overloading the safe local server:

1. CEP/UXP panel or existing Premiere MCP bridge reports active project/sequence state.
2. Agent duplicates the target sequence before mutations.
3. Tools operate on bounded actions: add marker, import media at playhead/range, add caption track, set scale/position, apply Lumetri preset, queue export.
4. Agent verifies via timeline snapshot, exported frame/contact sheet, or rendered file.
5. Destructive operations require explicit user confirmation.

## Slash-command equivalent workflows

- `orchestrator talking head`: validate source EDL/timeline, duplicate sequence, trim/polish longform, plan shorts, caption, QA.
- `batch export shorts`: generate export plan from markers/ranges, render each item with naming scheme, ffprobe outputs, report skipped/failed.
- `motion graphic for in/out`: extract transcript for selected range, generate scene plan/prompt, render Remotion/Hyperframes/Manim asset, import above captions.

## Acceptance criteria for live control

- Sequence backup exists before mutation.
- Every write tool returns a verifiable Premiere object ID/path/range.
- Batch exports produce a machine-readable report.
- User can manually inspect/tweak the timeline after each agent pass.
- If the bridge disconnects, the file-based XML/render workflow still completes the job.
