# 真实模型挑战评测

这是 TraceRisk 的一次可审计、可重放的真实模型挑战评测。它使用 40 条逐条手工策划的合成案例，验证模型输出是否能经过项目既有的结构、引用与证据支持性边界；它不是生产质量基准，也不以单一通过率作为成果。

数据集覆盖四类风险场景，各 10 条：明确证据、相似实体或相似数字造成的误引、同一事实的跨来源冲突、以及证据不足。每条样本冻结了预期处理方式、支持与迷惑 Chunk、允许的 `claim_code` 和人工编写的判断理由。现有引用与规则测试仍是 CI 回归门禁，不能用它们的通过结果代替本评测的真实模型结论。

## 冻结设置与已测结果

两次运行都使用 `deepseek-flash`、`temperature=0`、禁用 thinking、最多 2 次 HTTP 尝试，以及同一份数据集和价格快照。服务端每次返回的模型标识均为 `deepseek-flash`，system fingerprint 均为 `e72d3c7f663d3e1383b00b3884cb4501`；比较状态为 `clean`。唯一变更是 `review_routing`，由基线归因自动选择，未改变数据集、模型参数、Prompt 或其他行为节点。

基线将 20 个案例归因为 `human_judgment`（冻结加权影响 50），复测为 7；路由至人工复核的案例数为 16 到 1，首次可用案例数为 35 到 37，验证修复次数为 5 到 3。漏复核仍为 9，不能据此宣称整体安全性或普遍提升。完整逐指标、Token、调用、延迟与成本见 [comparison.md](comparison.md)，典型成功、失败和人工复核案例见 [typical-cases.md](typical-cases.md)。

本次未执行真实人工交互复核，因此两次运行的人工复核数量、修改字段数和复核耗时均为 `not_measured`；自动金标差异没有被记作人工修改。

## 可复现命令

从仓库根目录运行。`analyze`、`compare` 不读取 API Key；`check` 是一次独立真实连通性调用，`baseline` 和 `improved` 是真实模型运行，三者只从仓库根目录私有 `.env` 的 `MODEL_API_KEY` 读取密钥。请勿提交 `.env`、命令输出中的密钥，或覆盖已提交的原始 journal。

完整复现实验时，先在基线源码提交 `f8a8005` 上执行 `check`、`baseline`、`analyze`，再只应用 `f8a8005..72cd841` 中获授权的 `review_routing` 改动后执行 `improved` 和 `compare`。所有输出应写入新的空路径；下列已提交路径只用于展示本次实测命令，当前 checkout 中不得覆盖。`analysis.json` 生成后、执行 `improved` 前，是唯一允许修改实现的时间点。

```powershell
$env:PYTHONPATH = 'apps/api/src;.'
$python = 'apps/api/.venv/Scripts/python.exe'

# 一次独立真实连通性调用（不计入 40 例汇总）；需要私有 .env，输出路径必须不存在
& $python scripts/run_model_challenge.py check --config datasets/evaluation/model_challenge_config.json --output docs/evaluation/model-challenge/connectivity-check.json

# 新建目录时，执行未经优化的真实基线；需要私有 .env
& $python scripts/run_model_challenge.py baseline --dataset datasets/evaluation/model_challenge_cases.jsonl --config datasets/evaluation/model_challenge_config.json --output docs/evaluation/model-challenge/baseline

# 仅由基线 journal 重算归因与唯一允许的改进维度
& $python scripts/run_model_challenge.py analyze --baseline docs/evaluation/model-challenge/baseline --output docs/evaluation/model-challenge/analysis.json

# 如需测量人工过程指标，此时先复核基线；本次实验未执行
# & $python scripts/run_model_challenge.py review --run docs/evaluation/model-challenge/baseline

# 仅应用 analysis 授权的一个实现函数改动；本次对应 f8a8005..72cd841

# 仅在 single-dimension guard 通过后，在同一冻结输入上运行复测；需要私有 .env
& $python scripts/run_model_challenge.py improved --dataset datasets/evaluation/model_challenge_cases.jsonl --config datasets/evaluation/model_challenge_config.json --baseline docs/evaluation/model-challenge/baseline --analysis docs/evaluation/model-challenge/analysis.json --output docs/evaluation/model-challenge/improved

# 离线生成汇总、对比、典型案例与简历表述
& $python scripts/run_model_challenge.py compare --baseline docs/evaluation/model-challenge/baseline --improved docs/evaluation/model-challenge/improved --analysis docs/evaluation/model-challenge/analysis.json --output docs/evaluation/model-challenge

# 仅当基线已完成人工复核时，才对复测运行执行同样的交互式复核；本次两侧均未执行
# & $python scripts/run_model_challenge.py review --run docs/evaluation/model-challenge/improved
```

已提交的评测结果使用同一命令序列生成；不要对已提交目录重跑真实调用，以免供应商服务端状态或时间改变而覆盖审计证据。

## 产物与成本解释

- `baseline/`、`improved/`：每个阶段的 `manifest.json`、每次 HTTP 尝试的 `attempts.jsonl`、事件与最终逐案例 journal、冻结数据和配置副本；`summary.json` 由这些原始记录聚合。
- `analysis.json`：从基线 journal 重算的归因候选、影响权重、选择理由和输入哈希。
- `comparison-summary.json`、`comparison.md`、`typical-cases.md`、`resume-bullet.md`：只读原始 journal 后生成的机器汇总、人读对比、确定性典型案例和真实简历表述。
- `connectivity-check.json`：一次独立连通性调用的脱敏遥测，不参与 40 个案例的质量汇总。

成本字段名为 `conservative_estimated_cost_usd`，由 API 返回的 usage 和 2026-10-23 固定的 DeepSeek 单价计算，并按请求时刻的 UTC 峰谷规则取价；它不是账单金额。基线为 `0.008984346` USD，复测为 `0.005779857` USD。若 usage 缺失，评测器保留 `null` 而不估算。

## 审计与限制

每个运行清单保存数据、配置、依赖锁、Prompt 与递归本地源码清单哈希；改进清单只允许变更经分析选中的一个顶层行为函数。`comparison-summary.json` 为每一份输入 journal 保存 SHA-256，可用于核对派生产物的来源。

- 这是 40 条逐条手工策划的合成小样本，不代表真实企业风险或生产分布，且标签没有多名领域专家一致性检验。
- 仅有一组基线/复测；即使服务端可观察模型标识与 fingerprint 一致，也不能证明改进可泛化。
- 模型内部版本、负载和服务端实现不可完全控制；本报告只记录 API 可观察的返回模型标识与 fingerprint。
- 本地非生产环境的延迟、重试和成本不能外推到生产吞吐；评测隔离数据库与检索，也不衡量召回或端到端用户体验。
- 没有完成真实人工复核，人工字段修改数和复核耗时仍未测量。
