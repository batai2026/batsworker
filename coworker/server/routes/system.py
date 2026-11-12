"""The handful of routes that are not a domain: health, the agent roster, and the
OpenAI-compatible proxy.

`/v1/chat/completions` is what lets any OpenAI-format client use this runtime as a
backend — the one place the OpenAI wire shape is produced (`_openai_response`).

`/v1/health` is also the tokenless liveness probe: with a sidecar token configured it
answers `{"status": "ok"}` to an unauthenticated caller and discloses the workspace
and model only to an authenticated one.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request

from ..auth import request_authenticated
from ..manager import SessionManager
from ...providers import AssistantTurn


def build_system_router(manager: SessionManager, api_token: str) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/health")
    def health(request: Request) -> dict[str, Any]:
        if api_token and not request_authenticated(request, api_token):
            return {"status": "ok"}
        return {
            "status": "ok",
            "default_workspace": manager.default_workspace,
            "model": manager.model,
        }

    @router.get("/v1/agents")
    def agents() -> dict[str, Any]:
        return {"agents": manager.list_agents()}


    @router.post("/v1/chat/completions")
    def chat_completions(body: dict) -> dict[str, Any]:
        model = body.get("model", manager.model)
        turn = manager.provider_complete(
            model, body.get("messages", []), body.get("tools")
        )
        return _openai_response(model, turn)


    return router


def _openai_response(model: str, turn: AssistantTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in turn.tool_calls
        ]
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": turn.finish_reason or "stop",
            }
        ],
    }
