# Pico provider connections

Pico has two connection types with different security and lifecycle rules.

## API connections

The supported API surface is intentionally limited to:

- OpenAI API;
- Gemini API;
- Anthropic API;
- one local/OpenAI-compatible endpoint, with Ollama as the local preset and
  `custom` as the configurable alternative.

API keys are stored in the active profile's local `.env` with restrictive file
permissions. They are never returned by the setup API or serialized into
`config.json`.

## Subscription connections

Pico supports subscription sign-in through the provider-owned local clients:

- OpenAI Codex through the official `codex login` flow and CLI transport;
- Gemini through the official Gemini CLI's Google sign-in and CLI transport.

Pico only records redacted readiness metadata. It does not read, copy, refresh,
or replay either CLI's access or refresh tokens. Subscription CLI connections
also do not receive Pico tools; governed execution uses an API connection so
Pico's normal capability, proposal, approval, and audit controls remain in
charge.

Gemini's own documentation warns against third-party software harvesting or
piggybacking on Gemini CLI OAuth credentials. Pico therefore does not use the
legacy DAX auth file or the private Code Assist backend for Gemini subscription
access. See the [Gemini CLI authentication guidance](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx)
and [OAuth usage warning](https://github.com/google-gemini/gemini-cli/blob/main/docs/resources/faq.md).

## Readiness states

Provider setup exposes bounded states only:

- `not_configured`: required API key or endpoint is absent;
- `setup_required`: the official subscription client is missing or not logged
  in;
- `configured`: local endpoint or provider-owned credentials are present;
- `ready`: a safe API probe or provider-owned CLI status check succeeded;
- `unavailable`: the last bounded check failed;
- `deferred`: the provider remains in compatibility inventory but is outside
  Pico's supported product boundary.

Deferred providers are hidden from the default inventory, are never auto-probed,
and cannot be selected by runtime policy.

