#!/usr/bin/env python3
"""Install the Premiere Agent UXP panel for local Premiere development.

Adobe UXP Developer Tool can fail to discover Premiere even when the host is
running. Premiere itself reads a per-host PluginsInfo JSON file, so this script
registers the local UXP scaffold directly in the same shape Premiere already
uses for External development plugins.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UXP_SOURCE = PROJECT_ROOT / "premiere_uxp"
ADOBE_UXP_ROOT = Path.home() / "Library" / "Application Support" / "Adobe" / "UXP"
EXTERNAL_ROOT = ADOBE_UXP_ROOT / "Plugins" / "External"
PLUGINS_INFO = ADOBE_UXP_ROOT / "PluginsInfo" / "v1" / "premierepro.json"


def load_manifest() -> dict:
    manifest_path = UXP_SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = ["id", "name", "version", "host"]
    missing = [key for key in required if key not in manifest]
    if missing:
        raise SystemExit(f"manifest missing required keys: {', '.join(missing)}")
    host = manifest.get("host") or {}
    if host.get("app") != "premierepro":
        raise SystemExit("manifest host.app must be premierepro")
    return manifest


def plugin_dir_for(manifest: dict) -> Path:
    return EXTERNAL_ROOT / f"{manifest['id']}_{manifest['version']}"


def copy_plugin(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        UXP_SOURCE,
        dest,
        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc"),
    )


def read_plugins_info() -> dict:
    if not PLUGINS_INFO.exists():
        return {"plugins": []}
    try:
        data = json.loads(PLUGINS_INFO.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"cannot parse {PLUGINS_INFO}: {exc}") from exc
    if not isinstance(data, dict):
        data = {"plugins": []}
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        data["plugins"] = []
    return data


def backup_plugins_info() -> Path | None:
    if not PLUGINS_INFO.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = PLUGINS_INFO.with_suffix(PLUGINS_INFO.suffix + f".{stamp}.bak")
    shutil.copy2(PLUGINS_INFO, backup)
    return backup


def register_plugin(manifest: dict, dest: Path, dry_run: bool = False) -> dict:
    info = read_plugins_info()
    plugin_id = manifest["id"]
    min_version = str((manifest.get("host") or {}).get("minVersion") or "25.6")
    rel_path = f"$localPlugins/External/{dest.name}"
    entry = {
        "hostMinVersion": min_version,
        "name": manifest["name"],
        "path": rel_path,
        "pluginId": plugin_id,
        "status": "enabled",
        "type": "uxp",
        "versionString": manifest["version"],
    }
    info["plugins"] = [p for p in info.get("plugins", []) if p.get("pluginId") != plugin_id]
    info["plugins"].append(entry)
    info["plugins"].sort(key=lambda p: str(p.get("pluginId", "")))
    if not dry_run:
        PLUGINS_INFO.parent.mkdir(parents=True, exist_ok=True)
        backup_plugins_info()
        PLUGINS_INFO.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Premiere Agent UXP panel for local Premiere development")
    parser.add_argument("--dry-run", action="store_true", help="print planned install without writing")
    args = parser.parse_args()

    manifest = load_manifest()
    dest = plugin_dir_for(manifest)
    entry = register_plugin(manifest, dest, dry_run=args.dry_run)
    if not args.dry_run:
        EXTERNAL_ROOT.mkdir(parents=True, exist_ok=True)
        copy_plugin(dest)
    result = {
        "ok": True,
        "dry_run": args.dry_run,
        "source": str(UXP_SOURCE),
        "installed_dir": str(dest),
        "plugins_info": str(PLUGINS_INFO),
        "entry": entry,
        "next_step": "Restart Premiere Pro, then open Window → UXP Plugins → Premiere Agent.",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
