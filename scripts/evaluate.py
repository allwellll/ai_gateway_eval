#!/usr/bin/env python3
"""Run one model evaluation through the local AI Gateway Eval service.

The API key is sent only to the evaluation endpoint over the configured
transport. It is never written to disk or included in the output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BACKEND = os.getenv("EVAL_BACKEND_URL", "http://127.0.0.1:8080")


def request_json(base: str, path: str, method: str = "GET", payload: dict | None = None,
                token: str = "") -> dict:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(base.rstrip("/") + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
        except Exception:
            detail = {"detail": error.reason}
        message = detail.get("detail", detail) if isinstance(detail, dict) else detail
        raise RuntimeError(f"HTTP {error.code}: {message}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接评测服务: {error.reason}") from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="通过 AI Gateway Eval 服务评测一个模型并自动入库")
    result.add_argument("--endpoint", required=True, help="上游接口地址，例如 https://857728.com")
    result.add_argument("--key", required=True, help="上游 API key；仅用于本次评测，不会写入输出或文件")
    result.add_argument("--model", required=True, help="模型名称，例如 gpt-6-astra")
    result.add_argument("--backend", default=DEFAULT_BACKEND, help=f"评测服务地址，默认 {DEFAULT_BACKEND}")
    result.add_argument("--candy-runs", type=int, default=5, choices=range(1, 11), metavar="N")
    result.add_argument("--concurrency", type=int, default=16, choices=range(1, 25), metavar="N")
    result.add_argument("--timeout", type=int, default=300, metavar="SECONDS")
    result.add_argument("--tested-date", help="可选，按 YYYY-MM-DD 记录评测日期")
    result.add_argument("--model-rate", type=str, help="可选，模型倍率")
    result.add_argument("--recharge-rate", type=str, help="可选，充值倍率")
    result.add_argument("--note", default="", help="可选备注，不要填写 key")
    result.add_argument("--poll-interval", type=float, default=1.2, help="轮询间隔秒数，默认 1.2")
    result.add_argument("--json", action="store_true", help="只输出最终 JSON，便于其他 agent 解析")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.key.strip():
        parser().error("--key 不能为空")
    if args.timeout < 10 or args.timeout > 600:
        parser().error("--timeout 必须在 10 到 600 秒之间")
    token = os.getenv("RESULTS_API_TOKEN", "")
    payload = {
        "base_url": args.endpoint.strip(),
        "api_key": args.key,
        "models": [args.model.strip()],
        "candy_runs": args.candy_runs,
        "concurrency": args.concurrency,
        "timeout": args.timeout,
        "tested_date": args.tested_date,
        "model_rate": args.model_rate,
        "recharge_rate": args.recharge_rate,
        "note": args.note,
    }
    # Do not retain the secret longer than necessary in this process.
    try:
        created = request_json(args.backend, "/api/evaluations", "POST", payload, token)
    finally:
        payload["api_key"] = ""
    job_id = created.get("id")
    if not job_id:
        raise RuntimeError("服务未返回任务 ID")
    if not args.json:
        print(f"任务已创建: {job_id}", flush=True)
    while True:
        job = request_json(args.backend, f"/api/evaluations/{job_id}", token=token)
        if not args.json:
            print(f"{job.get('phase', job.get('status'))} · {job.get('completed', 0)}/{job.get('total', 0)}", flush=True)
        if job.get("status") not in ("queued", "running"):
            if job.get("status") == "save_failed":
                raise RuntimeError("评测完成但自动入库失败，请保留任务 ID 后调用 /save 重试")
            if job.get("status") != "completed":
                raise RuntimeError(job.get("phase") or "评测失败")
            output = {"job_id": job_id, "status": job.get("status"), "saved": job.get("saved", False),
                      "results": job.get("results", [])}
            if args.json:
                print(json.dumps(output, ensure_ascii=False))
            else:
                print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        time.sleep(max(0.2, args.poll_interval))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyboardInterrupt) as error:
        print(f"评测失败: {error}", file=sys.stderr)
        raise SystemExit(1)
