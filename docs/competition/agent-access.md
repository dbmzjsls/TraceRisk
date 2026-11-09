# 调查助手访问说明

公网入口待提供。当前验证范围为本机运行和取证。正式 HTTPS 地址、评审账号、可用期和维护联系人待部署后核验并登记，评审密码由提交系统单独提供。

## 操作路径

登录后打开 `/assistant`。选择企业与基准日，复制[三条提示词](conversations/manifest.json)并发送。示例 03 回到示例 01 的会话继续追问，点击回答中的引用查看本次冻结证据。

## 部署准备

在已授权主机上使用独立的 `tracerisk_demo` 和持久化 PostgreSQL 卷。按 [Compose 生命周期说明](../operations/docker-compose.md)使用默认 Mock/Fake 配置执行 `bootstrap-demo` 和 `verify-target --target tracerisk_demo`。随后在私有配置中设置 `MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY` 和 `JWT_SECRET`，启动真实模型服务。

```powershell
docker compose -f compose.yaml -f compose.competition.yaml config --quiet
docker compose -f compose.yaml -f compose.competition.yaml up -d --build --wait --wait-timeout 180 api worker web
```

覆盖文件把 API 与 Worker 指向同一展示库和模型配置，保留 Fake 检索。HTTPS 入口反向代理 Web 的本机端口，转发 `/assistant` 和同源 `/api`，SSE 路径保持流式传输。公开前配置独立分析员账号，更换或停用默认账号，并在独立浏览器验收登录返回、刷新、问答、引用及断流后的轮询。上线后将同一 HTTPS 助手地址用于 PPT 链接和二维码，并在最终 PPT 与手机上各验证一次。
