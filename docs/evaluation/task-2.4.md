# Task 2.4 验收记录：前端底座与真实浏览器闭环

验收日期：2026-09-15。范围是已确认的合成演示前端与最小 API 闭环；未开始阶段 3。

## 结论

Task 2.4 的五项计划要求均已完成。浏览器验收使用本地 FastAPI、PostgreSQL、Outbox 和独立 worker；调查、复核和报告的正常路径没有用拦截响应替代真实 API。受控浏览器拦截仅用于 401、409、422、网络错误以及不合资格复核/不可信报告内容等负向 UI 边界。

## 真实浏览器证据

可重复脚本为 `scripts/acceptance-task-2.4.cjs`，完整机器可读结果见 [task-2.4-browser-result.json](task-2.4-browser-result.json)。本次运行从 `2026-09-15T02:10:57.124Z` 到 `2026-09-15T02:12:15.783Z`，Chrome 为 `152.0.7977.84`，15 项断言全部通过；浏览器错误和意外 `/api/` 失败响应均为空。

真实路径由 analyst 创建 `519195b0-83ef-4823-a62d-8b46e68e86b0` 调查，并在 `awaiting_review` 停止。该 Run 为 `106800e4-7503-4c49-ab27-c16ed9b742c0`，保留四个已持久化步骤，实际规则分为 35，一条原始 AI 结论含两条引用。创建按钮的同 tick 双击只发出一次 POST；相同 Idempotency-Key 的真实 API 重放返回同一调查。

在人工复核前，旧 worker `exec78891` 已用 Ctrl+C 停止并确认以 exit 1 退出；新 worker `exec70990` 在真实调查到达 `awaiting_review` 后、reviewer 提交前启动。其 Windows 信号处理限制仅出现在启动输出，`exec70990` 是新的活进程证据。reviewer 批准后，新 worker 从 checkpoint 恢复并完成 Run，生成报告 `91e7d525-596c-408f-ab11-5b4462022489`。终态详情页在 6.5 秒内未再请求详情 API。

报告快照分别保留规则、原始 AI、人工批准和最终结论：规则分仍为 35、原始 AI 一条、最终结论一条、人工决定一条、两条引用未丢失。证据截图：

- [等待人工复核](task-2.4-awaiting-review.png)
- [完成报告](task-2.4-completed-report.png)
- [390 px 窄屏报告](task-2.4-report-narrow.png)

初版窄屏仅验证了无横向溢出，但视觉复核发现 136 px 侧栏使内容区过窄。`WorkspaceLayout.vue` 已改为在 `max-width: 767px` 进入时自动收起侧栏、仍允许用户通过菜单按钮展开，并在移动端选中菜单后重新收起；卸载时移除媒体查询监听。修复后的真实 Chrome 390 px 检查显示侧栏边框为 1 px、主内容宽 389 px、`document` 横向溢出为 0，截图文字可读。

其余浏览器断言包括：错误密码可见反馈；analyst 的复核入口隐藏且真实 POST 返回 403；reviewer 的创建入口隐藏且真实 POST 返回 403；过期 finding 真实 POST 为 409，空理由真实 POST 为 422；logout 和格式错误的 401 都清除 session、Vue Query 查询/变更缓存和敏感 DOM。受控传输还确认可见的 409、422 与网络错误反馈，以及 self、非当前 Run、过期版本时不显示提交按钮。

不可信文本仅以 Vue 插值显示，没有生成 DOM 节点；归档 HTML 在 `sandbox=""` iframe 中，既没有 `allow-scripts` 也没有 `allow-same-origin`，脚本探针未执行。

## 回归与来源核验

所有命令均在项目根目录执行；Python 命令使用 `UV_CACHE_DIR=C:/Project/TraceRisk/.uv-cache`。

| 检查 | 结果 |
| --- | --- |
| `pnpm --dir apps/web lint` | 通过，0 errors/warnings；窄屏修复后复跑通过 |
| `pnpm --dir apps/web typecheck` | 通过；窄屏修复后复跑通过 |
| `pnpm --dir apps/web build` | 窄屏修复后通过；JS 621.99 kB（gzip 184.37 kB）、CSS 5.65 kB（gzip 1.84 kB） |
| `pnpm --dir apps/web api:check` | 窄屏修复后通过，生成类型与本地 OpenAPI 一致 |
| `pnpm --dir apps/web test:auth` / `test:business` | 均通过 |
| `uv run --directory apps/api ruff check .` | 通过 |
| `uv run --directory apps/api ruff format --check .` | 78 个文件均已格式化 |
| `uv run --directory apps/api mypy src` | 53 个源文件无问题 |
| `uv run --directory apps/api pytest -q -p no:cacheprovider` | 74 passed，141.56 s |
| `uv run --directory apps/api alembic upgrade head` / `alembic check` | 均通过；无 schema drift |
| `uv run --directory apps/api python -m tracerisk.auth.seed` | 通过：`Demo accounts initialized; existing accounts preserved.` |
| `uv run --project apps/api python scripts/seed_synthetic.py` | 通过：两宗未结案件、一次 35 分规则命中、三条 Chunk-ready Evidence；既有记录保留 |
| 固定上游来源审计 | 16 个选定原件 SHA-256 和完整 MIT 原文逐字节匹配 |
| `node --check scripts/acceptance-task-2.4.cjs` | 通过 |

运行环境：Node `v24.18.0`、pnpm `11.13.1`、uv `0.10.8`、Chrome `152.0.7977.84`。Vite 构建保留非失败的 500 kB core bundle 警告。pytest 的唯一警告来自 Starlette TestClient 依赖使用已弃用的 `anyio.abc.BlockingPortal` 别名；本任务没有升级依赖。

## 范围与限制

本记录只覆盖本地合成演示：没有上线、发布、Git 初始化/提交、真实模型、向量检索、SSE、重新调查、生产 ACL 或阶段 3 数据。浏览器脚本不写入 JWT、配置秘密或环境文件；演示身份由环境变量提供，记录和截图不包含密码或 token。

Procrastinate 的现有 schema 是前置条件：其 `apply_schema_async` 对已存在状态类型并不幂等，曾会报 `DuplicateObject`。因此保留现有数据库并先检查表；不要为重跑验收重置数据库或重复应用该 schema。Alembic `upgrade head` 与 `check` 已在保留数据的实际演示库上通过。
