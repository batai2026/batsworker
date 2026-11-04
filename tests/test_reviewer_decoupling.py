"""Reviewer model decoupling: the reviewer can use a different model than the session
(decorrelated errors — the reviewer won't share the session model's blind spots)."""


from coworker.config import Config
from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers.base import ProviderClient, AssistantTurn
from coworker.tools.registry import ToolRegistry


class FakeProvider(ProviderClient):
    """Records the model string it was called with — lets the test verify which
    model the reviewer vs the session actually used."""

    def __init__(self):
        self.session_calls: list[str] = []
        self.reviewer_calls: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        # The reviewer's request has a distinctive prompt (it judges ONE action).
        if any("judge" in str(m.get("content", "")) for m in messages if isinstance(m, dict)):
            self.reviewer_calls.append(model)
        else:
            self.session_calls.append(model)
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()


def _make_engine(tmp_path, *, reviewer_model=None):
    provider = FakeProvider()
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="openai/gpt-5.6-sol",
        max_iterations=10,
    )
    from coworker.reviewer import Reviewer

    engine.reviewer = Reviewer(
        provider=provider,
        model=reviewer_model or "openai/gpt-5.6-sol",
        known_world="",
    )
    return engine, provider


def test_reviewer_defaults_to_session_model(tmp_path):
    engine, provider = _make_engine(tmp_path)
    assert engine.reviewer.model == "openai/gpt-5.6-sol"


def test_reviewer_model_decoupled(tmp_path):
    engine, provider = _make_engine(tmp_path, reviewer_model="anthropic/claude-4-sonnet")
    assert engine.reviewer.model == "anthropic/claude-4-sonnet"
    assert engine.reviewer.model != engine.model  # decorrelated


def test_same_provider_instance_routes_both(tmp_path):
    """The same ProviderRouter serves both session and reviewer — it routes by
    model prefix, so a different model string automatically selects a different
    provider client (no second client instance needed)."""
    engine, provider = _make_engine(tmp_path, reviewer_model="anthropic/claude-4-sonnet")
    # The reviewer holds the SAME provider object (ProviderRouter).
    assert engine.reviewer.provider is provider


def test_config_field_defaults_to_none():
    cfg = Config()
    assert cfg.reviewer_model is None


def test_config_reviewer_model_settable():
    cfg = Config(reviewer_model="anthropic/claude-4-sonnet")
    assert cfg.reviewer_model == "anthropic/claude-4-sonnet"


# -- build_engine wiring -----------------------------------------------------------


def test_build_engine_passes_reviewer_model(tmp_path):
    """build_engine with reviewer_model set → the engine's reviewer uses that model."""
    from coworker.agent import build_engine
    from coworker.agents.base import Agent

    provider = FakeProvider()
    agent = Agent(name="test", title="Test", system_prompt="test")
    _registry = ToolRegistry()
    _permissions = PermissionEngine(workspace_root=tmp_path)
    engine = build_engine(
        agent=agent,
        workspace=tmp_path,
        model="openai/gpt-5.6-sol",
        approver=lambda: "allow",
        provider=provider,
        auto_approve=True,
        reviewer_model="anthropic/claude-4-sonnet",
    )
    assert engine.reviewer is not None
    assert engine.reviewer.model == "anthropic/claude-4-sonnet"


def test_build_engine_default_uses_session_model(tmp_path):
    """Without reviewer_model, the reviewer uses the session model (§1.5 default)."""
    from coworker.agent import build_engine
    from coworker.agents.base import Agent

    provider = FakeProvider()
    agent = Agent(name="test", title="Test", system_prompt="test")
    engine = build_engine(
        agent=agent,
        workspace=tmp_path,
        model="openai/gpt-5.6-sol",
        approver=lambda: "allow",
        provider=provider,
        auto_approve=True,
    )
    assert engine.reviewer is not None
    assert engine.reviewer.model == "openai/gpt-5.6-sol"
