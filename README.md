# AI Gateway 模型评估方法手册

> **本机评测网页已实现**：运行 `./scripts/start.sh`，访问 `http://8.141.2.179:8080/`。
> 数据库、启动配置、接口说明和验证方法见 [DEPLOYMENT.md](DEPLOYMENT.md)。
> 网页由同一 FastAPI 服务提供，不使用 GitHub Pages；API key 仅供后端当前任务使用，不持久保存。

> 整理日期：2026-10-02
> 适用范围：sub2api 中转网关（127.0.0.1:23000）及其上游账号的模型真实性 / 质量评估
> 本手册对应的 Hermes skill：`sub2api-model-eval`（更细的 pitfalls 与历史案例见该 skill）

---

## 1. 评估体系概览

评估一个中转账号的模型，分两个独立维度，**不能互相替代**：

| 维度 | 工具 | 回答的问题 |
|---|---|---|
| **指纹归因（ModelTrace）** | 3 个固定探针，对比指纹 bank | 这个账号背后跑的**到底是哪个模型**？（标签 ≠ 实际） |
| **糖果题（candy eval）** | 固定推理题，答案 21，跑 N 次 | 这个上游的**推理质量**如何？（真模型也可能答错） |

⚠️ 指纹 p=1.0 不代表质量好；candy 0/5 也不代表是假模型。两层结论分开报告。

**核心纪律：永远不信 `model_mapping` / `/v1/models` 目录**，它们经常性过时（映射写 20+ 个模型，上游实际只开几个）。一切结论以**真实 key 实测**为准。

---

## 2. 环境与路径

| 项目 | 路径 / 地址 |
|---|---|
| sub2api 服务 | `http://127.0.0.1:23000` |
| 凭证 / 运行时 env | `/home/code_room/sub2api/runtime/.env` |
| **评估工具主目录** | `/tmp/codex-candy-eval/` |
| 指纹 bank | `/tmp/codex-candy-eval/modeltrace_data/unified_bank.json` |
| 报告输出 | `/tmp/codex-candy-eval/reports/candy-*.json`、`sub2api-modeltrace-*.json` |
| 每日巡检 cron 脚本 | `~/.hermes/scripts/fleet_fingerprint_watch.sh`（状态 `fleet_fingerprint_state.json`） |
| 网页报告发布 | nginx：`/home/code_room/nginx_room/nginx/public/` → `http://8.141.2.179/browse/...` |

注意：`/tmp` 下的工具视为可重建（本质就是 HTTP 客户端），丢了重建即可，不要因此宣布流程不可用。

---

## 3. ModelTrace 指纹归因

### 3.1 原理

- 向上游发 **3 个固定探针 prompt**（数字序列类），收集回答中的数字序列作为"指纹"。
- 与 `unified_bank.json` 中各模型的参考指纹比对，输出 top5 候选 + 概率。
- Bank 来自上游项目 `github.com/xqy2006/ModelTrace` → `data/unified_bank.json`；**下结论前先刷新 bank**（旧 bank 会把新模型自信地归到错误的旧质心）。

### 3.2 判定口径（硬性，用户定的）

| 情况 | 输出 |
|---|---|
| `top_model == 被测模型` 且计数精确 | `✅ 真 <model> <p>` —— **只有这个算真通过** |
| 计数 ✔️ 但 top ≠ 被测模型 | `❗疑似换模 → 实际 <top>`（禁止按对） |
| 无 ✔️ 但容错层给出归因 | `🟡 归因 <model>`（不算真通过） |
| 数字个数不符 | `❌` |
| 超时 / 5xx / 池空 | `❓` —— 传输层失败，**不算质量结论** |

报告 JSON 里的 `passed` 字段历史口径不准，出表时按 `top_model == tested_model` 重新判定。

### 3.3 ModelTrace 脚本清单

| 脚本 | 用途 |
|---|---|
| `modeltrace_core.py` | 严格模式核心（计数闸门） |
| `modeltrace_tolerant.py` | **容错归因器**：计数闸门关闭，非流式→限时 SSE 回退；弱计数上游（foxcode 类）必须用它 |
| `modeltrace_eval.py` | fleet 级 modeltrace 评测入口 |
| `sub2api_modeltrace_eval.py` | sub2api 账号批量 modeltrace |
| **`fleet_modeltrace_json.py`** | **出表首选**：单次调用、每账号每探针只发一次（无重试），覆盖 openai+anthropic 双平台，输出逐探针 `✔️/❌/❓` 序列 JSONL |
| `fleet_modeltrace_run.sh` | 严格→容错两阶段 pipeline（phase 2 会重探失败账号，故障多时慢，仅需要重采样行为时用） |
| `modeltrace_report.py` | 从 JSONL 渲染巡检表格（provider / 测试模型 / 倍率 / 准确率 / 耗时） |
| `modeltrace_single_direct.py` | 单个 anthropic 平台账号归因（`-a 账号 -m 模型 --platform anthropic`），走 `/v1/responses`（sub2api 翻译层） |
| `modeltrace_anthropic_native.py` | anthropic 账号的**原生** `/v1/messages` 交叉验证（翻译层归因可疑时用；优先顺序：native messages > translated responses） |
| `modeltrace_direct_fp.py` | **裸第三方站点**指纹（base_url + key，不经 sub2api），支持 `-m a,b,c` 多模型、`-p responses\|anthropic`，探针全并行 |
| `modeltrace_fleet_tolerant.py` / `modeltrace_cp_calib.py` | fleet 容错批量 / 冻结校准工具（后者为历史样本复现专用，勿改勿扩展） |
| `mt_anthropic_compat.py` | anthropic 兼容层 |

### 3.4 常用命令

```sh
cd /tmp/codex-candy-eval
export SUB2API_ENV_FILE=/home/code_room/sub2api/runtime/.env
export SUB2API_URL=http://127.0.0.1:23000

# ① fleet 指纹巡检出表（首选）
python3 -u fleet_modeltrace_json.py -n 3 --timeout 300 --output /tmp/fleet_rows.json

# ② 裸第三方站点指纹（多模型并行）
python3 -u modeltrace_direct_fp.py -u https://<host>/v1 -k sk-XXX \
  -m gpt-6-astra,gpt-5.6-sol -n 3 -r high --timeout 300 --output /tmp/fp.json

# ③ 单个 anthropic 账号归因
python3 modeltrace_single_direct.py -a cfm-kiro-014 -m <model> -n 9 -r high --platform anthropic
```

---

## 4. Candy Eval 糖果题

### 4.1 题目与判分

- 固定推理题（`candy_eval.py` 的默认 `CANDY_PROMPT`），**正确答案 = 21**。
- 干扰性：题干"可主动选形状"，多数模型误读为"盲抽"而答 **28/29** —— 这是最主要失败模式（前提误读，比算术错更严重）。
- 每 run 独立提问，`reasoning=high`；默认 **5 次**，临界/不稳时加到 10 次。
- 判分表：

| 结果 | 判定 |
|---|---|
| 21 | ✔️ 正确 |
| 29（或 28） | ✗ 错——盲抽误读 |
| 其他数字 | ✗ 错——算术/其他 |
| HTTP 错误 / 无可解析答案 | ❓ 硬失败——与可评分 run **分开记账** |

⚠️ 同一 repo 里还有 `candy_cases.py` 的 PROMPT_A/B 变体（PROMPT_B 明说盲抽，答 29 为对）。**对比报告前先确认用的是哪个 prompt 家族**；质量排名一律用默认 `CANDY_PROMPT`。

### 4.2 延迟是（per-upstream 的）正确性信号

默认糖果题上，正确的 run 往往慢（完整构造最坏情况），错的 run 快（盲抽捷径）。**但这是逐站信号，不可跨站搬用**：有的站慢 run 照样错。报告时按站说明，别带"慢=对"的先入假设。

### 4.3 Candy 脚本清单

| 脚本 | 用途 |
|---|---|
| `candy_eval.py` | 糖果题核心（`DEFAULT_MODEL = "gpt-6-astra"`，双协议，`test_response()`）；**改 fleet 默认模型的两个源头文件之一** |
| `sub2api_info_extract.py` | 只从 sub2api 提取 provider 信息（另一个 DEFAULT_MODEL 源头） |
| `sub2api_eval.py` | sub2api 账号批量糖果题；支持 `--include-unmapped`（mapping 不含该模型也直发上游测） |
| **`run_all_sub2api.sh`** | **fleet 糖果题入口**：自动注入 `--workers 20 --include-unmapped` |
| `run_one_sub2api.sh` | 单账号非默认模型：`./run_one_sub2api.sh ACCOUNT -m MODEL -n 5 -r high` |
| `candy_direct.py` | 绕过 sub2api，直打 provider URL/key |
| `candy_direct_fp.py` | 裸站点 candy，支持 `-p responses\|anthropic`；注意 `-n` 同时是样本数和线程数 |
| **`candy_direct_nc.py`** | `candy_direct_fp.py` 的继任：**`-n`（次数）与 `-c`（并发）分离**，`-c 0` = 旧行为 |
| `candy_direct_nc8k.py` / `candy_direct_lean.py` | 变体（8k / 精简） |
| `candy_iq_run.sh` / `candy_cases.py` / `cup_eval.py` | IQ 批量 / 显式 prompt 家族 / 水杯配对题（答案 8） |

### 4.4 常用命令

```sh
cd /tmp/codex-candy-eval
export SUB2API_ENV_FILE=/home/code_room/sub2api/runtime/.env
export SUB2API_URL=http://127.0.0.1:23000

# ① 全 fleet × gpt-6-astra × 5 次
./run_all_sub2api.sh -n 5 -r high
./run_all_sub2api.sh --list-only     # 空跑：会测哪些账号

# ② 单账号其他模型
./run_one_sub2api.sh auv -m gpt-5.6-sol -n 5 -r high

# ③ 裸站点 candy（gpt 系，responses 协议）
python3 candy_direct_fp.py -u https://<host>/v1 -k sk-XXX -m gpt-6-astra -n 5 -r high --timeout 240

# ④ 裸站点 candy（opus/claude 系，必须 -p anthropic；10 次并发 5）
python3 candy_direct_nc.py -u https://<host>/v1 -k sk-XXX -m <opus模型> \
  -p anthropic -n 10 -c 5 -r high --timeout 300 --output /tmp/candy.json
```

### 4.5 组合探针（指纹 + candy 一把梭）

| 脚本 | 用途 |
|---|---|
| **`provider_quality_probe.py`** | **裸站点首选入口**：`-u/-k/-m` + `-n` 探针 + `--candy-runs` + `--protocol` + `--only fingerprint\|candy\|both`，一次并行跑完两层 |
| `opus_quality_probe.py` | 单账号多模型 × 指纹+candy（经 sub2api admin API 枚举） |
| `fleet_model_eval.py` | fleet 通用版：`-a all-cfm` 或账号列表 × `-m` 模型列表 × `--only`，自动按平台推导 candy 协议，输出 JSONL |
| `direct_provider_probe.py` | 早期单发版本（被 provider_quality_probe.py 取代） |

```sh
cd /tmp/codex-candy-eval
python3 -u provider_quality_probe.py -u https://<host>/v1 -k sk-XXX \
  -m <model1>,<model2> -n 3 --candy-runs 3 --protocol anthropic --only both
```

---

## 5. 硬性纪律（用户逐条纠正过，违反 = 结论无效）

### 5.1 协议路由（不可交叉）

- **claude / opus / sonnet / haiku 命名的模型 → 只走 `/v1/messages`（anthropic 协议）**
- **gpt-* 模型 → 只走 `/v1/responses`（[OI] 协议）**
- 跨协议测出的 404 是**无效结论**（只证明该端点不服务这个命名），发现后必须撤回重测。
- key 是分接口的：`403 This group does not allow /v1/messages` 是**权限结论**，不是"账号没这个模型"。
- sub2api 账号的 platform 优先级高于模型名：anthropic 平台账号一律走 `/v1/messages`。
- 实现：路由逻辑单一源头在 `response_client.py`（`protocol_for_model` / `protocol_for_platform`），直连脚本 `-p` 仅作逃生口；`-p` 与模型名矛盾即为 bug。

### 5.2 gpt 系固定姿势（2026-10-02 用户定）

直打站点 `/v1/responses`，真实指纹探针 + 默认 candy（答案 21）：

1. **每个请求必须带 `User-Agent: curl/8.5.0`** —— 站点 WAF 秒拒 `Python-urllib/*`（0.3s 内 502，body 相同也拒）；`codex_cli_rs/*` 也被放行。历史上大量"502 故障"其实是 UA 假象。
2. **永不传 `max_output_tokens` / `max_tokens` / `max_completion_tokens` / `max_new_tokens`** —— 该字段本身就是某些分组的 502 触发器（去掉即 200）。截断一律用**本地墙钟超时**，不用上游 cap。政策模块：`no_cap_util.py`（`sanitize_body` / `assert_no_cap`）。
3. 审计命令（改完任何脚本跑一次，只应输出注释/docstring）：

```sh
cd /tmp/codex-candy-eval
grep -rn "max_output_tokens\|max_tokens" *.py | grep -v budget_tokens | grep -v no_cap_util
grep -c "User-Agent" *.py    # 新文件没有 UA = 未来的假 502 发生器
```

### 5.3 并发与故障

- **三层并发**：账号间并行 + 账号内探针并行 + 首个网关错误（5xx/403/401）熔断跳过后续探针。fleet 耗时 = 最慢的单次请求，不是总和。
- **故障账号不重试**：5xx/403/401 是该账号的**耐久结论**，带 `❓❓❓` 序列和原始错误串记录，move on。
- **429 exhausted → 503 No available accounts** 是池子从打满到抽空的过程，报"上游掉线/池空"，绝不是"模型不支持"。
- 并发是配额受限分组的放大器：整组并发 502 时，先串行小探针复测再下结论。

### 5.4 失败分类（读 error code，别一句"打不通"）

| 错误 | 含义 | 报告口径 |
|---|---|---|
| `model_price_error` (400) | 站点有该模型但未配价/未启用 | 未启用（站点配置问题） |
| `model_not_found` / "No available channel … under group" (404/503) | 该 key 的分组没绑这个模型的渠道 | 无可用渠道（换 key 可能行） |
| 带 max_tokens 的任何失败 | 请求形状问题 | **不是站点/模型结论**，去掉字段重测 |
| `502 Upstream access forbidden` | 账号/分组级上游封禁（可瞬时波动） | 与 CDN 挂（nginx HTML）、池空区分；UA A/B/C 对照先行 |
| 401/403 | key 无效 **或** 请求形状风控 | 先用轻探针分辨再定性 |
| `No available accounts` | 该模型族的站点账号池为空 | 未接入/无渠道 |
| 429 | 账号级/上游限流 | 记限流，几分钟后可重试一次 |

### 5.5 同站多 key = 不同分组

同一站点不同 key 的 `/v1/models` 列表互不重叠（如某站 claude key 22 个 claude 系标签，gpt key 8 个 gpt 系标签）。**可用性是 (站点 × key/分组) 的属性**，任何"模型 X 在站点 Y 不可用"的结论都不能跨 key 搬用；拿到新 key 先重拉 `/v1/models`。
反例也存在：全站混排目录站（一个 key 列几百个异构模型）`/v1/models` 零证据价值，直接发真实请求。

---

## 6. 倍率（成本）读取

- 唯一来源：sub2api `GET /admin/accounts/data` → 每账号 `extra.upstream_billing_probe.data.effective_rate_multiplier`（**不是**顶层 `rate_multiplier`，那只是本地覆盖值常为 1）。
- 工具：`fleet_multipliers.py`，输出 `multiplier_raw / multiplier / multiplier_known / cfm_discount` 分列。
- **cfm 折扣 = sub2api 倍率 × 0.15**，"cfm" 指 **blackaicoding.com 这个 provider**：判定 = 名字以 `cfm` 开头 **或** base_url 在 `blackaicoding.com`（两个信号取 OR；曾单看域名把 cfm02 的 raw 2.0 误判成 0.15，差 13 倍且方向反了）。
- 无 probe 数据的账号 → 报「倍率未知」，**禁止按名字/域名猜**。

---

## 7. 报告格式（用户多次重申）

### 7.1 巡检表格

- markdown 表格，**每账号一行**，探针结果**直接放逐探针 glyph 序列**（`✔️✔️❓`），不要散文、不要 `2/3` 计数、不要子表。
- 多模型巡检列：`provider | 测试模型 | 倍率 | 准确率 | 耗时`；准确率带 glyph + 原始 `ok/total`，耗时给总计 + 均次（如 `245s (均82s)`）。
- 跨协议时加 **接口** 列（`/v1/messages` vs `/v1/responses`）。
- 渲染工具：`modeltrace_report.py`，别手搓。

### 7.2 汇总网页

- 跨站/跨天汇总 → **一个亮色 HTML 大表**，走 `static-artifact-publishing` 流程发布（随机路径段、root 404、复用同一 URL），模板：`templates/eval-report-table.html`。
- 只放**新测**数据；工具修复（如 UA 修复）前的 ❓ 行是 artifact，标"待复测"，不进结论。
- 页面分「可评测」「无法评测」两表；传输/协议失败带原始错误串进后者。

---

## 8. 每日巡检 cron（fleet-fingerprint-watch）

- 每日 09:00，`no_agent` 纯脚本：`~/.hermes/scripts/fleet_fingerprint_watch.sh`（零 LLM token）。
- 口径：strict ModelTrace × 全 fleet × fleet 默认模型（`gpt-6-astra`），容错归因器自动兜底。
- **无变化不输出**；只在 真→假 / 假→真 / 掉线→恢复 / 归因变化 时推送告警（硬性偏好，禁心跳）。
- 双层超时：`cron.script_timeout_seconds: 900`（config.yaml）+ 脚本内 `timeout 840`；被杀的 tick 也能从 runlog + 部分 JSON 出 diff。
- **改了 fleet 默认模型或账号增删后，同步改巡检脚本并手动跑一次刷基线**（`rm fleet_fingerprint_state.json` 后跑），否则下个 tick 报假警。

---

## 9. 脚本速查总表（/tmp/codex-candy-eval/）

| 类别 | 文件 |
|---|---|
| 核心库 | `candy_eval.py`、`response_client.py`（协议路由源头）、`no_cap_util.py`（禁 cap 政策）、`modeltrace_core.py`、`modeltrace_tolerant.py` |
| sub2api 接入 | `sub2api_info_extract.py`、`sub2api_eval.py`、`sub2api_modeltrace_eval.py`、`fleet_accounts.py`（双平台枚举）、`fleet_multipliers.py`（倍率） |
| fleet 入口 | `run_all_sub2api.sh`、`run_one_sub2api.sh`、`run_all_sub2api_modeltrace.sh`、`run_one_sub2api_modeltrace.sh`、`fleet_modeltrace_json.py`（出表首选）、`fleet_modeltrace_run.sh`、`fleet_model_eval.py` |
| 裸站点直连 | `provider_quality_probe.py`（首选）、`modeltrace_direct_fp.py`、`candy_direct_fp.py`、`candy_direct_nc.py`、`candy_direct.py`、`direct_provider_probe.py` |
| 单账号/质量 | `opus_quality_probe.py`、`modeltrace_single_direct.py`、`modeltrace_anthropic_native.py` |
| 诊断 | `cfm_opus_diag.py`、`stream_diag.py`、`responses_stream.py`、`anthropic_stream.py` |
| 报告 | `modeltrace_report.py` |
| 数据 | `modeltrace_data/unified_bank.json`（指纹 bank） |
| 文档 | `README.md`、`MODELTRACE.md` |
| 测试 | `tests/test_*.py` |

> fleet 默认模型 = `gpt-6-astra`（用户 2026-09-27 定）。改默认模型 = 改 `candy_eval.py` + `sub2api_info_extract.py` 两处 `DEFAULT_MODEL`，并 `sed` 同步 `README.md` / `MODELTRACE.md` / `candy_direct.py`，改完 `./run_all_sub2api.sh --list-only` 验证。

---

## 10. 一分钟决策树

```
用户给了什么？
├─ sub2api 账号名          → fleet_accounts 枚举 → fleet_modeltrace_json.py / fleet_model_eval.py
├─ 裸 base_url + key       → 先 GET /v1/models（1 秒看分组）→ provider_quality_probe.py（指纹+candy 一把）
├─ "测 XXX 的质量"          → 执行！不要给方法论文档；先跑数再说话
└─ "模型 Y 在 Z 站上有没有"  → 对正确协议发真实请求；读 error code 分类；别跨 key 搬结论

测之前自检：
1. 协议对了吗？（claude/opus→/v1/messages；gpt→/v1/responses）
2. UA = curl/8.5.0 了吗？
3. body 里没有任何 max_*tokens 字段了吗？
4. bank 是最新的吗？（归因前刷一次上游 ModelTrace repo）
```
