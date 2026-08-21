# Premiere-side bridge scaffold

This folder is a scaffold for replacing the mock Python backend in `mcp/premiere_bridge_server.py` with real Adobe Premiere Pro calls from a CEP/UXP panel.

The Python bridge already implements the local HTTP JSON-RPC endpoint expected by `mcp/premiere_live_bridge.py`:

```bash
python mcp/premiere_bridge_server.py
export PREMIERE_AGENT_BRIDGE_URL=http://127.0.0.1:48791/jsonrpc
```

For now it uses a deterministic mock backend. A real Premiere panel should implement the same methods and either:

1. expose its own local HTTP JSON-RPC server, or
2. connect to the Python bridge and replace/forward `MockPremiereBackend.dispatch()` calls.

## Required JSON-RPC methods

All requests are JSON-RPC 2.0 POSTs.

### `status`

Return bridge and Premiere connection state.

```json
{"ok": true, "premiere_connected": true, "project_id": "...", "active_sequence_id": "..."}
```

### `get_active_project`

Return project id, name, path, and sequence count.

### `get_active_sequence`

Return sequence id/name, dimensions, fps, duration, marker count, selected range if known.

### `snapshot_sequence`

Return enough timeline state for the orchestrator to verify after a mutation:

- sequence id/name
- tracks and clip ids/names/time ranges
- markers
- selected range/in-out points
- optional exported still/contact sheet path

### `duplicate_sequence`

Params:

```json
{"sequence_id": "seq_123", "backup_name": "Main_AI_BACKUP"}
```

Return:

```json
{"ok": true, "sequence_id": "seq_123", "backup_sequence_id": "seq_backup"}
```

### `add_marker`

Params must include `backup_sequence_id`.

```json
{
  "sequence_id": "seq_123",
  "backup_sequence_id": "seq_backup",
  "time_s": 42.0,
  "label": "EDITOR NOTE",
  "color": "red",
  "comment": "Check this instruction before cutting."
}
```

### `import_media`

Import a generated asset — motion graphic, caption file, overlay, etc. — to the target sequence/range. Params must include `backup_sequence_id`.

### `queue_export`

Queue/render a sequence or selected range to a file. Params must include `backup_sequence_id`.

### `apply_basic_lumetri`

Apply bounded colour adjustments or a named safe preset. Params must include `backup_sequence_id`.

### `set_clip_transform`

Apply scale/position/keyframe-style transform to a clip/range. Params must include `backup_sequence_id`.

## Safety rules

- Never mutate the original sequence before `duplicate_sequence` has succeeded.
- Never implement destructive methods (`delete_clip`, `delete_track`, `overwrite_sequence`) until the orchestrator has a stronger confirmation and rollback path.
- Return stable ids for anything changed: marker id, clip id, sequence id, export job id.
- After every write, support `snapshot_sequence` so the orchestrator can verify state.
- If Premiere is disconnected, return a JSON-RPC error rather than silently no-oping.

## CEP implementation hints

CEP panels can call ExtendScript via `CSInterface.evalScript(...)`. The panel JS should:

1. listen for JSON-RPC requests,
2. map method names to ExtendScript functions,
3. JSON-stringify all return values,
4. surface Premiere/API errors as JSON-RPC errors.

See `cep_panel_stub.js` for the adapter shape.

## UXP implementation hints

UXP should expose the same JSON-RPC methods, but call the modern Premiere APIs where available. Keep method names and response shapes identical so the Python/Hermes side does not care whether CEP, UXP, or a hybrid panel handled the request.
