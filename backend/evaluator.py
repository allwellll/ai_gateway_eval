"""Concurrent evaluations. Credentials stay in the running task's memory."""
import asyncio
import hashlib
from datetime import datetime, timezone
import json
import os
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import aiohttp

from .modeltrace import analyze_global_outputs, generate_challenges, load_bank, parse_numbers
from .routing import canonical_model, endpoint_for, protocol_for_model
from .transport import PublicResolver, UpstreamError, call_model, redact

CANDY_PROMPT = """不使用任何外部工具回答以下问题：

在一个黑色的袋子里放有三种口味的糖果，每种糖果有两种不同的形状（圆形和五角星形，不同的形状靠手感可以分辨）。现已知不同口味的糖和不同形状的数量统计如下表。参赛者需要在活动前决定摸出的糖果数目，那么，最少取出多少个糖果才能保证手中同时拥有不同形状的苹果味和桃子味的糖？（同时手中有圆形苹果味匹配五角星桃子味糖果，或者有圆形桃子味匹配五角星苹果味糖果都满足要求）

        苹果味  桃子味  西瓜味
圆形       7      9      8
五角星形   7      6      4

"""


def extract_answer(text):
    """Read the final conclusion, never count a stray '21' in the reasoning."""
    cleaned = text.strip().replace("**", "").replace("\\(", "").replace("\\)", "")
    if re.fullmatch(r"\d{1,4}[。.!！]?", cleaned):
        return int(re.search(r"\d+", cleaned).group())
    # Models often explain why a smaller number fails after stating the answer,
    # e.g. "所以，20 颗仍可能无法配对，而 21 颗可以保证".  Do not let that
    # explanatory 20 override an explicit answer or minimum-count statement.
    candidates = []
    boxed = (r"\\boxed\s*\{\s*(\d{1,4})"
             r"(?:\s*\\text\s*\{[^{}]*\})?\s*\}")
    explicit = (r"(?:最终答案|答案)\s*(?:是|为|为：|is|=|：|:)?\s*"
                r"(?:\\boxed\s*\{\s*)?(\d{1,4})")
    minimum = (r"(?:最少|至少)(?:需要|要)?(?:取出|摸出|拿出)?\s*"
               r"(?:是|为|为：|is|=|：|:)?\s*(\d{1,4})(?!\d)")
    for match in re.finditer(boxed, cleaned, re.I):
        candidates.append((3, match.end(), int(match.group(1))))
    for match in re.finditer(explicit, cleaned, re.I):
        candidates.append((2, match.end(), int(match.group(1))))
    for match in re.finditer(minimum, cleaned, re.I):
        candidates.append((1, match.end(), int(match.group(1))))
    if candidates:
        priority = max(item[0] for item in candidates)
        return max((item for item in candidates if item[0] == priority), key=lambda item: item[1])[2]
    # A bare count is accepted only on the final nonempty line.
    match = re.fullmatch(r"(\d{1,4})\s*(?:颗|个)(?:糖果|糖)?[。.!！]?", cleaned.splitlines()[-1] if cleaned else "")
    if match:
        return int(match.group(1))
    # Last-resort conclusion parsing for answers without an explicit marker.
    for match in re.finditer(r"(?:因此|所以|综上|结论)([^。\n]{0,120})", cleaned, re.I):
        numbers = list(re.finditer(r"\d{1,4}", match.group(1)))
        if numbers:
            return int(numbers[-1].group())
    return None


async def fresh_bank():
    """Refresh before attributing; stale fallback never produces a firm verdict."""
    urls = ["https://api.github.com/repos/xqy2006/ModelTrace/contents/data/unified_bank.json",
            "https://raw.githubusercontent.com/xqy2006/ModelTrace/main/data/unified_bank.json"]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        for url in urls:
            try:
                async with session.get(url, headers={"Accept": "application/vnd.github.raw+json", "User-Agent": "ai-gateway-eval"}) as response:
                    response.raise_for_status()
                    bank = json.loads(await response.read())
                    # Exercise the actual scorer to validate shape and dimensions.
                    analyze_global_outputs([{"text": "[1,2,3]", "expected_count": 3}], bank)
                    return bank, True
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError, IndexError):
                continue
    return load_bank(), False


def summarize(model, fingerprint_outputs, candy_outputs, bank, refreshed):
    symbols, usable, exact, errors = [], [], True, []
    for output in fingerprint_outputs:
        numbers = parse_numbers(output.get("text", ""))
        match = len(numbers) == output["expected_count"] and not output.get("error")
        symbols.append("❓" if output.get("error") else "✔️" if match else "❌")
        exact = exact and match
        if output.get("error"):
            errors.append(output["error"])
        elif numbers:
            # Tolerant attribution can be shown, but can never produce 真/换模.
            usable.append({"text": output["text"], "expected_count": len(numbers)})
    attribution = analyze_global_outputs(usable, bank) if usable else None
    top_model = attribution["prediction"] if attribution else None
    covered = canonical_model(model) in bank["robust"]["model_order"]
    verdict = "存疑"
    if exact and attribution and covered and refreshed:
        verdict = "真" if canonical_model(model) == canonical_model(top_model) else "换模"
    if all(output.get("error") for output in fingerprint_outputs + candy_outputs):
        verdict = "无法评测"
    answers = [extract_answer(output.get("text", "")) if not output.get("error") else None for output in candy_outputs]
    scored = [answer for answer in answers if answer is not None]
    candy_correct = sum(answer == 21 for answer in scored)
    # A strong fingerprint match is not enough to call a model genuine when
    # Candy shows at most half of the scored rounds are correct.
    if verdict == "真" and scored and candy_correct * 2 <= len(scored):
        verdict = "存疑"
    errors.extend(output["error"] for output in candy_outputs if output.get("error"))
    note = [f"Candy 可评分 {len(scored)}/{len(answers)}，失败或无法解析 {len(answers) - len(scored)}"]
    if attribution:
        note.append(f"{'严格' if exact else '容错'}指纹归因 {attribution['probability']:.1%}；指纹统计仅作参考")
    if not covered:
        note.append("指纹库未收录此模型，无法确认真伪")
    if not refreshed:
        note.append("指纹库刷新失败，使用随项目附带的库，仅供参考")
    note.append(f"指纹库构建时间 {bank.get('built_at', '未知')}")
    note.extend(dict.fromkeys(errors))
    return {"fingerprint": "".join(symbols), "top_model": top_model,
            "candy": f"{sum(answer == 21 for answer in scored)}/{len(scored)}",
            "candy_answers": ",".join("?" if answer is None else str(answer) for answer in answers),
            "verdict": verdict, "note": "；".join(note)[:3400]}


async def run_evaluation(request, job, global_limit):
    job["phase"] = "正在更新指纹参考库"
    bank, refreshed = await fresh_bank()
    job["phase"] = "正在并发评测"
    key = request.api_key.get_secret_value()
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    key_prefix = key[:6]
    local_limit = asyncio.Semaphore(request.concurrency)
    circuit = asyncio.Event()
    resolver = PublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, limit=24)
    try:
        async with aiohttp.ClientSession(connector=connector, read_bufsize=1024 * 1024) as session:
            async def one_model(model):
                endpoint = endpoint_for(request.base_url, model)
                protocol = protocol_for_model(model)
                started = time.monotonic()
                stamp = datetime.now(timezone.utc)
                challenges = generate_challenges(3)
                steps = [*challenges, *[{"prompt": CANDY_PROMPT} for _ in range(request.candy_runs)]]
                status = job["models"][model]
                status["state"] = "running"

                async def one_step(index, step):
                    async with local_limit, global_limit:
                        if circuit.is_set():
                            output = {**step, "error": "该站点发生鉴权或网关错误，跳过尚未发送的请求"}
                        else:
                            status["steps"][index] = "running"
                            try:
                                content = await call_model(session, endpoint, model, key, step["prompt"], protocol, request.timeout)
                                output = {**step, "text": content}
                            except UpstreamError as exc:
                                if exc.status in (401, 403) or exc.status >= 500:
                                    circuit.set()
                                output = {**step, "error": redact(str(exc), key)}
                            except Exception:
                                output = {**step, "error": "请求处理失败，未重试"}
                        status["steps"][index] = "error" if output.get("error") else "done"
                        job["completed"] += 1
                        return output

                outputs = await asyncio.gather(*(one_step(i, step) for i, step in enumerate(steps)))
                summary = summarize(model, outputs[:3], outputs[3:], bank, refreshed)
                elapsed = round(time.monotonic() - started, 1)
                status.update(state="completed", elapsed_seconds=elapsed)
                row = {"result_uuid": str(uuid4()), "tested_at": stamp.isoformat(),
                       "tested_date": str(request.tested_date or stamp.astimezone(ZoneInfo("Asia/Shanghai")).date()),
                       "domain": urlsplit(endpoint).hostname, "model": model,
                       "key_hash": key_hash, "key_prefix": key_prefix,
                       "model_rate": request.model_rate, "recharge_rate": request.recharge_rate,
                       "request_ip": os.getenv("EVALUATION_EGRESS_IP") or None, **summary}
                row["note"] = redact(f"/{protocol}；耗时 {elapsed}s；{row['note']}" + (f"；{request.note}" if request.note else ""), key)[:4000]
                job["results"].append(row)
                return row

            await asyncio.gather(*(one_model(model) for model in request.models))
    finally:
        key = ""
        await resolver.close()
