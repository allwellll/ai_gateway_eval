# 数据分析与时间序列协议

首页 `http://8.141.2.179:8080/` 上方展示各模型的 Top 3 渠道，下方直接展示历史评测记录。默认查询过去 12 小时的全部模型，可切换 1 天、7 天或自定义上海日期范围。每个模型按域名 + Key 独立排名；常用预设模型优先展示。「新建评测」进入 `/#evaluate`，切换页面不取消后台任务。

## 首页联合查询接口

```http
GET /api/analytics/dashboard?window=12h&history_limit=30&history_offset=0
```

认证与 `/api/results` 一致。榜单和历史记录共享时间边界及全部筛选条件；历史分页不改变榜单。

| 参数 | 默认 | 含义 |
|---|---|---|
| `window` | `12h` | `12h`、`1d`、`7d`、`custom` |
| `date_from` / `date_to` | 无 | 仅 `custom` 使用，至少提供一个，格式 `YYYY-MM-DD`；起始日不得晚于结束日 |
| `domain` / `model` | 无 | 域名 / 模型精确匹配，域名转小写 |
| `key_hash` | 无 | 64 位小写十六进制完整哈希，精确匹配 |
| `key_recorded` | 无 | `false` 只查询未记录 Key 的旧数据；`true` 只查询已记录 Key |
| `fingerprint` / `top_model` / `candy` / `verdict` | 无 | 指纹、实际模型、Candy 原始值、判定精确匹配 |
| `history_limit` | 30 | 每页记录数，1～100 |
| `history_offset` | 0 | 记录偏移，不小于 0 |

筛选条件以 AND 组合。自定义日期包含起止两天：起始上海时间 00:00:00 至结束日下一天 00:00:00（不含）；只有结束日则查询当天，只有起始日则查询至今天。未来精确时间与未来日期均排除。精确时间使用 `tested_at` 的上海日期，不使用可手填业务日期。

返回 `window`、`from`、`to`、`timezone`、`summary`、`total_groups`，以及：

- `leaderboards[]`：每项包含 `model`、`total_channels`、`evaluations`、`channels`。
- `channels[]`：该模型最多 3 个渠道；含后文汇总指标、`model_rank`（模型内排名）、`is_reference`（是否满足优先参考条件）、`sequence`。
- `history`：`items`、`total`、`limit`、`offset`。每条包含数据库记录字段及计算得到的 `status`、`candy_accuracy`，不含完整 API Key。

历史记录按上海日期倒序，精确时间到秒显示。同一天中，仅日期记录排在精确时间记录前面，仅日期记录按 id 倒序；序列图按相反顺序绘制。只有日期的记录标记「仅日期」，不编造小时。

点击模型、域名、Key、指纹、实际模型、Candy、判定会在现有条件上增加该字段的精确筛选；同字段的新选择替换原值。筛选标签可单独移除或清除全部维度。点击时间栏直接打开日期选择器，选定后切换到该天；顶部日期框支持范围选择。日期选择保留其他维度，切换滚动时间窗口会清空自定义日期。

Key 文本只显示前 6 位加 `***`，悬停可查看短哈希以区分同前缀渠道；实际筛选始终使用完整哈希。缺失 Key 使用 `key_recorded=false`。

## 兼容聚合接口

```http
GET /api/analytics?window=12h&group_by=domain_key_model&limit=20&offset=0
```

认证与 `/api/results` 一致：若配置 `RESULTS_API_TOKEN`，需提供对应 Bearer 请求头。只读接口，不发起模型调用。

| 参数 | 默认 | 可选值 / 限制 |
|---|---|---|
| `window` | `12h` | `12h`：过去 12 小时，30 分钟一格；`1d`：过去 24 小时，1 小时一格；`7d`：过去 168 小时，6 小时一格 |
| `group_by` | `domain` | `domain`、`domain_key`、`domain_key_model` |
| `domain` | 无 | 域名精确匹配，转换小写，1～253 字符 |
| `model` | 无 | 模型名称精确匹配，1～150 字符 |
| `limit` | 20 | 每页分组数，1～50 |
| `offset` | 0 | 分组偏移，不小于 0 |

精确时间记录以服务当前时间为窗口结束，按 `tested_at` 取 `[from, to)`，不使用可手填的业务日期 `tested_date`。未来时间不计入，起始时刻包含，结束时刻不包含。`time_precision = date` 的记录没有具体小时，按与窗口重叠的上海日期纳入；未来日期不纳入。仅日期记录落在窗口起点之前时，桶统计将其锚定到首桶，桶不代表其真实小时。前端以 `Asia/Shanghai` 展示，API 时间带 UTC 偏移。

`GET /api/analytics/models?domain=example.com` 返回 `{ "models": [...] }`，供前端选择模型。域名筛选可省略；模型清单包含历史数据，不受短窗口限制，避免因暂时缺测隐藏模型。

## 返回结构

- `window`、`group_by`、`from`、`to`、`bucket_minutes`、`timezone`：本次查询参数及范围。
- `summary`：所有满足时间范围和筛选条件的记录统计，不受分组分页影响。
- `timeline`：全体记录的时间桶，12h/1d 各 24 个，7d 为 28 个。
- `total_groups`、`limit`、`offset`：分页信息。
- `groups`：当前页各组的身份字段、汇总指标、`rank`、`latest_status`、`last_tested_at`、兼容旧接口的 `timeline`，以及单条评测 `sequence`。
- `sequence_total`：组内全部评测记录数；`sequence` 每组最多返回最近 300 条，不影响汇总或排名。序列包含 `id`、域名 / Key 标识 / 模型、时间 / 日期 / 精度、原始指纹 / 判定 / Candy / 答案、综合 `status` 和单次 `candy_accuracy`。
- `recommendation`：全部匹配分组中满足证据条件的最高分渠道，包括 `rank`、渠道身份、评分、样本数；没有合格渠道时为 null。该字段不受当前分页限制。

分组字段为 `domain`、`key_hash`、`key_prefix`、`model`。不参与分组的 key / model 字段返回 null。缺少 key 的旧记录按 null key 单独归组；不同 key 即使前 6 位相同仍按完整哈希分开。页面显示前 6 位加 `***`，并附短哈希辅助区分；排名号不是稳定业务 ID，完整哈希才是统计标识。

分组按稳定性分降序，无评分排在最后；同分按样本数降序，再按域名、完整 key 哈希、模型排序。筛选或时间范围改变后，页码归零。

## 汇总字段

| 字段 | 含义 |
|---|---|
| `evaluations` | 评测记录数（一条记录包含多轮探针，不等于上游请求数） |
| `identity_matches` | `verdict = 真` 的记录数 |
| `suspected_swaps` | `verdict = 换模` 的记录数 |
| `uncertain` | 存疑、缺失或其他未知判定的记录数 |
| `unavailable` | `verdict = 无法评测` 的记录数 |
| `identity_match_rate` | 身份匹配记录数 / 全部评测记录数；无记录时 null |
| `good` / `warning` / `bad` / `unknown` | 按下表颜色规则分类的记录数，四项合计等于评测记录数 |
| `candy_correct` / `candy_scored` | 合法且非「无法评测」记录中，正确轮次 / 可评分轮次的总和 |
| `candy_accuracy` | 正确轮次总和 / 可评分轮次总和；分母为 0 返回 null，不返回 0% |
| `candy_invalid` | Candy 格式不合法或正确数大于分母的记录数，包括 null |
| `candy_unscored` | 合法但为 0/0 的记录数 |
| `candy_unknown_rounds` | `candy_answers` 中 `?` 的总数，独立于可评分分母统计 |
| `status` | 组或时段的汇总颜色状态 |
| `evidence_coverage` | 合法 Candy、有可评分轮次、非「无法评测」记录数 / 全部记录数；空集合为 0 |
| `stability_score` | 下述综合评分，0～1；页面乘以 100 显示；缺少可评分 Candy 或没有样本时为 null |

稳定性评分是浏览和比较用的启发式指标，不是未来可靠性的概率：

```text
base = 0.55 × identity_match_rate + 0.45 × candy_accuracy
score = clamp(base × evidence_coverage
              - 0.20 × bad / evaluations
              - 0.10 × (warning + unknown) / evaluations, 0, 1)
```

两种信号都必须有数据才评分。可评分覆盖率会降低部分缺失数据的分数，未知答案和其他黄色记录也有扣分。颜色仍报告每条记录的原始表现，综合分不会覆盖异常。少于 3 次评测在页面标记「样本少」。优先参考渠道必须有已记录 Key、至少 3 次评测、分数 ≥ 80、无红色评测、最近一条为绿色；不满足时不加优先参考标记，仍保留评分排名供比较。

Candy 的合法格式为 `^[0-9]{1,9}/[0-9]{1,9}$`，并满足正确数 ≤ 可评分数。不合法值不参与轮次加总；0/0 合法但不贡献轮次。聚合不会用 `candy_answers` 重新推算分数，导入方需保证两者一致。逐轮答案缺失时无法推算遗漏的失败轮次，页面仅显示明确记录的 `?` 数。

每个时间桶包含 `start`、`end` 和全部汇总字段。空桶保留，计数为 0、比例为 null、状态为 `empty`。

## 颜色规则

按顺序判定每条记录：

| 状态 | 颜色 | 规则 |
|---|---|---|
| `unknown` | 灰蓝 | 判定为「无法评测」，不把传输故障混入推理正确率 |
| `bad` | 红 | 判定为「换模」，或合法且有可评分轮次的 Candy 正确率 < 60% |
| `good` | 绿 | 判定为「真」，Candy 合法且有可评分轮次、正确率 ≥ 80%，逐轮答案没有 `?` |
| `warning` | 黄 | 其他情况，如存疑、Candy 60%～80%、未知答案或无可评分数据 |

质量状态是用于浏览的综合标记，不覆盖原始 `verdict`。例如指纹为真但 Candy 得分低，可以显示红色，同时身份匹配统计仍正常计数。

组或时段中出现红色记录则汇总为红；否则有黄色则黄；仅有绿色则绿；仅有无法评测则灰；绿色与无法评测混合为黄；没有记录为 `empty`。这样不会因平均分较高掩盖少量异常。

## 可视化

- 各模型独立展示 Top 3 渠道，比较稳定性、Candy、指纹、样本数和最近 18 次评测的紧凑序列。序列上排为指纹、下排为 Candy，分开显示颜色。榜单区域限制高度，模型较多时可在区域内滚动，历史记录紧接其后。
- 每次评测占一个位置，空档压缩；日期分隔显示月日，没有 Candy 数据时标记灰色。
- 日期来自精确时间的上海日期；仅日期记录使用 `tested_date`。精确记录按时间、id 排列；同日仅日期记录放在精确记录之后，按 id 升序排列。不会给仅日期记录编造小时。
- 点击渠道、模型或 Key 追加筛选；点击序列点或历史记录详情按钮打开单条评测详情。紧凑序列可横向滚动，默认显示最近部分。
- 历史记录分页不改变榜单排名，榜单始终基于全部筛选结果。
- 数据来自实际数据库，不补造样本。查询失败时隐藏结果并显示错误，避免新筛选条件下展示旧结果。
