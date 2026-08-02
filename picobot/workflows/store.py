"""Durable state for Pico's bounded n8n-style workflow builder.

The store deliberately owns graph definitions and execution evidence only.
Node execution is delegated to Pico's existing task, run, approval, browser,
and artifact contracts.  It never stores credentials, hidden reasoning, or
unbounded node payloads.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    kind: str
    title: str
    description: str | None
    config: dict[str, Any]
    x: int
    y: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowEdge:
    id: str
    source: str
    target: str
    condition: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    owner_id: str
    session_key: str
    title: str
    description: str | None
    state: str
    version: int
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    created_at: str
    updated_at: str
    approved_at: str | None = None
    archived_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["nodes"] = [node.to_dict() for node in self.nodes]
        data["edges"] = [edge.to_dict() for edge in self.edges]
        return data


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    workflow_id: str
    workflow_version: int
    owner_id: str
    session_key: str
    state: str
    current_node_id: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    ended_at: str | None
    failure_category: str | None = None
    result_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowNodeRun:
    id: str
    workflow_run_id: str
    node_id: str
    attempt: int
    state: str
    created_at: str
    updated_at: str
    started_at: str | None
    ended_at: str | None
    result_ref: str | None = None
    failure_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowEvent:
    id: str
    workflow_id: str
    workflow_run_id: str | None
    node_id: str | None
    event_type: str
    summary: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkflowStore:
    """Owner-scoped workflow definitions, runs, node runs, and event ledger."""

    _DEFINITION_STATES = {"draft", "approved", "archived"}
    _DEFINITION_TRANSITIONS = {
        "draft": {"approved", "archived"},
        "approved": {"archived"},
        "archived": set(),
    }
    _RUN_STATES = {
        "queued",
        "running",
        "waiting_for_approval",
        "waiting_for_input",
        "paused",
        "completed",
        "failed",
        "cancelled",
    }
    _RUN_TRANSITIONS = {
        "queued": {"running", "paused", "failed", "cancelled"},
        "running": {
            "waiting_for_approval",
            "waiting_for_input",
            "paused",
            "completed",
            "failed",
            "cancelled",
        },
        "waiting_for_approval": {"running", "paused", "failed", "cancelled"},
        "waiting_for_input": {"running", "paused", "failed", "cancelled"},
        "paused": {"queued", "cancelled"},
        "completed": set(),
        "failed": set(),
        "cancelled": set(),
    }
    _NODE_STATES = {
        "pending",
        "ready",
        "running",
        "waiting_for_approval",
        "waiting_for_input",
        "succeeded",
        "failed",
        "skipped",
        "cancelled",
    }
    _SUPPORTED_NODE_KINDS = {
        "manual_trigger",
        "schedule_trigger",
        "agent",
        "browser_read",
        "browser_action",
        "approval",
        "condition",
        "artifact",
        "wait",
        "end",
    }
    _MAX_NODES = 40
    _MAX_EDGES = 80
    _MAX_IDENTIFIER = 96
    _MAX_TITLE = 160
    _MAX_DESCRIPTION = 2_000
    _MAX_CONFIG_TEXT = 2_000
    _MAX_LIST = 100
    _SECRET_KEYS = {"token", "secret", "password", "api_key", "access_token", "refresh_token"}
    _EDGE_CONDITIONS = {"success", "error", "approved", "rejected", "timeout", "true", "false"}

    def __init__(self, workspace: Path):
        root = workspace / "workflows"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "pico-workflows.db"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    nodes_json TEXT NOT NULL,
                    edges_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    archived_at TEXT
                );
                CREATE INDEX IF NOT EXISTS workflows_owner_updated_idx
                    ON workflows(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS workflows_owner_state_idx
                    ON workflows(owner_id, state, updated_at DESC);
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workflow_version INTEGER NOT NULL,
                    owner_id TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_node_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    failure_category TEXT,
                    result_ref TEXT
                );
                CREATE INDEX IF NOT EXISTS workflow_runs_owner_updated_idx
                    ON workflow_runs(owner_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS workflow_runs_workflow_updated_idx
                    ON workflow_runs(workflow_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS workflow_node_runs (
                    id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    result_ref TEXT,
                    failure_category TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS workflow_node_runs_attempt_idx
                    ON workflow_node_runs(workflow_run_id, node_id, attempt);
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workflow_run_id TEXT,
                    node_id TEXT,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_events_run_created_idx
                    ON workflow_events(workflow_run_id, created_at DESC);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _required_identifier(cls, value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Workflow {label} is required")
        clean = value.strip()
        if len(clean) > cls._MAX_IDENTIFIER:
            raise ValueError(f"Workflow {label} is limited to {cls._MAX_IDENTIFIER} characters")
        return clean

    @classmethod
    def _text(cls, value: object, label: str, limit: int, *, required: bool) -> str | None:
        if value is None:
            if required:
                raise ValueError(f"Workflow {label} is required")
            return None
        if not isinstance(value, str):
            raise ValueError(f"Workflow {label} must be text")
        clean = " ".join(value.split())
        if not clean:
            if required:
                raise ValueError(f"Workflow {label} is required")
            return None
        if len(clean) > limit:
            raise ValueError(f"Workflow {label} is limited to {limit} characters")
        return clean

    @classmethod
    def _safe_config(cls, value: object, *, depth: int = 0) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("Workflow node config must be an object")
        if depth > 4 or len(value) > 24:
            raise ValueError("Workflow node config is too deep or large")
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Workflow node config keys must be non-empty text")
            normalized_key = key.strip().lower()
            if normalized_key in cls._SECRET_KEYS or any(secret in normalized_key for secret in cls._SECRET_KEYS):
                raise ValueError(f"Workflow node config cannot contain secret-like key '{key}'")
            if isinstance(item, dict):
                clean[key.strip()] = cls._safe_config(item, depth=depth + 1)
            elif isinstance(item, list):
                if len(item) > 40:
                    raise ValueError("Workflow node config lists are limited to 40 items")
                clean[key.strip()] = [
                    cls._safe_config(entry, depth=depth + 1)
                    if isinstance(entry, dict)
                    else cls._config_scalar(entry)
                    for entry in item
                ]
            else:
                clean[key.strip()] = cls._config_scalar(item)
        return clean

    @classmethod
    def _config_scalar(cls, value: object) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) > cls._MAX_CONFIG_TEXT:
                raise ValueError(f"Workflow node config text is limited to {cls._MAX_CONFIG_TEXT} characters")
            return value
        raise ValueError("Workflow node config values must be text, numbers, booleans, null, objects, or lists")

    @classmethod
    def _normalize_graph(
        cls, nodes: object, edges: object
    ) -> tuple[list[WorkflowNode], list[WorkflowEdge]]:
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= cls._MAX_NODES:
            raise ValueError(f"Workflow must contain between 1 and {cls._MAX_NODES} nodes")
        if not isinstance(edges, list) or len(edges) > cls._MAX_EDGES:
            raise ValueError(f"Workflow edges are limited to {cls._MAX_EDGES}")

        node_records: list[WorkflowNode] = []
        node_ids: set[str] = set()
        trigger_count = 0
        end_count = 0
        for raw in nodes:
            if not isinstance(raw, dict):
                raise ValueError("Workflow nodes must be objects")
            node_id = cls._required_identifier(raw.get("id"), "node id")
            if node_id in node_ids:
                raise ValueError(f"Duplicate workflow node id: {node_id}")
            node_ids.add(node_id)
            kind = cls._required_identifier(raw.get("kind"), "node kind")
            if kind not in cls._SUPPORTED_NODE_KINDS:
                raise ValueError(f"Unsupported workflow node kind: {kind}")
            if kind in {"manual_trigger", "schedule_trigger"}:
                trigger_count += 1
            if kind == "end":
                end_count += 1
            title = cls._text(raw.get("title"), "node title", cls._MAX_TITLE, required=True)
            description = cls._text(raw.get("description"), "node description", cls._MAX_DESCRIPTION, required=False)
            x = raw.get("x", 80)
            y = raw.get("y", 80)
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 5000:
                raise ValueError("Workflow node x position must be between 0 and 5000")
            if isinstance(y, bool) or not isinstance(y, (int, float)) or not 0 <= y <= 5000:
                raise ValueError("Workflow node y position must be between 0 and 5000")
            node_records.append(
                WorkflowNode(
                    id=node_id,
                    kind=kind,
                    title=title,
                    description=description,
                    config=cls._safe_config(raw.get("config")),
                    x=int(x),
                    y=int(y),
                )
            )

        if trigger_count != 1:
            raise ValueError("Workflow must contain exactly one trigger node")
        if end_count < 1:
            raise ValueError("Workflow must contain at least one end node")

        edge_records: list[WorkflowEdge] = []
        edge_ids: set[str] = set()
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
        for index, raw in enumerate(edges):
            if not isinstance(raw, dict):
                raise ValueError("Workflow edges must be objects")
            edge_id = cls._required_identifier(raw.get("id") or f"edge_{index + 1}", "edge id")
            if edge_id in edge_ids:
                raise ValueError(f"Duplicate workflow edge id: {edge_id}")
            edge_ids.add(edge_id)
            source = cls._required_identifier(raw.get("source"), "edge source")
            target = cls._required_identifier(raw.get("target"), "edge target")
            if source not in node_ids or target not in node_ids:
                raise ValueError("Workflow edge references an unknown node")
            if source == target:
                raise ValueError("Workflow edges cannot point to the same node")
            condition = cls._text(raw.get("condition") or "success", "edge condition", 80, required=True)
            if condition not in cls._EDGE_CONDITIONS:
                raise ValueError(f"Unsupported workflow edge condition: {condition}")
            note = cls._text(raw.get("note"), "edge note", 240, required=False)
            edge_records.append(WorkflowEdge(edge_id, source, target, condition, note))
            adjacency[source].append(target)

        # The first builder slice is a DAG.  Bounded loops can be added later
        # with an explicit iteration node and a persisted limit.
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("Workflow graph cannot contain cycles in this slice")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target in adjacency[node_id]:
                visit(target)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)

        trigger_id = next(node.id for node in node_records if node.kind in {"manual_trigger", "schedule_trigger"})
        reachable: set[str] = set()
        queue = [trigger_id]
        while queue:
            node_id = queue.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            queue.extend(adjacency[node_id])
        if reachable != node_ids:
            raise ValueError("Every workflow node must be reachable from the trigger")
        return node_records, edge_records

    @classmethod
    def _definition(cls, row: sqlite3.Row) -> WorkflowDefinition:
        raw_nodes = json.loads(row["nodes_json"])
        raw_edges = json.loads(row["edges_json"])
        nodes, edges = cls._normalize_graph(raw_nodes, raw_edges)
        data = dict(row)
        data.pop("nodes_json", None)
        data.pop("edges_json", None)
        return WorkflowDefinition(nodes=nodes, edges=edges, **data)

    @classmethod
    def _run(cls, row: sqlite3.Row) -> WorkflowRun:
        return WorkflowRun(**dict(row))

    @classmethod
    def _node_run(cls, row: sqlite3.Row) -> WorkflowNodeRun:
        return WorkflowNodeRun(**dict(row))

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= WorkflowStore._MAX_LIST:
            raise ValueError(f"Workflow list limit must be between 1 and {WorkflowStore._MAX_LIST}")
        return limit

    def create_draft(
        self,
        *,
        owner_id: str,
        session_key: str,
        title: str,
        description: str | None,
        nodes: object,
        edges: object,
    ) -> WorkflowDefinition:
        owner_id = self._required_identifier(owner_id, "owner")
        session_key = self._required_identifier(session_key, "session")
        title = self._text(title, "title", self._MAX_TITLE, required=True)
        description = self._text(description, "description", self._MAX_DESCRIPTION, required=False)
        normalized_nodes, normalized_edges = self._normalize_graph(nodes, edges)
        now = self._now()
        workflow = WorkflowDefinition(
            id=f"wf_{uuid.uuid4().hex[:12]}",
            owner_id=owner_id,
            session_key=session_key,
            title=title,
            description=description,
            state="draft",
            version=1,
            nodes=normalized_nodes,
            edges=normalized_edges,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflows(
                    id, owner_id, session_key, title, description, state, version,
                    nodes_json, edges_json, created_at, updated_at, approved_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    workflow.id,
                    workflow.owner_id,
                    workflow.session_key,
                    workflow.title,
                    workflow.description,
                    json.dumps([node.to_dict() for node in workflow.nodes]),
                    json.dumps([edge.to_dict() for edge in workflow.edges]),
                    now,
                    now,
                ),
            )
            self._event(connection, workflow.id, None, None, "workflow_created", "Draft workflow created.")
        return workflow

    def save_draft(
        self,
        owner_id: str,
        workflow_id: str,
        *,
        title: str,
        description: str | None,
        nodes: object,
        edges: object,
    ) -> WorkflowDefinition:
        workflow = self.get(owner_id, workflow_id)
        if workflow.state != "draft":
            raise ValueError("Only draft workflows can be edited")
        title = self._text(title, "title", self._MAX_TITLE, required=True)
        description = self._text(description, "description", self._MAX_DESCRIPTION, required=False)
        normalized_nodes, normalized_edges = self._normalize_graph(nodes, edges)
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE workflows SET title = ?, description = ?, version = version + 1,
                    nodes_json = ?, edges_json = ?, updated_at = ?
                WHERE id = ? AND owner_id = ? AND state = 'draft'
                """,
                (
                    title,
                    description,
                    json.dumps([node.to_dict() for node in normalized_nodes]),
                    json.dumps([edge.to_dict() for edge in normalized_edges]),
                    now,
                    workflow.id,
                    workflow.owner_id,
                ),
            )
            self._event(connection, workflow.id, None, None, "workflow_updated", "Draft workflow updated.")
            row = connection.execute("SELECT * FROM workflows WHERE id = ?", (workflow.id,)).fetchone()
        return self._definition(row)

    def get(self, owner_id: str, workflow_id: str) -> WorkflowDefinition:
        owner_id = self._required_identifier(owner_id, "owner")
        workflow_id = self._required_identifier(workflow_id, "id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflows WHERE id = ? AND owner_id = ?", (workflow_id, owner_id)
            ).fetchone()
        if row is None:
            raise KeyError("Workflow was not found")
        return self._definition(row)

    def list(self, owner_id: str, *, limit: int = 50) -> list[WorkflowDefinition]:
        limit = self._validate_limit(limit)
        owner_id = self._required_identifier(owner_id, "owner")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workflows WHERE owner_id = ? ORDER BY updated_at DESC LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [self._definition(row) for row in rows]

    def transition(self, owner_id: str, workflow_id: str, target: str) -> WorkflowDefinition:
        workflow = self.get(owner_id, workflow_id)
        if target not in self._DEFINITION_STATES:
            raise ValueError(f"Unsupported workflow state: {target}")
        if target != workflow.state and target not in self._DEFINITION_TRANSITIONS[workflow.state]:
            raise ValueError(f"Workflow cannot transition from {workflow.state} to {target}")
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE workflows SET state = ?, approved_at = ?, archived_at = ?, updated_at = ?
                WHERE id = ? AND owner_id = ? AND state = ?
                """,
                (
                    target,
                    now if target == "approved" else workflow.approved_at,
                    now if target == "archived" else workflow.archived_at,
                    now,
                    workflow.id,
                    workflow.owner_id,
                    workflow.state,
                ),
            )
            self._event(connection, workflow.id, None, None, f"workflow_{target}", f"Workflow marked {target}.")
            row = connection.execute("SELECT * FROM workflows WHERE id = ?", (workflow.id,)).fetchone()
        return self._definition(row)

    def start_run(self, owner_id: str, workflow_id: str, session_key: str) -> WorkflowRun:
        workflow = self.get(owner_id, workflow_id)
        if workflow.state != "approved":
            raise ValueError("Only approved workflows can run")
        session_key = self._required_identifier(session_key, "session")
        if session_key != workflow.session_key:
            raise ValueError("Workflow session does not match the active session")
        trigger = next(node for node in workflow.nodes if node.kind in {"manual_trigger", "schedule_trigger"})
        now = self._now()
        run = WorkflowRun(
            id=f"wfrun_{uuid.uuid4().hex[:12]}",
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            owner_id=workflow.owner_id,
            session_key=session_key,
            state="queued",
            current_node_id=trigger.id,
            created_at=now,
            updated_at=now,
            started_at=None,
            ended_at=None,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_runs(
                    id, workflow_id, workflow_version, owner_id, session_key, state,
                    current_node_id, created_at, updated_at, started_at, ended_at,
                    failure_category, result_ref
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, NULL, NULL, NULL, NULL)
                """,
                (
                    run.id,
                    run.workflow_id,
                    run.workflow_version,
                    run.owner_id,
                    run.session_key,
                    run.current_node_id,
                    now,
                    now,
                ),
            )
            self._event(connection, workflow.id, run.id, trigger.id, "run_queued", "Workflow run queued.")
        return run

    def get_run(self, owner_id: str, run_id: str) -> WorkflowRun:
        owner_id = self._required_identifier(owner_id, "owner")
        run_id = self._required_identifier(run_id, "run id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE id = ? AND owner_id = ?", (run_id, owner_id)
            ).fetchone()
        if row is None:
            raise KeyError("Workflow run was not found")
        return self._run(row)

    def list_runs(self, owner_id: str, workflow_id: str | None = None, *, limit: int = 50) -> list[WorkflowRun]:
        limit = self._validate_limit(limit)
        owner_id = self._required_identifier(owner_id, "owner")
        with self._connect() as connection:
            if workflow_id:
                rows = connection.execute(
                    "SELECT * FROM workflow_runs WHERE owner_id = ? AND workflow_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (owner_id, self._required_identifier(workflow_id, "workflow id"), limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM workflow_runs WHERE owner_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (owner_id, limit),
                ).fetchall()
        return [self._run(row) for row in rows]

    def transition_run(self, owner_id: str, run_id: str, target: str, *, failure_category: str | None = None) -> WorkflowRun:
        run = self.get_run(owner_id, run_id)
        if target not in self._RUN_STATES:
            raise ValueError(f"Unsupported workflow run state: {target}")
        if target != run.state and target not in self._RUN_TRANSITIONS[run.state]:
            raise ValueError(f"Workflow run cannot transition from {run.state} to {target}")
        failure = self._text(failure_category, "failure category", 120, required=False)
        now = self._now()
        started = run.started_at or (now if target == "running" else None)
        ended = now if target in {"completed", "failed", "cancelled"} else run.ended_at
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE workflow_runs SET state = ?, updated_at = ?, started_at = ?, ended_at = ?,
                    failure_category = ?
                WHERE id = ? AND owner_id = ? AND state = ?
                """,
                (target, now, started, ended, failure, run.id, run.owner_id, run.state),
            )
            self._event(connection, run.workflow_id, run.id, run.current_node_id, f"run_{target}", f"Workflow run marked {target}.")
            row = connection.execute("SELECT * FROM workflow_runs WHERE id = ?", (run.id,)).fetchone()
        return self._run(row)

    def record_node_run(
        self,
        owner_id: str,
        run_id: str,
        node_id: str,
        state: str,
        *,
        attempt: int = 1,
        result_ref: str | None = None,
        failure_category: str | None = None,
    ) -> WorkflowNodeRun:
        run = self.get_run(owner_id, run_id)
        node_id = self._required_identifier(node_id, "node id")
        workflow = self.get(owner_id, run.workflow_id)
        if node_id not in {node.id for node in workflow.nodes}:
            raise ValueError("Workflow node was not found in the run snapshot")
        if state not in self._NODE_STATES:
            raise ValueError(f"Unsupported workflow node state: {state}")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10:
            raise ValueError("Workflow node attempt must be between 1 and 10")
        result_ref = self._text(result_ref, "result reference", 400, required=False)
        failure = self._text(failure_category, "failure category", 120, required=False)
        now = self._now()
        node_run = WorkflowNodeRun(
            id=f"wfnod_{uuid.uuid4().hex[:12]}",
            workflow_run_id=run.id,
            node_id=node_id,
            attempt=attempt,
            state=state,
            created_at=now,
            updated_at=now,
            started_at=now if state in {"running", "waiting_for_approval", "waiting_for_input"} else None,
            ended_at=now if state in {"succeeded", "failed", "skipped", "cancelled"} else None,
            result_ref=result_ref,
            failure_category=failure,
        )
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM workflow_node_runs WHERE workflow_run_id = ? AND node_id = ? AND attempt = ?",
                (run.id, node_id, attempt),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO workflow_node_runs(
                        id, workflow_run_id, node_id, attempt, state, created_at, updated_at,
                        started_at, ended_at, result_ref, failure_category
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(asdict(node_run).values()),
                )
            else:
                prior = self._node_run(existing)
                connection.execute(
                    """
                    UPDATE workflow_node_runs
                    SET state = ?, updated_at = ?, started_at = ?, ended_at = ?,
                        result_ref = ?, failure_category = ?
                    WHERE id = ?
                    """,
                    (
                        state,
                        now,
                        prior.started_at or node_run.started_at,
                        node_run.ended_at or prior.ended_at,
                        result_ref or prior.result_ref,
                        failure or prior.failure_category,
                        prior.id,
                    ),
                )
            self._event(connection, run.workflow_id, run.id, node_id, f"node_{state}", f"Node {node_id} marked {state}.")
            row = connection.execute(
                "SELECT * FROM workflow_node_runs WHERE workflow_run_id = ? AND node_id = ? AND attempt = ?",
                (run.id, node_id, attempt),
            ).fetchone()
        return self._node_run(row)

    def latest_node_run(self, owner_id: str, run_id: str, node_id: str) -> WorkflowNodeRun | None:
        """Return the latest attempt for a node without exposing private payloads."""
        run = self.get_run(owner_id, run_id)
        node_id = self._required_identifier(node_id, "node id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_node_runs
                WHERE workflow_run_id = ? AND node_id = ?
                ORDER BY attempt DESC, updated_at DESC
                LIMIT 1
                """,
                (run.id, node_id),
            ).fetchone()
        return self._node_run(row) if row else None

    def advance_run(self, owner_id: str, run_id: str, next_node_id: str | None) -> WorkflowRun:
        run = self.get_run(owner_id, run_id)
        next_node_id = self._text(next_node_id, "next node id", self._MAX_IDENTIFIER, required=False)
        if next_node_id is not None:
            workflow = self.get(owner_id, run.workflow_id)
            if next_node_id not in {node.id for node in workflow.nodes}:
                raise ValueError("Next workflow node was not found")
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE workflow_runs SET current_node_id = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
                (next_node_id, now, run.id, run.owner_id),
            )
            self._event(connection, run.workflow_id, run.id, next_node_id, "run_advanced", "Workflow cursor advanced.")
            row = connection.execute("SELECT * FROM workflow_runs WHERE id = ?", (run.id,)).fetchone()
        return self._run(row)

    def detail(self, owner_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(owner_id, run_id)
        with self._connect() as connection:
            node_rows = connection.execute(
                "SELECT * FROM workflow_node_runs WHERE workflow_run_id = ? ORDER BY created_at ASC, attempt ASC",
                (run.id,),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT * FROM workflow_events WHERE workflow_run_id = ? ORDER BY created_at ASC, id ASC",
                (run.id,),
            ).fetchall()
        return {
            "run": run.to_dict(),
            "workflow": self.get(owner_id, run.workflow_id).to_dict(),
            "nodes": [self._node_run(row).to_dict() for row in node_rows],
            "events": [WorkflowEvent(**dict(row)).to_dict() for row in event_rows],
        }

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        workflow_id: str,
        run_id: str | None,
        node_id: str | None,
        event_type: str,
        summary: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workflow_events(id, workflow_id, workflow_run_id, node_id, event_type, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (f"wfev_{uuid.uuid4().hex[:12]}", workflow_id, run_id, node_id, event_type, summary[:600], WorkflowStore._now()),
        )
