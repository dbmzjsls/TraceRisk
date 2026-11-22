# 调查助手访问说明

公网入口待提供。当前验证范围为本机运行和取证。正式 HTTPS 地址、评审账号、可用期和维护联系人待部署后核验并登记，评审密码由提交系统单独提供。

## 比赛示例页

[本地示例页](http://127.0.0.1:8080/demo)展示西安银行股份有限公司的一审判决、二审判决与执行立案三份公开公告。每张卡保留披露日、摘录页码和巨潮资讯公告原文链接。登录后可另行打开调查助手及演示库。

[1600×900 示例页截图](ppt-materials/screenshots/competition-demo.png) · [公开公告摘录截图](ppt-materials/screenshots/competition-demo-evidence.png)

部署后可用同域的 `/demo` 浏览公开案例，`/assistant` 进入调查工作台。历史演示问答及运行快照见 [三组问答存档](conversations/README.md)。

## 登录后的公开资料案例

工作台使用独立的 `tracerisk_public_demo` 数据库。案例是苏州银行股份有限公司，基准日为 2026-06-25。身份与统一社会信用代码来自[苏州银行官网披露](https://www.suzhoubank.com/suzhoubank/attachDir/2025/10/2025103108321148794.pdf)，调查使用工作区文件包中已核对哈希的[诉讼事项进展公告 2026-026](https://static.cninfo.com.cn/finalpage/2026-06-25/1225386155.PDF#page=1)。只录入该公告中当事人地位和当时的一审进展，其他领域按资料缺口展示。

分析员从企业详情设置基准日并创建调查。复核员打开待复核发现并定位公告摘录。完成复核后，可在调查详情打开报告，或通过「在调查助手中提问」继续核对同次运行的引用。

## 本地运行

首次运行需创建空的 `tracerisk_public_demo`，定向执行 Alembic 迁移、`python -m tracerisk.workflows.queue`、`python -m tracerisk.workflows.spike_graph` 和 `python -m tracerisk.auth.seed`。使用项目 API 虚拟环境和可用的 BGE 模型，设置 `POSTGRES_DB=tracerisk_public_demo`、`EMBEDDING_PROVIDER=bge` 与 `EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5` 后，从仓库根目录运行 `python -m scripts.seed_public_demo`。脚本验证目标库并在同一事务内校对身份、公告记录和 BGE 索引。

准备本地数据库、签名和模型环境变量后，可切到公开资料工作台：

```powershell
docker compose -f compose.yaml -f compose.competition.yaml -f compose.public-demo.yaml up -d --build --wait api worker web
```

网页运行于 [本地工作台](http://127.0.0.1:8080)。公开案例页和鉴权后的工作台都在该入口。历史测试库与素材保留原有来源标识。
