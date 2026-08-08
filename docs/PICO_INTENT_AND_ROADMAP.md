# Pico: intent, shape, and what comes next

A handoff for any session picking this up cold. Written 8 August 2026.

## What Pico is

A scoped Claude. A personal workbench where one person thinks, manages their
projects, and executes work, using their own memory, driving whichever
commercial model suits the task.

The distinction that matters: a commercial product optimises for the median
user across millions, so its memory must be generic, its defaults safe, its
surface serving everyone slightly. Pico optimises for one person who is known
completely. That is a structural advantage, and it shows up as **narrowness**.
Pico can assume you work across six repositories, that decisions matter more
than facts, that standing instructions live in files you edit in a text editor.
No catalogue product can assume any of that.

Judge every proposed feature against this: does it serve one known person
better than a general product could? If the honest answer is "a general product
does this fine", it does not belong here.

## Why three surfaces

Not because Claude has three. Because **context shape becomes predictable**.

Context engineering has two hard problems: what to load, and how much. The
second is mechanical. The first is a ranking problem, and ranking needs signal
you usually lack.

Surfaces convert ranking into routing. A code turn always wants repository
state, the files in play, recent diffs. A thinking turn wants the thread, the
decisions, the open questions. A making turn wants the draft and its
references. You stop asking "what is relevant" every turn and start asking
"which shape am I in", which is far cheaper and far more reliable.

### What is shared and what is scoped

The risk of surfaces is silos, and a silo would destroy the reason Pico exists:
a decision made while coding must be there when you write about it weeks later.

- **Shared across all surfaces**: who you are, standing instructions, decisions,
  constraints, open questions. Facts about *you*, not about the mode. "Never use
  em dashes" is as true in code as in prose.
- **Scoped to the surface**: working material. Files and diffs belong to Code.
  Drafts and references belong to Design. Threads belong to Chat. Bulky,
  mode-specific, and exactly what should not bleed across.
- **The spine that crosses all three**: the project. A project has code and
  decisions and drafts. It is what makes a captured decision surface later, and
  it is why resume works at all.

Most of this exists already: memory has `scope` (personal / workspace / project)
and `project_id`. What is missing is the surface dimension on working material.

### One chrome, three centres

Rail position, wordmark, project block, surface switcher, model indicator,
capture and composer hold still between surfaces. Only the middle changes, and
only where the work genuinely differs. That is what makes it one product rather
than three tools sharing a login, and it is what keeps it buildable solo.

The rail's second block is the same slot with different contents: threads in
Chat, changed files in Code, drafts in Design. Never a different component.

## The fourth destination: Config

Skills, workers, workflows and schedules are **not** all state machines. A
workflow is; a worker is a function; a skill is declarative text. The real
commonality is that they are all **authored once and reused**, which also covers
providers and capability profiles.

So: Config is where you define the rules of the system. The other surfaces are
where you do work inside them.

They are layers, not siblings. A worker is the atom. A workflow is a graph of
workers. A skill is guidance attached to the agent or a step. A recipe is a
workflow plus what it calls.

Which means the existing DAG builder can author both, with one adjustment: **a
worker is a workflow marked deterministic, with no approval gates and a declared
typed output.** Same canvas, stricter contract.

## Workers, and why they matter

A **tool** is a primitive the model composes freely; the sequence is
probabilistic and the interpretation can be wrong. A **worker** is task-shaped:
whole job, own validation, typed output. The model chooses which worker and with
what inputs, and does not improvise the middle.

Ask a model "how many commits since 27 June" and it may miscount. A worker runs
one fixed sequence and returns an integer. Only one of those can have a unit
test.

Pico already has five de facto workers: `projects/awareness.py`,
`projects/resume.py`, `projects/home.py`, `context/compactor.py`, and
`prompt/compiler.py`. Deterministic, typed, testable, invoked rather than
reasoned through. The pattern exists but is unnamed, so it is not extensible.

**The find worth acting on**: the workflow engine pauses at every meaningful
node (`agent`, `browser_read`, `browser_action`, `wait`) partly for governance
and partly because *there was nothing to execute*. Workers are that missing
unit. A node calls a worker, gets typed output, moves on. Approval nodes still
pause, because that is a real authority boundary.

**Do not build a worker SDK yet.** Write the second and third workers as plain
modules, notice what genuinely repeats, and let the contract emerge. So far the
shared shape is: a pure function over stored records returning a typed result
plus a record of its sources. Smaller than anyone would design in advance.

## Where things stand

Five destinations: Home, Chat, Code, Design, Config. Down from fifteen. Nothing
was deleted; Context, Learning, Operations and the rest stay reachable through
the command palette, and Memory, Artifacts and Context are panels that belong
beside the work they describe rather than pages you navigate to.

**Working end to end**: capture and resume; project drift measured by
commits-since-capture rather than age; the prompt compiler; reply actions (copy,
save, retry, edit); Code reading the attached repository live; Config listing
everything authored, filtered by kind; streaming; durable workspace identity;
session search on an FTS5 index.

**Branches**: `feat/ui-ux-polish` carries the surface work.
`feat/pico-streaming` carries streaming at `e831187`.

## Roadmap, in order and with reasons

1. **Code's write path.** Describe a change, Pico drafts a diff, you approve,
   `operations/` applies it. The machinery exists (`actions.py` proposes and
   fingerprints, `executor.py` gates on an approval bound to that fingerprint,
   `workspace_executor.py` performs it) and has never had a surface. Three
   properties fall out: the model never holds a write capability, an approval
   binds to content rather than intent, and generating the diff versus applying
   it is a natural worker boundary. **Open decision: per-file or per-change-set
   approval.** Change-set is safer, because a change and its test are one
   thought and half-applying is worse than rejecting both.

2. **Capture from selection.** Select a sentence in a reply, press a key, it
   becomes a decision with the session attached. The reason there is one memory
   after months is not that memory is unvalued; it is that capture means
   retyping something you just read.

3. **The second and third workers**, as plain modules, to earn the contract.

4. **Workflow honesty and execution.** Retry and timeout are now enforced;
   `schedule_trigger` still fires nothing. Once workers exist, agent nodes can
   dispatch instead of pausing.

5. **Design surface.** Last, deliberately.

Deferred with reasons: no embeddings or semantic retrieval (guarded by
`tests/test_no_retrieval_boundary`-style boundary tests); no automatic memory
extraction until manual capture proves valuable; no new channels; no delivery
optimisation, since this is loopback-only and 383KB costs nothing.

## Working agreements earned the hard way

- **A persisted key or identity change needs a test starting from
  previous-version data.** Two stores key off identity: SQLite tables and JSONL
  session transcripts. When either changes, check both. Bump
  `_MIGRATION_VERSION` so workspaces that completed earlier steps run the new
  one.
- **Layout changes need verification at every breakpoint that changes layout
  direction**, not the one you are looking at.
- **Tests that assert something does *not* happen** (no provider call, no rows
  written) catch a class nothing else does.
- **When you remove a thing, grep for everything that depended on it.** Removing
  nav icons invalidated every rule written assuming icons existed, scattered
  across breakpoints and modes.
- **Find the choke point before patching sites.** Guarding `api()` once fixed
  what would have been 32 edits. Two title patches missed the real writer until
  a property setter was trapped.
- **Empty and failed must not look alike.** An empty state invites; a failure
  says what happened and what to do.
- **Commit at the end of each stretch.** Uncommitted work is invisible to
  whoever branches next and silently becomes theirs.

## Design rules for this UI

Anthropic's system, applied to Pico:

- Empty states are invitations, not apologies. Name the space, one line, a verb.
- Errors say what happened then what to do. One sentence, no first person.
- One accent per view. Reserve it for what Pico does, not every button.
- Serif for Pico's voice, sans for chrome. Typography says who is speaking
  before a word is read.
- Three type sizes, two weights. Fourteen sizes is why it felt busy.
- Space as separator, not lines. Boxes inside boxes is the density problem.
- Sentence case everywhere. No exclamation marks, no "please", no em dashes.
- Motion that explains a state change earns its place; motion that decorates one
  does not.
