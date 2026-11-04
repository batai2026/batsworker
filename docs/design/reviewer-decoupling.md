# Reviewer model decoupling

> Source: `coworker/config.py` (`reviewer_model`), `coworker/agent.py::build_engine`
> (Reviewer construction with `reviewer_model or model`), `manager.py` (config pass).

The Auto-Approve reviewer (§1.5) currently uses the **same model** as the
session — the rationale being "if it's trusted to drive the agent, it's strong
enough to review it." But same-model review has a blind spot: the reviewer
shares the session model's training data, biases, and injection
susceptibilities. A prompt injection that fools the session model likely fools
the reviewer too.

## The fix

`config.reviewer_model` (user-global, `None` default): when set, the reviewer
constructs with this model string instead of the session model. The same
`ProviderRouter` serves both — it routes by model prefix, so a different
provider family (e.g. session on `openai/gpt-5.6-sol`, reviewer on
`anthropic/claude-4-sonnet`) automatically selects a different provider
client. No second client instance, no second API key, no prompt changes.

## Why this works

The reviewer's prompt is **already independent** of the session prompt: it
receives a mechanically-extracted operation summary (tool name, arguments,
provenance) plus the session's "known world" facts — not the full conversation
history. Swapping the model changes only the judgment, not the input format.

## Trust model

| Config | Behavior |
|---|---|
| `reviewer_model` unset (default) | Reviewer = session model (§1.5, current behavior) |
| `reviewer_model` = different family | Decorrelated review — the reviewer's errors are independent of the session model's |
| `reviewer_model` = same family, different model | Weaker decorrelation (same training data lineage) but still useful |

The field is user-global (`_GLOBAL_ONLY_FIELDS`) — a repo config can't assign
the reviewer model, preserving the "a repo can't weaken security" invariant.

## Evaluation

`reports/` contains reviewer-model comparison evals (GPT-5.6-sol, GLM-5.2,
Kimi-k3, Muse-Spark — August 2026). The shadow mode (§1.5 shadow evaluation)
can A/B same-model vs different-model reviewer verdicts against human
decisions without changing the live path. The eval corpora in
`tests/corpora/` (benign/dangerous/injection/permission_gate JSONL) provide
the test set.
