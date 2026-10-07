# Task 1.2：Outbox 与 Procrastinate 调度

日期：2026-09-10。合成数据，仅供演示。

## 任务契约

| 项目 | 约定 |
|---|---|
| 目标 | 在真实 PostgreSQL 上验证业务事务、可靠派发和重复执行保护 |
| 输入 | company_id、created_by、as_of_date；企业与账号暂为不透明 UUID |
| 输出 | 同一事务保存 Investigation、Run #1、start Outbox；worker 保存唯一 dispatch_probe 步骤 |
| 允许修改 | investigation 模型与服务、workflows 下的 outbox/queue/worker、0001 迁移与 Alembic 模型注册、集成测试、依赖锁文件及实施记录 |
| 状态变化 | 有效 Run 从 queued 条件更新为 running；重试耗尽从 queued/running 条件更新为 failed |
| 数据约束 | Outbox 的 run_id/operation 唯一、步骤的 run_id/name 唯一、运行编号唯一；复合延迟外键确保当前 Run 属于调查 |
| 失败行为 | 入队失败保留未派发 Outbox；业务事务失败整体回滚；worker 重启后重试失联任务 |
| 幂等要求 | 重复扫描、重复 Job 或重复 worker 调用不能重复提交步骤 |
| 独立验收 | 提交后进程退出仍能派发；入队后确认丢失可重扫；worker 提交前后退出均可恢复且步骤只有一条 |
| 范围 | 不实现 HTTP 创建接口、请求幂等键、真实用户/企业表、LangGraph、人工复核、恢复操作或报告 |

## 实现与取舍

`create_investigation` 接受调用方持有的 AsyncSession，仅 flush、不隐式 commit、不直接入队。调用方使用 `sessions.begin()` 包住完整事务。API 请求层的身份校验与 Idempotency-Key 属于后续业务阶段，此处没有开放未鉴权写接口。

派发器按创建顺序锁定未确认 Outbox，使用 `FOR UPDATE SKIP LOCKED` 允许并发扫描。队列写入先独立提交，成功后才确认 Outbox；中间崩溃可重扫。Job 同时设置 `lock` 和 `queueing_lock`，值为 `run:{run_id}:start`。

入队前复用同键 todo/doing Job，避免产生一个等待任务阻塞失联 doing Job 的重试。并发插入仍由队列唯一约束保护，并捕获 AlreadyEnqueued。原 Job 若已经完成而 Outbox 尚未确认，重扫可能产生新的物理 Job；worker 的数据库结果校验仍保证有效步骤只提交一次。这里保证的是业务结果幂等，不声称物理 Job 恰好创建一次。

worker 在一个短事务内锁住 Investigation 和 Run，检查当前指针、状态与已有步骤，条件更新运行状态并保存步骤。锁持续到提交，所以结果写入前 Run 有效性不会被并发修改。相比引入租约系统，这足以验证当前无外部副作用的短步骤；后续模型调用不能直接照搬“整个调用期间持有数据库锁”的做法。

派发器每两秒扫描，同时通过 Procrastinate 心跳接口恢复超过 30 秒失联的本项目任务。普通异常最多执行三次，间隔一秒，沿用同一 Run；最终失败写入 failed。成功后的 WorkflowStep.attempt_count 记录对应队列执行次数；失败尝试详情由队列保留，当前不创建完整业务节点日志体系。

执行成功后 Run 保持 running，因为本 Spike 仅验证派发步骤，没有报告，不能伪造 completed。Task 1.3 将接入持久化中断和恢复。当前 resume Outbox 会明确拒绝，避免意外重跑 start。

队列 schema 由固定版本 Procrastinate 管理，业务表由 Alembic 管理；自动比较排除 procrastinate_ 前缀表。升级队列依赖时需要按其官方迁移流程处理，不在 API/worker 启动时自动重建队列表。

参考：[队列锁](https://procrastinate.readthedocs.io/en/stable/howto/advanced/queueing_locks.html)、[执行锁](https://procrastinate.readthedocs.io/en/stable/howto/advanced/locks.html)、[失联任务恢复](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html)、[队列迁移](https://procrastinate.readthedocs.io/en/stable/howto/production/migrations.html)。接口同时对照已安装的 Procrastinate 3.9.0 核验。

## 启动与测试

在仓库根目录执行：

```powershell
uv sync --directory apps/api --locked
docker compose up -d --wait --wait-timeout 60 postgres
uv run --directory apps/api alembic upgrade head
```

仅在**尚未初始化队列表的新数据库**执行一次：

```powershell
uv run --directory apps/api python -m tracerisk.workflows.queue
```

本次演示数据库已经初始化，不要重复执行 schema 创建命令；该命令不是队列升级工具。首次执行产生 Procrastinate 关于主模块创建 App 的提示，但固定 task name 与独立 worker 注册不受影响，schema 创建已成功。

分别在两个终端运行：

```powershell
uv run --directory apps/api python -m tracerisk.workflows.outbox
```

```powershell
uv run --directory apps/api python -m tracerisk.workflows.worker
```

独立验收：

```powershell
uv run --directory apps/api pytest tests/integration/test_outbox_dispatch.py -v
uv run --directory apps/api pytest -v
uv run --directory apps/api ruff check .
uv run --directory apps/api ruff format --check .
uv run --directory apps/api mypy src
uv run --directory apps/api alembic check
docker compose config --quiet
```

集成测试账号须能创建临时数据库。本地 Compose 的演示账号满足此条件；测试连接沿用环境配置，但每个用例创建独立的随机 `tracerisk_test_<uuid>` 数据库。清理只删除该用例创建且名称通过检查的数据库，不清空演示库。

## 实际验收结果

| 检查 | 结果 |
|---|---|
| Task 1.1 在线基线升级、重复升级、current、check | 通过，补验时 head 为 0000 |
| 首轮 Outbox 集成测试 | 10 项通过 |
| 补齐边界后的完整 pytest | 19 项通过，23.01 秒；18 项集成测试加 1 项健康测试 |
| 最后调整步骤尝试次数后的针对性复验 | 4 项通过，覆盖两种 worker 崩溃及重试成功/耗尽 |
| Ruff / 格式 | 通过，19 个 Python 文件格式有效 |
| mypy src | 14 个源码文件通过严格类型检查 |
| 演示库 upgrade head | 成功，迁移为 0001 |
| Procrastinate schema 初始化 | 成功 |
| Alembic check（包含队列表的演示库） | 无新迁移差异 |
| Compose config | 通过 |
| 派发器及 worker 模块启动检查 | 两个进程正常启动，检查后均关闭 |
| PostgreSQL 容器 | healthy，仅发布到 127.0.0.1:5432 |

最终测试失败数为零。仍有 Task 1.1 已记录的两条 Starlette/AnyIO 弃用警告，未屏蔽。

测试覆盖：创建事务整体回滚、提交后真实子进程退出、入队前失败、入队提交后的确认缺口、并发扫描、并发执行、队列锁竞争、worker 事务中退出、worker 提交结果后退出、失联恢复、四种非执行状态与非当前 Run、Outbox 唯一性、当前 Run 归属、短暂失败恢复、重试耗尽，以及迁移降到 0000 再升级的回环。

worker 崩溃通过子进程 `os._exit` 实际触发；失联心跳过期在独立测试库中将时间提前一分钟模拟，没有等待真实 30 秒。这验证恢复条件与流程，不是恢复耗时指标。当前未验证 PostgreSQL 服务整体中断、网络分区、长期运行或负载性能。

## 安全与交付状态

未使用真实企业资料或模型密钥，未调用外部模型，未提交 Git。演示数据库仅应用 schema，没有插入业务演示调查；测试数据库已按用例清理。PostgreSQL 服务保留运行，临时派发器、worker 和故障子进程均已退出。

Task 1.2 完成。阶段 1 的 Stage Gate 仍需 Task 1.3 验证 LangGraph 持久化中断、worker 重建和重复恢复；尚未进入后续业务阶段。
