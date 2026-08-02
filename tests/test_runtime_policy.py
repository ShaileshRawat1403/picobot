"""Runtime policy tests: durable global policy, session overrides, and
the AgentLoop/WebChannel attachment gates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from picobot.agent.loop import AgentLoop
from picobot.bus.events import InboundMessage
from picobot.bus.queue import MessageBus
from picobot.channels.web import WebChannel
from picobot.config.loader import load_config
from picobot.config.schema import RuntimePolicyConfig
from picobot.policy.runtime import (
    RuntimePolicyService,
    default_policy,
    resolve_effective_policy,
)
from picobot.providers.base import LLMProvider, LLMResponse
from picobot.session.manager import SessionManager

SESSION_POLICY_KEY = "pico_runtime_policy"
SECRET = "test-secret-key-123"


def _write_config(
    tmp_path: Path,
    *,
    anthropic_key: str = "",
    openai_key: str = "",
    provider: str = "auto",
    model: str = "anthropic/claude-sonnet-4",
) -> Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "workspace": str(tmp_path / "workspace"),
                        "provider": provider,
                        "model": model,
                    }
                },
                "providers": {
                    "anthropic": {"apiKey": anthropic_key},
                    "openai": {"apiKey": openai_key},
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _session(tmp_path: Path, key: str):
    manager = SessionManager(tmp_path / "workspace")
    return manager, manager.get_or_create(key)


class TestGlobalPolicy:
    def test_global_policy_persists_and_versions(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)

        summary = service.set_global(
            {
                "provider": "anthropic",
                "model": "anthropic/claude-sonnet-4",
                "reasoning_effort": "high",
                "response_mode": "concise",
            }
        )

        assert summary["policy"]["provider"] == "anthropic"
        assert summary["policy"]["model"] == "anthropic/claude-sonnet-4"
        assert summary["policy"]["reasoningEffort"] == "high"
        assert summary["policy"]["responseMode"] == "concise"
        assert summary["policy"]["version"] == 1
        assert summary["policy"]["updatedAt"] is not None

        reloaded = RuntimePolicyService(config_path)
        policy = reloaded.get_global()
        assert policy.provider == "anthropic"
        assert policy.model == "anthropic/claude-sonnet-4"
        assert policy.reasoning_effort == "high"
        assert policy.response_mode == "concise"
        assert policy.version == 1

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        assert raw["policy"]["provider"] == "anthropic"
        assert raw["policy"]["reasoningEffort"] == "high"

    def test_global_update_bumps_version(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        service.set_global({"provider": "anthropic", "model": "anthropic/claude-sonnet-4"})

        summary = service.set_global({"response_mode": "detailed"})

        assert summary["policy"]["version"] == 2
        assert summary["policy"]["responseMode"] == "detailed"
        assert summary["policy"]["provider"] == "anthropic"

    def test_unknown_provider_rejected(self, tmp_path: Path):
        service = RuntimePolicyService(_write_config(tmp_path, anthropic_key=SECRET))

        with pytest.raises(ValueError, match="Unknown Pico provider"):
            service.set_global(
                {"provider": "not-a-provider", "model": "anthropic/claude-sonnet-4"}
            )

    def test_unready_provider_rejected(self, tmp_path: Path):
        service = RuntimePolicyService(_write_config(tmp_path, openai_key=""))

        with pytest.raises(ValueError, match="not ready"):
            service.set_global({"provider": "openai", "model": "openai/gpt-4o"})

    def test_mismatched_provider_model_rejected(self, tmp_path: Path):
        service = RuntimePolicyService(
            _write_config(tmp_path, anthropic_key=SECRET, openai_key=SECRET)
        )

        with pytest.raises(ValueError, match="routes to provider"):
            service.set_global({"provider": "openai", "model": "anthropic/claude-sonnet-4"})

    def test_unroutable_model_rejected(self, tmp_path: Path):
        service = RuntimePolicyService(_write_config(tmp_path))

        with pytest.raises(ValueError, match="No configured provider"):
            service.set_global({"model": "totally-unknown-model"})

    def test_invalid_reasoning_effort_rejected(self, tmp_path: Path):
        service = RuntimePolicyService(_write_config(tmp_path, anthropic_key=SECRET))

        with pytest.raises(ValueError, match="Reasoning effort"):
            service.set_global({"reasoning_effort": "extreme"})

    def test_invalid_response_mode_rejected(self, tmp_path: Path):
        service = RuntimePolicyService(_write_config(tmp_path, anthropic_key=SECRET))

        with pytest.raises(ValueError, match="Response mode"):
            service.set_global({"response_mode": "loud"})

    def test_clear_resets_global(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        service.set_global(
            {"provider": "anthropic", "model": "anthropic/claude-sonnet-4"}
        )

        service.set_global({"clear": True})

        policy = service.get_global()
        assert policy.provider is None
        assert policy.model is None
        assert policy.reasoning_effort is None
        assert policy.response_mode == "default"


class TestSessionOverrides:
    def test_override_isolated_and_retained_on_resume(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        service.set_global(
            {
                "provider": "anthropic",
                "model": "anthropic/claude-sonnet-4",
                "response_mode": "concise",
            }
        )

        manager_a, session_a = _session(tmp_path, "web:web:client-a:session-1")
        summary_a = service.set_session_override(session_a, {"response_mode": "detailed"})
        session_a.updated_at = datetime.now(timezone.utc)
        manager_a.save(session_a)

        assert summary_a["effective"]["responseMode"] == "detailed"
        assert summary_a["effective"]["source"] == "session"
        assert summary_a["effective"]["provider"] == "anthropic"

        resumed_manager = SessionManager(tmp_path / "workspace")
        resumed = resumed_manager.get_or_create("web:web:client-a:session-1")
        assert resumed.metadata[SESSION_POLICY_KEY]["response_mode"] == "detailed"
        assert resumed.metadata[SESSION_POLICY_KEY]["version"] == 1

        resumed_summary = service.session_summary(resumed)
        assert resumed_summary["effective"]["responseMode"] == "detailed"
        assert resumed_summary["effective"]["source"] == "session"
        assert resumed_summary["override"]["responseMode"] == "detailed"

        sibling = resumed_manager.get_or_create("web:web:client-b:session-2")
        sibling_summary = service.session_summary(sibling)
        assert sibling_summary["effective"]["responseMode"] == "concise"
        assert sibling_summary["effective"]["source"] == "global"
        assert sibling_summary["override"]["provider"] is None

    def test_clear_removes_override(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        _, session = _session(tmp_path, "web:web:client-a:session-1")
        service.set_session_override(session, {"response_mode": "concise"})
        assert SESSION_POLICY_KEY in session.metadata

        service.set_session_override(session, {"clear": True})

        assert SESSION_POLICY_KEY not in session.metadata


class TestEffectivePolicyResolution:
    def test_per_key_merge_override_over_global(self):
        global_policy = RuntimePolicyConfig(
            provider="anthropic",
            model="anthropic/claude-sonnet-4",
            response_mode="concise",
            version=2,
        )
        meta = {SESSION_POLICY_KEY: {"reasoning_effort": "medium", "version": 1}}

        effective = resolve_effective_policy(
            global_policy,
            meta,
            fallback_model="fallback-model",
            fallback_provider="fallback-provider",
        )

        assert effective.provider == "anthropic"
        assert effective.model == "anthropic/claude-sonnet-4"
        assert effective.response_mode == "concise"
        assert effective.reasoning_effort == "medium"
        assert effective.source == "session"
        assert effective.version == 1
        assert effective.policy_valid

    def test_reasoning_effort_gated_for_unsupported_provider(self):
        meta = {SESSION_POLICY_KEY: {"provider": "gemini_oauth", "reasoning_effort": "high"}}

        effective = resolve_effective_policy(
            default_policy(),
            meta,
            fallback_model="fallback-model",
            fallback_provider="fallback-provider",
        )

        assert effective.provider == "gemini_oauth"
        assert effective.reasoning_effort is None
        assert effective.reasoning_effort_requested == "high"
        assert not effective.reasoning_effort_supported
        assert not effective.reasoning_effort_applied

    def test_reasoning_effort_forwarded_when_supported(self):
        meta = {SESSION_POLICY_KEY: {"reasoning_effort": "high"}}

        effective = resolve_effective_policy(
            default_policy(),
            meta,
            fallback_model="fallback-model",
            fallback_provider="anthropic",
        )

        assert effective.reasoning_effort == "high"
        assert effective.reasoning_effort_supported
        assert effective.reasoning_effort_applied

    def test_global_values_produce_global_source(self):
        global_policy = RuntimePolicyConfig(
            provider="anthropic", model="anthropic/claude-sonnet-4"
        )

        effective = resolve_effective_policy(
            global_policy,
            {},
            fallback_model="fallback-model",
            fallback_provider="fallback-provider",
        )

        assert effective.source == "global"
        assert effective.provider == "anthropic"
        assert effective.model == "anthropic/claude-sonnet-4"

    def test_invalid_selection_falls_back_to_process_defaults(self):
        def _boom(provider, model):
            raise ValueError("provider 'anthropic' is not ready to serve turns")

        global_policy = RuntimePolicyConfig(
            provider="anthropic", model="anthropic/claude-sonnet-4"
        )

        effective = resolve_effective_policy(
            global_policy,
            {},
            fallback_model="fallback-model",
            fallback_provider="fallback-provider",
            validate=_boom,
        )

        assert not effective.policy_valid
        assert effective.provider == "fallback-provider"
        assert effective.model == "fallback-model"
        assert effective.reason == "provider 'anthropic' is not ready to serve turns"


class _StartupProvider(LLMProvider):
    def get_default_model(self) -> str:
        return "anthropic/claude-opus-4-5"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        return LLMResponse(
            content="Done.",
            provider_name="startup-default",
            model_name="startup-model",
        )


class _PolicyServingProvider(LLMProvider):
    def __init__(self, recorded: list):
        super().__init__()
        self.name = "anthropic"
        self.recorded = recorded

    def get_default_model(self) -> str:
        return "anthropic/claude-sonnet-4"

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.recorded.append(
            {
                "model": model,
                "reasoning_effort": kwargs.get("reasoning_effort"),
                "system_prompt": messages[0]["content"],
            }
        )
        return LLMResponse(
            content="Done.",
            provider_name="anthropic",
            model_name="anthropic/claude-sonnet-4",
        )


class TestLoopAttachesPolicy:
    def _message(self, content: str) -> InboundMessage:
        return InboundMessage(
            channel="web", sender_id="browser:owner-a", chat_id="chat-a", content=content
        )

    @pytest.mark.asyncio
    async def test_policy_changes_affect_only_later_runs(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        recorded: list[dict] = []
        agent = AgentLoop(
            bus=MessageBus(),
            provider=_StartupProvider(),
            workspace=tmp_path / "workspace",
            runtime_policy_service=service,
            provider_factory=lambda provider_name, model: _PolicyServingProvider(recorded),
        )

        await agent._process_message(self._message("hello without policy"))
        first = agent.runs.list("web:browser:owner-a", session_key="web:chat-a")[0]
        assert "~v" not in first.policy_revision

        service.set_global(
            {
                "provider": "anthropic",
                "model": "anthropic/claude-sonnet-4",
                "reasoning_effort": "high",
            }
        )

        response = await agent._process_message(self._message("hello with policy"))
        runs = agent.runs.list("web:browser:owner-a", session_key="web:chat-a")
        assert len(runs) == 2
        second = next(run for run in runs if "~v" in run.policy_revision)
        first = next(run for run in runs if "~v" not in run.policy_revision)

        assert second.state == "completed"
        assert second.provider == "anthropic"
        assert second.model == "anthropic/claude-sonnet-4"
        assert second.policy_revision.endswith(
            "~v1:anthropic:anthropic/claude-sonnet-4:high:default"
        )
        assert response is not None and response.metadata["run_id"] == second.id
        assert "~v" not in first.policy_revision
        assert recorded and recorded[0]["model"] == "anthropic/claude-sonnet-4"
        assert recorded[0]["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_queue_run_snapshots_resolved_provider_and_model(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        service.set_global({"provider": "anthropic", "model": "anthropic/claude-sonnet-4"})
        recorded: list[dict] = []
        agent = AgentLoop(
            bus=MessageBus(),
            provider=_StartupProvider(),
            workspace=tmp_path / "workspace",
            runtime_policy_service=service,
            provider_factory=lambda provider_name, model: _PolicyServingProvider(recorded),
        )

        queued = agent._queue_run(self._message("queued turn"))

        assert queued.state == "queued"
        assert queued.provider == "anthropic"
        assert queued.model == "anthropic/claude-sonnet-4"
        assert queued.policy_revision.endswith(
            "~v1:anthropic:anthropic/claude-sonnet-4:-:default"
        )

    @pytest.mark.asyncio
    async def test_queued_turn_keeps_the_policy_at_submission(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        recorded: list[dict] = []
        agent = AgentLoop(
            bus=MessageBus(),
            provider=_StartupProvider(),
            workspace=tmp_path / "workspace",
            runtime_policy_service=service,
            provider_factory=lambda provider_name, model: _PolicyServingProvider(recorded),
        )
        service.set_global({"provider": "anthropic", "model": "anthropic/claude-sonnet-4"})
        session = agent.sessions.get_or_create("web:chat-a")
        service.set_session_override(session, {"response_mode": "concise"})
        queued = agent._queue_run(self._message("queued before the change"))

        service.set_session_override(session, {"response_mode": "detailed"})
        await agent._process_message(self._message("queued before the change"), queued_run_id=queued.id)

        run = agent.runs.get("web:browser:owner-a", queued.id)
        assert run.policy_revision.endswith("~v1:anthropic:anthropic/claude-sonnet-4:-:concise")
        assert "Be concise." in recorded[0]["system_prompt"]
        assert "Be thorough" not in recorded[0]["system_prompt"]

    def test_response_mode_is_injected_for_later_turns(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        _, session = _session(tmp_path, "web:web:client-a:session-1")
        agent = AgentLoop(
            bus=MessageBus(),
            provider=_StartupProvider(),
            workspace=tmp_path / "workspace",
            runtime_policy_service=service,
        )

        default_prompt = agent._context_snapshot(session, agent._effective_policy(session))
        assert "Response preference for this turn" not in default_prompt

        service.set_session_override(session, {"response_mode": "concise"})
        concise_prompt = agent._context_snapshot(session, agent._effective_policy(session))
        assert "Response preference for this turn" in concise_prompt
        assert "Be concise." in concise_prompt

    def test_session_stance_is_explicit_in_prompt_and_fails_closed(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        _, session = _session(tmp_path, "web:web:client-a:session-1")
        agent = AgentLoop(
            bus=MessageBus(),
            provider=_StartupProvider(),
            workspace=tmp_path / "workspace",
            runtime_policy_service=service,
        )

        session.metadata["pico_session_stance"] = "decide"
        decide_prompt = agent._context_snapshot(session, agent._effective_policy(session))
        assert "Current stance: Decide." in decide_prompt
        assert "criteria, tradeoffs" in decide_prompt
        assert "lead with the useful answer" in decide_prompt
        assert "ask one focused question" in decide_prompt
        assert "do not mechanically add headings" in decide_prompt

        session.metadata["pico_session_stance"] = "not-a-stance"
        safe_prompt = agent._context_snapshot(session, agent._effective_policy(session))
        assert "Current stance: Explore." in safe_prompt
        assert session.metadata["pico_session_stance"] == "explore"


class TestWebChannelPolicyWiring:
    def test_channel_scope_isolation_and_no_secret_leak(self, tmp_path: Path, monkeypatch):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        service.set_global({"provider": "anthropic", "model": "anthropic/claude-sonnet-4"})

        channel = WebChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
        monkeypatch.setattr(
            WebChannel, "_runtime_config", staticmethod(lambda: load_config(config_path))
        )
        monkeypatch.setattr(
            WebChannel, "_policy_service", staticmethod(lambda: RuntimePolicyService(config_path))
        )

        policy = channel._policy_service()
        manager = channel._session_manager()
        session_a = manager.get_or_create("web:web:client-a:session-1")
        session_b = manager.get_or_create("web:web:client-b:session-2")

        policy.set_session_override(session_a, {"response_mode": "detailed"})
        session_a.updated_at = datetime.now(timezone.utc)
        manager.save(session_a)

        resumed = SessionManager(tmp_path / "workspace").get_or_create(
            "web:web:client-a:session-1"
        )
        summary_a = policy.session_summary(resumed)
        summary_b = policy.session_summary(session_b)

        assert summary_a["effective"]["responseMode"] == "detailed"
        assert summary_a["effective"]["source"] == "session"
        assert summary_b["effective"]["responseMode"] == "default"
        assert summary_b["effective"]["source"] == "global"

        assert SECRET not in json.dumps(summary_a)
        assert SECRET not in json.dumps(summary_b)

    def test_global_and_session_responses_never_include_credentials(self, tmp_path: Path):
        config_path = _write_config(tmp_path, anthropic_key=SECRET)
        service = RuntimePolicyService(config_path)
        service.set_global({"provider": "anthropic", "model": "anthropic/claude-sonnet-4"})
        _, session = _session(tmp_path, "web:web:client-a:session-1")
        service.set_session_override(session, {"response_mode": "concise"})

        raw = json.loads(config_path.read_text(encoding="utf-8"))
        assert SECRET not in json.dumps(service.summary())
        assert SECRET not in json.dumps(service.session_summary(session))
        assert SECRET not in json.dumps(raw)
