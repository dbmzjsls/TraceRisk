# 简历条目与证据

以下描述本地实现和固定合成样本；没有真实用户量、生产流量、真实模型准确率、延迟或成本数字。

- 基于 FastAPI、PostgreSQL、Procrastinate 与 LangGraph 实现风险调查，将调查/Outbox/事件原子保存，支持持久化中断、人工复核与恢复；两次独立本地 E2E 均通过 8/8 路径。[证据：工作流和验收](evaluation/e2e-results.json)
- 实现确定性规则、按风险类型混合检索、Run 级证据冻结与引用/结构化输出校验；固定 Mock/Fake 样本规则精确匹配 13/13、宏平均 Recall@5 为 16.0/16、持久引用合法 2/2、最终证据覆盖 1/1、完整 Mock 输出成功 2/2；Fake 仅验证管线，真实模型未运行。[证据：公式、样本与模式](evaluation/results.json)
- 实现复核幂等性、独立人工替换版本和不可变报告，保留原 AI、决定与引用来源，验证重复复核不重复恢复或生成报告及报告字节稳定性。[证据：人工修改与重放](evaluation/e2e-results.json)
- 完成 Vue 3 调查、复核与报告界面，以持久 SSE 高水位和 Last-Event-ID 实现恢复、去重与失败轮询，支持引用定位和人工替换稿编辑。[证据：Stage 5](evaluation/stage-5-validation.json)、[当前 E2E](evaluation/e2e-results.json)
- 编写锁定 Mock/Fake 的 CI 配置与有所有权约束的清理工具；hosted CI 的 backend / frontend 均通过，后端覆盖 410 项测试，并在独立 fresh-clone Docker 栈完成四服务健康与五分钟全流程演练。[证据：当前验收入口](acceptance.md)
