# 模型评测脚本

`scripts/evaluate.py` 是其他 agent 和自动化任务调用模型评测的统一入口。它接收上游接口地址、API key 和模型名，提交到当前评测服务，等待并发评测完成，并返回结果。评测服务完成任务后会自动写入 PostgreSQL 的 `ai_gateway_eval_results` 表。

## 快速使用

在项目根目录执行：

```bash
.venv/bin/python scripts/evaluate.py \
  --endpoint https://857728.com \
  --key "$UPSTREAM_API_KEY" \
  --model gpt-6-astra
```

默认服务地址是 `http://127.0.0.1:8080`，也可以通过 `EVAL_BACKEND_URL` 或 `--backend` 指定：

```bash
EVAL_BACKEND_URL=http://127.0.0.1:8080 \
.venv/bin/python scripts/evaluate.py \
  --endpoint https://857728.com \
  --key "$UPSTREAM_API_KEY" \
  --model opus-5-5 \
  --candy-runs 5 \
  --json
```

`--json` 只输出最终 JSON，适合被其他 agent 或 CI 解析。普通模式会显示任务进度，最后仍输出完整结果 JSON。

## 参数

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--endpoint` | 是 | 无 | 上游兼容接口地址，可以是域名、`/v1` 或完整路径 |
| `--key` | 是 | 无 | 上游 API key，只在当前任务中使用 |
| `--model` | 是 | 无 | 单个模型名；接口类型由服务按模型自动判断 |
| `--backend` | 否 | `http://127.0.0.1:8080` | 本地评测服务地址 |
| `--candy-runs` | 否 | `5` | Candy 轮数，1～10 |
| `--concurrency` | 否 | `16` | 服务端并发请求数，1～24 |
| `--timeout` | 否 | `300` | 单次请求超时，10～600 秒 |
| `--tested-date` | 否 | 当前时间 | 只需要日期时使用 `YYYY-MM-DD` |
| `--model-rate` / `--recharge-rate` | 否 | 空 | 计费倍率 |
| `--note` | 否 | 空 | 备注，不要填入 key |
| `--json` | 否 | 关闭 | 只输出最终 JSON |

## 返回示例

成功时退出码为 `0`，并返回：

```json
{
  "job_id": "任务 ID",
  "status": "completed",
  "saved": true,
  "results": [
    {
      "domain": "857728.com",
      "model": "gpt-6-astra",
      "fingerprint": "✔️✔️✔️",
      "top_model": "gpt-6-astra",
      "candy": "5/5",
      "candy_answers": "21,21,21,21,21",
      "verdict": "真"
    }
  ]
}
```

`results` 中的记录已经经过服务端入库。脚本不会输出完整 key；数据库只保存 key 的 SHA-256 标识和前 6 位前缀（例如 `sk-abc***`）。

## 入库与失败处理

脚本调用的流程是：

1. `POST /api/evaluations` 创建任务。服务端根据模型名称选择 `messages` 或 `responses` 路径，并执行指纹和 Candy 测试。
2. 轮询 `GET /api/evaluations/{job_id}`，直到状态变为 `completed`、`save_failed` 或 `failed`。
3. `completed` 且 `saved=true` 表示结果已经写入 PostgreSQL。

如果评测请求已经完成但自动入库失败，服务会返回 `save_failed`。不要重复发起上游评测；使用原任务 ID 重试保存：

```bash
curl -X POST http://127.0.0.1:8080/api/evaluations/JOB_ID/save
```

若服务配置了 `RESULTS_API_TOKEN`，脚本会从同名环境变量读取 Bearer token。建议通过环境变量或密钥管理器传入 `--key` 和 token，避免把秘密写入 shell 历史、日志或提交内容。

## 给其他 agent 的约定

其他 agent 不应直接调用上游模型，也不应直接执行 SQL。统一调用：

```bash
.venv/bin/python scripts/evaluate.py --endpoint URL --key KEY --model MODEL --json
```

解析标准输出中的最后一个 JSON 对象；以 `saved: true` 作为入库成功条件，以 `results` 作为本次评测结果。脚本退出码非 `0` 时应记录错误并停止后续统计。
