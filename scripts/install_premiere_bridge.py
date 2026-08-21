#!/usr/bin/env python3
"""Install the Premiere Agent CEP bridge panel for local development.

This creates/updates a symlink in the user's Adobe CEP extensions folder and
optionally enables unsigned CEP extensions for development builds.
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "premiere_bridge"
BUNDLE_ID = "com.econfilms.premiereagent.bridge"


def cep_extensions_dir() -> Path:
    system = platform.system().lower()
    home = Path.home()
    if system == "darwin":
        return home / "Library" / "Application Support" / "Adobe" / "CEP" / "extensions"
    if system == "windows":
        return home / "AppData" / "Roaming" / "Adobe" / "CEP" / "extensions"
    return home / ".cep" / "extensions"


def enable_mac_debug() -> None:
    # CEP debug mode is per-CSXS major version; setting a practical range keeps
    # local unsigned panels visible across current Premiere versions.
    for version in range(7, 13):
        subprocess.run(
            ["defaults", "write", f"com.adobe.CSXS.{version}", "PlayerDebugMode", "1"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Install Premiere Agent CEP panel symlink")
    ap.add_argument("--no-debug", action="store_true", help="Do not enable macOS unsigned CEP debug mode")
    ap.add_argument("--target", help="Override CEP extensions directory")
    args = ap.parse_args()

    target_dir = Path(args.target).expanduser().resolve() if args.target else cep_extensions_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    link = target_dir / BUNDLE_ID
    if link.exists() or link.is_symlink():
        if link.is_symlink() or link.is_file():
            link.unlink()
        else:
            raise SystemExit(f"Refusing to replace non-symlink directory: {link}")
    os.symlink(EXTENSION, link, target_is_directory=True)
    if platform.system().lower() == "darwin" and not args.no_debug:
        enable_mac_debug()
    print(f"Installed CEP bridge symlink: {link} -> {EXTENSION}")
    print("Restart Premiere Pro, then open Window → Extensions → Premiere Agent Bridge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
