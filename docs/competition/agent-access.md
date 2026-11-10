# 调查助手访问说明

公网入口待提供。当前验证范围为本机运行和取证。正式 HTTPS 地址、评审账号、可用期和维护联系人待部署后核验并登记，评审密码由提交系统单独提供。

## 比赛示例页

[本地示例页](http://127.0.0.1:8080/demo)可直接浏览。三张案例卡集中展示完整提示词、实跑回答、待复核状态、冻结证据与原始截图。点击「带入助手试问」，登录后即可看到预填的企业、基准日和问题。司法追问从综合调查开始，收到结果后选择「司法追问」。

[1600×900 示例页截图](ppt-materials/screenshots/competition-demo.png) · [司法证据展开截图](ppt-materials/screenshots/competition-demo-evidence.png)

部署后可把 HTTPS 的 `/demo` 作为案例浏览入口，将同域的 `/assistant` 和评审账号填入智能体提交信息。重新生成展示数据和原始截图资源可运行 `python scripts/export_competition_examples.py`，脚本先核对已存档的实跑文件哈希。

## 操作路径

本机 Docker 演示已启动，使用 `tracerisk_demo` 和真实模型配置。浏览器已验证直接打开、登录返回、刷新和企业目录加载。[本地调查助手](http://127.0.0.1:8080/assistant)

登录后打开 `/assistant`。选择企业与基准日，复制[三条提示词](conversations/manifest.json)并发送。示例 03 回到示例 01 的会话继续追问，点击回答中的引用查看本次冻结证据。

## 部署准备

在已授权主机上使用独立的 `tracerisk_demo` 和持久化 PostgreSQL 卷。按 [Compose 生命周期说明](../operations/docker-compose.md)使用默认 Mock/Fake 配置执行 `bootstrap-demo` 和 `verify-target --target tracerisk_demo`。随后在私有配置中设置 `MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY` 和 `JWT_SECRET`，启动真实模型服务。

```powershell
docker compose -f compose.yaml -f compose.competition.yaml config --quiet
docker compose -f compose.yaml -f compose.competition.yaml up -d --build --wait --wait-timeout 180 api worker web
```

覆盖文件把 API 与 Worker 指向同一展示库和模型配置，保留 Fake 检索。HTTPS 入口反向代理 Web 的本机端口，转发 `/demo`、`/assistant` 和同源 `/api`，SSE 路径保持流式传输。公开前配置独立分析员账号，更换或停用默认账号，并在独立浏览器验收示例页浏览、登录返回、预填、刷新、问答、引用及断流后的轮询。上线后将同一 HTTPS 助手地址用于 PPT 链接和二维码，并在最终 PPT 与手机上各验证一次。
