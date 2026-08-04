# Pico Project Brain

Pico Home is the durable source of truth for the owner's work context. It does
not claim ambient knowledge of every file or repository. A project becomes
useful context through explicit connected sources and refreshable evidence.

## What Pico retains

- the project purpose, relationships, and owner-selected orientation;
- Pico artifacts, decisions, tasks, missions, workflows, and run evidence;
- explicit local/GitHub source snapshots with a timestamp and change
  fingerprint.

## Refreshable source intelligence

Refreshing a declared local Git source records only bounded, redacted signals:

- branch state and safe changed-file count;
- up to eight recent commit summaries;
- no recursive project scan, source contents, absolute paths, or secret-like
  filenames.

Refreshing a declared GitHub repository records public repository metadata and
up to eight public commit summaries. It does not use stored GitHub credentials
or make a repository writable.

Snapshots are durable owner-scoped evidence. Pico shows when a source was last
refreshed and whether the source materially changed since its preceding
refresh. The active session receives only a compact summary from the latest
explicit snapshot; it never receives a repository dump.

## Execution boundary

Project awareness gives Pico better suggestions and allows direct work only
within the project's active operating mode and existing capability profile. It
is not implicit filesystem authority. Sensitive, external, credential-bearing,
or destructive work remains a hard stop even for a single-owner Pico Home.
