# 评测结果数据库与写入协议

版本：1.0 · 核对日期：2026-10-02

本文依据当前运行服务、PostgreSQL 实际表结构和 `backend/app.py` 整理，供评测程序、脚本及其他客户端接入。标准写入入口为 **`POST /api/results`**；每条记录表示某个站点、某个 key、某个模型的一次评测汇总。

## 1. 服务与数据库

| 项目 | 当前配置 |
|---|---|
| 公网 API 基址 | `http://8.141.2.179:8080` |
| 同机 API 基址 | `http://127.0.0.1:8080` |
| 批量写入 | `POST /api/results` |
| 查询结果 | `GET /api/results` |
| 健康检查 | `GET /health` |
| 交互式接口文档 | `http://8.141.2.179:8080/docs` |
| OpenAPI JSON | `http://8.141.2.179:8080/openapi.json` |
| 数据库引擎 | PostgreSQL 18.4 |
| 数据库 / 用户 | `ai_gateway_eval_test` / `ai_gateway_eval_test` |
| 数据表 | `public.ai_gateway_eval_results` |
| 当前数据库地址 | `172.20.0.2:5432`，Docker 内网地址，容器重建后可能变化 |
| 数据库凭据 | 项目根目录 `.env`，使用 `DATABASE_URL` 或 `PG*` 变量 |

公网服务端口 `8080` 是 HTTP API，不是 PostgreSQL 端口。数据库 `5432` 当前未映射到公网。浏览器及外部客户端通过 HTTP API 写入，不需要数据库密码。本文和示例均不包含真实 key、数据库密码或服务口令。

## 2. 请求约定与认证

```http
POST /api/results HTTP/1.1
Host: 8.141.2.179:8080
Content-Type: application/json
Authorization: Bearer <RESULTS_API_TOKEN>
```

- 使用 UTF-8 JSON。
- `Authorization` 仅在后端配置非空 `RESULTS_API_TOKEN` 时必填；未配置时可以省略。
- 这里的 Bearer token 是**本项目的服务访问口令**，不是上游模型的 API key，也不是 PostgreSQL 密码。
- 写入请求顶层只能包含 `results`，长度为 **1～12**。单条记录也拒绝未定义字段。
- 不传 `id`、`api_key`、`key`、`endpoint`、`protocol` 或 `elapsed_ms`。如需保存协议、耗时等描述，可写入 `note`。
- 当前服务使用 HTTP。访问口令和请求数据通过 HTTP 传输；部署方式见 [DEPLOYMENT.md](../DEPLOYMENT.md)。

## 3. 字段协议

“API 默认值”指调用 `/api/results` 时的行为，与直接执行 SQL 的列默认值不完全相同。除 `domain`、`model` 外，输入字段均可省略；标为“可 null”的字段也可显式传 `null`。

| 字段 | JSON 类型 | PostgreSQL 类型 | 写入要求 / API 默认值 | 含义与约束 |
|---|---|---|---|---|
| `id` | 响应为整数 | `bigint`（bigserial） | **只读，禁止提交** | 数据库自增主键 |
| `result_uuid` | UUID 字符串 | `uuid NOT NULL` | 可省略，自动生成 UUID；不可 null | 全表唯一的去重标识，调用方应提前生成并保留 |
| `tested_at` | ISO 8601 字符串 | `timestamptz NOT NULL` | 可省略，默认服务当前 UTC 时间；不可 null | 必须有时区，如 `2026-10-02T16:30:00+08:00` 或 `2026-10-02T08:30:00Z` |
| `tested_date` | `YYYY-MM-DD` 字符串 | `date NOT NULL` | 可省略或 null，从 `tested_at` 换算上海日期 | 业务日期，允许手动指定，不强制与执行时间同日 |
| `time_precision` | `timestamp` / `date` | `text NOT NULL` | 默认 `timestamp`；只传日期、不传时间时自动为 `date` | `date` 必须提供 `tested_date`，时间字段存上海午夜作为排序锚点，不代表真实评测时间 |
| `domain` | 字符串 | `text NOT NULL` | **必填，不可 null** | 1～253 字符；填写主机名或 IP，不包含协议、路径、查询、凭据；服务转成小写 |
| `model` | 字符串 | `text NOT NULL` | **必填，不可 null** | 1～150 字符，保留被测模型原名，如 `gpt-6-astra`、`opus-5-5` |
| `key_hash` | 字符串 | `varchar(64)` | 默认 null，须与 `key_prefix` 成对提供 | 完整 key 的 SHA-256，恰好 64 位小写十六进制 |
| `key_prefix` | 字符串 | `varchar(6)` | 默认 null，须与 `key_hash` 成对提供 | key 的前 6 个字符；长度 1～6；存储值不带 `***` |
| `model_rate` | 数字或十进制字符串 | `numeric(6,3)` | 默认 null | 模型计费倍率，范围 `0 ≤ 值 < 1000`，最多 3 位有效小数 |
| `recharge_rate` | 数字或十进制字符串 | `numeric(6,3)` | 默认 null | 充值倍率，约束同上 |
| `request_ip` | 字符串 | `inet` | 默认 null，空字符串也转成 null | 评测请求的出口 IPv4 / IPv6，不是网页访问者 IP；不接受 CIDR 网段 |
| `fingerprint` | 字符串 | `text` | 默认 null | 最长 100 字符，建议保存逐探针 `✔️` / `❌` / `❓` |
| `top_model` | 字符串 | `text` | 默认 null | 最长 150 字符，指纹归因得到的实际模型名称 |
| `candy` | 字符串 | `text` | 默认 null | 最长 50 字符，建议格式 `正确数/可评分数`，例如 `2/3` |
| `candy_answers` | 字符串 | `text` | 默认 null | 最长 500 字符，按轮次逗号分隔，例如 `21,28,?` |
| `verdict` | 字符串 | `text` | 默认 `存疑`；不可 null | 仅允许 `真`、`换模`、`存疑`、`无法评测` |
| `note` | 字符串 | `text` | 默认 null | 最长 4000 字符，保存错误说明、耗时、协议等，不填写凭据 |

补充规则：

- 字符串字段会去除首尾空白；key 本身区分大小写。
- `domain` 写入时会拒绝 `/`、`?`、`#`、`@` 和空格。此处是结果元数据，不执行 DNS 存在性验证；客户端应提交纯主机名。
- 写入结果接口对 `model` 仅校验长度，不要求模型存在或符合任务接口的名称正则。
- `fingerprint`、`candy`、`candy_answers` 是文本字段，当前接口只校验长度，不校验数值关系或重新计算评分。
- 缺失数据用 null。倍率未知不能填 0，0 表示已知为零。
- API 返回的倍率通常为 JSON 数字，不保证保留输入的尾随零；例如输入 `"1.000"` 可返回 `1.0`。
- PostgreSQL 自增 `id` 可能大于 JavaScript 安全整数上限；外部系统应以 `result_uuid` 作为稳定标识。

## 4. Key 标识与展示

本协议不存储完整 key。写入前按以下顺序生成标识：

1. 去掉完整 key 的首尾空白。
2. 用 UTF-8 编码计算 SHA-256，输出小写十六进制，写入 `key_hash`。
3. 取前 6 个字符写入 `key_prefix`，不追加任何遮罩。
4. 前端展示 `key_prefix + "***"`，例如 `sk-abc***`。

```python
import hashlib

normalized_key = api_key.strip()
if not normalized_key or "\r" in normalized_key or "\n" in normalized_key:
    raise ValueError("API key 不能为空或包含换行")
key_hash = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()
key_prefix = normalized_key[:6]
display_key = key_prefix + "***"
```

完整 key 的哈希**不加盐、不拼接域名或模型**。同一个 key 在多个模型、多个站点上的哈希一致；按站点和模型分析时，应同时使用 `domain`、`model` 分组。前 6 位可能重复，不能把 `key_prefix` 当成唯一标识。

`key_hash` 与 `key_prefix` 必须同时非 null 或同时为 null。旧记录两者均为 null，前端显示“未记录”。结果导入接口只验证这两个字段的格式及是否成对，不接收完整 key，也无法重新核验哈希是否与原 key 匹配。

## 5. 完整写入示例

下面是可提交的示例请求。示例哈希来自虚构 key `sk-demo-example-not-real`，没有使用真实凭据。

```json
{
  "results": [
    {
      "result_uuid": "dbf53185-7d6e-4d8a-b1b9-8e44bcae8f38",
      "tested_at": "2026-10-02T16:30:00+08:00",
      "tested_date": "2026-10-02",
      "domain": "example.com",
      "model": "gpt-6-astra",
      "key_hash": "c04868dea238e56640b2cebbf926f2d39d4dfb4e70011789e16f6603c5fdebcb",
      "key_prefix": "sk-dem",
      "model_rate": "1.000",
      "recharge_rate": "0.800",
      "request_ip": "8.141.2.179",
      "fingerprint": "✔️✔️✔️",
      "top_model": "gpt-6-astra",
      "candy": "2/3",
      "candy_answers": "21,28,21",
      "verdict": "真",
      "note": "/responses；耗时 82.1s；Candy 可评分 3/3，失败或无法解析 0"
    }
  ]
}
```

将请求体保存为 `result.json` 后提交：

```bash
curl --fail-with-body -sS \
  http://8.141.2.179:8080/api/results \
  -H 'Content-Type: application/json' \
  --data-binary @result.json
```

配置服务口令时，追加 `Authorization: Bearer <RESULTS_API_TOKEN>` 请求头。不要把模型 key 填到该请求头中。

最小合法请求：

```json
{
  "results": [
    {
      "result_uuid": "50a9d991-039f-4783-bfc1-4a768ecee013",
      "domain": "example.com",
      "model": "gpt-6-astra"
    }
  ]
}
```

如果省略 UUID，服务也可以保存，但客户端无法使用原请求进行可靠去重重试，因此示例仍明确提供 UUID。

## 6. 成功响应、事务与去重

成功状态码：**`200 OK`**。

```json
{
  "items": [
    {
      "id": 123,
      "result_uuid": "dbf53185-7d6e-4d8a-b1b9-8e44bcae8f38",
      "tested_at": "2026-10-02T08:30:00+00:00",
      "tested_date": "2026-10-02",
      "domain": "example.com",
      "model": "gpt-6-astra",
      "key_hash": "c04868dea238e56640b2cebbf926f2d39d4dfb4e70011789e16f6603c5fdebcb",
      "key_prefix": "sk-dem",
      "model_rate": 1.0,
      "recharge_rate": 0.8,
      "request_ip": "8.141.2.179",
      "fingerprint": "✔️✔️✔️",
      "top_model": "gpt-6-astra",
      "candy": "2/3",
      "candy_answers": "21,28,21",
      "verdict": "真",
      "note": "/responses；耗时 82.1s；Candy 可评分 3/3，失败或无法解析 0"
    }
  ],
  "count": 1
}
```

示例 `id` 仅用于说明。时间响应可能使用不同偏移表示同一瞬间，不应依赖固定的时区字符串或 JSON 字段顺序。

- 同一 HTTP 批次在一个数据库事务中执行；数据库操作失败时回滚本批次新增记录。请求校验失败时不会进入写入逻辑。
- `result_uuid` 在全表唯一，数据库使用 `ON CONFLICT (result_uuid) DO NOTHING`。
- UUID 已存在时返回数据库中的原记录，**不更新原值**。即使重复请求改了 `note`、key 或模型，也不会覆盖。
- `items` 按请求记录顺序返回。`count` 是返回记录数，不是新增行数；同批提交重复 UUID 时也可能返回相同记录多次。
- 接口当前不提供更新、删除或纠错 API。不要通过更换 UUID 反复提交同一结果，以免影响统计。
- 网络超时或 503 后，如果无法判断是否成功，可以用**相同 `result_uuid`**重试保存；不要重新发起付费评测。
- 响应中的 `id` 只读；如将查询结果重新导入，需要去掉 `id`，保留原 `result_uuid` 和允许的字段。

## 7. 日期及评分口径

### 日期

`tested_at` 是实际评测时间；导入有精确时间的历史结果时应显式填写。日期和时间都省略时使用保存时的服务时间。

只有日期的历史结果，传 `tested_date` 并省略 `tested_at`，服务自动标记 `time_precision = date`；也可显式传 `time_precision: "date"`。此时数据库 `tested_at` 保存该日期的上海午夜作为技术锚点，页面只显示日期，不显示虚构的时分。同日的仅日期记录按 `id` 升序排列；与精确时间记录混合时，放在该日精确记录之后。既有数据默认保留 `timestamp`，不自动猜测午夜记录的精度。

若仅指定业务日期、仍需使用服务当前时间，请显式传 `time_precision: "timestamp"`。本机在线评测始终提供精确 `tested_at`，不受此推断影响。

`tested_date` 用于日期筛选。省略或 null 时，API 按 `tested_at` 转换成 `Asia/Shanghai` 日期。例如：

```text
2026-10-01T18:00:00Z → tested_date = 2026-10-02
```

允许手动指定 `tested_date`，用于按业务日期归档。不同 key、不同模型的汇总不应共用一条记录；每个组合使用独立 UUID。

### 评分

| 字段 / 值 | 建议含义 |
|---|---|
| 指纹 `✔️` | 返回数字数量精确满足该探针要求，不等同于身份已确认 |
| 指纹 `❌` | 有输出，但数字数量不符 |
| 指纹 `❓` | 请求失败、超时或被熔断跳过 |
| `candy = "2/3"` | 3 个可评分答案中有 2 个答案为 21 |
| `candy_answers = "21,?,28"` | 保持原轮次顺序；`?` 表示错误或无法可靠解析，正确率为 `1/2` |
| `candy = "0/0"` | 没有可评分答案，不应视为 0% 正确率 |
| `verdict = "真"` | 严格指纹归因与被测模型一致；Candy 可答错 |
| `verdict = "换模"` | 严格指纹归因与被测模型不同；前端显示“疑似换模” |
| `verdict = "存疑"` | 指纹不完整、只能容错归因、参考库未收录或未成功刷新等 |
| `verdict = "无法评测"` | 当前评测器在指纹与 Candy 请求全部失败时使用 |

写入接口是结果存储接口，不重新执行探针、不重新验证判定；第三方导入方应遵守相同口径。指纹归因是统计参考，不能作为身份认证。

## 8. 查询及写后核对

```http
GET /api/results?domain=example.com&model=gpt-6-astra&key_prefix=sk-dem&date_from=2026-10-01&date_to=2026-10-31&limit=20&offset=0
```

| 参数 | 规则 |
|---|---|
| `domain` | 可选，完整域名精确匹配，转小写；不是模糊搜索 |
| `model` | 可选，完整模型名精确匹配，区分大小写 |
| `key_hash` | 可选，64 位小写 SHA-256，精确匹配特定 key |
| `key_prefix` | 可选，1～6 字符，精确匹配保存的前缀；不是任意长度的前缀搜索 |
| `date_from` / `date_to` | 可选，`YYYY-MM-DD`，按 `tested_date` 筛选，两端均包含 |
| `limit` | 默认 50，范围 1～200 |
| `offset` | 默认 0，不小于 0 |

条件之间为 AND 关系。返回格式：

```json
{
  "items": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

记录按 `tested_at DESC, id DESC` 排序；`total` 为全部符合筛选条件的记录数。非空 `items` 的结构与写入响应相同。`date_from > date_to` 返回 422。

当前没有按 `result_uuid` 查询的专用接口。写后核对可以检查保存响应里的 UUID，或按站点、模型、key 和日期查询记录。

## 9. 按 Key 聚合

已提供 `GET /api/analytics` 聚合统计接口，支持滚动时间范围、分组汇总及时间序列；详见 [数据分析协议](ANALYTICS.md)。也可由数据库管理员执行只读 SQL。例如统计每日每站每模型、每个 key 的评测数：

```sql
SELECT tested_date, domain, model, key_hash, key_prefix,
       count(*) AS evaluations,
       count(*) FILTER (WHERE verdict = '真') AS identity_matches,
       count(*) FILTER (WHERE verdict = '换模') AS suspected_swaps,
       count(*) FILTER (WHERE verdict = '存疑') AS uncertain,
       count(*) FILTER (WHERE verdict = '无法评测') AS unavailable
FROM public.ai_gateway_eval_results
WHERE key_hash IS NOT NULL
GROUP BY tested_date, domain, model, key_hash, key_prefix
ORDER BY tested_date DESC, domain, model;
```

Candy 跨记录正确率应采用 **正确轮次总和 ÷ 可评分轮次总和**，不能直接平均每条记录的百分比。累计分母为 0 时返回未知。由于导入 API 不校验 `candy` 文本格式，解析前需检查格式及 `0 ≤ 正确数 ≤ 可评分数`；不合规记录单独记录为数据质量问题。

## 10. 错误响应

| HTTP 状态码 | 当前接口含义 | 调用方处理 |
|---|---|---|
| 200 | 保存成功，或 UUID 重复并返回已存在记录 | 核对 `items` 与 `result_uuid` |
| 401 | 配置了服务口令但认证失败 | 检查 `RESULTS_API_TOKEN` 对应的请求头 |
| 422 | 字段、类型、JSON 结构、枚举、长度等校验失败 | 修正请求，不盲目重试 |
| 503 | 数据库操作失败 | 保留原 UUID 和结果，稍后重试保存 |

示例：

```json
{
  "detail": [
    {
      "loc": ["body", "results", 0, "tested_at"],
      "msg": "Value error, tested_at 必须包含时区",
      "type": "value_error"
    }
  ]
}
```

字段校验错误不会回显原始输入。认证失败、数据库异常的 `detail` 是字符串，校验失败的 `detail` 是数组；客户端需要兼容两种类型。

## 11. 当前数据库约束及直接写库

数据库索引：

| 索引 | 字段 |
|---|---|
| `ai_gateway_eval_results_pkey` | 主键 `id` |
| `idx_eval_uuid` | 唯一索引 `result_uuid` |
| `idx_eval_dmt` | `(domain, model, tested_at DESC)` |
| `idx_eval_date` | `(tested_date DESC)` |
| `idx_eval_tested_at` | `(tested_at DESC)`，用于滚动时间范围查询 |
| `idx_eval_key_dmt` | `(key_hash, domain, model, tested_at DESC)` |

表结构及幂等迁移 SQL 见 [schema.sql](../schema.sql)。直接写库时有以下差异：

- `id`、`result_uuid`、`tested_at`、`tested_date`、`time_precision`、`domain`、`model` 是数据库 NOT NULL 列；其他列允许 NULL，包括 `verdict`。
- 数据库 `verdict` 列本身没有默认值或枚举约束，`存疑` 默认值和四种判定的限制来自 API。
- 数据库 `tested_date` 默认使用**数据库当前时间的上海日期**，不是所传 `tested_at` 的日期；写历史记录时必须自己设置。
- 数据库没有成对 key 字段、哈希格式、文本业务长度或倍率非负的 CHECK 约束；这些校验在 API 层。绕过 API 的写入程序必须自行遵守协议。
- 数据库密码从 `.env` 读取。SQL 使用参数绑定，不拼接用户输入。

同机 Python 直接写库示例，复用 API 的字段模型做校验，并使用同一事务与去重逻辑：

```python
# 在项目根目录使用 .venv/bin/python 执行。
import json
from pathlib import Path
from backend.app import ResultBatch, save_results

batch = ResultBatch.model_validate_json(Path("result.json").read_text(encoding="utf-8"))
result = save_results(batch)
print(json.dumps({"count": result["count"],
                  "result_uuids": [str(row["result_uuid"]) for row in result["items"]]}))
```

上述调用在同机进程中直接连接数据库，不经过 HTTP 认证；仅供有权读取 `.env` 的维护脚本使用。外部应用应使用第 2～6 节的 HTTP 协议。

## 12. 自动评测与直接保存的区别

| 流程 | 入参中的 key | 是否调用模型 | 结果写入 |
|---|---|---|---|
| `POST /api/evaluations` | 完整 `api_key`，仅用于当前任务 | 是，会消耗上游额度 | 服务计算哈希与前缀，评测完成后自动保存 |
| `POST /api/results` | `key_hash` + `key_prefix`，不接受完整 key | 否 | 直接保存调用方提供的结果 |
| `POST /api/evaluations/{id}/save` | 不重复提交 key 或结果 | 否 | 重试保存该任务已有结果，沿用原 UUID |

使用 `/api/evaluations` 的客户端先获得 `202` 与任务 ID，再轮询 `/api/evaluations/{id}`。只有 `saved=true` 才表示结果已保存；评测完成但 `status=save_failed` 时可调用该任务的 `/save`，不要重新运行评测。详细任务参数见 [部署与接口说明](../DEPLOYMENT.md) 和服务 `/docs`。
