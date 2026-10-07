# Task 1.3：LangGraph 中断、重启与恢复

日期：2026-09-11。合成数据，仅供演示。

## 任务契约

| 项目 | 约定 |
|---|---|
| 目标 | 使用真实 PostgreSQL checkpoint 验证人工中断、worker 重建、复核恢复与报告唯一性 |
| 输入 | 当前有效 run_id、运行级复核决定、reviewer_id、幂等键和理由 |
| 输出 | 持久化 interrupt、唯一复核决定、resume Outbox、唯一报告快照 |
| 修改范围 | Spike 图、复核与报告模型/服务、现有队列和 worker、迁移 0002、公共测试 fixture、恢复测试、依赖与实施记录 |
| 状态变化 | queued → running → awaiting_review → resume_queued → running → completed；不可恢复执行错误进入 failed |
| 数据约束 | 运行级复核部分唯一索引、复核请求键唯一、报告 run_id 唯一；沿用 Outbox 和 Run 归属约束 |
| 失败行为 | 复核事务整体回滚；checkpoint/业务提交间崩溃后按数据库状态继续；终态不恢复 |
| 幂等要求 | 相同复核请求返回原决定；重复恢复不生成新报告、不改写原快照 |
| 独立验收 | 初次执行与恢复在不同 worker 进程中完成，thread_id 始终等于 Run ID |
| 范围 | 仅零 AI 结论的运行级复核；不增加鉴权接口、AI 分析、真实规则、HTML 报告或前端 |

## 实现

`spike_graph.py` 使用 `AsyncPostgresSaver`，状态只携带 Run、复核和报告 ID。`review_interrupt` 在中断之前没有非幂等写入，恢复参数是已经提交的复核决定 ID，报告服务再次核对该决定属于当前 Run。

初次执行以 `thread_id=str(run_id)` 启动；以同步持久化模式写完 checkpoint 后，才把业务状态发布为 awaiting_review。此时队列 Job 完成，worker 不被人工等待占用。若 checkpoint 已提交而状态尚未更新就崩溃，新 worker 读取既有中断并补写业务状态。

复核服务在同一事务中保存决定、条件更新为 resume_queued 并写入恢复 Outbox。相同复核员和幂等键、相同内容返回原决定，内容改变则冲突；同一键不能用于两个 Run。重复提交完成后的请求仍返回原决定，不再次派发恢复。并发请求通过运行行锁与唯一约束保护。

worker 恢复时先条件切换到 running，再读取 checkpoint：仍有中断时提交 `Command(resume=review_id)`；中断已消费而后续节点未完成时，以空输入继续已保存的图状态。报告写入和 completed 状态在同一业务事务提交，受报告唯一约束保护。

start/resume 共用 `lock=run:{run_id}`，等待队列仍按 `queueing_lock=run:{run_id}:{operation}` 分开。图执行另外使用 Run 对应的 PostgreSQL 会话 advisory lock，防止同一 Run 的直接重放或多 worker 同时推进 checkpoint；进程退出导致连接关闭时该锁释放。业务行锁只用于短事务，没有跨图执行一直持有业务行锁。

报告是标有 `workflow_spike` 的不可变 JSON 快照，保留运行、决定、理由和证据不足标记。批准或驳回均完成运行级复核，不伪造 AI 结论、风险分数或“无风险”判断。正式 HTML 风险报告属于 Task 2.3。

复核员和企业仍为 Spike 的 UUID 输入，禁止调查发起人复核自己，但尚未实现真实账号角色认证。请求级幂等目前落在复核表的 reviewer_id/idempotency_key 唯一约束，后续 HTTP 业务阶段需接入完整的 IdempotencyRequest 契约；没有对外开放未鉴权写接口。

复核表预留成对的 ai_finding_id/finding_version 空字段，本任务仅写运行级决定；多条 AI 结论逐条复核、修改结论及引用校验留到阶段 2，不能直接调用本 Spike 服务绕过这些未来校验。

参考：[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[持久化](https://docs.langchain.com/oss/python/langgraph/persistence)。实际安装并锁定 LangGraph 1.2.11、langgraph-checkpoint-postgres 3.1.2；未调用生成模型或下载 embedding 权重。

## 初始化与运行

在仓库根目录执行：

```powershell
uv sync --directory apps/api --locked
docker compose up -d --wait postgres
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api python -m tracerisk.workflows.spike_graph
```

最后一条是 checkpoint schema 初始化/升级命令，可以重复执行。队列 schema 仍按 [Task 1.2](task-1.2.md) 首次初始化；现有演示库已经初始化，不重复创建队列表。

分别在两个终端启动派发器和 worker：

```powershell
uv run --directory apps/api python -m tracerisk.workflows.outbox
```

```powershell
uv run --directory apps/api python -m tracerisk.workflows.worker
```

沿用既有 start Job 名称，以便现有 Outbox 能继续执行；worker 现在在派发步骤后推进真实 Spike 图。复核通过 `submit_review` 服务提交，HTTP 接口尚未开放，可通过集成测试演示完整过程。

Alembic 管理业务表，Procrastinate 与 LangGraph 分别管理自己的 schema；模型差异比较明确排除队列表以及四张 checkpoint 表。

## 测试与结果

```powershell
uv run --directory apps/api pytest tests/integration/test_interrupt_resume.py -v
uv run --directory apps/api pytest -v
uv run --directory apps/api ruff check .
uv run --directory apps/api ruff format --check .
uv run --directory apps/api mypy src
uv run --directory apps/api alembic check
docker compose config --quiet
```

| 验证 | 实际结果 |
|---|---|
| 首批图集成测试 | 10 项通过 |
| 最终完整回归 | 30 项通过，64.83 秒：11 项图恢复、18 项原有集成测试、1 项健康测试 |
| Ruff / 格式 | 通过，29 个 Python 文件格式有效 |
| mypy | 21 个源码文件通过严格类型检查 |
| 业务迁移 | 演示库已升级至 0002 |
| checkpoint 初始化 | 连续执行两次成功 |
| Alembic check | 无新增模型差异 |
| Compose config / PostgreSQL | 配置有效，容器 healthy |

最终没有失败测试；保留一条 Starlette/AnyIO BlockingPortal 的依赖弃用警告，未屏蔽。

具体覆盖：

- 使用一个 worker 进程执行到中断，退出后由新的解释器、worker、graph 和 saver 恢复；数据库 thread_id 不变。
- 等待人工时没有 doing Job；恢复后仅一条复核决定、一条恢复 Outbox 和一份报告。
- 相同请求重复提交、完成后再次提交、不同内容复用键、不同键重复复核、同一键并发用于不同 Run。
- 并发复核及并发图恢复，原始快照不被覆盖。
- 复核前置状态、自我复核、事务最后 Outbox 插入失败时的整体回滚。
- 无决定、失败 Run 不恢复；新 Run 获得独立 thread_id，旧 checkpoint 保留。
- 批准与驳回均保留证据不足标记。
- 四个真实子进程强制退出窗口：checkpoint 写入后但 awaiting_review 更新前、恢复状态切换后但读取图前、消费中断后但报告执行前、报告提交后但图/Job 确认前。
- 原有 Outbox、队列锁、事务回滚、重试耗尽和业务迁移回退/再升级仍通过。旧派发测试显式隔离图执行，新增测试完整接入真实图。

故障退出采用 `os._exit`，没有正常资源清理；仅在独立测试数据库内提前心跳时间模拟超过 30 秒，不将该测试耗时作为真实恢复时延指标。

## 交付状态与限制

阶段 1 的 Outbox、checkpoint、中断重启与重复恢复技术门槛已通过。报告内容与复核仍是上述限定的 Spike，不代表后续 AI 引用、多结论复核、账号权限和 HTML 报告已完成。

尚未做数据库整体中断、网络分区或长期负载验证。报告已完成而最后 graph checkpoint 尚未写完时，以业务库的 completed 和唯一报告为准，重复 Job 不重启终态运行；保留的 checkpoint 可用于诊断，不强行重写历史。

测试使用独立随机数据库，并显式关闭外部 LangSmith tracing。演示库只应用 schema，调查、复核和报告记录均为零；未使用真实业务资料、模型密钥，未执行 Git 提交。PostgreSQL 保持运行，测试子进程已退出。
