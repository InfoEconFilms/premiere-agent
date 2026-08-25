# Premiere Agent UXP frontend handoff — 2026-08-24

## Current status

The live Premiere review-frame/contact-sheet lane is working, and the first UXP frontend scaffold has been added to the repository.

Latest pushed commits relevant to this handoff:

```text
36cb63c Use short H264 proof clips for review frames
446c2a6 Add Premiere Agent UXP frontend scaffold
c3b75e7 Add local Premiere UXP installer
```

Last full verification:

```text
101 passed  0 failed  1 skipped
```

## Working review-frame lane

The live bridge can export review frames from the active Premiere sequence. On this machine, Premiere/QE still export fails and AME still-image presets are unavailable, so the successful fallback is short H.264 proof clips.

Verified working fallback chain:

```text
QE still export fails
→ AME still preset unavailable
→ H.264 short proof clip fallback works
→ local ffmpeg frame extraction/contact sheet works
```

Known working JSON-RPC method:

```text
export_sequence_review_frames
```

Example returned method:

```text
ame_h264_short_proof_export
```

Example proof clip notes include:

```text
AME proof clip duration_s: 0.25
```

Local contact sheet path used in testing:

```text
/private/tmp/premiere-agent-review-frames/contact_sheet.jpg
```

## UXP frontend scaffold

New scaffold directory:

```text
premiere_uxp/
├── manifest.json
├── index.html
├── index.cjs
├── styles.css
└── THIRD_PARTY_NOTICES.md
```

UXP plugin id:

```text
com.econfilms.premiereagent.uxp
```

The scaffold has been reset to a reference-derived frontend:

1. `premiere_uxp/` is copied from the MIT-licensed `uxp-plugin/` reference build in `leancoderkavy/premiere-pro-mcp` and then rebranded/registerable as `com.econfilms.premiereagent.uxp`.
2. The reference WebSocket bridge, command registry, workspace broker, event journal, transcript support, workflow modules, and capability probing are preserved as the authoritative UXP architecture.
3. Econ/Premiere Agent additions live in a small separate adapter file: `premiere_uxp/premiere-agent-workflows.cjs`.
4. The adapter adds only the currently useful local CEP fallback buttons: verify CEP, read sequence, list markers, and export review frames to `/private/tmp/premiere-agent-review-frames`.

Current UXP panel controls inherited from the reference build:

- connect to the UXP WebSocket bridge;
- refresh/publish Premiere state;
- choose/revoke an approved workspace folder;
- show workspace/status output.

Current Premiere Agent adapter controls:

- verify the local CEP JSON-RPC bridge;
- read the active sequence through the CEP bridge;
- list active-sequence markers through the CEP bridge;
- export review frames through the verified CEP fallback path.

Do not re-add marker/caption write buttons directly to the starter panel until the reference UXP command surface and safety/verification contract are mapped deliberately for Econ workflows.

The UI scaffold adapts MIT-licensed patterns from:

```text
https://github.com/leancoderkavy/premiere-pro-mcp
```

Attribution is recorded in:

```text
premiere_uxp/THIRD_PARTY_NOTICES.md
```

## UXP load failure diagnosis

The user reported this exact error:

```text
Action: Plugin Load
com.econfilms.premiereagent.uxp
Plugin Load Failed.
Host Application specified is not available. Make sure the host application is started.
```

Findings:

- The manifest host id was correct: `premierepro`.
- Adobe's Premiere UXP docs use `host.app: "premierepro"`.
- The external Premiere Pro MCP UXP plugin also uses `host.app: "premierepro"`.
- The running Premiere process reported `PPRO 26.3.0`, which satisfies the UXP minimum.
- Premiere's UXP log confirmed:

```text
Product: premierepro
Product ID: PPRO
Version: 26.3.0
```

Actual issue:

Premiere was reading:

```text
~/Library/Application Support/Adobe/UXP/PluginsInfo/v1/premierepro.json
```

and that file only registered an older dev plugin:

```text
546120d7
```

`com.econfilms.premiereagent.uxp` was not registered, so Premiere never attempted to load it. The failure was therefore at UXP Developer Tool host-discovery/registration, not at our manifest JS/runtime.

## Local UXP installer fix

Added script:

```text
scripts/install_premiere_uxp.py
```

Run from repo root:

```bash
python3 scripts/install_premiere_uxp.py
```

What it does:

1. Validates `premiere_uxp/manifest.json`.
2. Copies the UXP panel into Adobe's local External plugin folder:

```text
~/Library/Application Support/Adobe/UXP/Plugins/External/com.econfilms.premiereagent.uxp_0.1.0
```

3. Updates the Premiere per-host PluginsInfo file:

```text
~/Library/Application Support/Adobe/UXP/PluginsInfo/v1/premierepro.json
```

4. Makes a timestamped backup of the previous PluginsInfo file.

Verified registered entry:

```json
{
  "hostMinVersion": "25.6",
  "name": "Premiere Agent",
  "path": "$localPlugins/External/com.econfilms.premiereagent.uxp_0.1.0",
  "pluginId": "com.econfilms.premiereagent.uxp",
  "status": "enabled",
  "type": "uxp",
  "versionString": "0.1.0"
}
```

The manifest was adjusted to match the working local Premiere registration convention:

```json
"host": { "app": "premierepro", "minVersion": "25.6" }
```

Next manual verification step:

```text
Enable Premiere Pro Developer Mode if needed, quit and reopen Premiere Pro, then open Window → UXP Plugins → Premiere Agent.
```

Observed follow-up:

```text
After enabling Developer Mode in Premiere Pro, the Premiere Agent UXP panel appeared in Premiere.
```

If it still does not appear, inspect the newest log under:

```text
~/Library/Logs/Adobe/Adobe Premiere Pro 2026/UXPLogs_*.log
```

Search for:

```text
com.econfilms.premiereagent.uxp
Premiere Agent
Failed to parse the manifest.json file
Expected 'host.app' to be premierepro
```

## Architecture decision

Do not abandon CEP yet. Current direction is hybrid:

```text
UXP frontend + documented UXP reads
CEP/ExtendScript localhost JSON-RPC bridge for proven live tooling and QE fallbacks
```

Reasons:

- CEP bridge already works for Hermes/MCP → local JSON-RPC → ExtendScript/QE.
- UXP is the better long-term panel/frontend target.
- Some Premiere operations still need ExtendScript/QE or proven CEP bridge fallbacks.
- Professional source of truth remains XML/FCPXML/SRT; live bridge and review renders are additive.

## Next useful implementation steps

1. Restart Premiere and verify the UXP panel appears.
2. Open `Window → UXP Plugins → Premiere Agent`.
3. Click **Refresh** to verify direct UXP project/sequence state.
4. Keep CEP bridge panel open and click **Verify** in the UXP panel.
5. Click **List markers** and **Review frames** from UXP panel to validate hybrid UI actions.
6. After panel load is confirmed, add screenshots/UX polish and move toward a proper UXP-native transport/WebSocket bridge.

## Safety constraints to preserve

- Live writes remain safety-gated.
- Use dry-run first where applicable.
- Duplicate/backup sequence before timeline mutation.
- No destructive operations by default.
- Read back/verify every write.
- Do not upload footage or use paid/cloud/generative video services without explicit consent.
- Keep XML/FCPXML/SRT as the professional handoff source of truth.
