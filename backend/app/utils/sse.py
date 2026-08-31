"""SSE 事件封装。"""
import json
from typing import Any


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def step(step_type: str, title: str, detail: dict[str, Any] | None = None) -> str:
    """过程可视化事件：intent / retrieve / thinking / tool_call。"""
    return sse_event("step", {"type": step_type, "title": title, "detail": detail or {}})


def clarify(question: str, missing_fields: list[str], intents: list[dict]) -> str:
    return sse_event(
        "clarify",
        {"question": question, "missing_fields": missing_fields, "intents": intents},
    )


def token(text: str) -> str:
    return sse_event("token", {"text": text})


def reasoning(text: str) -> str:
    return sse_event("reasoning", {"text": text})


def sources(items: list[dict[str, Any]]) -> str:
    return sse_event("sources", {"sources": items})


def done(message_id: str) -> str:
    return sse_event("done", {"message_id": message_id})


def error(code: str, message: str) -> str:
    return sse_event("error", {"code": code, "message": message})
