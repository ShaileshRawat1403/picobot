# Pico thinking stances

Pico is a local thinking/workbench agent, so a session should communicate its
intent without turning the product into a personality catalogue. Each session
has one small, explicit working stance:

| Stance | Use it when Pico should… |
| --- | --- |
| Explore | open up a question, clarify assumptions, and surface possibilities |
| Decide | compare criteria and tradeoffs, then make a recommendation |
| Make | turn intent into a concrete draft, plan, or other work product |
| Review | inspect existing work for gaps, risks, and precise revisions |

The stance is durable session metadata. It is included in the system prompt
for the turn and recorded in context evidence so a later run can be understood
without exposing private prompt text or hidden reasoning.

Stance is deliberately not a capability control. Changing it never enables a
tool, changes a provider/model, bypasses approval, or changes the active mission
profile. Unknown or corrupted values fail closed to `explore`.

The chat cockpit exposes the current stance as a compact session-intent
control. The four choices are intentionally phrased as work modes, not claims
about Pico's identity or autonomy.

Every stance also shares a response discipline: lead with the useful answer,
make only material assumptions visible, ask one focused question when blocked,
and suggest one concrete next step when it helps. This is guidance rather than
a rigid response template, so ordinary conversation remains natural.
