"""Client for the DeepSeek chat completions API.

Only the standard library is used, so the service starts on a machine
without pip access. The upstream stream is OpenAI compatible, which also
covers self-hosted gateways such as Ollama (``DEEPSEEK_BASE_URL``).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

from config import Settings
from prompts import PROPOSAL_END, PROPOSAL_START
from protocol import fallback_summary, sanitize_changes

UPSTREAM_ERRORS = {
    400: "请求格式不被模型接受，请换一种说法再试。",
    401: "DeepSeek API key 无效，请检查 .env 里的 DEEPSEEK_API_KEY。",
    402: "DeepSeek 账户余额不足，请在平台充值后再试。",
    422: "请求参数有误，请稍后再试。",
    429: "请求太频繁，请等几秒再发送。",
    500: "DeepSeek 服务暂时不可用，请稍后重试。",
    503: "DeepSeek 服务繁忙，请稍后重试。",
}


class UpstreamError(RuntimeError):
    """Raised when the upstream API cannot complete a request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ProposalSplitter:
    """Separate reply text from the trailing machine readable block.

    The model streams text token by token, so the splitter keeps a tail
    buffer that may still turn out to be a sentinel and only emits text
    that is guaranteed to be outside the block.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_block = False
        self._block = ""
        self._closed = False

    def feed(self, chunk: str) -> str:
        """Consume *chunk*, return reply text that is safe to send."""
        if not chunk or self._closed:
            return ""
        self._buffer += chunk
        out: list[str] = []

        while True:
            if not self._in_block:
                index = self._buffer.find(PROPOSAL_START)
                if index == -1:
                    keep = len(PROPOSAL_START) - 1
                    if len(self._buffer) > keep:
                        cut = len(self._buffer) - keep
                        out.append(self._buffer[:cut])
                        self._buffer = self._buffer[cut:]
                    break
                out.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(PROPOSAL_START):]
                self._in_block = True
                continue

            end = self._buffer.find(PROPOSAL_END)
            if end == -1:
                break
            self._block = self._buffer[:end]
            self._buffer = ""
            self._closed = True
            break

        return "".join(out)

    def finish(self) -> str:
        """Flush leftover text once the upstream stream is finished."""
        if self._closed:
            return ""
        self._closed = True
        if self._in_block:
            # An unterminated block is malformed output: drop it rather
            # than leak raw JSON into the chat bubble.
            self._buffer = ""
            return ""
        leftover = self._buffer
        self._buffer = ""
        return leftover

    @property
    def block(self) -> str:
        return self._block


def parse_proposal(raw: str, goals_count: int | None) -> dict[str, Any] | None:
    """Turn the raw block text into a validated proposal, if possible."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    changes = sanitize_changes(payload.get("changes"), goals_count)
    if not changes:
        return None
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = fallback_summary(changes)
    return {"summary": summary.strip()[:120], "changes": changes}


def _payload(settings: Settings, messages: list[dict[str, str]]) -> bytes:
    return json.dumps(
        {
            "model": settings.model,
            "messages": messages,
            "stream": True,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
        }
    ).encode("utf-8")


def iter_deltas(
    settings: Settings, messages: list[dict[str, str]]
) -> Iterator[str]:
    """Yield content deltas from the upstream streaming endpoint."""
    request = urllib.request.Request(
        settings.chat_endpoint,
        data=_payload(settings, messages),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "xingjian-agent/1.0",
        },
    )
    try:
        response = urllib.request.urlopen(
            request, timeout=settings.timeout
        )
    except urllib.error.HTTPError as error:
        message = UPSTREAM_ERRORS.get(
            error.code, f"DeepSeek 返回 {error.code}，请稍后重试。"
        )
        raise UpstreamError(message, status=error.code) from error
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise UpstreamError(
            f"连不上 DeepSeek（{reason}），请检查网络或 DEEPSEEK_BASE_URL。"
        ) from error
    except TimeoutError as error:
        raise UpstreamError("DeepSeek 响应超时，请重试。") from error

    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
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
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content


def iter_reply(
    settings: Settings,
    messages: list[dict[str, str]],
    goals_count: int | None = None,
    context: str = "home",
) -> Iterator[tuple[str, Any]]:
    """Yield ``("delta", text)`` and ``("proposal", dict)`` events."""
    del context  # the panel is already described inside the state block
    splitter = ProposalSplitter()
    for chunk in iter_deltas(settings, messages):
        text = splitter.feed(chunk)
        if text:
            yield "delta", text
    tail = splitter.finish()
    if tail.strip():
        yield "delta", tail
    proposal = parse_proposal(splitter.block, goals_count)
    if proposal:
        yield "proposal", proposal


def complete(
    settings: Settings, messages: list[dict[str, str]]
) -> str:
    """Collect a full non-streaming reply (used by the report endpoint)."""
    parts: list[str] = []
    for kind, value in iter_reply(settings, messages):
        if kind == "delta":
            parts.append(value)
    return "".join(parts).strip()
