# Stage 3 完整业务案例验收

验收日期：2026-09-16。全部数据均为本地合成数据，基准日固定为
`2026-09-11`，没有调用外部数据源、模型或 embedding 服务。

## 结果

Stage 3 的 10 家可复现企业、四条确定性规则、覆盖与冲突策略、不可变报告、
五类生命周期审计和终态重新调查均已完成。最终完整后端检查为
`139 passed, 1 warning`；Ruff、格式、mypy、Alembic 漂移检查和前端 OpenAPI
契约检查全部通过。

## 十个固定场景

| 顺序 | 场景键 | 预期规则分 | 主要边界 |
| ---: | --- | ---: | --- |
| 1 | `existing-judicial` | 35 | 保留 Task 2.4 企业及三条证据；两宗未结案件只命中一次司法规则 |
| 2 | `clean-operations` | 0 | 四类来源均可用，未发现风险 |
| 3 | `all-rules-95` | 95 | 司法、经营、新闻、核心知识产权四条规则全部命中 |
| 4 | `alias-homonym` | 35 | 历史别名及同名干扰按 company_id 隔离 |
| 5 | `duplicate-news` | 0 | 两篇报道共享一个故事键，只计一个独立故事 |
| 6 | `cross-source-conflict` | 35 | 司法权威来源决定评分，同时保留冲突双方并强制复核 |
| 7 | `unavailable-source` | 0 | 工商 unavailable、知识产权 stale，作为证据不足复核 |
| 8 | `news-window-boundaries` | 20 | 仅 D−364 到 D 含两端进入连续 365 天窗口 |
| 9 | `business-date-boundaries` | 25 | D 后解除在 D 仍有效；D 当日解除和 D 后新增不命中 |
| 10 | `prompt-injection` | 0 | 指令样文本仅作为 Evidence.content，不改变规则或路由 |

目录使用固定种子、私有随机实例、语义 UUID5、稳定排序和 UTF-8 SHA-256。
连续生成及扰动全局 random 后的逐字段结果由单元测试验证一致。固定企业
`20000000-0000-0000-0000-000000000001` 和 Evidence
`21000000-0000-0000-0000-000000000001..003` 保持原值。

## 规则与 95 分证明

| 规则 | 条件摘要 | 分值 | 95 分实测证据数 |
| --- | --- | ---: | ---: |
| `JUDICIAL_OPEN_CASE_V1` | D 当日或之前最后状态至少一宗 open | 35 | 1 |
| `BUSINESS_ABNORMAL_V1` | D 当日仍 active 的经营异常 | 25 | 1 |
| `NEGATIVE_NEWS_V1` | D−364 至 D 至少两个独立负面故事 | 20 | 2 |
| `CORE_IP_ANOMALY_V1` | 核心知识产权 invalid/ownership_dispute | 15 | 1 |

真实运行 `c98dd94f-610f-4b20-9937-1aa6fc365cd1` 的 Run
`a19d3e00-326c-4f7b-9603-c9d5633ab6e7` 持久化四条 finding，分数为
`35 + 25 + 20 + 15 = 95`。它以 `high_risk` 和 `model_recommended`
进入复核，批准后生成报告 `3fee0687-e1ed-4a3a-bcb8-8a854664b4b8`；报告仍为
95 分。聚合单元测试还覆盖 0、单规则、60 和 95，并证明 AI 和人工决定对象
不能改变规则投影。

## 真实 API、Outbox 与 worker 矩阵

`docs/evaluation/stage-3-runtime-result.json` 是完整机器可读记录。所有调查通过
FastAPI 创建，由 `tracerisk.workflows.outbox` 派发并由独立 worker 执行。

| 场景 | Investigation / Run / Report | 规则分 | 初次路由和恢复 | 冻结检索域 |
| --- | --- | ---: | --- | --- |
| normal | `d5e1e876…` / `a63bc933…` / `b876a06b…` | 0 | 直接 completed，无复核原因 | intellectual_property |
| all-rules-95 | `c98dd94f…` / `a19d3e00…` / `3fee0687…` | 95 | awaiting_review；1 条 finding 决定后 completed | business, intellectual_property, judicial, news |
| coverage-missing | `4e4e7dc1…` / `d882ad9e…` / `dd7277a0…` | 0 | 零 AI；1 条运行级决定后 completed；原因 `evidence_insufficient` | 空 |
| authoritative-conflict | `9329aab7…` / `f994633f…` / `f520ec5b…` | 35 | 司法权威值 open 计分；原因 `conflict`；复核后 completed | judicial |
| prompt-injection | `11609062…` / `7984a42c…` / `52cc45c1…` | 0 | 直接 completed；正文不改变控制字段 | news |

来源不足报告冻结四项覆盖记录，其中 business 为 unavailable、IP 为 stale，
`insufficiency_details` 为 `business:unavailable`、
`intellectual_property:stale`。权威冲突报告保存 business/closed 与
judicial/open 两侧 Evidence ID、哈希、日期，`authority_source=judicial`、
`resolved_value=open`。这两类信息只决定复核，不改变规则分。

零 AI 恢复在真实矩阵中验证一次运行级决定只创建一次 resume Outbox，恢复后
分数仍为 0。完整测试还覆盖多 AI finding 全部处理后才创建一次恢复 Outbox，
以及精确重放不增加决定、Outbox、审计或报告。冻结检索 manifest 的测试验证
缺行、改 rank 或后到事实均不能改变既有 Run 的事实宇宙。

## 终态重新调查

只在 normal 的 Run #1 已 completed 后调用
`POST /investigations/{id}/runs`。它原子创建 Run #2
`0377bccb-bc61-4b3c-9daf-d31e05b3ebd5`、唯一 start Outbox、唯一 replay 行和
`investigation.rerun_created` 审计，切换 current pointer，随后完成报告
`ab641811-756e-486c-a021-ec4c879bc138`。旧 Run #1 仍为 completed，旧报告
`b876a06b-43e1-4d35-8689-191181c5d593` 前后 JSON 完全相等；数据库快照哈希为
`059d2f4de14af8effa53decc78ce894451d94222458eb5d404146d0ab2f34724`。

集成测试另验证 failed 终态、四种非终态 409、相同键重放、跨调查键冲突，
以及四个并发相同请求只产生一个新 Run、current pointer、Outbox、replay 和审计。

## 审计证据

保留数据库的这次真实矩阵含 15 条新生命周期证据：

| 类型 | 条数 |
| --- | ---: |
| `investigation.created` | 5 |
| `investigation.rerun_created` | 1 |
| `review.submitted` | 3 |
| `report.generated` | 6 |

这些 action key 全部唯一，元数据扫描没有密码、Authorization/Token、幂等键、
Evidence 正文、AI summary/reasoning 或复核 reason。第五类 `workflow.failed` 由
完整测试中的真实 Procrastinate worker permanent 与 retry-exhausted 两个分支
验证：每个失败 Run 恰好一条事件，重复投递仍为一条。另一个事务测试将
`review.submitted`、`report.generated`、`workflow.failed` 放在同一测试数据库中，
断言三类各一条且敏感正文不进入元数据。五个业务边界的审计插入失败测试均
验证业务动作整体回滚。

## 数据库导入和迁移

保留数据库先执行 Alembic upgrade/current/check，结果为 `0009 (head)` 且
`No new upgrade operations detected.`。没有重置 PostgreSQL，也没有运行
Procrastinate schema setup。随后连续两次执行 `scripts/seed_stage3.py`：

```text
catalog/persisted: companies=10, company_aliases=1, evidence=25, source_coverage=40
first inserted:    companies=0,  company_aliases=0, evidence=0,  source_coverage=0
second inserted:   companies=0,  company_aliases=0, evidence=0,  source_coverage=0
stable_hashes_verified: true
```

数据库在 Task 6 开始前已经由 Task 2 导入，因此第一次验收重放也为零新增。
两次均精确验证固定企业和三条固定 Evidence。`0008` 创建 alias/coverage，
`0009` 创建 audit/rerun；当前唯一 head 为 `0009`。

## 最终命令与结果

```powershell
$env:UV_CACHE_DIR='C:/Project/TraceRisk/.uv-cache'
uv run --directory apps/api pytest -q -p no:cacheprovider
# 139 passed, 1 warning in 174.69s

uv run --directory apps/api ruff check .
# All checks passed!
uv run --directory apps/api ruff format --check .
# 103 files already formatted
uv run --directory apps/api mypy src
# Success: no issues found in 66 source files
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api alembic current
# 0009 (head)
uv run --directory apps/api alembic check
# No new upgrade operations detected.
pnpm --dir apps/web api:check
# API types are consistent with local OpenAPI.
```

唯一警告原文：

```text
C:\Project\TraceRisk\apps\api\.venv\Lib\site-packages\starlette\testclient.py:53:
DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use
anyio.from_thread.BlockingPortal instead.
```

这是 Starlette 测试客户端的第三方弃用警告，没有隐藏或抑制。

## 范围审计和复现

Stage 3 期间 `apps/web` 唯一修改文件为自动生成的
`apps/web/src/api/generated.ts`；没有增加重新调查按钮、路由、composable 或 UI
文案。未加入模型凭据、外部调用、真实企业数据、Stage 4 代码或新迁移。当前
实现是本地合成评测闭环，不代表生产抓取、真实模型质量、多租户 ACL、负载或
部署验收。

复现保留数据库导入：

```powershell
$env:UV_CACHE_DIR='C:/Project/TraceRisk/.uv-cache'
uv run --directory apps/api alembic upgrade head
uv run --project apps/api python scripts/seed_stage3.py
uv run --project apps/api python scripts/seed_stage3.py
```

真实运行需分别启动 API、Outbox 和 worker；既有数据库不得再次执行
Procrastinate schema setup：

```powershell
uv run --directory apps/api python -m tracerisk
uv run --directory apps/api python -m tracerisk.workflows.outbox
uv run --directory apps/api python -m tracerisk.workflows.worker
# Set DEMO_ANALYST_PASSWORD and DEMO_REVIEWER_PASSWORD without printing them.
uv run --project apps/api python .superpowers/sdd/stage-3-runtime-acceptance.py
```
