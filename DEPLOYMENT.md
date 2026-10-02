# Gateway Lab 部署与接口

本项目包含同源网页、FastAPI 评测服务及 PostgreSQL 结果存储。评测由服务器发起，前端只负责提交任务、轮询进度和展示记录。

## 本机启动

需要 Python 3.10+、可用的 PostgreSQL 和 Python venv。当前机器已经创建 `ai_gateway_eval_test` 数据库、同名用户与结果表，凭据在本地 `.env` 中。

```bash
cd /home/code_room/ai_gateway_eval
./scripts/start.sh
```

当前机器已安装并启动 `ai-gateway-eval.service`，开机自动启动。管理命令：

```bash
systemctl status ai-gateway-eval
systemctl restart ai-gateway-eval
journalctl -u ai-gateway-eval -n 50 --no-pager
```

服务运行时无需再次执行 `start.sh`；手动启动前先 `systemctl stop ai-gateway-eval`，避免端口冲突。

默认监听 `0.0.0.0:8080`，浏览器打开 `http://8.141.2.179:8080/`。阿里云安全组及主机防火墙需要允许相应端口。端口可通过 `PORT=8081 ./scripts/start.sh` 修改。启动会幂等检查数据库 schema。

公网 HTTP 会明文传输用户输入的 API key，实际使用应配置下面的 HTTPS。完整 API key 不落库、不写文件、不写日志；数据库只保存 `key_hash`（完整 key 的 SHA-256）和 `key_prefix`（前 6 位），网页显示前 6 位加 `***`，任务完成或取消后释放；浏览器成功提交后清空 key 输入框，也不会使用 localStorage/sessionStorage 保存 key。任务在内存中，服务重启会丢失进行中的任务；已落库结果不受影响。请以单 worker 运行，暂不支持多进程任务共享。

## Nginx + HTTPS

推荐为 `8.141.2.179` 配置域名（例如 `eval.example.com`）并申请浏览器信任的证书。DNS A 记录指向此 IP；开放 80/443；用 Certbot、acme.sh 或阿里云证书服务申请/续期证书。证书必须匹配实际访问地址。直接访问 IP 时必须使用覆盖该 IP 的可信证书，普通域名证书和自签名证书不能替代。

```text
浏览器 → https://eval.example.com:443 → Nginx → http://127.0.0.1:8080
```

1. 使用 `HOST=127.0.0.1 ./scripts/start.sh` 启动服务。
2. 根据 `scripts/nginx-https.conf.example` 配置站点，替换域名和证书路径。
3. 执行 `sudo nginx -t` 后重载 Nginx，访问 `https://eval.example.com/`。
4. 长期运行可以使用 `scripts/ai-gateway-eval.service.example`，填写实际路径后安装为 systemd 服务。先安装 Python 依赖。

模板不会自动修改现有 Nginx 或申请证书。

## 前端发布方式

当前网页与 API 由同一个本机服务提供，不部署 GitHub Pages，也没有自动发布工作流。默认无需 CORS 设置。`web/config.js` 的 `backendUrl` 留空即可使用当前页面所在地址。

## 环境变量

| 变量 | 用途 |
|---|---|
| `DATABASE_URL` 或 `PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD` | PostgreSQL 凭据，只供后端读取 |
| `CORS_ORIGINS` | 逗号分隔的允许跨域来源；同源访问无需添加 |
| `RESULTS_API_TOKEN` | 可选服务访问口令，保护评测和查询/保存 API；与模型 key 无关。配置后在网页服务设置填写 |
| `EVALUATION_EGRESS_IP` | 评测服务器出口 IP，当前为用户提供的 `8.141.2.179`；不是访问者 IP。如果使用代理或出口发生变化，需要修改，未知留空 |
| `ALLOWED_PRIVATE_HOSTS` | 显式允许评测的内网主机，逗号分隔。默认禁止内网、回环和云元数据地址；不自动跟随上游重定向 |

`.env` 权限为 600 且被 Git 忽略。网页只发布 `web/`，无法访问项目根目录、凭据或源码。服务默认不启用口令，知道地址的用户可以评测并查询共享记录；如需限制使用，在 `.env` 设置 `RESULTS_API_TOKEN`。

## 评测规则

- 默认仅勾选 `gpt-6-astra`；提供 `opus-5-5`、`gpt-6-sol`、`opus-4-8`，支持手填和多选。
- `claude/opus/sonnet/haiku` 名称走 `messages`；GPT 等名称走 `responses`。发送模型名称保持用户原值；只在指纹比较时将 `opus-5-5` 等明确别名对应至 `claude-opus-5-5`。
- 自动处理域名、尾斜杠、`/v1`、完整 messages/responses/chat-completions 路径，保留自定义前缀。显式输入无版本端点（如 `/messages`）时保留无版本形式。
- 默认 3 次指纹 + 5 次 Candy，模型与轮次同时并发，默认并发 16，服务全局上限 24，同时最多 4 个任务。进度按真实已完成请求显示。
- 请求带 `User-Agent: curl/8.5.0`，reasoning 为 high，不传输出 token 上限。支持 Responses 与 Anthropic JSON/SSE；墙钟超时默认 300 秒。上游若要求 `max_tokens` 会按真实错误记录，不会绕过 README 的无上限约定。
- 首次 401/403/5xx 后跳过该站点尚未发送的请求，已在途请求可完成；不自动重试付费请求。
- 每次任务前刷新 ModelTrace 官方参考库。刷新失败时使用随项目附带的库并标记「存疑」。数字数量不精确时只展示容错归因，不标为「真」或「换模」。未知模型未收录时也仅作参考。
- Candy 解析最终答案，保留 21/28/29 等逐轮值，不以正文出现「21」判正确。失败或无法可靠解析为 `?`，从可评分分母排除并在备注注明。Candy 分数不影响模型身份判定。
- 倍率由用户手填，未知留空，不猜测站点计费。所有选中模型共用本次倍率输入，可分开提交不同倍率的模型。
- `tested_at` 保存实际执行时间；`tested_date` 默认为执行时间对应的上海日期，也可手动指定。
- 结果添加 `result_uuid`，重复保存不会插入重复行。任务结果保留 1 小时（最多 256 个任务），历史记录持久保存。导入 API 接受用户自报结果，不能作为可信认证证明。

## 数据分析首页

网页默认展示质量分析，通过「新建评测」进入评测界面。支持域名、域名＋Key、域名＋Key＋模型三个分组维度，查看总体及单组时间趋势、热力分布和质量明细。范围默认为过去 12 小时，可切换 1 天或 7 天；按真实执行时间 `tested_at` 统计。完整计算口径与接口见 [数据分析协议](docs/ANALYTICS.md)。

## 按 Key 统计

新评测自动填写 `key_hash` 与 `key_prefix`。同一个 key 的标识稳定，不同 key 即使前 6 位相同也可以区分。历史数据的这两个字段保留 NULL（页面显示「未记录」），无法回填未曾保存的 key。

网页历史查询可按前 6 位筛选；这种筛选可能匹配多个 key。精确查询与后续聚合应使用 `key_hash`，不使用 `key_prefix`。例如：

```sql
SELECT domain, model, key_hash, key_prefix,
       count(*) AS evaluations,
       count(*) FILTER (WHERE verdict = '真') AS identity_matches
FROM ai_gateway_eval_results
WHERE key_hash IS NOT NULL
GROUP BY domain, model, key_hash, key_prefix;
```

批量保存结果接口接受成对的 `key_hash` 和 `key_prefix`，不接受完整 API key。哈希和前缀也会随结果 JSON 导出，便于离线聚合。

## API

完整字段、写入示例、去重和统计口径见 [数据库与写入协议](docs/DATABASE_WRITE_PROTOCOL.md)。

可访问 `/docs` 查看完整交互式接口文档。配置服务口令时，请求头为 `Authorization: Bearer <RESULTS_API_TOKEN>`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 数据库健康检查 |
| GET | `/api/analytics` | 12 小时 / 1 天 / 7 天的分组统计与时间序列 |
| POST | `/api/evaluations` | 提交评测，返回 202 与任务 ID |
| GET | `/api/evaluations/{id}` | 进度、每轮状态、已完成结果、保存状态 |
| POST | `/api/evaluations/{id}/cancel` | 取消在途任务 |
| POST | `/api/evaluations/{id}/save` | 重试保存完成的结果，不重新评测 |
| POST | `/api/results` | 批量保存结果，严格禁止额外字段（不接受 API key） |
| GET | `/api/results` | 分页查询，可筛选 `domain`、`model`、`key_hash`、`key_prefix`、`date_from`、`date_to`、`limit`、`offset` |

创建任务示例（key 仅发给你自己的服务，不应放在命令历史或 URL 中）：

```json
{
  "base_url": "https://857728.com",
  "api_key": "USER_SUPPLIED_KEY",
  "models": ["gpt-6-astra", "opus-5-5"],
  "candy_runs": 5,
  "concurrency": 16,
  "timeout": 300
}
```

保存结果的请求体为 `{"results": [...]}`，单条结构见 `schema.sql` 和 `/docs`。日期筛选以 `tested_date` 为准，结果按 `tested_at DESC` 排序。

## 验证

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
RUN_DB_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
npm test
```

数据库测试使用唯一的 `.invalid` 测试域名，完成后清理本次测试行。上游模型调用使用模拟响应，不消耗真实站点额度。ModelTrace 的 MIT 授权保存在 `backend/modeltrace_data/LICENSE`。
