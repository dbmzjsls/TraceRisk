# Task 2.1：认证与预置角色

日期：2026-09-11。合成数据，仅供演示。

## 任务契约

| 项目 | 约定 |
|---|---|
| 输入 | JSON 用户名/密码，或 Authorization: Bearer 令牌 |
| 输出 | 30 分钟有效的 JWT，当前用户 ID、用户名及角色 |
| 数据变化 | 迁移 0003 新建 users；初始化命令创建 analyst、reviewer、admin |
| 数据约束 | 用户名唯一、角色 CHECK 约束、UUID 主键、Argon2id 密码哈希 |
| 失败行为 | 凭证无效返回 401，角色不允许返回 403，输入不合法返回 422，签名密钥未配置返回 503 |
| 幂等要求 | 重复初始化保留已有账号 ID、密码和角色 |
| 范围 | 认证及可复用角色依赖；注册、账号管理、令牌刷新及业务接口留在本任务之外 |

## 接口与角色

`POST /auth/login` 接收 `{"username":"analyst","password":"AnalystDemo!2026"}`，返回 `access_token`、`token_type: bearer`、`expires_in: 1800`，响应带 `Cache-Control: no-store`。

`GET /auth/me` 使用 `Authorization: Bearer <access_token>` 返回用户 ID、用户名和角色，不返回密码哈希。

`require_roles(Role.REVIEWER)` 等后端依赖按明确允许的角色判断；admin 没有隐含的角色绕过权限，接口需要同时允许时显式传入多个角色。每次请求查数据库获取当前角色，因此角色变更对已有令牌立即生效。此处只检查身份和角色，后续业务接口仍需执行“不能复核自己创建的调查”等对象级约束。

JWT 固定使用 HS256，校验签名、签发时间、过期时间、主体 UUID、issuer 与 audience，要求五个字段均存在。令牌只携带身份，不携带可被误信任的角色。用户名不存在与密码错误使用同一错误响应，均执行一次哈希验证；异步接口在线程中执行密码验证。

采用 JSON 登录加 HTTP Bearer，满足当前演示账号闭环；没有引入表单解析、OAuth 登录流程或额外账号服务。实现参考 [FastAPI 官方 JWT 与密码哈希示例](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)，锁定 pwdlib 0.3.1 与 PyJWT 2.13.0。

## 本地运行

项目根目录 `.env` 的 `JWT_SECRET` 至少 32 个字符。本次已生成随机本地密钥，未写入实施文档或输出日志，`.env` 由现有忽略规则排除。新环境可按 `.env.example` 注释生成并配置；未配置时认证返回 503，健康检查仍可使用。

在项目根目录执行：

```powershell
docker compose up -d
uv sync --directory apps/api
uv run --directory apps/api alembic upgrade head
uv run --directory apps/api python -m tracerisk.auth.seed
uv run --directory apps/api python -m tracerisk
```

API 默认地址为 `http://127.0.0.1:8000`，可在 `/docs` 查看登录及当前用户接口。

| 演示用户名 | 演示密码 | 角色 |
|---|---|---|
| analyst | AnalystDemo!2026 | analyst |
| reviewer | ReviewerDemo!2026 | reviewer |
| admin | AdminDemo!2026 | admin |

以上为公开的本地演示凭证。初始化采用用户名冲突时不更新策略，在一个事务内提交三个账号；不会在 API 启动时自动重置数据。令牌过期后重新登录；本任务没有主动登出撤销或刷新令牌接口。

## 验证

| 检查 | 结果 |
|---|---|
| 完整 pytest 回归 | 35 passed，75.51 秒 |
| Ruff lint / format | 通过，37 个 Python 文件格式一致 |
| mypy strict | 27 个源文件通过 |
| Alembic upgrade / check | 演示库升级到 0003，无 schema 差异 |
| 演示库 ASGI 冒烟 | 三个账号登录及 /auth/me 均成功，健康检查成功 |
| OpenAPI | /auth/me 声明 HTTP Bearer 安全要求 |

保留一条既有 Starlette/AnyIO BlockingPortal 弃用警告，无失败测试。

专项集成测试覆盖三个账号登录及用户信息、Argon2id 哈希、重复初始化保留修改、未知用户与错误密码、缺失和错误格式凭证、拒绝提交角色字段、错误密钥/算法/过期时间/签发时间/主体/签发者/受众，以及必填 JWT 字段缺失。测试用路由验证角色 403、正确访问 200、未登录 401，以及修改数据库角色后旧令牌立即失去原角色权限。

测试在独立随机 PostgreSQL 数据库运行并清理，不改动演示账号。真实演示库应用迁移并创建三个账号；旧 Spike 的 UUID 输入与表关系沿用阶段 1 契约，尚未开放对应业务 HTTP 写接口。
