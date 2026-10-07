# 当前验收入口

更新日期：2026-10-07（Asia/Shanghai）

本文是 TraceRisk 当前发布与求职交付状态的唯一入口。状态只使用 `passed`、`failed`、`pending`、`not_run`；历史阶段记录不覆盖这里的当前结论。

| 验收项 | 状态 | 当前证据 |
|---|---|---|
| Git 发布基线 | `pending` | 正在整理首个可发布提交。 |
| Hosted GitHub Actions | `pending` | 将对首个发布提交运行 backend / frontend 两个 job。 |
| 全新 Docker 环境 | `pending` | 将从远端提交的全新 clone、独立镜像、独立 Compose project 和独立 volume 验收。 |
| 五分钟脚本演练 | `pending` | 将在上述全新 Docker 环境按[五分钟演示](demo-script.md)演练。 |
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

Git、hosted CI、全新 Docker 和五分钟演练只有在命令成功、资源隔离得到核对、临时资源完成受控清理后才会改为 `passed`。
