"""Bounded, streaming HTTP calls with no retries or credential forwarding."""
import asyncio
import json
import os
from ipaddress import ip_address
from urllib.parse import urlsplit

import aiohttp


class UpstreamError(Exception):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def redact(value: str, key: str) -> str:
    return value.replace(key, "[REDACTED]") if key else value


def check_address(address: str, host: str):
    allowed = {h.strip() for h in os.getenv("ALLOWED_PRIVATE_HOSTS", "").split(",") if h.strip()}
    if host not in allowed and not ip_address(address).is_global:
        raise ValueError("评测地址不能指向本机、内网或保留地址")


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=0):
        records = await self.resolver.resolve(host, port, family)
        for record in records:
            check_address(record["host"], host)
        return records

    async def close(self):
        await self.resolver.close()


def text_from_json(data):
    if not isinstance(data, dict):
        return ""
    if data.get("error"):
        raise UpstreamError(json.dumps(data["error"], ensure_ascii=False)[:800])
    if data.get("status") in ("incomplete", "failed", "cancelled"):
        raise UpstreamError("上游输出未完成：" + str(data.get("incomplete_details") or data.get("status")))
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    if isinstance(data.get("content"), list):
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
    return "".join(b.get("text", "") for item in data.get("output", []) if isinstance(item, dict)
                   for b in item.get("content", []) if b.get("type") in ("output_text", "text"))


class StreamParser:
    def __init__(self):
        self.parts = []
        self.final = ""
        self.complete = False

    def feed(self, raw):
        if raw.strip() == "[DONE]":
            self.complete = True
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UpstreamError("上游 SSE 返回无效 JSON") from exc
        kind = event.get("type", "")
        if kind in ("error", "response.failed", "response.incomplete") or event.get("error"):
            raise UpstreamError(json.dumps(event.get("error") or event.get("response") or event, ensure_ascii=False)[:800])
        if kind == "response.output_text.delta":
            self.parts.append(event.get("delta", ""))
        elif kind == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "text":
                self.parts.append(block.get("text", ""))
        elif kind == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
            self.parts.append(event["delta"].get("text", ""))
        elif kind == "response.completed":
            self.final = text_from_json(event.get("response", {}))
            self.complete = True
        elif kind == "message_delta" and event.get("delta", {}).get("stop_reason") in ("max_tokens", "model_context_window_exceeded"):
            raise UpstreamError("上游截断了输出")
        elif kind == "message_stop":
            self.complete = True

    def text(self):
        if not self.complete:
            raise UpstreamError("上游流提前断开，未收到完成事件")
        return self.final or "".join(self.parts)


async def read_body(response):
    # Protect memory without imposing model token limits.
    maximum = 4 * 1024 * 1024
    if "text/event-stream" not in response.headers.get("Content-Type", ""):
        content = bytearray()
        async for chunk in response.content.iter_chunked(16384):
            content.extend(chunk)
            if len(content) > maximum:
                raise UpstreamError("上游响应超过 4 MiB")
        return text_from_json(json.loads(content))
    parser, data, size = StreamParser(), [], 0
    while True:
        line = await response.content.readline()
        if not line:
            break
        size += len(line)
        if size > maximum:
            raise UpstreamError("上游响应超过 4 MiB")
        line = line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                parser.feed("\n".join(data)); data = []
                if parser.complete:
                    break
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data and not parser.complete:
        parser.feed("\n".join(data))
    return parser.text()


async def call_model(session, endpoint, model, key, prompt, protocol, timeout):
    parsed = urlsplit(endpoint)
    try:
        address = ip_address(parsed.hostname)
    except ValueError:
        pass  # DNS names are checked and resolved once by PublicResolver.
    else:
        check_address(str(address), parsed.hostname)
    headers = {"User-Agent": "curl/8.5.0", "Authorization": "Bearer " + key, "Accept": "text/event-stream"}
    if protocol == "messages":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}, "stream": True}
    else:
        payload = {"model": model, "input": prompt, "reasoning": {"effort": "high"}, "store": False, "stream": True}
    try:
        async with session.post(endpoint, headers=headers, json=payload, allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=timeout, connect=20)) as response:
            if response.status >= 300:
                raw = (await response.content.read(8192)).decode("utf-8", "replace")
                raise UpstreamError(f"HTTP {response.status}: {redact(raw, key)[:700]}", response.status)
            text = await read_body(response)
            if not text.strip():
                raise UpstreamError("上游未返回可评分的文本")
            return text
    except asyncio.TimeoutError as exc:
        raise UpstreamError(f"请求超时（{timeout} 秒），未重试") from exc
    except UpstreamError as exc:
        raise UpstreamError(redact(str(exc), key), exc.status) from None
    except Exception as exc:
        raise UpstreamError(redact(str(exc), key)[:700]) from None
