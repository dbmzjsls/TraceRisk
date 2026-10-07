# Task 2.4 本地运行与浏览器验收 Runbook

本 Runbook 仅用于本地合成演示。不要把账户密码、JWT 或 `.env` 内容写进终端记录、截图或提交物。

## 前置条件

- PostgreSQL 和既有演示数据可用；先保留数据，不重置数据库。
- 先运行 `uv run --directory apps/api alembic upgrade head` 和 `uv run --directory apps/api alembic check`。Procrastinate 的 `apply_schema_async` 不是对既有 schema 的幂等操作，重复应用会碰到已有状态类型；在确认相关表存在后不要再次调用它。
- Node `v24.18.0`、pnpm `11.13.1`、uv `0.10.8`，以及可供 Playwright 使用的 Chrome。验收脚本的本次实测 Chrome 是 `152.0.7977.84`。
- 通过环境变量配置本地演示 analyst/reviewer 凭据；脚本支持 `DEMO_ANALYST_PASSWORD`、`DEMO_REVIEWER_PASSWORD`，不需要也不应打印其值。

## 启动本地服务

在启动服务前，从仓库根目录运行下列幂等 seed 命令：

```powershell
uv run --directory apps/api python -m tracerisk.auth.seed
uv run --project apps/api python scripts/seed_synthetic.py
```

第一个命令初始化演示账户并保留既有账户。第二个命令初始化合成司法故事并保留既有记录，同时校验两宗未结案件、一次 35 分规则命中和三条 Chunk-ready Evidence。

在四个独立终端从仓库根目录运行下列命令：

```powershell
uv run --directory apps/api python -m tracerisk
uv run --directory apps/api python -m tracerisk.workflows.outbox
uv run --directory apps/api python -m tracerisk.workflows.worker
pnpm --dir apps/web dev
```

API 默认在 `http://localhost:8000`，Vite 默认在 `http://localhost:3200`，前端通过 `/api` 代理访问 API。dispatcher 是持续扫描的本地进程，约每两秒扫描一次 Outbox；worker 是独立进程。仅停止本次验收明确启动的进程，不要终止其他人的服务。

## 执行浏览器闭环

先确保 `.superpowers/sdd/task-2.4-resume-ready` 不存在，再执行：

```powershell
$env:PLAYWRIGHT_MODULE = 'C:/Users/jsls/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'
node scripts/acceptance-task-2.4.cjs
```

`WEB_URL` 可改为非默认的本地 Vite 地址。脚本以真实 API 创建 `2026-09-11` 的调查，并在首次进入真实 `awaiting_review` 状态时等待 gate 文件。脚本输出 `AWAITING_REVIEW` 后：

1. 停止旧 worker，并确认它已经退出。
2. 以相同 worker 命令启动一个全新 worker。
3. 创建 `.superpowers/sdd/task-2.4-resume-ready`，仅写入旧/新 worker 的非敏感进程证据。

脚本随后以 reviewer 继续真实批准、checkpoint 恢复和报告读取，并把 JSON 结果和三张截图写入 `docs/evaluation/`。它始终关闭 Chrome。正常路径始终走真实 API；只有明确标注的负向 UI 场景使用受控传输拦截。

## 完成后的检查

```powershell
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
pnpm --dir apps/web api:check
pnpm --dir apps/web test:auth
pnpm --dir apps/web test:business
uv run --directory apps/api ruff check .
uv run --directory apps/api ruff format --check .
uv run --directory apps/api mypy src
uv run --directory apps/api pytest -q -p no:cacheprovider
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api alembic check
node --check scripts/acceptance-task-2.4.cjs
```

成功标准是：浏览器 JSON 的 `phase` 为 `passed`、15 项检查通过、`apiFailures` 和 `browserErrors` 为空；报告仍显示真实规则分 35、一条原始 AI 结论、两条引用和人工决定；终态不继续轮询。390 px 复核还应确认自动收起侧栏后正文可读且没有横向溢出。
