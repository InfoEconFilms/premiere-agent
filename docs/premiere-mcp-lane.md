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

It exposes safe tools:

| Tool | Side effect | Purpose |
| --- | --- | --- |
| `premiere_agent_validate_edl` | read-only | Validate an `edl.json` before render/import/export. |
| `premiere_agent_render_preview` | writes MP4/report | Render a flattened review MP4 using `helpers/render_preview.py`. |
| `premiere_agent_build_social_package` | writes social package | Build main/vertical/square derivative outputs. |
| `premiere_agent_batch_export_plan` | writes JSON plan | Turn ranges/sequences into a naming-safe export manifest. |
| `premiere_agent_nle_safety_checklist` | read-only | Return the mandatory safety steps before live-NLE mutation. |

## Hermes registration

Once the server is ready, add it to Hermes as a stdio MCP server:

```bash
hermes mcp add premiere-agent \
  --command python \
  --args /Users/sivmacstudio/Premiere_AI/premiere-agent/mcp/premiere_agent_mcp.py
hermes mcp test premiere-agent
```

Then restart the session or run `/reload-mcp` in a Hermes CLI session.

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
