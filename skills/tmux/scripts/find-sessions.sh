#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: find-sessions.sh [OPTIONS]

List and manage tmux sessions for Picobot.

Options:
  -L, --socket       tmux socket name (passed to tmux -L)
  -S, --socket-path  tmux socket path (passed to tmux -S)
  -A, --all          scan all sockets under PICOBOT_TMUX_SOCKET_DIR
  -C, --create       auto-create socket directory if missing
  -M, --migrate      migrate sessions from nanobot socket directory
  -q, --query        case-insensitive substring to filter session names
  -j, --json         output as JSON
  -h, --help         show this help

Environment:
  PICOBOT_TMUX_SOCKET_DIR  base directory for picobot sockets (default: /tmp/picobot-tmux-sockets)
  PICOBOT_TMUX_SOCKET_NAME  socket name suffix (default: picobot)

Examples:
  find-sessions.sh -A                    # list all sessions on all picobot sockets
  find-sessions.sh -C                     # create socket dir and list sessions
  find-sessions.sh -M                     # migrate from nanobot to picobot
  find-sessions.sh -S /tmp/my.sock -j    # JSON output for specific socket
USAGE
}

socket_name=""
socket_path=""
query=""
scan_all=false
auto_create=false
migrate_mode=false
json_output=false

_default_socket_dir="${TMPDIR:-/tmp}/picobot-tmux-sockets"
socket_dir="${PICOBOT_TMUX_SOCKET_DIR:-${_default_socket_dir}}"
socket_suffix="${PICOBOT_TMUX_SOCKET_NAME:-picobot}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -L|--socket)      socket_name="${2:-}"; shift 2 ;;
    -S|--socket-path) socket_path="${2:-}"; shift 2 ;;
    -A|--all)         scan_all=true; shift ;;
    -C|--create)      auto_create=true; shift ;;
    -M|--migrate)     migrate_mode=true; shift ;;
    -q|--query)       query="${2:-}"; shift 2 ;;
    -j|--json)        json_output=true; shift ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$scan_all" == true && ( -n "$socket_name" || -n "$socket_path" ) ]]; then
  echo "Cannot combine --all with -L or -S" >&2
  exit 1
fi

if [[ -n "$socket_name" && -n "$socket_path" ]]; then
  echo "Use either -L or -S, not both" >&2
  exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "Error: tmux not found in PATH" >&2
  exit 1
fi

migrate_nanobot_sockets() {
  local nanobot_dir="${TMPDIR:-/tmp}/nanobot-tmux-sockets"
  local picobot_dir="${socket_dir}"
  
  if [[ ! -d "$nanobot_dir" ]]; then
    echo "No nanobot socket directory found at $nanobot_dir"
    return 0
  fi
  
  echo "Migrating sessions from $nanobot_dir to $picobot_dir..."
  
  shopt -s nullglob
  local nanobot_sockets=("$nanobot_dir"/*)
  shopt -u nullglob
  
  if [[ ${#nanobot_sockets[@]} -eq 0 ]]; then
    echo "No nanobot sockets found"
    return 0
  fi
  
  mkdir -p "$picobot_dir"
  
  local migrated=0
  for sock in "${nanobot_sockets[@]}"; do
    if [[ -S "$sock" ]]; then
      local basename=$(basename "$sock")
      local target="$picobot_dir/$basename"
      if [[ ! -e "$target" ]]; then
        cp "$sock" "$target" 2>/dev/null || ln -s "$sock" "$target" 2>/dev/null || true
        echo "  Migrated: $basename"
        ((migrated++)) || true
      fi
    fi
  done
  
  echo "Migration complete: $migrated socket(s) migrated"
  return 0
}

ensure_socket_dir() {
  if [[ ! -d "$socket_dir" ]]; then
    if [[ -n "${PICOBOT_TMUX_SOCKET_DIR:-}" ]]; then
      echo "Error: PICOBOT_TMUX_SOCKET_DIR=$socket_dir does not exist" >&2
      exit 1
    fi
    mkdir -p "$socket_dir"
    echo "Created socket directory: $socket_dir"
  fi
}

if [[ "$migrate_mode" == true ]]; then
  migrate_nanobot_sockets
  exit $?
fi

if [[ "$auto_create" == true ]]; then
  ensure_socket_dir
fi

list_sessions_json() {
  local label="$1"; shift
  local tmux_cmd=(tmux "$@")
  local sessions_json="[]"
  
  if ! sessions_raw="$("${tmux_cmd[@]}" list-sessions -F '{"name":"#{session_name}","attached":#{session_attached},"created":"#{session_created_string}"}' 2>/dev/null)"; then
    return 1
  fi
  
  if [[ -n "$sessions_raw" ]]; then
    sessions_json="["
    while IFS= read -r line; do
      [[ -n "$line" ]] || continue
      [[ "$sessions_json" != "[" ]] && sessions_json+=","
      sessions_json+="$line"
    done <<< "$sessions_raw"
    sessions_json+="]"
  fi
  
  echo "$sessions_json"
}

list_sessions() {
  local label="$1"; shift
  local tmux_cmd=(tmux "$@")
  
  if ! sessions="$("${tmux_cmd[@]}" list-sessions -F '#{session_name}\t#{session_attached}\t#{session_created_string}' 2>/dev/null)"; then
    echo "No tmux server found on $label" >&2
    return 1
  fi
  
  if [[ -n "$query" ]]; then
    sessions="$(printf '%s\n' "$sessions" | grep -i -- "$query" || true)"
  fi
  
  if [[ -z "$sessions" ]]; then
    echo "No sessions found on $label"
    return 0
  fi
  
  echo "Sessions on $label:"
  printf '%s\n' "$sessions" | while IFS=$'\t' read -r name attached created; do
    attached_label=$([[ "$attached" == "1" ]] && echo "attached" || echo "detached")
    printf '  - %s (%s, started %s)\n' "$name" "$attached_label" "$created"
  done
}

list_sessions_json_filtered() {
  local label="$1"; shift
  local tmux_cmd=(tmux "$@")
  local all_json
  
  if ! all_json=$(list_sessions_json "$label" "${@}"); then
    echo "[]"
    return 1
  fi
  
  if [[ -z "$query" ]]; then
    echo "$all_json"
    return 0
  fi
  
  echo "$all_json" | python3 -c "
import json, sys
try:
    sessions = json.load(sys.stdin)
    filtered = [s for s in sessions if '$query'.lower() in s.get('name', '').lower()]
    print(json.dumps(filtered))
except:
    print('[]')
" 2>/dev/null || echo "$all_json"
}

if [[ "$scan_all" == true ]]; then
  if [[ ! -d "$socket_dir" ]]; then
    echo "Socket directory not found: $socket_dir" >&2
    echo "Run with --create to initialize" >&2
    exit 1
  fi
  
  shopt -s nullglob
  sockets=("$socket_dir"/*)
  shopt -u nullglob
  
  if [[ ${#sockets[@]} -eq 0 ]]; then
    echo "No sockets found under $socket_dir" >&2
    exit 1
  fi
  
  if [[ "$json_output" == true ]]; then
    echo "{"
    echo "  \"sockets\": ["
    first=true
    for sock in "${sockets[@]}"; do
      if [[ ! -S "$sock" ]]; then
        continue
      fi
      [[ "$first" == true ]] && first=false || echo ","
      sock_name=$(basename "$sock")
      echo -n "    {\"path\":\"$sock\",\"sessions\":"
      list_sessions_json_filtered "socket path '$sock'" -S "$sock"
      echo -n "}"
    done
    echo "  ]"
    echo "}"
  else
    exit_code=0
    for sock in "${sockets[@]}"; do
      if [[ ! -S "$sock" ]]; then
        continue
      fi
      echo ""
      list_sessions "socket path '$sock'" -S "$sock" || exit_code=$?
    done
    exit "$exit_code"
  fi
  exit 0
fi

tmux_cmd=(tmux)
socket_label="default socket"

if [[ -n "$socket_name" ]]; then
  tmux_cmd+=(-L "$socket_name")
  socket_label="socket name '$socket_name'"
elif [[ -n "$socket_path" ]]; then
  tmux_cmd+=(-S "$socket_path")
  socket_label="socket path '$socket_path'"
fi

if [[ "$json_output" == true ]]; then
  list_sessions_json_filtered "$socket_label" "${tmux_cmd[@]:1}"
else
  list_sessions "$socket_label" "${tmux_cmd[@]:1}"
fi
