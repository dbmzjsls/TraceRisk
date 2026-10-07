# 开源底座与引入边界

本文落实《TraceRisk v3 实施计划》第 4 节与 Task 0.1。项目使用合成数据，仅供演示。

## 本任务契约

| 项目 | 约定 |
|---|---|
| 目标 | 固定第三方来源及允许使用的范围 |
| 输入 | 根目录实施计划、公开仓库许可证与配置、BGE 模型资料 |
| 输出 / 允许修改文件 | `THIRD_PARTY_NOTICES.md`、本文；完成后更新原计划 Task 0.1 勾选状态 |
| 状态变化 / 数据库约束 | 仅文档状态变化；没有业务状态、数据库或迁移 |
| 失败行为 | 无法核验的来源明确记录，不擅自更换 commit 或宣称已引入 |
| 幂等要求 | 后续更新同一来源条目，不重复追加矛盾记录 |
| 独立验收样例 | 可从来源记录定位固定前端版本、BGE 维度和许可证，区分计划与实际使用 |
| 范围 | 不安装依赖、不创建应用、不下载权重、不执行 Git 提交 |

## 选择与取舍

前端采用计划选定的轻量模板，以复用布局和导航；后端采用更简单的最小工程，仅参考已有实践，避免先引入再裁剪完整业务框架。两者的来源策略不同，但均以 TraceRisk 的 MVP 范围为准。

前端固定来源为 [vue-naive-admin](https://github.com/zclzone/vue-naive-admin.git)，计划分支 `2.x`，commit `a55f0a2e6fe0561ce62c3452b96ec4b4cbcb8553`，MIT 许可证。第三方归属详见 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。

## Task 2.4 前端裁剪清单

Task 2.4 已执行最小源文件提取：16 个固定 commit 源文件下载并检查，完整 MIT 原文保留；14 个源文件的结构或配置适配、1 个 reset 仅检查未提取。原始 SHA-256、缓存路径、本地对应和逐项修改见 [upstream-manifest.json](../../apps/web/upstream-manifest.json)。适配文件不声称原样保留。示例原文件采用未提取策略，而非复制后删除；登录和 shell 内的无关例子已从适配结构删除。业务页面已接入真实本地 API，并在 Task 2.4 浏览器闭环中使用；结果见 [验收记录](../evaluation/task-2.4.md)。

| 处理 | 内容 |
|---|---|
| 保留 | Vue 3、Vite、Naive UI、Pinia、Vue Router、UnoCSS |
| 保留 | 基础布局、导航、登录页外壳和主题 |
| 保留并适配 | 错误页、lint 与构建配置 |
| 删除 | 示例业务页面和 Mock API |
| 删除 | 动态权限配置后台 |
| 删除 | 无关菜单和演示组件 |
| 删除 | 模板自带业务接口 |
| 删除 | 与 TraceRisk 无关的状态和权限封装 |
| 替换 | 服务端状态统一交给 TanStack Vue Query |
| 替换 | Pinia 只保存登录用户、Token 和角色 |
| 替换 | API 类型从 FastAPI OpenAPI 生成 |
| 添加 | 必要的运行时响应校验使用 Zod |
| 补齐 | TypeScript、类型检查及计划要求的验证脚本 |

核验差异：固定 commit 的 `package.json` 没有声明 TypeScript 或 `vue-tsc`，也没有 `typecheck`、`lint` 脚本，仅提供 `lint:fix`。Task 2.4 Task 2 已本地补齐 TypeScript、vue-tsc、ESLint 和所需脚本，没有更换来源版本。配置能力属于本地适配新增；公开验收记录见 [Task 2.4](../evaluation/task-2.4.md)。

## 后端参考边界

允许参考的仓库：

- [fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)：工程组织、配置、依赖和异常。
- [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)：配置、数据库会话、迁移和测试。
- [FastAPI-boilerplate](https://github.com/benavlabs/FastAPI-boilerplate)：异步数据库会话、迁移、异常和测试。

后端仍采用 FastAPI、Pydantic v2、SQLAlchemy 2、Alembic 和 PostgreSQL 的模块化单体。任务派发按实施计划使用 Transactional Outbox 与 Procrastinate；工作流按计划使用 LangGraph。参考项目中的 OAuth、SQLAdmin、Redis、Taskiq、邮件、支付、SaaS 插件系统和通用 CRUD 框架不在引入范围。

## 模型边界

固定使用 `BAAI/bge-small-zh-v1.5`，512 维，cosine 距离，CPU 执行；模型卡声明 MIT。该模型负责检索 embedding，不能代替生成模型。生成模型保持计划中的 Mock 和 OpenAI-compatible 两个适配器范围。

单元测试不下载模型，使用 Fake Embedding。阶段 4 才下载真实权重并建立索引版本记录；当前不存在真实检索质量或性能指标。

## Task 0.1 验收

在项目根目录执行计划原定检查。为避免本文记录命令时自匹配，下方以等价的字符组正则展示，扫描范围和检查意图保持一致：

```powershell
rg -n 'T[B]D|T[O]DO|D[i]fy.*底座|R[A]GFlow.*底座|A[l]eph.*底座' THIRD_PARTY_NOTICES.md docs/references
```

预期没有匹配，退出码 1 表示通过，退出码 2 表示执行错误。人工核对固定 commit、许可来源、裁剪清单、引用范围和实际引入状态。
