# 当前验收入口

更新日期：2026-10-08（Asia/Shanghai）

本文是 TraceRisk 当前发布与求职交付状态的唯一入口。状态只使用 `passed`、`failed`、`pending`、`not_run`；历史阶段记录不覆盖这里的当前结论。

| 验收项 | 状态 | 当前证据 |
|---|---|---|
| Git 发布基线 | `passed` | 私有远端 `dbmzjsls/TraceRisk` 已建立；发布基线始于提交 [`a15e2c4`](https://github.com/dbmzjsls/TraceRisk/commit/a15e2c459972946c8bbe6caa1313869a7975cce8)，忽略规则、发布文档和稳定 Windows API 入口已纳入版本控制。 |
| Hosted GitHub Actions | `passed` | 提交 [`2b2b6ca`](https://github.com/dbmzjsls/TraceRisk/commit/2b2b6cad0377dc6382e6aebcdfb0a24b63ac00a3) 的 [backend / frontend 两个 job](https://github.com/dbmzjsls/TraceRisk/actions/runs/37650916546) 均通过；后端 410 项测试通过。 |
| 全新 Docker 环境 | `passed` | 从远端提交 `bfc7c4b` 全新 clone，无缓存构建独立镜像，使用独立 Compose project / volume 启动 PostgreSQL、API、worker、Web；四服务健康且 API、Web、Web→API 代理均返回 200。 |
| 五分钟脚本演练 | `passed` | 在上述全新 Docker 栈按[五分钟演示](demo-script.md)完成创建、95 分等待复核、人工修改、其余通过、恢复和不可变报告核对；自动化计时 8 秒。 |
| 固定 Mock/Fake 评测 | `passed` | [机器结果](evaluation/results.json)、[人读结果](evaluation/results.md)；仅验证合成管线。 |
| 两次本地八路径 E2E | `passed` | [E2E 汇总](evaluation/e2e-results.json)；每次 8/8。 |
| 真实模型质量、成本与延迟 | `not_run` | 没有真实模型调用或相关结论。 |
| 真实外部数据与生产部署 | `not_run` | 没有生产流量、真实数据授权、容量、备份恢复或生产部署结论。 |

## 当前验收入口

- 首次或每日 Docker 操作：[Compose 指南](operations/docker-compose.md)
- 现场路径与截图备用：[五分钟演示](demo-script.md)
- 技术讲解：[架构](design/system-architecture.md)、[面试证据](interview-notes.md)
- 求职表述：[简历条目](resume-bullets.md)
- 自动化门禁：[GitHub Actions 工作流](../.github/workflows/ci.yml)

## 本轮隔离与清理结论

- Fresh Compose project 为 `traceriskfreshbfc7c4b`，数据卷为 `traceriskfreshbfc7c4b_postgres_data`；演示库仅含 `tracerisk_demo`，保留库检查为不存在。
- 容器内 API / worker UID 为 `10001`，Web UID 为 `101`；演练生成一份 95 分报告，人工替换版本 1 与原始 AI 快照同时可见。
- 演练后删除了该 project 的四个容器、网络、独立数据卷与两个临时镜像标签；资源计数均为 0。全新 clone 已移入回收站，可恢复。
- 原有 `tracerisk` 四容器 ID、健康状态、`tracerisk_postgres_data` 创建时间（2026-09-10）和共享 `stage6` 镜像 ID 在前后核对中保持不变。
