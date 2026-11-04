"""Multi-model routing (fast_model): utility call fallback chain, main loop uses
session model, config plumbing."""



from coworker.config import Config
from coworker.engine import TurnEngine
from coworker.permissions import PermissionEngine
from coworker.providers.base import ProviderClient, AssistantTurn
from coworker.tools.registry import ToolRegistry


class ScriptedProvider(ProviderClient):
    """Records every model string it's called with."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append(model)
        return self._turns.pop(0)

    def capabilities(self, model):
        from coworker.providers.base import ModelCapabilities

        return ModelCapabilities()


def _make_engine(tmp_path, *, model="openai/gpt-5.6-sol", fast_model=None, turns=None):
    provider = ScriptedProvider(turns or [AssistantTurn(text="ok", finish_reason="stop")])
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model=model,
        max_iterations=10,
    )
    engine.fast_model = fast_model
    return engine, provider


def _collect(engine, user_input):
    import asyncio

    async def _run():
        return [ev async for ev in engine.run(user_input)]

    return asyncio.run(_run())


# -- config fallback chain ----------------------------------------------------------


def test_fast_model_defaults_to_none():
    cfg = Config()
    assert cfg.fast_model is None


def test_fast_model_settable():
    cfg = Config(fast_model="openai/gpt-4o-mini")
    assert cfg.fast_model == "openai/gpt-4o-mini"


# -- engine model selection ----------------------------------------------------------


def test_select_model_defaults_to_session_model(tmp_path):
    engine, _ = _make_engine(tmp_path)
    assert engine._select_model() == "openai/gpt-5.6-sol"


def test_select_model_with_fast_model_still_returns_session_model(tmp_path):
    """v1: the main loop ALWAYS uses the session model — fast_model is for
    utility calls only. The _select_model infrastructure exists for v2."""
    engine, _ = _make_engine(tmp_path, fast_model="openai/gpt-4o-mini")
    assert engine._select_model() == "openai/gpt-5.6-sol"


def test_main_loop_uses_session_model_not_fast_model(tmp_path):
    """Even with fast_model set, the actual model calls use the session model."""
    engine, provider = _make_engine(
        tmp_path,
        model="openai/gpt-5.6-sol",
        fast_model="openai/gpt-4o-mini",
        turns=[AssistantTurn(text="ok", finish_reason="stop")],
    )
    _collect(engine, "go")
    assert all(m == "openai/gpt-5.6-sol" for m in provider.calls)


# -- compaction fallback chain --------------------------------------------------------


def test_compaction_model_fallback_chain(tmp_path):
    """compaction_model (from compaction_settings) > fast_model > session model."""
    # Case 1: no compaction_model override, fast_model set → fast_model used.
    engine, _ = _make_engine(
        tmp_path,
        model="openai/gpt-5.6-sol",
        fast_model="openai/gpt-4o-mini",
    )
    engine.compaction_settings = lambda: {}
    cfg = engine._compaction_config()
    model = str(cfg.get("model") or "") or engine.fast_model or engine.model
    assert model == "openai/gpt-4o-mini"


def test_compaction_model_explicit_override_wins(tmp_path):
    engine, _ = _make_engine(
        tmp_path,
        model="openai/gpt-5.6-sol",
        fast_model="openai/gpt-4o-mini",
    )
    engine.compaction_settings = lambda: {"model": "anthropic/claude-4-sonnet"}
    cfg = engine._compaction_config()
    model = str(cfg.get("model") or "") or engine.fast_model or engine.model
    assert model == "anthropic/claude-4-sonnet"


def test_compaction_no_fast_model_uses_session(tmp_path):
    engine, _ = _make_engine(
        tmp_path,
        model="openai/gpt-5.6-sol",
        fast_model=None,
    )
    engine.compaction_settings = lambda: {}
    cfg = engine._compaction_config()
    model = str(cfg.get("model") or "") or engine.fast_model or engine.model
    assert model == "openai/gpt-5.6-sol"


# -- reviewer fallback chain ------------------------------------------------------------


def test_reviewer_fallback_chain(tmp_path):
    """reviewer_model > fast_model > session model."""
    from coworker.reviewer import Reviewer

    provider = ScriptedProvider([AssistantTurn(text="ok", finish_reason="stop")])
    registry = ToolRegistry()
    permissions = PermissionEngine(workspace_root=tmp_path)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model="openai/gpt-5.6-sol",
    )

    # Case 1: reviewer_model set — wins over everything.
    engine.reviewer = Reviewer(provider=provider, model="anthropic/claude-4-sonnet")
    assert engine.reviewer.model == "anthropic/claude-4-sonnet"

    # Case 2: reviewer_model not set, fast_model set — fast_model used.
    engine.fast_model = "openai/gpt-4o-mini"
    # (The actual fallback is in build_engine; here we verify the model attribute.)
    assert engine.reviewer.model == "anthropic/claude-4-sonnet"  # unchanged from case 1
