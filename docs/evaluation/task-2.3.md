# Task 2.3 验收记录

验收日期：2026-09-14。范围为本地 PostgreSQL 上的合成司法案例、Mock AI、持久化复核恢复与 HTML 报告；未调用外部模型服务。

## 版本与数据库

通过运行环境的 `importlib.metadata.version` 读取：FastAPI 0.141.1、Pydantic 2.13.5、SQLAlchemy 2.0.52、LangGraph 1.2.11、langgraph-checkpoint 4.2.0、langgraph-checkpoint-postgres 3.1.2、Procrastinate 3.9.0、Psycopg 3.3.5。AI 使用确定性 Mock adapter，没有真实模型版本或凭据。

`alembic upgrade head` 成功，`alembic current` 为 `0006 (head)`，`alembic check` 输出 `No new upgrade operations detected.`。迁移 0005 添加 `ai_findings`、`evidence_chunks`、`retrieved_evidence`，建立 Evidence 公司/版本约束、复核引用，并为 `risk_reports` 添加 HTML 字段。0006 用 `(ai_finding_id, run_id)` 复合外键保证复核结论属于同一 Run。已有 `companies`、`evidence`、`investigations`、`workflow_runs`、`dispatch_outbox`、`rule_findings`、`rule_finding_evidence`、`review_decisions`、`risk_reports` 与 LangGraph PostgreSQL checkpoint、Procrastinate queue 一起支持恢复。

`docker compose ps` 确认 `postgres:17` 的 `tracerisk-postgres-1` 为 healthy，端口仅绑定 `127.0.0.1:5432`。首次非提权读取因 Docker config 与 named pipe 权限失败；授权范围内的只读提权检查成功。

## 实际行为

Seed 连续运行两次均报告：两宗去重未结案、一次 35 分规则命中、三条持久化 Evidence 可供 Chunk 使用、已有记录保留。固定 UUID 与冲突忽略保证幂等；额外校验读取数据库实际行，核对公司、司法来源、风险类别、结构化 open/negative 事实、非空正文与实际 SHA-256。Seed 不创建 Chunk、Investigation、Outbox、复核或报告。Chunk 与 Run 检索记录由工作流创建。

Mock 将两宗案件汇总为一条司法结论，包含两条案件引用。严格 Pydantic schema 限制字段、枚举、长度并禁止额外字段。引用必须属于已保存的同一 Run/风险类别检索集合及同一企业，Chunk 必须属于指定 Evidence；引用、Chunk、Evidence 哈希必须与持久化 Chunk 正文 SHA-256 相同，摘录须非空且为正文连续子串。司法 open/negative 事实、事实键与日期必须支持结论；错误引用或畸形输出为永久错误，不重试或保存无效草稿。当前 demo read 权限为 analyst/reviewer/admin，不代表生产企业级授权。

规则总分范围为 0–95，高风险阈值为 60。人工复核原因独立保留：`high_risk`、`evidence_insufficient`、`conflict`、`model_evidence_insufficient`、`model_recommended`。真实合成故事为 35 分，其暂停原因恰为 Mock 的 `model_recommended`；直接完成 E2E 仅关闭此推荐，仍使用真实持久化规则和引用。证据冲突触发复核而不额外加分。

`GET /reviews/pending` 向 reviewer/admin 展示当前 awaiting-review Run 的未处理结论，排除创建人自己的调查。`POST /reviews/{finding_id}` 要求 JWT、Idempotency-Key、当前 finding_version、approve/reject 与 reason；只允许 reviewer/admin，创建人不能自审。相同请求重放返回原 decision_id；相同 key 不同内容、过期版本、旧 Run 或不符合状态的请求返回 409。所有当前版本 AI finding 都完成决定后才进入 resume_queued 并创建一次 resume Outbox；存在 AI finding 时禁止 Run-level 决定，零 AI finding 的 spike 使用 Run-level 复核。HTTP 没有新增 Run-level 复核接口。

`GET /reports/{report_id}` 向 analyst/reviewer/admin 返回存储快照及 HTML，不存在时返回 404。生成服务再次检查当前 Run、规则分数、检索充分性与完整人工复核，防止绕过 barrier。HTML 仅对不可变快照值作 JSON 文本渲染并用 `html.escape` 转义；不执行来源或结论中的 HTML。原始 AI 字段与人工决定分别保留：approve 保留最终结论，reject 从 final_findings 移除结论，原始 AI 不被修改。报告显示“合成数据，仅供演示”。

唯一约束、事务锁、版本检查、Outbox 和 checkpoint 恢复保证重复复核/恢复不会产生重复决定、resume Outbox 或报告。真实 worker 新进程 E2E 验证复核后恢复与重复 resume，报告快照序列化字节和 HTML UTF-8 字节保持一致。报告提交与 completed 状态处于同一事务。

## 验证结果

所有 uv 命令使用 `UV_CACHE_DIR=C:/Project/TraceRisk/.uv-cache`。

| 命令 | 结果 |
| --- | --- |
| `uv run --directory apps/api ruff check .` | All checks passed |
| `uv run --directory apps/api ruff format --check .` | 73 files already formatted |
| `uv run --directory apps/api mypy src` | 50 source files，无问题 |
| `uv run --directory apps/api pytest -q -p no:cacheprovider` | 70 passed，1 warning，114.60s |
| `uv run --directory apps/api alembic upgrade head` | 成功 |
| `uv run --directory apps/api alembic check` | 无 schema drift |
| `uv run --project apps/api python scripts/seed_synthetic.py`（两次） | 两宗未结案、一次 35 分命中、三条 Chunk-ready Evidence |
| `docker compose ps`（只读提权） | PostgreSQL healthy |

完整 pytest 未筛除 crash 或 E2E，包括持久化 checkpoint/status 恢复、三个 resume 崩溃窗口及两条完整 E2E。保留的一条依赖警告来自 Starlette TestClient 使用已弃用的 `anyio.abc.BlockingPortal` 别名；本任务不升级依赖。禁用 pytest cacheprovider 避免已有本地缓存目录权限警告。

## 取舍与后续范围

保留原有 conflict-ignore seed 并校验实际行，比更新已有 Evidence 或在 seed 中启动工作流更简单，且保护已有记录；已有演示行被修改为不支持故事时会明确报错，不自动覆盖。全部数据仅为 synthetic。Task 2.4 前端、真实 LLM、向量检索、完整四类规则矩阵及生产权限隔离均未实施。Task 2.3 完成的是这一最小司法闭环，不扩大至上述后续范围。
