# Pico supported surface

This is the maintainability checkpoint for Pico's deliberately small product
surface. A provider, tool, or connector is shown as supported only when its
readiness, profile, evidence, and negative-test contract exists.

## Supported now

| Area | Supported surface | Boundary |
| --- | --- | --- |
| Model APIs | OpenAI API, Gemini API, Anthropic API | Write-only local API-key setup; no secret readback |
| Local model | Ollama | One configured local/OpenAI-compatible endpoint |
| Custom model | One named OpenAI-compatible endpoint | Explicit URL and key policy; no gateway catalogue |
| Subscriptions | Official Codex CLI and official Gemini CLI | Provider-owned login/transport; Pico sees redacted readiness only |
| Thinking work | Personal work profile, memory, skills, artifacts, session search | No external writes by default |
| Research | Research profile with bounded web search/fetch | Read-only external access |
| Browser | Browser-review and browser-action profiles | One explicitly shared tab; typed actions require approval |
| Work governance | Missions, bounded tasks, approvals, runs, activity, artifacts | Local, single-owner, evidence-first |
| Workspace | Inspect, diagnostics, and approved bounded build proposals | Workspace bounds and explicit approval |

## Optional or explicitly enabled

- GitHub pull-request review through the local `gh` CLI.
- Read-only Google Calendar through the configured local token.
- Governed MCP servers that pass the registry readiness and profile checks.
- Delegated read-only research at depth zero.
- Local schedules and heartbeat checks.

These capabilities remain hidden from a normal personal-work session unless the
owner selects the corresponding profile or enables the local integration.

## Deferred or excluded

- Pico-managed OAuth adapters until a provider-owned public flow is justified.
  If added, OS keychain storage is mandatory; plaintext token files are not a
  fallback.
- Anthropic subscription OAuth, private Code Assist transports, ChatGPT web
  endpoints, browser-cookie reuse, and legacy DAX credential reads.
- Messaging, email, Drive, Notion, Slack, and other connector catalogues until
  one real weekly personal workflow justifies a vertical slice.
- PDF, DOCX, XLSX, and ICS derived exports.
- Remote browser control, desktop automation, cloud workers, SSO, and
  multi-user enterprise deployment.

## Registry rule

Deferred providers remain compatibility metadata only. They are not included in
the normal provider inventory, are never auto-probed, and cannot be selected by
runtime policy. New integrations must be added behind the existing capability,
profile, readiness, approval, activity, and evidence contracts.
