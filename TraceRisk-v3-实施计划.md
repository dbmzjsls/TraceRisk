# TraceRisk v3 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 按阶段使用本文“Skills 使用计划”中指定的 Skill。严格逐任务实施；每个任务完成后运行验证并等待用户确认，不得自动连续实施多个阶段。

**Goal:** 在 14～21 天内完成一个基于合成企业数据的 AI 风险证据研判平台，通过确定性规则、混合检索、结构化 AI 研判和人工复核，生成可追溯的风险报告。

**Architecture:** 采用 Vue 3 前端、FastAPI 模块化单体后端、独立 worker 和 PostgreSQL。业务事务通过 Transactional Outbox 与 Procrastinate 任务队列衔接，LangGraph 负责编排、持久化中断和人工复核恢复；PostgreSQL 是业务状态的唯一权威来源。

**Tech Stack:** Vue 3、TypeScript、Vite、Naive UI、Pinia、TanStack Vue Query、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、PostgreSQL、pgvector、Procrastinate、LangGraph、BAAI/bge-small-zh-v1.5、pytest、Ruff、mypy、Docker Compose、GitHub Actions。

---

## 1. 项目目标

TraceRisk 是一个用于实习求职展示的 AI 全栈项目，不模拟完整的生产级企业风控系统。项目集中展示以下能力：

- Python AI 应用工程；
- FastAPI 与 PostgreSQL 后端开发；
- Vue 3 管理工作台开发；
- 确定性规则与可解释评分；
- RAG 检索和证据引用；
- 结构化模型输出；
- LangGraph 工作流与人工中断恢复；
- 异步任务、幂等性和状态约束；
- AI 质量评测；
- Docker 化交付。

核心产品承诺：

> 每条最终风险结论都能追溯到规则命中、证据片段、模型调用和人工复核记录。

## 2. MVP 完成标准

满足以下条件后停止扩张功能，进入求职投递：

- Docker Compose 能启动 Web、API、worker 和 PostgreSQL；
- 未配置真实生成模型密钥时，Mock 模型仍能完成完整流程；
- 本地中文 embedding 能完成真实语义检索；
- 五分钟内能演示企业浏览、调查、AI 研判、复核和报告；
- 每个风险分数都能定位到规则、版本和证据；
- 每条 AI 结论都引用本次检索到的证据片段；
- 跨企业、检索范围外、内容哈希不匹配和事实矛盾的引用会被拒绝；
- 人工修改结论不会覆盖 AI 原始输出；
- 同一复核请求重复提交不会重复恢复工作流或生成报告；
- worker 重启后可以恢复等待人工复核的工作流；
- 固定评测集可以重复运行；
- README 可以指导陌生用户完成启动和演示；
- 简历中的指标均来自实际测试或评测结果。

## 3. 范围边界

### 3.1 首版必须实现

- 三个预置演示账号；
- 分析员、复核员、管理员三类角色；
- 8～12 家合成企业；
- 工商、司法、新闻和知识产权四类来源；
- 调查、运行、步骤和事件记录；
- 四条确定性规则；
- Transactional Outbox；
- Procrastinate 后台任务；
- LangGraph 工作流；
- 关键词与向量混合检索；
- Pydantic 结构化 AI 输出；
- Mock 生成模型；
- 一个 OpenAI-compatible 生成模型适配器；
- 证据引用硬校验和结构化支持性校验；
- 人工批准、修改和驳回；
- HTML 风险报告；
- 前期轮询和后期 SSE 进度；
- 核心测试与评测；
- Docker Compose 和基础 CI。

### 3.2 首版明确不做

- 用户注册、找回密码和完整账号管理；
- 多租户、组织架构和计费；
- 文件上传、OCR 和在线数据抓取；
- 企业关系图谱；
- 通用规则 DSL 和规则配置后台；
- 多 Agent 自主协作；
- 多模型路由；
- Redis、Celery、Kafka；
- PDF 报告；
- 多人会签、复核撤销；
- 任务取消；
- 管理员配置中心；
- 大规模性能测试；
- Kubernetes；
- 移动端专项适配。

## 4. 开源底座与来源固定

### 4.1 前端底座

使用轻量 Vue 管理后台作为前端起点：

| 项目 | 固定值 |
|---|---|
| Repository | `https://github.com/zclzone/vue-naive-admin.git` |
| Branch | `2.x` |
| Commit | `a55f0a2e6fe0561ce62c3452b96ec4b4cbcb8553` |
| License | MIT |

保留：

- Vue 3、TypeScript、Vite；
- Naive UI、Pinia、Vue Router、UnoCSS；
- 基础布局、导航、登录页外壳和主题；
- 错误页、lint 和构建配置。

删除：

- 示例业务页面和 Mock API；
- 动态权限配置后台；
- 无关菜单和演示组件；
- 模板自带业务接口；
- 与 TraceRisk 无关的状态和权限封装。

替换：

- 服务端状态统一交由 TanStack Vue Query；
- Pinia 只保存登录用户、Token 和角色；
- API 类型根据 FastAPI OpenAPI 生成；
- 必要的运行时响应校验使用 Zod。

### 4.2 后端起点

后端不直接 fork 大型模板。使用 `uv init` 建立最小工程，只参考下列项目的配置、数据库会话、迁移、异常和测试写法：

- `https://github.com/zhanymkanov/fastapi-best-practices`
- `https://github.com/fastapi/full-stack-fastapi-template`
- `https://github.com/benavlabs/FastAPI-boilerplate`

不得从参考模板引入 OAuth、SQLAdmin、Redis、Taskiq、邮件、支付、SaaS 插件系统或通用 CRUD 框架。

### 4.3 第三方归属

创建 `THIRD_PARTY_NOTICES.md`，记录：

- 仓库 URL、分支、commit 和许可证；
- 实际保留或借鉴的内容；
- 已删除的模板功能；
- 本地 embedding 模型名称与许可证。

## 5. 最终仓库结构

```text
tracerisk/
├── apps/
│   ├── web/
│   │   ├── src/
│   │   │   ├── api/
│   │   │   │   ├── generated/
│   │   │   │   └── client.ts
│   │   │   ├── features/
│   │   │   │   ├── auth/
│   │   │   │   ├── companies/
│   │   │   │   ├── investigations/
│   │   │   │   ├── reviews/
│   │   │   │   └── reports/
│   │   │   ├── layouts/
│   │   │   ├── pages/
│   │   │   ├── router/
│   │   │   └── stores/
│   │   ├── package.json
│   │   └── vite.config.ts
│   └── api/
│       ├── src/tracerisk/
│       │   ├── auth/
│       │   ├── companies/
│       │   ├── evidence/
│       │   ├── investigations/
│       │   ├── rules/
│       │   ├── retrieval/
│       │   ├── ai/
│       │   ├── workflows/
│       │   ├── reviews/
│       │   ├── reports/
│       │   ├── audit/
│       │   ├── db/
│       │   ├── config.py
│       │   └── main.py
│       ├── tests/
│       │   ├── unit/
│       │   ├── integration/
│       │   ├── evaluation/
│       │   └── e2e/
│       ├── migrations/
│       ├── pyproject.toml
│       └── uv.lock
├── datasets/
│   ├── synthetic/
│   └── evaluation/
├── docs/
│   ├── design/
│   ├── evaluation/
│   └── references/
├── scripts/
├── compose.yaml
├── pnpm-workspace.yaml
├── THIRD_PARTY_NOTICES.md
├── .env.example
└── README.md
```

## 6. 领域模型

### 6.1 身份与企业

- `User`：预置演示用户；
- `Role`：分析员、复核员、管理员；
- `Company`：统一企业实体；
- `CompanyAlias`：历史名称和同名干扰；
- `DataSource`：工商、司法、新闻、知识产权；
- `SourceCoverage`：调查基准日的数据源可用性；
- `Evidence`：可被规则和报告引用的证据；
- `EvidenceChunk`：用于检索和 AI 引用的证据片段。

### 6.2 调查与执行

- `Investigation`：用户发起的企业调查业务容器；
- `WorkflowRun`：调查的一次执行；
- `WorkflowStep`：工作流节点执行记录；
- `WorkflowEvent`：用于时间线和 SSE 的持久化事件；
- `DispatchOutbox`：业务事务与任务队列之间的可靠派发表。

### 6.3 结果与复核

- `RuleFinding`：确定性规则命中快照；
- `RetrievedEvidence`：本次运行提供给模型的证据集合；
- `AIFinding`：AI 产生的结构化原始结论；
- `ModelInvocation`：模型调用尝试；
- `ReviewDecision`：复核员对 AI 结论的决定；
- `RiskReport`：最终不可变报告快照；
- `AuditEvent`：重要业务操作审计。

## 7. 状态与重试契约

### 7.1 Investigation

`Investigation` 保存 `company_id`、`created_by`、`as_of_date` 和 `current_run_id`，不重复保存细粒度执行状态。前端展示状态来自当前 Run。

### 7.2 WorkflowRun 状态

```text
queued
  ↓
running
  ↓
awaiting_review
  ↓
resume_queued
  ↓
running
  ↓
completed
```

失败分支：

```text
queued | running | resume_queued → failed
```

规则：

- 创建调查时创建 Run #1；
- Procrastinate 自动重试沿用同一个 Run；
- 节点重试只增加 `attempt_count`；
- 用户主动“重新调查”才创建新 Run；
- 仅当前 Run 为 `completed` 或 `failed` 时允许重新调查；其他状态返回冲突；
- 新 Run 使用新的 LangGraph `thread_id`；
- `thread_id = workflow_run.id`；
- 历史 Run 永不覆盖；
- `awaiting_review` 不是失败且不占用 worker；
- 已完成或失败的 Run 不能恢复。

## 8. 任务派发与事务契约

默认使用 Transactional Outbox：

```text
同一个数据库事务
├── INSERT investigation
├── INSERT workflow_run
└── INSERT dispatch_outbox
        ↓ COMMIT
派发器扫描未派发 Outbox
        ↓
创建 Procrastinate Job
        ↓
标记 Outbox dispatched
```

约束：

- `dispatch_outbox(run_id, operation)` 唯一；
- Job 使用 `queueing_lock=run:{run_id}:{operation}`；
- 派发器允许重复扫描；
- worker 开始和提交节点结果前都检查 Run 是否仍有效；
- API 不使用内存后台任务承担可靠执行；
- 阶段 1 Spike 必须验证 worker 崩溃后的补偿派发。

## 9. 幂等与数据库约束

必须建立：

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

Task 0.2 用户确认补充：零 AI 结论但强制复核时，记录运行级 ReviewDecision，`ai_finding_id` 与 `finding_version` 为空；增加部分唯一约束 `UNIQUE(run_id) WHERE ai_finding_id IS NULL`。普通结论复核仍要求结论 ID、版本并保留原唯一约束。

所有状态变化使用带旧状态条件的 `UPDATE`；受影响行数不是 1 时返回冲突，不继续产生副作用。

## 10. 规则规格

所有规则相对于 `investigation.as_of_date` 运行，每条规则最多命中一次。

| 规则编码 | 条件 | 去重键 | 分值 |
|---|---|---|---:|
| `JUDICIAL_OPEN_CASE_V1` | 至少存在一条 `status=open` 案件 | `case_number` | 35 |
| `BUSINESS_ABNORMAL_V1` | 至少一条基准日已发生且未解除的异常（`resolved_at IS NULL` 或解除日期晚于基准日） | `abnormal_record_id` | 25 |
| `NEGATIVE_NEWS_V1` | 基准日 D−364 至 D（含两端）至少两条独立负面报道 | `canonical_story_id` | 20 |
| `CORE_IP_ANOMALY_V1` | 核心专利失效或存在权属争议 | `ip_record_id` | 15 |

风险等级：

- 0～29：低风险；
- 30～59：中风险；
- 60～95：高风险。

界面显示真实满分，例如 `60 / 95`。证据冲突不加分，只触发人工复核。

Task 0.2 用户确认：所有状态按基准日当日有效状态判断，基准日后的事实不倒灌；异常基准日后解除仍在当日有效，基准日当天解除则不再命中。新闻窗口为包含基准日在内的连续 365 个自然日。

## 11. 数据覆盖与冲突

### 11.1 SourceCoverage

每个来源在调查基准日记录：

- `available`：来源可用，查询可以得出有记录或无记录；
- `unavailable`：来源不可用；
- `stale`：来源数据超过允许时效。

只有 `available` 且结果为空，才能解释为“未发现风险”。`unavailable` 或 `stale` 必须标记证据不足并进入复核。

### 11.2 确定性冲突

满足以下条件时判定冲突：

- 同一企业；
- 同一 `fact_key`；
- 不同来源；
- 存在互斥的规范化值。

冲突时保存双方证据，调查必须进入人工复核。Task 0.2 用户确认：司法、经营、知识产权事实分别优先采用司法、工商、知识产权来源；无法确定优先级的冲突事实不计分并标记证据不足，其他独立无争议事实仍按规则计算。

## 12. RAG 规格

### 12.1 数据边界

- 规则读取结构化事实；
- `prepare_context` 不向模型传递全部证据正文；
- AI 只读取 `retrieve_evidence` 选择的 Chunk。

### 12.2 切片

- 每条结构化事件优先形成一个独立 Chunk；
- 新闻按自然段切分；
- Chunk 保存企业、来源、风险类型、时间、`fact_type`、`fact_value`、`polarity` 和内容哈希；
- 首版不做复杂语义切片和 reranker。

### 12.3 检索

按司法、经营、新闻和知识产权分别检索，每类独立取 Top-K，再执行：

1. 当前企业过滤；
2. 当前用户权限过滤；
3. 重复证据合并；
4. 关键词与向量分数融合；
5. 跨风险类型合并；
6. 保存最终 RetrievedEvidence。

### 12.4 Embedding 模式

单元测试使用确定性 Fake Embedding，不下载模型。

本地演示和评测固定使用：

```text
model: BAAI/bge-small-zh-v1.5
dimension: 512
distance: cosine
device: CPU
```

模型或维度变化必须重建索引。Mock 与真实 embedding 的评测结果分别报告。

## 13. AI 输出与引用校验

模型业务接口：

```text
analyze_risk(context) -> StructuredRiskFindings
```

实现：

- `MockModelAdapter`；
- `OpenAICompatibleAdapter`。

单条 AI 结论字段：

```text
finding_key
risk_type
claim_code
title
summary
reasoning
evidence_citations
evidence_sufficiency
confidence_level
```

证据引用字段：

```text
evidence_id
chunk_id
content_hash
quoted_excerpt
```

硬校验：

- Evidence 属于当前企业；
- Chunk 属于该 Evidence；
- Chunk 在本次检索集合中；
- 内容哈希一致；
- 摘录确实存在于 Chunk；
- 引用符合当前用户数据权限；
- `claim_code` 与 Chunk 的结构化事实一致；
- 人工修改后重新执行相同校验。

模型不能输出或修改风险分数。结构化输出失败最多重试一次，第二次失败后 Run 进入 `failed`。

## 14. LangGraph 工作流

```text
prepare_context
      ↓
apply_rules
      ↓
retrieve_evidence
      ↓
generate_findings
      ↓
validate_findings
      ↓
route_result
  ├── generate_report
  └── review_interrupt
              ↓
         resume_review
              ↓
         generate_report
```

规则：

- `review_interrupt` 使用持久化 checkpointer；
- API 保存复核决定并创建恢复 Outbox；
- 初次执行和恢复均只在 worker 内发生；
- `interrupt()` 前不执行非幂等写入；
- 报告生成受 `UNIQUE(run_id)` 保护；
- 高风险、数据不足、证据冲突、模型证据不足或模型建议复核时进入人工复核。

## 15. 人工复核契约

复核操作：批准、修改、驳回。

复核绑定：

```text
run_id
ai_finding_id
finding_version
reviewer_id
idempotency_key
decision
reason
```

提交条件：

- Run 是 Investigation 的当前有效 Run；
- Run 状态为 `awaiting_review`；
- AI Finding 版本未改变；
- 当前 Finding 没有有效复核决定；
- 分析员不能复核自己创建的调查。

Task 0.2 用户确认：多条 AI 结论逐条保存决定，全部处理完才进行一次状态转换并创建恢复 Outbox；零结论但来源缺失或冲突等原因强制复核时，在同一复核流程保存一次运行级决定。运行级决定受独立唯一约束保护，不伪造 AI 结论。具体事务与幂等契约见 `docs/design/workflow-state-machine.md`。

驳回固定语义：AI 原始结论保留，但不纳入最终风险结论；保存驳回理由，不重新调用模型，不修改规则分数。

## 16. 页面与 API

### 16.1 页面

1. 登录页；
2. 企业列表；
3. 企业详情；
4. 调查列表；
5. 调查详情与时间线；
6. 待复核列表及详情；
7. 风险报告。

### 16.2 API 分组

```text
/auth
/companies
/companies/{id}/evidence
/investigations
/investigations/{id}
/investigations/{id}/runs
/runs/{id}/events
/reviews/pending
/reviews/{finding_id}
/runs/{id}/review（零结论运行级复核）
/reports/{id}
/audit-events
```

写接口要求 `Idempotency-Key`：

- 创建调查；
- 用户主动重新调查；
- 提交复核决定；
- 安排工作流恢复。

## 17. 合成数据与评测集

固定随机种子生成 8～12 家企业，至少覆盖：

- 正常经营；
- 未结司法案件；
- 经营异常；
- 负面舆情；
- 知识产权异常；
- 同名企业干扰；
- 重复转载新闻；
- 案件状态冲突；
- 数据源缺失；
- 日期边界；
- 无相关证据；
- 提示词注入；
- 合法引用但事实相反。

README 和所有业务页面必须显示“合成数据，仅供演示”。

## 18. Skills 使用计划

| 阶段 | 必用 Skills | 用途 |
|---|---|---|
| 设计与计划 | `brainstorming`、`domain-modeling`、`writing-plans` | 固定范围、术语和任务 |
| 技术 Spike | `async-python-patterns`、`postgresql-table-design`、`langchain-architecture` | 事务、worker、checkpoint |
| FastAPI | `fastapi-python` | API、依赖、异常与异步边界 |
| RAG | `langchain-rag` | 切片、混合检索、引用与评测 |
| Vue | `vue-best-practices`、`frontend-design` | Composition API、状态与交互 |
| Docker | `docker-expert` | Compose、健康检查和启动顺序 |
| 验收 | `webapp-testing` | 浏览器端到端验证 |

每个阶段开始前完整读取对应 Skill。不得因 Skill 示例自动增加本计划之外的功能。

---

# 19. 分阶段实施任务

## 阶段 0：规格与底座冻结（第 1 天）

### Task 0.1：记录底座和第三方来源

**Files:**

- Create: `THIRD_PARTY_NOTICES.md`
- Create: `docs/references/open-source-foundations.md`

- [x] 记录前端模板 URL、`2.x` 分支、固定 commit 和 MIT 许可证。
- [x] 记录 FastAPI 参考项目及仅允许借鉴的范围。
- [x] 记录 BGE 模型、512 维向量和许可证。
- [x] 列出前端底座保留、删除、替换清单。
- [x] 检查文档中不存在“直接复制 Dify、RAGFlow 或 Aleph”的表述。

**Validation:**

Run:

```powershell
rg -n "TBD|TODO|Dify.*底座|RAGFlow.*底座|Aleph.*底座" THIRD_PARTY_NOTICES.md docs/references
```

Expected: 不出现 `TBD`、`TODO` 或将大型项目作为底座的表述。

### Task 0.2：固化领域与状态规格

**Files:**

- Create: `docs/design/domain-model.md`
- Create: `docs/design/workflow-state-machine.md`
- Create: `docs/design/evidence-contract.md`
- Create: `docs/design/rule-specification.md`

- [x] 写明 Investigation 与 WorkflowRun 的职责差异。
- [x] 写明自动重试沿用 Run，主动重新调查创建 Run。
- [x] 写明所有状态转换和非法转换。
- [x] 写明引用字段、内容哈希和支持性校验。
- [x] 写明四条规则的时间、去重和计分口径。
- [x] 写明数据不足与零风险的区别。

**Validation:** 手工走查正常、失败、复核、重复复核和主动重新调查五个场景，确保每个场景只有一种解释。

**Commit:**

```powershell
git add THIRD_PARTY_NOTICES.md docs
git commit -m "docs: define tracerisk v3 architecture contracts"
```

> 仅在用户明确授权提交时执行 Commit 步骤。

## 阶段 1：高风险技术 Spike（第 1～2 天）

### Task 1.1：初始化最小后端与 PostgreSQL

**Files:**

- Create: `apps/api/pyproject.toml`
- Create: `apps/api/src/tracerisk/main.py`
- Create: `apps/api/src/tracerisk/config.py`
- Create: `apps/api/src/tracerisk/db/session.py`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/migrations/env.py`
- Create: `compose.yaml`
- Create: `.env.example`

- [x] 使用 uv 建立 Python 3.12 项目。
- [x] 添加 FastAPI、Pydantic Settings、SQLAlchemy、psycopg、Alembic、pytest、Ruff 和 mypy。
- [x] 建立 `/health`。
- [x] 建立 PostgreSQL Compose 服务和健康检查。
- [x] 建立空数据库迁移。

**Validation:**

```powershell
uv run --directory apps/api pytest tests/test_health.py -v
uv run --directory apps/api ruff check .
uv run --directory apps/api mypy src
docker compose config
```

Expected: 测试、lint、类型检查通过，Compose 配置有效。

Task 1.1 执行记录（2026-09-10）：上述检查通过，另通过格式、离线迁移和实际 HTTP 启动检查。Docker 引擎管道不可用，真实 PostgreSQL 启动与在线迁移尚未验证，详见 [验证记录](docs/evaluation/task-1.1.md)。

Task 1.2 开始前已补验：Docker 引擎恢复，PostgreSQL healthy，在线迁移、重复升级和模型差异检查均通过，原环境限制解除。

### Task 1.2：实现最小 Outbox 与 Procrastinate 调度

**Files:**

- Create: `apps/api/src/tracerisk/investigations/models.py`
- Create: `apps/api/src/tracerisk/workflows/outbox.py`
- Create: `apps/api/src/tracerisk/workflows/queue.py`
- Create: `apps/api/src/tracerisk/workflows/worker.py`
- Create: `apps/api/migrations/versions/0001_spike_tables.py`
- Create: `apps/api/tests/integration/test_outbox_dispatch.py`

- [x] 在一个事务内创建 Investigation、WorkflowRun 和 DispatchOutbox。
- [x] 实现 Outbox 重复扫描安全性。
- [x] 使用 queueing lock 防止重复等待任务。
- [x] 验证 API 提交后崩溃仍能补偿派发。

**Validation:**

```powershell
uv run --directory apps/api pytest tests/integration/test_outbox_dispatch.py -v
```

Expected: 重复派发两次只产生一个有效任务，同一 Run 不重复执行。

Task 1.2 执行记录：18 项真实 PostgreSQL 集成用例及 1 项健康测试通过；最后补齐尝试次数后，4 项受影响用例复验通过。API 提交后崩溃通过调用同一创建服务的子进程提交后退出模拟，尚未开放 HTTP 写接口。实现、启动与边界见 [验证记录](docs/evaluation/task-1.2.md)。

### Task 1.3：验证 LangGraph 中断、重启与恢复

**Files:**

- Create: `apps/api/src/tracerisk/workflows/spike_graph.py`
- Create: `apps/api/src/tracerisk/reviews/service.py`
- Create: `apps/api/src/tracerisk/reports/service.py`
- Create: `apps/api/tests/integration/test_interrupt_resume.py`

- [x] 使用稳定 `thread_id=run_id`。
- [x] 图执行到 `interrupt()` 后进入 `awaiting_review`。
- [x] 终止并重建 worker/graph 实例。
- [x] 保存复核决定并派发恢复任务。
- [x] 使用同一 thread_id 恢复。
- [x] 重复恢复只生成一份报告。

**Validation:**

```powershell
uv run --directory apps/api pytest tests/integration/test_interrupt_resume.py -v
```

Expected: worker 重建后恢复成功；复核和报告均只有一条有效记录。

Task 1.3 执行记录（2026-09-11）：11 项图恢复测试及完整 30 项回归通过，Ruff、格式、mypy、迁移和 Compose 检查通过。演示库迁移为 0002，checkpoint 初始化可重复执行。复核与报告为零 AI 结论的运行级 Spike；正式业务留到阶段 2。详见 [验收记录](docs/evaluation/task-1.3.md)。

**Stage Gate:** 未通过本阶段，不得进入企业、RAG 或前端业务开发。

## 阶段 2：最小纵向产品（第 3～6 天）

### Task 2.1：认证与预置角色

**Files:**

- Create: `apps/api/src/tracerisk/auth/models.py`
- Create: `apps/api/src/tracerisk/auth/schemas.py`
- Create: `apps/api/src/tracerisk/auth/service.py`
- Create: `apps/api/src/tracerisk/auth/router.py`
- Create: `apps/api/tests/integration/test_auth.py`

- [x] 建立三个预置账号。
- [x] 实现密码哈希和 JWT 登录。
- [x] 实现后端角色依赖。
- [x] 验证未登录、错误角色和正常访问。

实施与验收记录：[Task 2.1](docs/evaluation/task-2.1.md)。

### Task 2.2：一家企业和一条规则

**Files:**

- Create: `apps/api/src/tracerisk/companies/models.py`
- Create: `apps/api/src/tracerisk/companies/router.py`
- Create: `apps/api/src/tracerisk/evidence/models.py`
- Create: `apps/api/src/tracerisk/rules/engine.py`
- Create: `apps/api/src/tracerisk/rules/judicial.py`
- Create: `apps/api/tests/unit/rules/test_judicial.py`
- Create: `scripts/seed_synthetic.py`

- [x] 建立一个司法风险企业故事。
- [x] 实现 `JUDICIAL_OPEN_CASE_V1`。
- [x] 确保多条未结案件只加一次 35 分。
- [x] 保存规则版本、参数和证据引用。

实施与验收记录：[Task 2.2](docs/evaluation/task-2.2.md)。

### Task 2.3：Mock AI、复核和 HTML 报告

**Files:**

- Create: `apps/api/src/tracerisk/ai/schemas.py`
- Create: `apps/api/src/tracerisk/ai/mock_adapter.py`
- Create: `apps/api/src/tracerisk/ai/validation.py`
- Create: `apps/api/src/tracerisk/reviews/router.py`
- Create: `apps/api/src/tracerisk/reports/models.py`
- Create: `apps/api/src/tracerisk/reports/render.py`
- Create: `apps/api/tests/e2e/test_minimal_investigation.py`

- [x] 使用一条固定检索证据生成结构化结论。
- [x] 验证引用范围、哈希和摘录。
- [x] 高风险进入人工复核。
- [x] 批准后恢复并生成 HTML 报告。
- [x] 重复复核只产生一份报告。

验收记录：[Task 2.3 验收](docs/evaluation/task-2.3.md)（合成司法故事汇总为一条结论、两条案件引用；35 分示例因 Mock 推荐复核暂停，高风险阈值另为 60）。

### Task 2.4：引入并裁剪前端底座

**Files:**

- Create: `apps/web/`
- Modify: `apps/web/package.json`
- Modify: `apps/web/src/router/`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/features/auth/`
- Create: `apps/web/src/features/companies/`
- Create: `apps/web/src/features/investigations/`
- Create: `apps/web/src/features/reviews/`
- Create: `apps/web/src/features/reports/`

- [x] 从固定 commit 提取前端底座。
- [x] 删除裁剪清单中的所有示例内容。
- [x] 添加 Vue Query。
- [x] 完成登录、企业详情、调查详情、复核和报告最小页面。
- [x] 早期进度使用轮询。

**Validation:**

```powershell
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
uv run --directory apps/api pytest tests/e2e/test_minimal_investigation.py -v
```

Expected: 浏览器能够完成登录到报告的完整闭环。

## 阶段 3：完整业务案例（第 7～10 天）

### Task 3.1：完整合成数据

**Files:**

- Create: `apps/api/src/tracerisk/synthetic/generator.py`
- Create: `apps/api/src/tracerisk/synthetic/scenarios.py`
- Create: `apps/api/tests/unit/synthetic/test_reproducibility.py`
- Create: `datasets/synthetic/README.md`

- [x] 使用固定随机种子生成 8～12 家企业。
- [x] 覆盖同名、重复新闻、冲突、缺失、日期边界和注入场景。
- [x] 验证连续生成结果的业务字段和内容哈希一致。

### Task 3.2：补齐四条规则

**Files:**

- Create: `apps/api/src/tracerisk/rules/business.py`
- Create: `apps/api/src/tracerisk/rules/news.py`
- Create: `apps/api/src/tracerisk/rules/intellectual_property.py`
- Create: `apps/api/tests/unit/rules/test_business.py`
- Create: `apps/api/tests/unit/rules/test_news.py`
- Create: `apps/api/tests/unit/rules/test_intellectual_property.py`
- Create: `apps/api/tests/unit/rules/test_score.py`

- [x] 实现去重、日期边界和满分 95。
- [x] 验证模型和人工复核无法修改规则分数。

### Task 3.3：来源覆盖、冲突和审计

**Files:**

- Create: `apps/api/src/tracerisk/evidence/coverage.py`
- Create: `apps/api/src/tracerisk/evidence/conflicts.py`
- Create: `apps/api/src/tracerisk/audit/models.py`
- Create: `apps/api/src/tracerisk/audit/service.py`
- Create: `apps/api/tests/unit/evidence/test_coverage.py`
- Create: `apps/api/tests/unit/evidence/test_conflicts.py`

- [x] 区分“未发现风险”和“来源不可用”。
- [x] 对互斥事实生成确定性冲突。
- [x] 记录创建调查、重新调查、失败、复核和报告审计。

## 阶段 4：真实 RAG 与模型适配（第 11～14 天）

### Task 4.1：Evidence Chunk 与本地 embedding

**Files:**

- Create: `apps/api/src/tracerisk/retrieval/chunking.py`
- Create: `apps/api/src/tracerisk/retrieval/embeddings.py`
- Create: `apps/api/src/tracerisk/retrieval/indexing.py`
- Create: `apps/api/tests/unit/retrieval/test_chunking.py`
- Create: `apps/api/tests/integration/test_vector_index.py`

- [x] 固定 BGE small zh v1.5 和 512 维。
- [x] 建立索引版本元数据。
- [x] 启动时拒绝模型/维度与现有索引不匹配。
- [x] 单元测试默认使用 Fake Embedding。

### Task 4.2：按风险类型混合检索

**Files:**

- Create: `apps/api/src/tracerisk/retrieval/keyword.py`
- Create: `apps/api/src/tracerisk/retrieval/vector.py`
- Create: `apps/api/src/tracerisk/retrieval/hybrid.py`
- Create: `apps/api/tests/evaluation/test_retrieval_recall.py`
- Create: `datasets/evaluation/retrieval_cases.jsonl`

- [x] 四类风险独立检索。
- [x] 完成企业过滤、权限过滤、去重和融合。
- [x] 人工标注相关证据集合。
- [x] 分别报告 Fake 与 BGE Recall@5。

### Task 4.3：真实生成模型适配器

**Files:**

- Create: `apps/api/src/tracerisk/ai/protocol.py`
- Create: `apps/api/src/tracerisk/ai/openai_compatible.py`
- Create: `apps/api/src/tracerisk/ai/prompts.py`
- Create: `apps/api/tests/unit/ai/test_structured_output.py`
- Create: `apps/api/tests/unit/ai/test_prompt_injection.py`

- [x] 配置模型超时和一次有限重试。
- [x] 固定提示词版本。
- [x] 证据以不可信数据边界传入。
- [x] 保存模型、Token、耗时和错误分类。
- [x] CI 不调用真实模型。

### Task 4.4：支持性校验

**Files:**

- Modify: `apps/api/src/tracerisk/ai/validation.py`
- Create: `apps/api/tests/unit/ai/test_citation_support.py`

- [x] 使用 `claim_code` 对照 Chunk 的事实和 polarity。
- [x] 拒绝“合法 ID 但事实相反”的结论。
- [x] 对无法确定的自由文本标记复核，不声称完全语义验证。

## 阶段 5：SSE 与界面打磨（第 15～18 天）

### Task 5.1：持久化工作流事件与 SSE

**Files:**

- Create: `apps/api/src/tracerisk/workflows/events.py`
- Create: `apps/api/src/tracerisk/workflows/sse.py`
- Create: `apps/api/tests/integration/test_sse_reconnect.py`
- Create: `apps/web/src/features/investigations/useInvestigationEvents.ts`

- [x] 每个节点追加递增序号事件。
- [x] SSE 先补历史，再推送新事件。
- [x] 支持最后事件编号重连。
- [x] 终态关闭连接。
- [x] SSE 失败回退轮询。

### Task 5.2：完成七个页面和所有状态

**Files:**

- Create/Modify: `apps/web/src/pages/login/`
- Create/Modify: `apps/web/src/pages/companies/`
- Create/Modify: `apps/web/src/pages/investigations/`
- Create/Modify: `apps/web/src/pages/reviews/`
- Create/Modify: `apps/web/src/pages/reports/`

- [x] 完成加载、空数据、失败、无权限、运行中、待复核和失败状态。
- [x] 明确区分规则、AI、人工和最终结果。
- [x] 点击引用可定位证据摘录。
- [x] 展示数据覆盖不足和冲突。
- [x] 页面显示“合成数据，仅供演示”。

**Validation:**

```powershell
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
```

Expected: lint、类型检查和生产构建通过。

## 阶段 6：评测、交付与缓冲（第 19～21 天）

### Task 6.1：自动评测

**Files:**

- Create: `apps/api/tests/evaluation/test_rules.py`
- Create: `apps/api/tests/evaluation/test_citations.py`
- Create: `apps/api/tests/evaluation/test_workflow_recovery.py`
- Create: `scripts/run_evaluation.py`
- Create: `docs/evaluation/results.md`

- [x] 计算规则准确率、Recall@5、引用合法率、证据覆盖率和结构化输出成功率。
- [x] 测试 Outbox、重试、重启、重复复核和报告唯一性。
- [x] 真实模型成本只在实际调用后报告。
- [x] Mock 与真实模式结果分开。

### Task 6.2：Docker 和 CI

**Files:**

- Create: `apps/web/Dockerfile`
- Create: `apps/api/Dockerfile`
- Modify: `compose.yaml`
- Create: `.github/workflows/ci.yml`

- [x] Compose 启动 Web、API、worker 和 PostgreSQL。
- [x] 配置数据库健康检查和服务启动顺序。
- [x] CI 执行后端测试、Ruff、mypy、前端 lint、类型检查和构建。
- [x] CI 不下载 BGE、不调用真实 LLM。

### Task 6.3：端到端验收

**Files:**

- Create: `apps/web/e2e/investigation.spec.ts`
- Create: `docs/evaluation/e2e-checklist.md`

- [x] 低风险直接报告。
- [x] 高风险进入复核。
- [x] 修改结论后报告保留原始 AI 输出。
- [x] 驳回结论不进入最终结论。
- [x] 证据冲突强制复核。
- [x] 数据不足但规则零分。
- [x] 非法引用被拒绝。
- [x] 重复复核不产生重复报告。

### Task 6.4：求职包装

**Files:**

- Create: `README.md`
- Create: `docs/demo-script.md`
- Create: `docs/interview-notes.md`
- Create: `docs/resume-bullets.md`
- Create: `docs/design/system-architecture.md`

- [x] README 提供本地与 Docker 启动方法。
- [x] 准备五分钟演示脚本。
- [x] 准备架构、事务、RAG 和幂等性面试讲解。
- [x] 截图展示企业详情、调查时间线、复核和报告。
- [x] 简历描述只使用实际评测数字。

**Final Validation:**

```powershell
docker compose config
docker compose up --build -d
uv run --directory apps/api pytest -v
uv run --directory apps/api ruff check .
uv run --directory apps/api ruff format --check .
uv run --directory apps/api mypy src
pnpm --dir apps/web lint
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
docker compose ps
```

Expected:

- 所有自动检查通过；
- 四个服务健康；
- 无真实模型密钥也能完成五分钟演示；
- 报告可追溯到规则、证据、AI 和人工复核。

---

## 20. 范围不足时的删减顺序

按以下顺序删减：

1. 独立管理员审计页面，只保留审计记录；
2. 真实生成模型适配器，只保留 Mock LLM；
3. SSE 断线续传，保留轮询；
4. 报告历史展示；
5. ECharts 图表；
6. 复杂筛选和主题设置。

不能删除：

- 合成数据；
- 四条确定性规则；
- Mock LLM；
- 真实或可验证的检索；
- Pydantic 结构化输出；
- 证据引用与支持性校验；
- 人工复核；
- HTML 报告；
- Outbox 与幂等测试；
- 最小评测集。

## 21. 每阶段执行纪律

开始阶段前必须汇报：

- 本阶段目标；
- 本阶段不做什么；
- 预计创建和修改的文件；
- 验收命令；
- 当前已知风险。

阶段结束后必须汇报：

- 实际完成内容；
- 主要修改文件；
- 实际执行命令；
- 通过和失败的测试；
- 未执行测试及原因；
- 安全或数据泄漏检查；
- 遗留限制；
- 是否请求进入下一阶段。

未经用户确认，不得自动进入下一阶段。

## 22. vibe coding 任务模板

每个实现任务必须包含：

```text
目标
输入与输出
允许修改的文件
状态变化
数据库约束
失败行为
幂等要求
独立验收样例
明确禁止扩张的范围
验证命令和预期结果
```

任务粒度控制在半天以内。AI 不得自行补全未定义的业务语义，再使用自己生成的测试证明其正确。

## 23. 最终交付物

- 可运行代码仓库；
- Docker Compose；
- 合成数据生成器；
- 自动测试与 AI 评测结果；
- 产品、领域、架构和安全文档；
- 数据模型图和 LangGraph 流程图；
- 页面截图；
- 五分钟演示脚本；
- 面试讲解提纲；
- 3～5 条基于真实结果的简历描述；
- `THIRD_PARTY_NOTICES.md`。
