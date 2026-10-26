# 典型案例

以下案例从基线结果中按影响严重度降序、case_id 升序确定性选择。

## 成功案例

- case_id：`insufficient-001`
- 期望/实际：`reject` / `reject`
- 一级归因：`none`
- 输入/引用 Chunk：1 / 0

## 失败案例

- case_id：`conflict-001`
- 期望/实际：`manual_review` / `reject`
- 一级归因：`human_judgment`
- 输入/引用 Chunk：2 / 0

## 人工复核案例

- case_id：`explicit-001`
- 期望/实际：`accept` / `manual_review`
- 一级归因：`human_judgment`
- 输入/引用 Chunk：1 / 1
