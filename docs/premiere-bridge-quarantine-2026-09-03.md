# Premiere Agent MCP/CEP/UXP quarantine

Date: 2026-09-03

## Reason

Si has a working Claude/Premiere setup based on:

```text
https://github.com/leancoderkavy/premiere-pro-mcp
```

The Econ Films `premiere-agent` live MCP/CEP/UXP bridge should remain quarantined so it does not interfere with that working build.

This quarantine preserves the repo code for reference/history but disables local runtime/config/install artifacts.

## Scope quarantined

Local repo preserved:

```text
/Users/sivmacstudio/Premiere_AI/premiere-agent
```

Quarantined/disabled local integration points:

- Hermes MCP server `premiere-agent` in the default profile.
- Hermes MCP server `premiere-agent` in the `premiere-bot` profile.
- Claude Desktop MCP server entry `premiere-agent` pointing at this repo's Python MCP server.
- CEP extension symlink/folder `com.econfilms.premiereagent.bridge`.
- UXP plugin registration/copies for `com.econfilms.premiereagent.uxp`.
- Stray Python `premiere_agent_mcp.py` processes launched from this repo.

The external/working bridge was intentionally left alone:

```text
MCPBridgeCEP
premiere-pro-mcp
com.mcp.premiere.bridge.*
```

## Actions performed

### Hermes MCP removals

Ran:

```bash
hermes mcp remove premiere-agent
premiere-bot mcp remove premiere-agent
```

Verification after removal:

```text
default profile: no premiere-agent MCP server listed
premiere-bot: No MCP servers configured
```

The default profile still had a separate disabled entry:

```text
premiere-pro-uxp ... disabled
```

That entry was left untouched.

### Claude Desktop MCP removal

Backed up:

```text
/Users/sivmacstudio/Library/Application Support/Claude/claude_desktop_config.json.premiere-agent-quarantine-20260903-152131.bak
```

Removed only the old MCP server entry:

```text
mcpServers.premiere-agent
```

That entry had pointed at:

```text
/Volumes/SSD Storage 1/Premiere_AI/premiere-agent/mcp/premiere_agent_mcp.py
```

Verification after removal:

```json
"claude_old_mcp_entries": []
```

### CEP symlink quarantine

Moved:

```text
/Users/sivmacstudio/Library/Application Support/Adobe/CEP/extensions/com.econfilms.premiereagent.bridge
```

To:

```text
/Users/sivmacstudio/Library/Application Support/Adobe/CEP/extensions/_quarantined_econfilms_premiere_agent/com.econfilms.premiereagent.bridge
```

Verification:

```json
"cep_econfilms_exists": false,
"cep_quarantine_exists": true
```

### UXP registration/copy quarantine

Backed up Premiere UXP registration:

```text
/Users/sivmacstudio/Library/Application Support/Adobe/UXP/PluginsInfo/v1/premierepro.json.premiere-agent-quarantine-20260903-152131.bak
```

Removed the active UXP registration entry for:

```text
com.econfilms.premiereagent.uxp
```

Saved the removed registration manifest to:

```text
/Users/sivmacstudio/Library/Application Support/Adobe/UXP/Plugins/External/_quarantined_econfilms_premiere_agent/removed-registration-20260903-152131.json
```

Moved installed UXP copies:

```text
/Users/sivmacstudio/Library/Application Support/Adobe/UXP/Plugins/External/com.econfilms.premiereagent.uxp_0.2.0
/Users/sivmacstudio/Library/Application Support/Adobe/UXP/Plugins/External/com.econfilms.premiereagent.uxp_0.1.0
```

To:

```text
/Users/sivmacstudio/Library/Application Support/Adobe/UXP/Plugins/External/_quarantined_econfilms_premiere_agent/com.econfilms.premiereagent.uxp_0.2.0
/Users/sivmacstudio/Library/Application Support/Adobe/UXP/Plugins/External/_quarantined_econfilms_premiere_agent/com.econfilms.premiereagent.uxp_0.1.0
```

Verification:

```json
"uxp_copy_020_exists": false,
"uxp_copy_010_exists": false,
"uxp_quarantine_020_exists": true,
"uxp_quarantine_010_exists": true,
"uxp_registry_entries": 0
```

### Process cleanup

Stopped old Python MCP processes launched from this repo path:

```text
/Volumes/SSD Storage 1/Premiere_AI/premiere-agent/mcp/premiere_agent_mcp.py
```

Verification after cleanup:

```json
"old_econ_mcp_processes": []
```

Port check:

```bash
lsof -nP -iTCP:48791 -sTCP:LISTEN
```

Verification result:

```text
no listener on TCP port 48791
```

External `premiere-pro-mcp`/`MCPBridgeCEP` processes were still present and left untouched.

## Quarantine report

A local JSON action report was saved at:

```text
/Users/sivmacstudio/.hermes/quarantine-reports/premiere-agent-quarantine-20260903-152131.json
```

## How to re-enable later, if explicitly requested

Do not re-enable automatically. If Si explicitly asks to restore this old bridge, reverse only the necessary pieces:

1. Re-add Hermes MCP if needed:

```bash
hermes mcp add premiere-agent --command python --args /Users/sivmacstudio/Premiere_AI/premiere-agent/mcp/premiere_agent_mcp.py
```

2. Reinstall CEP symlink if needed:

```bash
cd /Users/sivmacstudio/Premiere_AI/premiere-agent
python3 scripts/install_premiere_bridge.py
```

3. Reinstall UXP if needed:

```bash
cd /Users/sivmacstudio/Premiere_AI/premiere-agent
python3 scripts/install_premiere_uxp.py
```

4. Restart Premiere/Claude/Hermes sessions so stale in-memory extensions/configs are flushed.

## Current policy

Prefer the working external bridge:

```text
leancoderkavy/premiere-pro-mcp
```

Keep this repo's live bridge code available only as reference and fallback implementation history. Do not run its MCP/CEP/UXP link unless Si explicitly asks to re-enable it.
