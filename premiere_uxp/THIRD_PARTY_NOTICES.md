# Third-party notices

This UXP panel scaffold adapts UI and architecture patterns from:

- Project: MCP for Adobe Premiere Pro
- Repository: https://github.com/leancoderkavy/premiere-pro-mcp
- License: MIT
- Copyright: Copyright (c) 2025 Premiere Pro MCP Contributors

The original MIT license permits use, copying, modification, merging, publishing,
distribution, sublicensing, and sale, provided the copyright and permission notice
are included in copies or substantial portions of the software.

The local Premiere Agent UXP panel is adapted for Econ Films' `premiere-agent`
architecture and currently uses a hybrid strategy:

1. UXP panel for documented Premiere API read probes and user-facing frontend.
2. Existing CEP/ExtendScript JSON-RPC bridge as the compatibility fallback for
   localhost transport, QE/ExtendScript-only behavior, and already-verified live
   tools.
