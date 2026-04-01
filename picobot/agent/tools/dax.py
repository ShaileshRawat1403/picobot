"""DAX tool for Picobot ingress integration.

This tool allows Picobot to interact with DAX workflows through the Soothsayer API.
Picobot is thin ingress only - all business logic remains in DAX.
"""

import json
from typing import Any

import httpx

from picobot.agent.tools.base import Tool
from picobot.bus.dax_auth import get_admin_numbers, get_default_url, is_authorized
from picobot.bus.dax_service import DaxPollingService, get_dax_service

DRAFT_PATTERNS = [
    "create", "write", "generate", "make", "build",
    "implement", "add", "modify", "refactor", "fix", "edit",
    "delete", "remove", "cleanup", "wipe", "purge",
    "setup", "install", "deploy", "migrate", "optimize"
]

DRAFT_KEYWORDS = [
    "script", "file", "code", "function", "class", "test",
    "config", "report", "documentation", "docs", "readme",
    "backup", "deploy", "migration", "patch", "diff",
    "folder", "directory", "temp", "cache", "project", "repo",
    "environment", "stack", "server", "database", "pipeline"
]

ANALYZE_PATTERNS = [
    "analyze", "explore", "understand", "inspect", "survey",
    "map the code", "review", "assess", "audit", "check the codebase",
    "analyse", "look at the code", "examine", "investigate",
    "look into", "read the code", "browse", "scan"
]

ANALYZE_KEYWORDS = [
    "codebase", "code", "repo", "repository", "project", "architecture",
    "structure", "files", "components", "modules", "dependencies"
]

USER_ERROR_MESSAGES = {
    "DAX_UNAVAILABLE": "⚠️ *Service Temporarily Unavailable*\n\nSorry, the workflow service is temporarily unavailable. Please try again in a few moments.",
    "RUN_CREATION_FAILED": "❌ *Failed to Start Workflow*\n\nCould not create the workflow. Please try again or rephrase your request.",
    "INVALID_INTENT": "❓ *Could Not Understand*\n\nI couldn't understand your request. Try saying:\n• 'analyze the codebase' or 'explore this repo' to analyze code\n• 'create', 'write', or 'generate' followed by what you'd like.",
    "APPROVAL_EXPIRED": "⏰ *Approval Expired*\n\nThe approval request has expired. The workflow has been cancelled.",
    "WORKFLOW_FAILED": "❌ *Workflow Failed*\n\nThe workflow encountered an error during execution.",
    "PERMISSION_DENIED": "🔒 *Permission Denied*\n\nYou don't have permission to perform this action.",
    "TIMEOUT": "⏱️ *Request Timeout*\n\nThe request took too long. Please try again.",
    "NO_APPROVALS": "ℹ️ *No Pending Approvals*\n\nThere are no pending approval requests for your active workflows.",
    "WORKFLOW_HINT_IGNORED": "ℹ️ *Workflow Adjusted*\n\nI adjusted the workflow type based on your request. This provides better results.",
    "CONTRACT_MUTATED": "❌ *Contract Mutated*\n\nThe workflow attempted to perform an unapproved action and was terminated.",
}


def classify_intent(message: str) -> dict[str, Any] | None:
    """Classify user message to determine workflow intent.
    
    Returns dict with workflow class if matched, None otherwise.
    Uses simple keyword matching - no ML.
    Priority: repo_analyze > draft_and_approve > generic
    """
    text = message.lower()

    has_analyze_action = any(p in text for p in ANALYZE_PATTERNS)
    has_analyze_object = any(k in text for k in ANALYZE_KEYWORDS)

    if has_analyze_action and has_analyze_object:
        return {
            "workflowClass": "repo_analyze",
            "workflowHint": "repo_analyze",
            "kind": "analysis"
        }

    has_action = any(p in text for p in DRAFT_PATTERNS)
    has_object = any(k in text for k in DRAFT_KEYWORDS)

    if has_action or has_object:
        return {
            "workflowClass": "draft_and_approve",
            "kind": "workflow_step"
        }

    return None


def format_approval_message(approval: dict) -> str:
    """Format approval request for WhatsApp display.
    
    Uses presentation-safe output from DAX/Soothsayer.
    Delegates to DaxPollingService for consistent formatting.
    """
    return DaxPollingService.format_approval_message(approval)


def format_run_status(status: dict) -> str:
    """Format run status for WhatsApp display."""
    lines = []

    workflow = status.get("workflow", {})
    progress = status.get("progress", {})
    trust = status.get("trust", {})

    if workflow:
        lines.append(f"🤖 *{workflow.get('classLabel', workflow.get('class', 'Workflow'))}*")
    else:
        lines.append("🤖 *Workflow Running*")

    if status.get("status") == "running":
        lines.append(f"\nStep: {progress.get('currentStepLabel', progress.get('currentStep', 'Working...'))}")
        lines.append(f"Progress: {progress.get('percentage', 0)}%")
    elif status.get("status") == "waiting_approval":
        lines.append("\n⏳ Waiting for approval...")
    elif status.get("status") == "completed":
        lines.append("\n✅ *Completed*")
        if status.get("terminalReasonLabel"):
            lines.append(f"Result: {status.get('terminalReasonLabel')}")
    elif status.get("status") == "failed":
        lines.append("\n❌ *Failed*")
        if status.get("terminalReasonLabel"):
            lines.append(f"Reason: {status.get('terminalReasonLabel')}")
        if status.get("terminalReasonDescription"):
            lines.append(f"{status.get('terminalReasonDescription')}")

    return "\n".join(lines)


class DaxTool(Tool):
    """Tool for interacting with DAX workflows through Soothsayer API."""

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._dax_url = get_default_url(self._config.get("url"))
        self._admin_numbers = get_admin_numbers(self._config.get("admin_numbers"))

    @property
    def name(self) -> str:
        return "dax"

    @property
    def description(self) -> str:
        return (
            "Interact with DAX workflow engine. "
            "Use to create workflow runs, check status, and handle approvals. "
            "Supported workflows: "
            "repo_analyze (analyze, explore, understand, inspect codebase), "
            "draft_and_approve (create, write, generate files/code)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create_run", "get_status", "get_approvals", "resolve_approval", "resolve_latest_approval", "classify_intent", "handoff"],
                    "description": "Action to perform"
                },
                "message": {
                    "type": "string",
                    "description": "User message or full request content"
                },
                "workflow_class": {
                    "type": "string",
                    "description": "Optional: Specific workflow class to use (e.g. repo_analyze, draft_and_approve)"
                },
                "workflow_hint": {
                    "type": "string",
                    "description": "Optional: Hint to guide the workflow"
                },
                "kind": {
                    "type": "string",
                    "description": "Optional: Kind of intent (e.g. analysis, workflow_step)"
                },
                "run_id": {
                    "type": "string",
                    "description": "Run ID for status/approval actions"
                },
                "approval_id": {
                    "type": "string",
                    "description": "Approval ID for resolving approvals"
                },
                "decision": {
                    "type": "string",
                    "enum": ["approve", "deny"],
                    "description": "Approval decision"
                },
                "actor_id": {
                    "type": "string",
                    "description": "User ID making the decision"
                },
                "channel": {
                    "type": "string",
                    "description": "Channel for notifications (default: whatsapp)"
                },
            },
            "required": ["action"]
        }

    async def execute(self, **kwargs: Any) -> str:
        """Execute DAX tool action."""
        action = kwargs.get("action")

        if action == "classify_intent":
            message = kwargs.get("message", "")
            result = classify_intent(message)
            if result:
                return json.dumps(result, indent=2)
            return json.dumps({"error": USER_ERROR_MESSAGES["INVALID_INTENT"]})

        elif action in ["create_run", "handoff"]:
            message = kwargs.get("message", "")
            chat_id = kwargs.get("actor_id", "unknown")
            channel = kwargs.get("channel", "whatsapp")

            w_class = kwargs.get("workflow_class")
            w_hint = kwargs.get("workflow_hint")
            w_kind = kwargs.get("kind")

            if not is_authorized(chat_id, self._admin_numbers):
                authorized_numbers = get_admin_numbers(self._admin_numbers)
                return json.dumps({
                    "error": USER_ERROR_MESSAGES["PERMISSION_DENIED"],
                    "authorizedNumbers": authorized_numbers
                })

            intent = classify_intent(message) or {}

            # Explicit parameters override classified intent
            if w_class: intent["workflowClass"] = w_class
            if w_hint: intent["workflowHint"] = w_hint
            if w_kind: intent["kind"] = w_kind

            # If no intent found and no parameters provided, we can't create a run
            if not intent and not w_class:
                return json.dumps({"error": USER_ERROR_MESSAGES["INVALID_INTENT"]})

            payload = {
                "intent": {
                    "input": message,
                    "kind": intent.get("kind", "workflow_step"),
                    "workflowClass": intent.get("workflowClass", "draft_and_approve"),
                    "workflowHint": intent.get("workflowHint")
                },
                "metadata": {
                    "source": "picobot",
                    "chatId": chat_id,
                    "initiatedBy": chat_id
                }
            }

            try:
                async with _HttpClient(self._dax_url) as client:
                    response = await client.post("/soothsayer/runs", payload)

                    if response.get("runId"):
                        run_id = response["runId"]

                        dax_service = get_dax_service()
                        if dax_service:
                            dax_service.track_run(run_id, chat_id, channel)

                        return json.dumps({
                            "success": True,
                            "runId": run_id,
                            "status": response.get("status", "created"),
                            "message": "Workflow started. I'll notify you when approval is needed."
                        }, indent=2)
                    else:
                        return json.dumps({"error": "Failed to create run", "details": response})

            except Exception as e:
                return json.dumps({
                    "error": USER_ERROR_MESSAGES["DAX_UNAVAILABLE"],
                    "details": str(e)
                })

        elif action == "get_status":
            run_id = kwargs.get("run_id")
            if not run_id:
                return json.dumps({"error": "run_id is required"})

            try:
                async with _HttpClient(self._dax_url) as client:
                    response = await client.get(f"/soothsayer/runs/{run_id}")
                    return json.dumps(response, indent=2)
            except Exception as e:
                return json.dumps({"error": f"Failed to get status: {str(e)}"})

        elif action == "get_approvals":
            run_id = kwargs.get("run_id")
            if not run_id:
                return json.dumps({"error": "run_id is required"})

            try:
                async with _HttpClient(self._dax_url) as client:
                    response = await client.get(f"/soothsayer/runs/{run_id}/approvals")

                    pending = [a for a in response if a.get("status") == "pending"]

                    if not pending:
                        return json.dumps({"approvals": [], "message": "No pending approvals"})

                    formatted = [format_approval_message(a) for a in pending]
                    return json.dumps({
                        "approvals": pending,
                        "formatted": formatted,
                        "count": len(pending)
                    }, indent=2)
            except Exception as e:
                return json.dumps({"error": f"Failed to get approvals: {str(e)}"})

        elif action == "resolve_approval":
            run_id = kwargs.get("run_id")
            approval_id = kwargs.get("approval_id")
            decision = kwargs.get("decision")
            actor_id = kwargs.get("actor_id", "unknown")

            if not all([run_id, approval_id, decision]):
                return json.dumps({"error": "run_id, approval_id, and decision are required"})

            try:
                async with _HttpClient(self._dax_url) as client:
                    response = await client.post(
                        f"/soothsayer/runs/{run_id}/approvals/{approval_id}",
                        {
                            "decision": decision,
                            "actorId": actor_id,
                            "source": "soothsayer"
                        }
                    )

                    if response.get("status"):
                        is_idempotent = response.get("idempotent", False)

                        if is_idempotent:
                            status = response["status"]
                            return json.dumps({
                                "success": True,
                                "status": status,
                                "idempotent": True,
                                "message": f"ℹ️ This approval was already {status}. No changes made."
                            }, indent=2)

                        emoji = "✅" if decision == "approve" else "❌"
                        action_word = "approved" if decision == "approve" else "denied"
                        return json.dumps({
                            "success": True,
                            "status": response["status"],
                            "message": f"{emoji} *{action_word.title()}* - Your request is being {'executed' if decision == 'approve' else 'cancelled'}."
                        }, indent=2)
                    else:
                        return json.dumps({"error": "Failed to resolve approval", "details": response})
            except Exception as e:
                return json.dumps({"error": f"Failed to resolve approval: {str(e)}"})

        elif action == "resolve_latest_approval":
            """Resolve the most recent pending approval across all active runs."""
            decision = kwargs.get("decision")
            actor_id = kwargs.get("actor_id", "unknown")

            if not is_authorized(actor_id, self._admin_numbers):
                authorized_numbers = get_admin_numbers(self._admin_numbers)
                return json.dumps({
                    "error": USER_ERROR_MESSAGES["PERMISSION_DENIED"],
                    "authorizedNumbers": authorized_numbers
                })

            if not decision:
                return json.dumps({"error": "decision is required"})

            try:
                async with _HttpClient(self._dax_url) as client:
                    overview = await client.get("/soothsayer/overview")

                    pending_approvals = overview.get("pendingApprovals", [])
                    if not pending_approvals:
                        return json.dumps({
                            "success": False,
                            "error": "No pending approvals found"
                        })

                    latest_approval = pending_approvals[0]
                    latest_run_id = latest_approval.get("runId")
                    latest_approval_id = latest_approval.get("approvalId")

                    if not latest_run_id or not latest_approval_id:
                        return json.dumps({
                            "success": False,
                            "error": "Could not find approval details"
                        })

                    resolve_response = await client.post(
                        f"/soothsayer/runs/{latest_run_id}/approvals/{latest_approval_id}",
                        {
                            "decision": decision,
                            "actorId": actor_id,
                            "source": "soothsayer"
                        }
                    )

                    if resolve_response.get("status"):
                        is_idempotent = resolve_response.get("idempotent", False)

                        if is_idempotent:
                            return json.dumps({
                                "success": True,
                                "status": resolve_response["status"],
                                "idempotent": True,
                                "runId": latest_run_id,
                                "approvalId": latest_approval_id,
                                "message": f"ℹ️ This approval was already {resolve_response['status']}. No changes made."
                            })

                        emoji = "✅" if decision == "approve" else "❌"
                        action_word = "approved" if decision == "approve" else "denied"
                        return json.dumps({
                            "success": True,
                            "status": resolve_response["status"],
                            "runId": latest_run_id,
                            "approvalId": latest_approval_id,
                            "message": f"{emoji} *{action_word.title()}* - Your request is being {'executed' if decision == 'approve' else 'cancelled'}."
                        })
                    else:
                        return json.dumps({
                            "success": False,
                            "error": "Failed to resolve approval",
                            "details": resolve_response
                        })

            except Exception as e:
                return json.dumps({"error": f"Failed to resolve approval: {str(e)}"})

        return json.dumps({"error": f"Unknown action: {action}"})


class _HttpClient:
    """Simple async HTTP client for DAX API with better error handling."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def __aenter__(self):
        self.session = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args):
        await self.session.aclose()

    async def get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        response = await self.session.get(url)
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "text": response.text}
        try:
            return response.json()
        except Exception as e:
            return {"error": "Invalid JSON response", "details": str(e), "text": response.text}

    async def post(self, path: str, data: dict) -> dict:
        url = f"{self.base_url}{path}"
        response = await self.session.post(url, json=data)
        if response.status_code not in (200, 201, 202):
            return {"error": f"HTTP {response.status_code}", "text": response.text}
        try:
            return response.json()
        except Exception as e:
            return {"error": "Invalid JSON response", "details": str(e), "text": response.text}
