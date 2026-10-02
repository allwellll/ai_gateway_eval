# 模型倍率与充值倍率补丁表

> 同步自 Hermes 主记忆（sub2api 评测口径），作为 ai_gateway_eval 写入时的倍率默认值。
> 与 `docs/DATABASE_WRITE_PROTOCOL.md` 配合使用；禁止按域名/账号名猜模型倍率。

## 1. 模型倍率（写入 `model_rate`）

优先级：
1. **优先**取 `/v1/models` probe 返回的 `effective_rate_multiplier`（当次实测有效倍率）。
2. probe 缺失时按本表补充。

| 来源 | 账号 / 备注 | 模型倍率 |
|---|---|---|
| foxcode | — | 1.5 |
| hetu（sk-YT 开头 key） | — | 0.11 |
| hetu024（sk-lH 开头 key） | — | 0.24 |
| vapi07 | — | 0.35 |
| hetu-ds-2 | — | 0.13 |
| 默认（无记录） | — | 1.0 |

## 2. 充值倍率（写入 `recharge_rate`）

| 账号 | 充值倍率 |
|---|---|
| cfm02 | 0.15 |
| codexforme | 0.15 |
| foxcode | 0.25 |
| codesub02 | 0.2 |
| cfm-kiro-014 | 0.15 |
| vapi07 | 2 |
| 默认（无记录） | 1.0 |

## 3. 整体倍率口径

```
overall_rate = recharge_rate × model_rate
```

## 4. 使用注意

- 倍率未知不能填 0，应填 `null`（0 表示已知为零）。
- 每次写入前如能 probe `/v1/models`，应以 probe 返回值覆盖本表。
- 写入数据库前请查阅 `docs/DATABASE_WRITE_PROTOCOL.md` 字段协议与去重规则。

同步时间：2026-10-02
