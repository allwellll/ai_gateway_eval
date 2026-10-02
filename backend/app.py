"""Evaluation jobs and result storage; no credential persistence."""
from contextlib import asynccontextmanager
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from ipaddress import ip_address
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
import psycopg
from fastapi.staticfiles import StaticFiles

from .db import connection, ensure_schema, load_dotenv
from .evaluator import run_evaluation
from .routing import endpoint_for, protocol_for_model

load_dotenv()
SHANGHAI = ZoneInfo("Asia/Shanghai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    app.state.jobs = {}
    app.state.tasks = {}
    app.state.limit = asyncio.Semaphore(24)
    yield
    tasks = list(app.state.tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="AI Gateway Eval", version="1.0.0", lifespan=lifespan)
origins = os.getenv("CORS_ORIGINS", "https://allwellll.github.io,http://localhost:8080,http://127.0.0.1:8080").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip().rstrip("/") for o in origins if o.strip()],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Authorization"], max_age=600)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Do not echo arbitrary input (including accidentally pasted credentials).
    return JSONResponse(status_code=422, content={"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]})


@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exc: psycopg.Error):
    logging.error("Database operation failed: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请稍后重试；页面可保留或导出未保存结果。"})


def authorize(authorization: str | None = Header(default=None)):
    token = os.getenv("RESULTS_API_TOKEN", "")
    if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, "结果服务访问口令不正确")


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    result_uuid: UUID = Field(default_factory=uuid4)
    tested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tested_date: date | None = None
    domain: str = Field(min_length=1, max_length=253)
    model: str = Field(min_length=1, max_length=150)
    model_rate: Decimal | None = Field(default=None, ge=0, lt=1000, max_digits=6, decimal_places=3)
    recharge_rate: Decimal | None = Field(default=None, ge=0, lt=1000, max_digits=6, decimal_places=3)
    request_ip: str | None = None
    fingerprint: str | None = Field(default=None, max_length=100)
    top_model: str | None = Field(default=None, max_length=150)
    candy: str | None = Field(default=None, max_length=50)
    candy_answers: str | None = Field(default=None, max_length=500)
    verdict: Literal["真", "换模", "存疑", "无法评测"] = "存疑"
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("request_ip")
    @classmethod
    def valid_ip(cls, value):
        return str(ip_address(value)) if value else None

    @field_validator("tested_at")
    @classmethod
    def aware_datetime(cls, value):
        if value.tzinfo is None:
            raise ValueError("tested_at 必须包含时区")
        return value

    @field_validator("domain")
    @classmethod
    def host_only(cls, value):
        if any(c in value for c in "/?#@ "):
            raise ValueError("domain 只能包含域名或 IP，不能包含路径、查询或凭据")
        return value.lower()

    @model_validator(mode="after")
    def date_default(self):
        if self.tested_date is None:
            self.tested_date = self.tested_at.astimezone(SHANGHAI).date()
        return self


class ResultBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[EvaluationResult] = Field(min_length=1, max_length=12)


COLUMNS = list(EvaluationResult.model_fields)
INSERT_SQL = f"INSERT INTO ai_gateway_eval_results ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))}) ON CONFLICT (result_uuid) DO NOTHING RETURNING *"


@app.get("/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "service": "ai-gateway-eval"}


@app.post("/api/results", dependencies=[Depends(authorize)])
def save_results(batch: ResultBatch):
    rows = []
    with connection() as conn:
        for result in batch.results:
            values = result.model_dump()
            row = conn.execute(INSERT_SQL, [values[c] for c in COLUMNS]).fetchone()
            if row is None:
                row = conn.execute("SELECT * FROM ai_gateway_eval_results WHERE result_uuid = %s", (result.result_uuid,)).fetchone()
            rows.append(row)
    return {"items": rows, "count": len(rows)}


@app.get("/api/results", dependencies=[Depends(authorize)])
def query_results(domain: str | None = Query(default=None, max_length=253),
                  model: str | None = Query(default=None, max_length=150),
                  date_from: date | None = None, date_to: date | None = None,
                  limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "起始日期不能晚于结束日期")
    clauses, args = [], []
    for field, value, op in (("domain", domain, "="), ("model", model, "="), ("tested_date", date_from, ">="), ("tested_date", date_to, "<=")):
        if value is not None:
            clauses.append(f"{field} {op} %s")
            args.append(value.lower() if field == "domain" else value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connection() as conn:
        total = conn.execute("SELECT count(*) AS total FROM ai_gateway_eval_results" + where, args).fetchone()["total"]
        rows = conn.execute("SELECT * FROM ai_gateway_eval_results" + where + " ORDER BY tested_at DESC, id DESC LIMIT %s OFFSET %s", [*args, limit, offset]).fetchall()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    base_url: str = Field(min_length=1, max_length=1000)
    api_key: SecretStr = Field(min_length=1, max_length=1000)
    models: list[str] = Field(default_factory=lambda: ["gpt-6-astra"], min_length=1, max_length=12)
    candy_runs: int = Field(default=5, ge=1, le=10)
    concurrency: int = Field(default=16, ge=1, le=24)
    timeout: int = Field(default=300, ge=10, le=600)
    tested_date: date | None = None
    model_rate: Decimal | None = Field(default=None, ge=0, lt=1000, max_digits=6, decimal_places=3)
    recharge_rate: Decimal | None = Field(default=None, ge=0, lt=1000, max_digits=6, decimal_places=3)
    note: str = Field(default="", max_length=400)

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        key = value.get_secret_value().strip()
        if not key or "\r" in key or "\n" in key:
            raise ValueError("API key 不能为空或包含换行")
        return SecretStr(key)

    @field_validator("models")
    @classmethod
    def valid_models(cls, values):
        import re
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,149}", v) for v in values):
            raise ValueError("模型名称格式不正确")
        return list(dict.fromkeys(values))

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        endpoint_for(value, "gpt-6-astra")
        return value


def get_job(job_id):
    job = app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期；已保存结果可在历史记录中查询")
    return job


async def execute_job(job_id, request):
    job = get_job(job_id)
    job["status"] = "running"
    try:
        await run_evaluation(request, job, app.state.limit)
        job["phase"] = "正在保存结果"
        try:
            batch = ResultBatch(results=job["results"])
            await asyncio.to_thread(save_results, batch)
            job.update(status="completed", saved=True, phase="评测完成，结果已保存")
        except Exception:
            job.update(status="save_failed", phase="评测完成，保存失败；可重试保存或导出结果")
    except asyncio.CancelledError:
        job.update(status="cancelled", phase="任务已取消；已完成的结果可手动保存")
    except Exception:
        job.update(status="failed", phase="评测执行失败，请检查接口地址与服务配置")
    finally:
        request = None
        job["finished_at"] = time.time()
        app.state.tasks.pop(job_id, None)


@app.post("/api/evaluations", status_code=202, dependencies=[Depends(authorize)])
async def create_evaluation(request: EvaluationRequest):
    jobs = app.state.jobs
    for identifier, previous in list(jobs.items()):
        if previous.get("finished_at", float("inf")) < time.time() - 3600:
            jobs.pop(identifier, None)
    if len(app.state.tasks) >= 4:
        raise HTTPException(429, "服务正在执行较多任务，请稍后再试")
    if len(jobs) >= 256:
        for identifier in list(jobs):
            if identifier not in app.state.tasks:
                jobs.pop(identifier)
                break
    identifier = secrets.token_urlsafe(24)
    jobs[identifier] = {"id": identifier, "status": "queued", "phase": "等待执行", "saved": False,
        "completed": 0, "total": len(request.models) * (3 + request.candy_runs), "results": [],
        "models": {model: {"endpoint": endpoint_for(request.base_url, model), "protocol": protocol_for_model(model),
                            "state": "queued", "steps": ["queued"] * (3 + request.candy_runs)} for model in request.models}}
    app.state.tasks[identifier] = asyncio.create_task(execute_job(identifier, request))
    return {"id": identifier, "status": "queued"}


@app.get("/api/evaluations/{job_id}", dependencies=[Depends(authorize)])
def evaluation_status(job_id: str):
    return get_job(job_id)


@app.post("/api/evaluations/{job_id}/cancel", dependencies=[Depends(authorize)])
async def cancel_evaluation(job_id: str):
    job = get_job(job_id)
    if job["phase"] == "正在保存结果":
        raise HTTPException(409, "评测已结束，正在保存结果")
    task = app.state.tasks.get(job_id)
    if task:
        task.cancel()
        await task
    return job


@app.post("/api/evaluations/{job_id}/save", dependencies=[Depends(authorize)])
async def retry_save(job_id: str):
    job = get_job(job_id)
    if job["status"] not in ("completed", "save_failed", "cancelled") or not job["results"]:
        raise HTTPException(409, "当前没有可保存的已完成结果")
    await asyncio.to_thread(save_results, ResultBatch(results=job["results"]))
    job.update(saved=True, phase="结果已保存")
    if job["status"] == "save_failed":
        job["status"] = "completed"
    return job


# Publish only web/, never the project root or .env.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web", html=True), name="web")
