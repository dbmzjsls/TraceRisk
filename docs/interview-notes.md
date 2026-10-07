# 面试讲解与证据

## 事务与所有权

API 认证和参数校验后，service 用 `sessions.begin()` 定义事务；调查创建同时写 Investigation、Run、Outbox、审计和事件，保存任务意图。复核和报告锁 Investigation/Run，检查 current Run，拒绝历史版本写入。见 [调查 service](../apps/api/src/tracerisk/investigations/service.py)、[复核 service](../apps/api/src/tracerisk/reviews/service.py)、[报告 service](../apps/api/src/tracerisk/reports/service.py)。

Outbox 调度使用 `FOR UPDATE SKIP LOCKED`，先入队再标记 dispatched。队列和业务事务不是同一原子提交，崩溃可重投，依靠队列锁、Run 执行锁和业务幂等约束兜底，不能宣称 exactly-once 消息投递。见 [Outbox](../apps/api/src/tracerisk/workflows/outbox.py)、[队列](../apps/api/src/tracerisk/workflows/queue.py)、[图执行](../apps/api/src/tracerisk/workflows/spike_graph.py)。

## 重试与人工中断

combined service 监督 Outbox 调度、Procrastinate 消费和健康；健康要求调度与队列分别新鲜。临时失败可重试，索引不就绪、检索完整性破坏等永久失败分开处理。节点业务写入有完成标记和唯一约束，重跑检查已有结果。见 [worker](../apps/api/src/tracerisk/workflows/worker.py)、[supervisor](../apps/api/src/tracerisk/workflows/service.py)、[恢复验收](evaluation/e2e-results.json)。

LangGraph 使用 PostgreSQL checkpoint、`thread_id=run_id` 和同步持久化。durable interrupt 落盘后才发布 `awaiting_review`。所有复核完成后事务写 resume Outbox；恢复使用 `Command(resume=review_id)`，已有 checkpoint 时继续状态。人工等待不占用持续阻塞的 HTTP 请求。见 [图与中断](../apps/api/src/tracerisk/workflows/spike_graph.py)、[状态机](design/workflow-state-machine.md)。

## 规则、RAG 与引用

工商、司法、新闻、知识产权规则处理基准日期、去重和冲突，评分仅来自规则。证据不足和零分是两个维度。见 [规则规范](design/rule-specification.md)、[规则引擎](../apps/api/src/tracerisk/rules/engine.py)。

检索按风险类型融合关键词与向量候选，索引版本检查 provider/model/dimension 等契约。结果冻结到 Run，生成、复核、报告读取冻结集合，不重新使用当前索引。保留 BGE 和临时 Fake 索引分开，Fake 仅验证管线。见 [混合检索](../apps/api/src/tracerisk/retrieval/hybrid.py)、[冻结服务](../apps/api/src/tracerisk/evidence/service.py)。

模型输出先过 Pydantic，再检查 evidence ID、chunk ID、content hash、连续摘录、当前 Run 检索归属和主张支持。结构正确不代表事实正确；非法引用不能保存为可信发现。见 [校验器](../apps/api/src/tracerisk/ai/validation.py)、[AI service](../apps/api/src/tracerisk/ai/service.py)、[引用评测](evaluation/results.json)。真实模型 `not_run`，不宣称真实模型准确率。

## 人工替换与不可变报告

修改携带严格完整 draft 和 finding version；服务端检查资格、禁止自审、current Run 和冻结引用。人工版本、ReviewDecision、审计和恢复屏障同一事务保存。原 AI 不覆盖，最终 finding 记录 `source=human`、原 AI ID 与 human version。数据库约束拒绝 AI/human UPDATE/DELETE。见 [复核 contract](../apps/api/src/tracerisk/reviews/schemas.py)、[保存实现](../apps/api/src/tracerisk/reviews/service.py)、[迁移](../apps/api/migrations/versions/0012_human_finding_versions.py)。

报告保存 JSON 快照和 HTML，记录规则、证据、原 AI 与人工决定。已完成 Run 重复生成返回旧报告；相同复核键与内容返回旧决定，换内容复用键拒绝；全部发现处理完才恢复。见 [报告生成](../apps/api/src/tracerisk/reports/service.py)、[人工来源与字节稳定性验收](evaluation/e2e-results.json)。幂等性由具体业务约束完成，不仅依赖前端禁用按钮。

## SSE 与前端

事件和业务状态同事务保存，序列按 Run 增长。服务端读取 high-water 快照，排空 `Last-Event-ID` 之后到水位的历史，发送 `stream.ready`，随后继续读取持久事件。流不用长事务。前端 fetch 流支持认证 header，按序去重、重连和失败轮询。见 [SSE](../apps/api/src/tracerisk/workflows/sse.py)、[事件](../apps/api/src/tracerisk/workflows/events.py)、[前端连接](../apps/web/src/features/investigations/useInvestigationEvents.ts)、[Stage 5 验证](evaluation/stage-5-validation.json)。

## 交付边界与取舍

PostgreSQL 同时承载业务、队列、checkpoint 与 pgvector，一个 combined worker 降低演示运维复杂度，但需要共同健康检查和明确初始化顺序。没有额外 broker、Redis 或对象存储。见 [架构](design/system-architecture.md)、[Compose](../compose.yaml)。

[CI](../.github/workflows/ci.yml) 锁定依赖并使用 Mock/Fake，执行后端测试/静态检查和前端契约/构建；hosted backend / frontend 已通过，后端覆盖 410 项测试。本地两次 8/8 E2E 证据保留，另从远端提交完成独立 fresh-clone Docker 四服务启动和五分钟人工复核演练；资源隔离、受控清理与提交链接见[验收入口](acceptance.md)，历史阶段记录不覆盖该状态。

生产工作仍包括真实数据授权与质量、真实模型独立评测、容量/成本测量、部署和备份演练，均未作为现有能力交付。保留数据只读，临时目标由生成名/所有权记录清理，密钥和认证日志不进入交付。见 [lifecycle](../scripts/database_lifecycle.py)和[验收入口](acceptance.md)。
