# 第三方来源与使用记录

记录日期：2026-09-10。对应《TraceRisk v3 实施计划》Task 0.1。

前端固定底座已在 Task 2.4 实际提取、适配并用于本地真实浏览器闭环；模型权重仍未下载。实际文件对应和原始 SHA-256 见 [upstream-manifest.json](apps/web/upstream-manifest.json)，完整 MIT 原文见 [LICENSE.upstream](apps/web/LICENSE.upstream)，最终验收见 [Task 2.4 验收](docs/evaluation/task-2.4.md)。

## 前端底座：vue-naive-admin

| 项目 | 固定值 |
|---|---|
| 仓库 | https://github.com/zclzone/vue-naive-admin.git |
| 计划分支 | `2.x` |
| 固定 commit | `a55f0a2e6fe0561ce62c3452b96ec4b4cbcb8553` |
| 许可证 | MIT |
| 版权所有 | Copyright (c) 2023 Ronnie Zhang(大脸怪) |
| 当前实际保留、修改或删除 | 2026-09-14：最小提取 16 个源文件用于读取审计；MIT 原文保留，普通 shell/header/sidebar/logo/menu、登录外壳/表单、主题、错误页及核心配置适配到 apps/web；reset 仅检查未提取。未整体复制模板。 |

已读取固定 commit 的 [LICENSE](https://github.com/zclzone/vue-naive-admin/blob/a55f0a2e6fe0561ce62c3452b96ec4b4cbcb8553/LICENSE) 和 [package.json](https://github.com/zclzone/vue-naive-admin/blob/a55f0a2e6fe0561ce62c3452b96ec4b4cbcb8553/package.json)。代码以 commit 为准，不随分支最新版本移动；本次未验证该 commit 与当前分支头的关系。

实际保留并适配基础布局、导航、登录页外壳、主题、错误页，以及 Vue 3、Vite、Naive UI、Pinia、Vue Router、UnoCSS 的工程配置；改为 TypeScript Composition API，添加必要 Vue Query、Zod、类型生成和 lint 工具。版权保留在适配文件。所有本地页面均非“原样复制”；具体来源和改动见 manifest。完整裁剪说明见 [底座实施边界](docs/references/open-source-foundations.md)。

示例业务、Mock/模板 API、角色选择、动态权限、引导、chart/Excel、Git hooks 和外部代理按最小提取策略排除（原始文件未下载、未复制，不能称为从本地删除）。所提取登录和 shell 内的验证码、快捷账号、保存密码及无关操作实际删除。TraceRisk auth/store/client/router 为本地新实现，不冒充上游保留代码。

引入代码时保留上游完整 MIT 许可证和版权声明，并记录改动。本文件的来源说明不代替上游许可证原文。

## 后端参考资料

后端使用 `uv init` 独立建立 Python 3.12 最小工程，不整体 fork 参考模板。

| 参考仓库 | 允许参考内容 | 当前实际使用 |
|---|---|---|
| https://github.com/zhanymkanov/fastapi-best-practices | 模块组织、配置、依赖和异常处理写法 | 已查看项目资料；未复制代码 |
| https://github.com/fastapi/full-stack-fastapi-template | 配置、数据库会话、迁移和测试写法 | 已查看项目资料；未复制代码 |
| https://github.com/benavlabs/FastAPI-boilerplate | 异步会话、迁移、异常和测试写法 | 已查看项目资料；未复制代码 |

实施计划没有为这三个参考项目固定分支或 commit；当前不把其浮动版本声明为代码依赖。若后续实际复制片段，先记录对应 commit、源文件、许可证及本地落点，并保留适用声明。

禁止从参考项目引入 OAuth、SQLAdmin、Redis、Taskiq、邮件、支付、SaaS 插件系统或通用 CRUD 框架。

## 本地 embedding 模型

| 项目 | 值 |
|---|---|
| 模型 | `BAAI/bge-small-zh-v1.5` |
| 发布方 | Beijing Academy of Artificial Intelligence（BAAI） |
| 模型仓库 | https://huggingface.co/BAAI/bge-small-zh-v1.5 |
| 许可证 | MIT（模型卡声明） |
| 向量维度 | 512 |
| 距离 / 设备 | cosine / CPU（项目约定） |
| 当前实际使用 | 未下载权重，未构建索引，未产生评测结果 |

来源：[模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)及 [模型配置](https://huggingface.co/BAAI/bge-small-zh-v1.5/raw/main/config.json)。模型配置的 `hidden_size` 为 512。

阶段 4 引入时记录实际模型 revision 和许可证；索引记录模型与维度，发生变化时重建索引。单元测试使用确定性 Fake Embedding；本地演示和真实检索评测使用 BGE，两类结果分开报告。
