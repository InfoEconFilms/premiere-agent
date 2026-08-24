# Hermes Bot transfer — Premiere Agent

Date: 2026-08-24

## What was transferred

Created a dedicated Hermes Bot/profile for Premiere Agent work:

```text
premiere-bot
```

Profile directory:

```text
/Users/sivmacstudio/.hermes/profiles/premiere-bot
```

Wrapper command created by Hermes:

```bash
premiere-bot chat
```

The profile was created by cloning the default profile, then specializing its SOUL and config for Premiere Agent work.

## Bot purpose

`premiere-bot` is a specialist Hermes Bot for Econ Films / Si post-production workflows:

- Premiere Agent repo development;
- Adobe Premiere MCP/live bridge operations;
- XML/FCPXML/SRT professional handoff;
- live sequence read/status tools;
- marker listing/readback;
- review-frame/contact-sheet exports;
- SRT/caption import scaffolding;
- editorial note marker passes;
- CEP/ExtendScript debugging;
- UXP frontend/installer troubleshooting.

## Important local repo

```text
/Users/sivmacstudio/Premiere_AI/premiere-agent
```

## Bot SOUL

The bot SOUL was written to:

```text
/Users/sivmacstudio/.hermes/profiles/premiere-bot/SOUL.md
```

Key instructions encoded there:

- remain Hermes Agent, but specialize as Premiere Bot;
- keep XML/FCPXML/SRT as professional source of truth;
- treat MP4 previews/contact sheets/social derivatives as additive;
- use live Premiere operations with safety gates;
- dry-run/read first where available;
- duplicate/backup sequence before timeline mutation;
- verify every live write by reading back the exact target;
- do not upload footage or use paid/cloud/generative services without explicit consent;
- load `premiere-agent`, `hermes-agent`, agentic video, CEP/UXP, and software lifecycle skills as relevant;
- remember the current UXP registration failure/fix.

## Skill access

The bot profile inherited the repo skill external directory:

```yaml
skills:
  external_dirs:
    - /Users/sivmacstudio/Premiere_AI/premiere-agent
```

This allows the bot to load the repo's `premiere-agent` skill directly.

Verified during readiness test: the bot auto-loaded `premiere-agent`.

## MCP server

The bot profile inherited and verified the Premiere MCP server:

```yaml
mcp_servers:
  premiere-agent:
    command: python
    args:
      - /Users/sivmacstudio/Premiere_AI/premiere-agent/mcp/premiere_agent_mcp.py
    enabled: true
```

Verification command:

```bash
premiere-bot mcp test premiere-agent
```

Verified result:

```text
✓ Connected
✓ Tools discovered: 17
```

Discovered tools included:

- `premiere_agent_validate_edl`
- `premiere_agent_render_preview`
- `premiere_agent_build_social_package`
- `premiere_agent_batch_export_plan`
- `premiere_agent_nle_safety_checklist`
- `premiere_agent_live_bridge_protocol_spec`
- `premiere_agent_live_bridge_status`
- `premiere_agent_verify_premiere_connection`
- `premiere_agent_get_sequence_structure`
- `premiere_agent_list_markers`
- `premiere_agent_plan_live_job`
- `premiere_agent_duplicate_sequence`
- `premiere_agent_add_marker`
- `premiere_agent_add_editorial_markers`
- `premiere_agent_export_review_frames`
- `premiere_agent_import_captions`
- `premiere_agent_queue_export`

## Config cleanup

The cloned profile initially warned about a stale/unknown `messaging` toolset. Removed it from:

```text
/Users/sivmacstudio/.hermes/profiles/premiere-bot/config.yaml
```

After cleanup, the bot readiness test started without the warning.

## Readiness tests

First readiness test:

```bash
premiere-bot chat -q "Readiness check: in 5 bullets max, identify your role, the Premiere Agent repo path, the MCP server name, the UXP installer command, and the safety rule for live Premiere writes. Do not modify files."
```

Result included the correct role, repo, MCP server, UXP installer, and live-write safety rule.

Second readiness test after config cleanup:

```bash
premiere-bot chat -q "Readiness check after config cleanup: answer in one sentence with your role, repo path, MCP server name, and UXP installer command. Do not modify files."
```

Verified response:

```text
I’m Premiere Bot, working from /Users/sivmacstudio/Premiere_AI/premiere-agent, using the premiere-agent MCP server, and the UXP installer command is python3 scripts/install_premiere_uxp.py.
```

## Bot Mode / UI note

Hermes Bot Mode treats a Bot as a profile. `premiere-bot` should now appear as a bot/profile in the desktop Bots UI. CLI access is also available:

```bash
premiere-bot chat
hermes -p premiere-bot chat
```

## Next transfer steps

1. Open the Hermes Desktop Bots view and confirm `premiere-bot` appears.
2. Start a Bot Chat with it and ask for Premiere Agent status.
3. Optionally start the bot gateway if this bot should be reachable over Telegram/Discord/etc.:

```bash
premiere-bot gateway start
```

4. If transferring to the Oracle/Coolify cloud Hermes instance, package this profile as a Hermes profile distribution or recreate the same pieces there:
   - `SOUL.md`;
   - `skills.external_dirs` or copied `premiere-agent` skill;
   - `mcp_servers.premiere-agent` if the cloud box can reach a Premiere bridge or a remote proxy;
   - matching toolsets and model config;
   - no copied secrets unless explicitly approved.

## Open architecture question

The local bot can use the live Premiere MCP because it runs on the Mac next to Premiere and the CEP/UXP panels. A cloud bot cannot directly call `127.0.0.1:48791` on the Mac unless we add a secure relay/tunnel or use the cloud bot only for planning/code/review work. Keep live NLE control local until a safe remote bridge design is explicitly approved.
