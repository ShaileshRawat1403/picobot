# Provider migration boundary

Pico's supported subscription connections use the official provider-owned
Codex and Gemini CLIs. Pico observes only bounded readiness metadata and never
reads, copies, refreshes, or replays their credentials.

The former direct subscription transports were removed from the runtime
surface. They depended on private provider endpoints and local token material,
which is outside Pico's supported boundary. Existing configuration values for
`openai_codex` and `gemini_oauth` remain recognized as subscription provider
names, but runtime construction resolves them only to the official CLI
transport in `picobot.providers.subscription_cli`.

Rook's PKCE, loopback callback, state validation, refresh, and disconnect
mechanics remain useful reference patterns for a future provider-owned OAuth
adapter. Any such adapter must use documented public endpoints and OS keychain
storage; Pico will not reintroduce plaintext token files or private endpoint
emulation.
