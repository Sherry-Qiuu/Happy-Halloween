"""Streaming chat completion with OpenAI-style tool calls.

The gateway returns ``tool_calls`` as streamed fragments, so the caller
gets text as it arrives and the finished call list at the end.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

from config import Settings
from deepseek import UPSTREAM_ERRORS, UpstreamError


def _request(settings: Settings, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> urllib.request.Request:
    body: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "stream": True,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    return urllib.request.Request(
        settings.chat_endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
            "User-Agent": "morrow-agent/1.0",
        },
        method="POST",
    )


def iter_events(
    settings: Settings,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yield ``("delta", text)`` and one final ``("turn", payload)``.

    ``payload`` is ``{"content": str, "tool_calls": [...], "finish_reason": str}``.
    """
    request = _request(settings, messages, tools)
    content_parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish_reason = ""

    try:
        response = urllib.request.urlopen(request, timeout=settings.timeout)
    except urllib.error.HTTPError as error:
        message = UPSTREAM_ERRORS.get(error.code, f"DeepSeek 返回 {error.code}")
        raise UpstreamError(message, status=error.code) from error
    except urllib.error.URLError as error:
        raise UpstreamError(f"连不上模型服务（{getattr(error, 'reason', error)}）") from error
    except TimeoutError as error:
        raise UpstreamError("模型响应超时") from error

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if text:
                content_parts.append(text)
                yield "delta", text
            for item in delta.get("tool_calls") or []:
                index = int(item.get("index") or 0)
                slot = calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if item.get("id"):
                    slot["id"] = item["id"]
                function = item.get("function") or {}
                if function.get("name"):
                    slot["function"]["name"] += function["name"]
                if function.get("arguments"):
                    slot["function"]["arguments"] += function["arguments"]

    yield "turn", {
        "content": "".join(content_parts),
        "tool_calls": [calls[key] for key in sorted(calls)],
        "finish_reason": finish_reason,
    }
