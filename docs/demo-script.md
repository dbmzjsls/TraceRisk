# 五分钟合成演示

使用 [README](../README.md) 的 Mock/Fake 本地或 Docker 拓扑；当前运行结论见[验收入口](acceptance.md)。全部数据为合成数据。演示前检查 API 和 combined worker 健康，在 `/companies` 列表选择信用代码为 `SYNTHETIC-COMPANY-003` 的“远景精工（合成）有限公司”（稳定场景 `all-rules-95`；列表较长时可用浏览器页面查找），基准日期固定为 `2026-09-11`。使用独立演示库，不修改保留的 `tracerisk`。

| 时间 | 操作、可见状态和讲解 |
|---|---|
| 0:00–0:35 | 展示全局“合成数据，仅供演示”。说明规则、证据、AI、人工和报告分开记录，规则负责评分。 |
| 0:35–1:15 | 用 `analyst` 打开 `/companies/{company_id}`。展示信用代码和基准日期表单，发起调查，跳转 `/investigations/{investigation_id}`。 |
| 1:15–2:10 | 等待 `awaiting_review`。左栏出现“等待人工复核”，右栏显示规则、原始 AI、覆盖与冲突、检索证据。点击引用定位冻结证据；缺证据和零分不等于安全。 |
| 2:10–3:15 | 退出后用 `reviewer` 进入 `/reviews`，选择该调查的待复核发现。`/reviews/{finding_id}` 展示原始 AI。选择“修改（modify）”，改标题/摘要/推理并填写理由。结构与引用只读，提交保存独立人工版本。通过剩余发现；创建者不可自审。 |
| 3:15–4:15 | 回到调查，等待 `completed`。时间线显示“复核完成，等待恢复执行”和“调查完成，风险报告已生成”。进入不可变报告：最终发现含人工来源，原 AI 和人工决定仍可追溯，规则分数保持 95。 |
| 4:15–5:00 | 打开架构图和评测。解释 Outbox、持久 checkpoint、幂等复核/报告和 SSE 恢复。五项分子/分母来自固定 Mock/Fake 样本，真实模型未运行。 |

现场备用：先在活跃的 `tracerisk_demo` 完成一次调查并保留 URL；若已有完成记录，可从 `/investigations` 列表打开。若运行环境不可用，展示下面的已完成合成截图与证据，不对保留数据库补种。

- [企业详情](images/company-detail.png)：合成公司与发起调查入口。
- [调查时间线](images/investigation-timeline.png)：真实 worker 到达等待复核。
- [复核页面](images/review-detail.png)：原始 AI 与人工替换稿。
- [风险报告](images/risk-report.png)：人工最终结果及不可变快照。

四张备用截图来自一次已清理临时目标的真实页面捕获；动态 URL 不作为发布文件保留。稳定入口是场景与信用代码。

验收还覆盖低风险直出、驳回、冲突强制复核、缺数据零分、非法引用拒绝和重复复核/报告稳定性。[E2E 结果](evaluation/e2e-results.json) 保存两次独立验收；本次截图不替代历史 E2E 的浏览器证据。

2026-10-08 已在远端提交 `bfc7c4b` 的 fresh-clone Docker 栈完成一次全流程演练：规则分数 95，首条发现保存人工替换版本 1，其余发现通过，最终报告同时保留人工来源和原始 AI 快照；自动化计时 8 秒。环境隔离、清理及 hosted CI 结果见[当前验收入口](acceptance.md)。
