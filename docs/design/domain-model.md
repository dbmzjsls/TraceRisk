# 领域模型

依据：[TraceRisk v3 实施计划](../../TraceRisk-v3-实施计划.md)第 6～16 节。日期：2026-09-10。

状态：Task 0.2 规格。D1、D2、D3 已于 2026-09-10 经用户明确确认，以下为合并后的业务契约。

## Task 0.2 契约

| 项目 | 内容 |
|---|---|
| 目标 | 固化领域职责、运行状态、证据引用和四条规则的口径 |
| 输入 | 根目录实施计划、Task 0.1 来源文档、用户对歧义的答复 |
| 输出及允许修改文件 | 本文、`workflow-state-machine.md`、`evidence-contract.md`、`rule-specification.md`，以及原计划的任务进度 |
| 状态变化 | 仅文档；不创建数据库或修改业务状态 |
| 数据库约束 | 在本文登记计划中的唯一约束，迁移留到后续任务 |
| 失败行为 | 未确定的业务语义列为未决项，不以自拟测试替代用户决定 |
| 幂等要求 | 修订已有规格；不生成相互冲突的多份领域定义 |
| 独立验收 | 状态机文档中的五类场景都有唯一结果，未决项关闭后才能宣告冻结 |
| 禁止扩张 | 不实现应用、不引入多租户、规则 DSL、任务取消或多人会签；不提交 Git |
| 验证 | 人工核对计划、五场景走查；PowerShell 验证相对链接；`rg -n 'T[B]D|T[O]DO' docs/design` 无匹配 |

## 术语与职责

| 术语 | 业务含义与边界 |
|---|---|
| User / Role | 三个预置演示账号及分析员、复核员、管理员角色；无注册和完整账号管理 |
| Company | 企业身份，以企业 ID 区分；相同名称不表示同一企业 |
| CompanyAlias | 企业历史名称及同名干扰信息，不能绕过企业身份边界 |
| DataSource | 工商、司法、新闻、知识产权来源分类 |
| SourceCoverage | 某企业在调查基准日的来源可用性；无记录与来源不可用是不同事实 |
| Evidence | 规则与报告可引用的来源证据 |
| EvidenceChunk | 供检索与 AI 引用的证据片段，归属于一条 Evidence |
| Investigation | 一次调查业务容器，记录企业、发起人、基准日以及当前运行 |
| WorkflowRun | 该调查的一次执行，承载状态和本次结果；自动重试不是新运行 |
| WorkflowStep | 一个运行中节点的执行记录，重试增加尝试次数 |
| WorkflowEvent | 某运行的持久化进度事件，供时间线和 SSE 读取 |
| DispatchOutbox | 已提交业务需要执行的派发意图，衔接业务事务和后台队列 |
| RuleFinding | 本次执行的确定性规则命中快照，是风险分数来源 |
| RetrievedEvidence | 本次运行实际提供给模型的片段集合，是引用范围边界 |
| AIFinding | 结构化 AI 原始结论；人工操作不能覆盖它 |
| ModelInvocation | 一次模型调用尝试，记录模型、提示词版本、耗时、用量和错误 |
| ReviewDecision | 复核员针对某版本结论作出的批准、修改或驳回决定；零结论强制复核时记录运行级决定 |
| RiskReport | 本次运行最终不可变报告快照 |
| AuditEvent | 创建调查、重新调查、失败、复核和报告等重要业务操作记录 |
| IdempotencyRequest | 用户写请求的幂等记录，用于避免重复业务副作用 |

Task 2.2 将司法案件状态明确为结构化 Evidence：`fact_key` 表示案件号，
`fact_value` 表示该时点的规范化状态。规则按调查基准日读取每个案件的最新状态；
同案号重复证据属于同一事实，多条不同的未结案件仍只形成一次规则命中。

## 调查、运行与结果的关系

`Investigation` 保存 `company_id`、`created_by`、`as_of_date`、`current_run_id`。不再保存一份细粒度状态，界面状态取自当前 `WorkflowRun`。同一调查有多个历史运行，当前运行必须属于该调查。

创建调查同时创建 Run #1。队列自动重试和节点重试均沿用原 Run；用户主动重新调查才创建下一 Run，并使用新的 `thread_id=run_id`。旧 Run、规则快照、AI 原始输出、复核记录和报告均保留。

每个 Run 分别关联节点、事件、Outbox、规则命中、检索集合、AI 结论、模型调用、复核决定和至多一份报告。不得把新 Run 的证据或复核决定套用到旧 Run。

业务状态以 PostgreSQL 为准；LangGraph checkpoint 提供执行恢复上下文，队列状态不替代业务状态。无需为 Investigation 维护第二套同步状态，这是最小且更容易保持一致的设计。

## 数据约束

以下直接来自计划，后续迁移必须落实：

```text
UNIQUE rule_findings(run_id, rule_code)
UNIQUE retrieved_evidence(run_id, risk_type, chunk_id)
UNIQUE ai_findings(run_id, finding_key)
UNIQUE review_decisions(run_id, ai_finding_id)
UNIQUE risk_reports(run_id)
UNIQUE dispatch_outbox(run_id, operation)
UNIQUE workflow_events(run_id, sequence_number)
UNIQUE idempotency_requests(user_id, idempotency_key)
```

D1 补充：运行级决定使用空 `ai_finding_id` / `finding_version`，单独建立 `UNIQUE(run_id) WHERE ai_finding_id IS NULL`；普通结论决定要求两字段均非空。现有 `(run_id, ai_finding_id)` 唯一约束保留。只有该 Run 没有 AIFinding 且强制复核时才允许运行级决定，不能用它绕过多条结论逐条复核。

唯一约束之外还需校验归属：当前 Run 属于调查，复核的 AIFinding 属于该 Run，Chunk 属于 Evidence，Evidence 属于调查企业。引用细则见 [证据契约](evidence-contract.md)。

所有业务状态变化采用带旧状态条件的更新。影响行数不是 1 时返回冲突，并回滚本次伴随写入，不继续产生副作用。事务及恢复细则见 [状态机](workflow-state-machine.md)。

## 人工与最终结果

结论级复核绑定 `run_id`、`ai_finding_id`、`finding_version`、`reviewer_id`、`idempotency_key`、`decision`、`reason`。提交时必须为当前有效 Run、状态为 `awaiting_review`、版本一致且尚无该结论的有效决定。运行级决定沿用用户、状态、幂等和理由校验，不存在的结论 ID 与版本不得伪造。分析员不能复核自己创建的调查；后端实施角色约束，前端隐藏按钮不构成授权。

批准采用 AI 结论；修改保存独立的人工结论并重新校验引用；驳回保留原始 AI 输出与理由，最终风险结论不纳入被驳回项。任何复核操作均不重新调用模型、不改变规则分数。报告区分规则、原始 AI、人工决定和最终结论，无复核的直接报告路径不能伪造人工复核记录。

## 已确认的补充决策

| 编号 | 原计划缺口 | 已确认决定及代价 |
|---|---|---|
| D1 | 多条结论的恢复时点，以及零结论时如何复核 | 全部结论处理完后恢复一次；零结论但强制复核时保存运行级决定。后者需补充独立唯一约束，不能仅依赖含空 `ai_finding_id` 的现有约束 |
| D2 | 处理中能否重新调查 | 只允许 `completed` / `failed` 新建 Run；其余返回冲突。更简单，避免扩张为取消或替换执行机制 |
| D3 | 日期端点、基准日后解除、来源冲突优先级 | 新闻窗口为 D−364 至 D；按基准日有效状态判断；按对应领域来源优先，无法定优先级的冲突事实不计分并复核 |

D1 已确认，运行级决定只处理零结论情况下的复核，不增加多人会签或新复核页面。详细迁移须保证每 Run 至多一条此类决定，普通结论仍沿用现有唯一约束。
