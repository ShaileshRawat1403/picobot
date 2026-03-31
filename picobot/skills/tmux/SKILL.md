---
name: tmux
description: Remote-control tmux sessions for interactive CLIs by sending keystrokes and scraping pane output.
metadata: {"picobot":{"emoji":"🧵","os":["darwin","linux"],"requires":{"bins":["tmux"]}}}
---

# Picobot tmux Skill

Manage tmux sessions for running interactive terminal applications with Picobot.

## Quickstart

### Basic Setup

```bash
# Default socket directory (auto-created)
SOCKET_DIR="${TMPDIR:-/tmp}/picobot-tmux-sockets"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/picobot.sock"

# Create a new session
tmux -S "$SOCKET" new -d -s "picobot-shell" -n main

# Send commands
tmux -S "$SOCKET" send-keys -t "picobot-shell" -- 'python3 -q' Enter
```

### Using the Helper Script

```bash
SOCKET_DIR="${PICOBOT_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/picobot-tmux-sockets}"

# List all sessions across picobot sockets
./find-sessions.sh --all

# Create socket dir if missing and list
./find-sessions.sh --create

# Migrate from nanobot sessions (one-time)
./find-sessions.sh --migrate

# JSON output for scripting
./find-sessions.sh --all --json
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PICOBOT_TMUX_SOCKET_DIR` | `/tmp/picobot-tmux-sockets` | Base directory for socket files |
| `PICOBOT_TMUX_SOCKET_NAME` | `picobot` | Socket name suffix |

### Socket Directory Convention

```
/tmp/picobot-tmux-sockets/
├── picobot.sock        # Default socket
├── agent-1.sock        # Agent sessions
├── agent-2.sock        # ...
└── ...
```

## Targeting Panes

- Target format: `session:window.pane` (defaults to `:0.0`)
- Keep names short; avoid spaces
- Inspect: `tmux -S "$SOCKET" list-sessions`, `tmux -S "$SOCKET" list-panes -a`

## Common Operations

### Session Management

```bash
# List sessions on socket
tmux -S "$SOCKET" list-sessions

# Capture pane output
tmux -S "$SOCKET" capture-pane -p -J -t session:0.0 -S -200

# Send text to pane
tmux -S "$SOCKET" send-keys -t session:0.0 -l -- 'echo hello'
tmux -S "$SOCKET" send-keys -t session:0.0 Enter

# Send control keys
tmux -S "$SOCKET" send-keys -t session:0.0 C-c    # Ctrl+C
tmux -S "$SOCKET" send-keys -t session:0.0 C-d    # Ctrl+D

# Attach/detach
tmux -S "$SOCKET" attach -t session
tmux -S "$SOCKET" send-keys -t session C-b d      # Detach
```

### Waiting for Output

Use `wait-for-text.sh` to poll for expected output:

```bash
./wait-for-text.sh -t session:0.0 -p 'Ready' -T 30

# With fixed string matching
./wait-for-text.sh -t session:0.0 -p '$ ' -F -T 60

# Custom poll interval
./wait-for-text.sh -t session:0.0 -p 'Error:' -T 120 -i 1.0
```

## Python REPL Notes

For Python REPL interactions, set the environment variable:

```bash
export PYTHON_BASIC_REPL=1
tmux -S "$SOCKET" send-keys -t session:0.0 -- 'python3 -q' Enter
```

## Parallel Agent Orchestration

Run multiple coding agents in parallel:

```bash
SOCKET="${TMPDIR:-/tmp}/picobot-army.sock"

# Create multiple sessions
for i in 1 2 3 4 5; do
  tmux -S "$SOCKET" new-session -d -s "agent-$i"
done

# Launch agents in different workdirs
tmux -S "$SOCKET" send-keys -t agent-1 "cd /tmp/project1 && codex --yolo 'Fix bug X'" Enter
tmux -S "$SOCKET" send-keys -t agent-2 "cd /tmp/project2 && codex --yolo 'Fix bug Y'" Enter

# Poll for completion
for sess in agent-1 agent-2; do
  if tmux -S "$SOCKET" capture-pane -p -t "$sess" -S -3 | grep -q "❯"; then
    echo "$sess: DONE"
  else
    echo "$sess: Running..."
  fi
done

# Get full output
tmux -S "$SOCKET" capture-pane -p -t agent-1 -S -500
```

**Tips:**
- Use separate git worktrees for parallel fixes
- `pnpm install` first before running agents in fresh clones
- Check for shell prompt (`❯` or `$`) to detect completion

## Cleanup

```bash
# Kill a session
tmux -S "$SOCKET" kill-session -t SESSION

# Kill all sessions on socket
tmux -S "$SOCKET" list-sessions -F '#{session_name}' | xargs -r -n1 tmux -S "$SOCKET" kill-session -t

# Remove socket and all sessions
tmux -S "$SOCKET" kill-server
rm "$SOCKET"
```

## Helper Scripts Reference

### find-sessions.sh

```
Usage: find-sessions.sh [OPTIONS]

Options:
  -L, --socket        tmux socket name (-L flag)
  -S, --socket-path   tmux socket path (-S flag)
  -A, --all           scan all picobot sockets
  -C, --create        auto-create socket directory
  -M, --migrate       migrate from nanobot sockets
  -q, --query         filter by session name
  -j, --json          JSON output format
```

### wait-for-text.sh

```
Usage: wait-for-text.sh -t target -p pattern [OPTIONS]

Options:
  -t, --target    session:window.pane target
  -p, --pattern   regex pattern to match
  -F, --fixed     treat pattern as literal string
  -T, --timeout   seconds to wait (default: 15)
  -i, --interval  poll interval (default: 0.5)
  -l, --lines     history lines to inspect (default: 1000)
```

## Platform Support

- **macOS**: Supported (install tmux via Homebrew: `brew install tmux`)
- **Linux**: Supported (install via package manager)
- **Windows**: Use WSL with tmux installed inside

This skill requires `tmux` to be available on PATH.
