# Task 1.1：最小后端与 PostgreSQL 验证记录

日期：2026-09-10。合成数据，仅供演示。

后续补验（Task 1.2）：用户启动 Docker Desktop 后，PostgreSQL healthy，在线升级、重复升级、current 与 Alembic check 均通过；异步会话在真实数据库集成测试中通过验证。原始环境失败记录保留如下，当前已解除。最新迁移为 0001，详见 [Task 1.2](task-1.2.md)。

## 任务契约

| 项目 | 约定 |
|---|---|
| 目标 | 初始化 Python 3.12 FastAPI 工程、数据库会话、空迁移和 PostgreSQL Compose 服务 |
| 输入 | 实施计划 Task 1.1、阶段 0 领域与状态契约 |
| 输出 | `apps/api` 工程、`uv.lock`、`compose.yaml`、`.env.example`、`.gitignore` 与本记录 |
| 允许修改 | 上述文件及实施计划 Task 1.1 完成状态 |
| 状态变化 | 不创建业务 Run，不实现业务状态转换 |
| 数据库约束 | 空基线 `0000` 不创建业务表，仅由 Alembic 管理迁移版本 |
| 失败行为 | 配置无效则启动失败；数据库连接/迁移错误向调用方传播，不伪报数据库就绪 |
| 幂等要求 | `alembic upgrade head` 应可重复执行；Compose 复用本项目命名卷 |
| 独立验收 | 无数据库时 `/health` 返回 200 和 `{"status":"ok"}`；四项计划检查通过 |
| 范围 | 不实现 Outbox、队列、LangGraph、认证、业务表、前端或 pgvector 索引 |

## 已实现

使用 `uv init` 创建 `tracerisk` 包，Python 范围为 `>=3.12,<3.13`，锁定依赖至 `uv.lock`。配置使用 Pydantic Settings，读取仓库根目录 `.env`，环境变量优先。SQLAlchemy URL 通过字段构造，密码采用 SecretStr，避免配置对象直接显示密码；模板中的密码仅为公开的本地演示值。

API 生命周期创建异步 engine 和 session factory，关闭时 dispose。会话依赖关闭时回滚未提交工作，业务服务负责显式事务提交，便于后续 Investigation、Run 和 Outbox 在同一事务保存。

`/health` 仅表示进程存活，不查询数据库。数据库就绪由 PostgreSQL 服务的 `pg_isready` 健康检查判断。这个划分让健康接口单元测试无需启动数据库，不会把进程存活误写成数据库可用。

迁移 CLI 使用同步 psycopg 连接，API 使用异步 psycopg，二者没有在同一调用链混用阻塞操作。Windows 启动入口显式使用 SelectorEventLoop，满足 psycopg 异步连接要求；其他平台沿用默认循环。启动入口为 `python -m tracerisk`。

Compose 使用 PostgreSQL 17，仅绑定 `127.0.0.1`，数据保存在项目命名卷中；本阶段没有 Web/API/worker 容器，这些留到计划后续任务。

实现参考：[SQLAlchemy psycopg 支持](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg)、[Alembic 迁移文档](https://alembic.sqlalchemy.org/en/latest/cookbook.html)、[uv 项目初始化](https://docs.astral.sh/uv/concepts/projects/init/)。未复制前述 FastAPI 参考仓库代码。

## 本地运行

在仓库根目录执行。默认值已与 Compose 对齐，无需复制 `.env` 即可运行本地演示；需要改端口或账号时，以 `.env.example` 为模板创建根目录 `.env`。容器内 API 接入将在后续阶段配置 `POSTGRES_HOST=postgres`。

```powershell
uv sync --directory apps/api --locked
docker compose up -d --wait --wait-timeout 45 postgres
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api python -m tracerisk
```

启动后访问 `http://127.0.0.1:8000/health` 或 `http://127.0.0.1:8000/docs`。空迁移之后的数据库没有企业和用户表。

## 实际检查结果

| 命令或检查 | 结果 |
|---|---|
| `uv run --directory apps/api pytest tests/test_health.py -v` | 1 passed；配置数据库端口为 1，验证接口不依赖连接成功 |
| `uv run --directory apps/api ruff check .` | 通过 |
| `uv run --directory apps/api ruff format --check .` | 10 个文件格式通过 |
| `uv run --directory apps/api mypy src` | 7 个源码文件严格类型检查通过 |
| `docker compose config --quiet` | 通过；与计划的 config 相同配置校验，避免输出解析后的密码 |
| `uv run --directory apps/api alembic heads` | 单一 head：`0000` |
| `uv run --directory apps/api alembic upgrade head --sql` | 通过；仅版本表与基线登记 SQL，没有业务表 |
| 临时启动 `python -m tracerisk` 后 HTTP GET `/health` | 200 和预期 JSON，测试进程已关闭 |
| `docker compose up -d --wait --wait-timeout 45 postgres` | 失败：本机 `dockerDesktopLinuxEngine` 命名管道不存在 |

测试有两条依赖弃用警告：Starlette 的 TestClient 建议迁移至 httpx2，以及 AnyIO BlockingPortal 别名弃用。当前不影响通过，未屏蔽警告，也未为消除警告额外增加依赖。

本机使用 CPython 3.12.8。uv 缓存及 Docker 配置读取最初受沙箱限制，后续经工具授权执行；Docker Desktop 启动已尝试，但引擎仍不可连接。该失败是运行环境问题，不能计作数据库验收通过。

## 剩余验证及复验方法

真实数据库上的迁移升级、重复升级、回退及异步 SQL 查询未执行，因为 PostgreSQL 容器无法启动。Docker 引擎恢复后，先确认本项目数据库仍为空基线，再执行：

```powershell
docker compose up -d --wait --wait-timeout 45 postgres
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api alembic current
uv run --directory apps/api alembic check
docker compose ps
```

预期两次升级均成功、current 为 `0000`、自动比较无业务模型差异、postgres 为 healthy。回退和异步连接检查应在本项目测试数据库中补做；后续阶段增加迁移后，不再把 `0000` 当作最新版本。

## 安全与范围检查

新增文件不含个人身份信息、真实密钥或业务数据；uv 自动生成的作者信息已从工程元数据移除。`.env`、虚拟环境、缓存及日志已加入忽略规则，保留公开 `.env.example`。未调用生成模型，未向外部服务发送证据，未删除已有文件或卷，未执行 Git 提交。

Task 1.1 的实现与计划指定检查已完成；真实 PostgreSQL 运行验证仍受上述环境限制。Task 1.2 尚未开始。
