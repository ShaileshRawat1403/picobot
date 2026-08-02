# Pico governed browser actions

Pico's browser bridge is local-first and single-tab. `browser-review` can read
the one tab explicitly shared by the owner. `browser-action` adds only three
bounded writes: navigate to a visible HTTP(S) URL, click a bounded selector, or
type non-sensitive text into a bounded selector.

Every write follows:

`propose → inspect → approve → dispatch → result → audit`

The proposal and command ledgers retain owner/session/share binding, a safe
target summary, payload fingerprint, status, expiry, and safe result summary.
Private payloads are never returned to the web UI or extension response.

Immediately before dispatch Pico rechecks the active share, extension tab,
owner/session, active profile, payload fingerprint, and approval expiry. The
extension polls using the existing share token and executes typed operation
records only. It has no arbitrary JavaScript command path and no permission to
discover or control other tabs.

Sensitive paths and controls containing login, authentication, password,
recovery, payment, checkout, billing, or secret-like input are rejected. A
revoked or replaced share invalidates queued commands. Results are recorded as
success or a bounded failure category so a run can be resumed from evidence.
