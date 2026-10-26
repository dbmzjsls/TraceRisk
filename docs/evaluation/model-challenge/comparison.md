# TraceRisk 真实模型挑战评测前后对比

本报告按原始 journal 聚合计数与过程指标，不以单一通过率作为成果。

## 单变量实验

- 选中问题：`human_judgment`
- 唯一变更维度：`review_routing`
- 受影响案例：20
- 冻结加权影响：50
- 相关归因案例：20 → 7 （delta -13）

| 指标 | 基线 | 复测 |
| --- | ---: | ---: |
| 首次可用案例 | 35 | 37 |
| 正确接受 | 0 | 15 |
| 正确拒绝 | 15 | 15 |
| 正确转人工复核 | 1 | 1 |
| 路由到人工复核 | 16 | 1 |
| 不必要人工复核 | 15 | 0 |
| 漏复核 | 9 | 9 |
| 模型逻辑调用 | 45 | 43 |
| HTTP 尝试 | 45 | 43 |
| 重试 | 5 | 3 |
| API 失败 | 0 | 0 |
| 执行失败案例 | 0 | 0 |
| 输入 Chunk | 60 | 60 |
| 实际引用 Chunk | 25 | 21 |
| 输入 Token | 57679 | 54821 |
| 输出 Token | 8206 | 7276 |
| 总 Token | 65885 | 62097 |
| 缓存命中 Token | 31232 | 46319 |
| 缓存未命中 Token | 26447 | 8502 |
| 成本（USD） | 0.008984346 | 0.005779857 |
| 模型耗时（ms） | 194553 | 203688 |
| 失败定位耗时（ms） | 123 | 181 |
| 已复核案例 | not measured | not measured |
| 人工修改字段实例 | not measured | not measured |
| 人工复核耗时（ms） | not measured | not measured |
| 人工测量状态 | not_measured | not_measured |

## 错误归因

| 归因 | 基线 | 复测 |
| --- | ---: | ---: |
| structure_error | 0 | 0 |
| citation_error | 0 | 0 |
| support_error | 5 | 3 |
| human_judgment | 20 | 7 |

校验错误类型（基线 / 复测）：

- `support_error`：5 / 3

## 模型与运行边界

- 请求模型：deepseek-flash → deepseek-flash
- 返回模型：deepseek-flash → deepseek-flash
- system fingerprint：e72d3c7f663d3e1383b00b3884cb4501 → e72d3c7f663d3e1383b00b3884cb4501
- 返回模型计数分布：`{"deepseek-flash": 45} → {"deepseek-flash": 43}`
- fingerprint 计数分布：`{"e72d3c7f663d3e1383b00b3884cb4501": 45} → {"e72d3c7f663d3e1383b00b3884cb4501": 43}`
- 缺失返回模型：0 → 0
- 缺失 fingerprint：0 → 0
- 因果比较状态：`clean`

## 局限

- 逐条手工策划的合成小样本挑战集（本次 40 条），不代表生产分布。
- 仅包含一组基线/复测，不能证明改进可泛化。
- 模型版本仅按 API 返回的模型标识与 system fingerprint 记录。
- 本地非生产环境的延迟、重试与成本不能外推至生产系统。
- 评测隔离数据库与检索，不衡量端到端召回质量。
- 未完成真实人工复核，修改字段数与复核耗时为 not measured。

## 冻结环境

- Python：3.12.8 | packaged by Anaconda, Inc. | (main, Dec 11 2024, 16:48:34) [MSC v.1929 64 bit (AMD64)]
- OS：Windows-11-10.0.26100-SP0
- 数据集 SHA-256：`0fce7f2cf949bf45d2d19073ae65ee6e8523019f893addf924976ee546ad384e`
- 配置 SHA-256：`e7501d3ffd0164aa1c9c7e9562ad8cdea65d218fa17c03fb6eae952123aa0133`
- 依赖锁 SHA-256：`d9a056b8fcbb8da057a6335826503eefaccd9597e2d968bd6c044b098d9de313`
- 源码清单 SHA-256：`97f274a4b721baabb2ab9c772e3a2db2fe1cf1f5f77fbf117bd21c9390545c00`

## Provenance

- `baseline/attempts.jsonl`: `91a7e71785c9664885386cfd873f883926b204e64192bb1b4bd2196b4b6354dd`
- `baseline/case-events.jsonl`: `944f13231ccb656a8ba91db6a0c7679a59c68e90efc746d8aab44a42c2c4b5ef`
- `baseline/cases.jsonl`: `616385cb3977126a015477045b23f2fe42a12b77c843518f49790701d5fb7f29`
- `baseline/config.json`: `e7501d3ffd0164aa1c9c7e9562ad8cdea65d218fa17c03fb6eae952123aa0133`
- `baseline/dataset.jsonl`: `0fce7f2cf949bf45d2d19073ae65ee6e8523019f893addf924976ee546ad384e`
- `baseline/manifest.json`: `0c88c58a25073d046d87f18791c03c5a5b8d5a01e6054fb8c99e89d2b4455ec0`
- `improved/attempts.jsonl`: `36ec9b745c5117ead6f045a3ba7f1d3fbdb7860d8a82b868215713ba2c3e13d9`
- `improved/case-events.jsonl`: `f02cdb411e10c1053e9b16c19945261b04c3d78f8dd72c81ee25bbe3b05e21ae`
- `improved/cases.jsonl`: `2b721d93a295c00eefd47983c786bbfca2933937e1e23bf1f24ac6bab6737db9`
- `improved/config.json`: `e7501d3ffd0164aa1c9c7e9562ad8cdea65d218fa17c03fb6eae952123aa0133`
- `improved/dataset.jsonl`: `0fce7f2cf949bf45d2d19073ae65ee6e8523019f893addf924976ee546ad384e`
- `improved/manifest.json`: `72598d05ad723fff340fa8745bd0bc1e2fdeabe47613a43bfb1f16a5db540569`
- `analysis/analysis.json`: `ed7f4bcf989bc144d5735106b7930edab50e960ff8b1002866b87b4ad7fcf5e1`
