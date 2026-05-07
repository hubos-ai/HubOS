# HubOS 常见问题

## 安装与启动

### HubOS 如何安装？

推荐使用 pip 安装：

```bash
pip install hubos
```

安装完成后运行：

```bash
hubos app
```

浏览器访问 http://127.0.0.1:8088 即可打开管理台。

---

### HubOS 如何更新？

根据安装方式选择对应方法：

1. **pip 安装**：

```bash
pip install --upgrade hubos
```

2. **源码安装**：

```bash
cd HubOS
git pull origin main
pip install -e .
```

3. **Docker**：

```bash
docker pull hubos/hubos:latest
docker run -p 127.0.0.1:8088:8088 -v hubos-data:/app/working hubos/hubos:latest
```

升级后重启 `hubos app` 生效。

---

### 为什么启动后页面显示空白？

1. 确认后端已正常启动（终端无报错）
2. 检查端口 8088 是否被占用：`lsof -i :8088`
3. 清除浏览器缓存后重试
4. 尝试使用 http://127.0.0.1:8088（而非 localhost）访问

---

## 频道配置

### 如何接入微信？

在管理台 **频道 → 微信** 页面，填入 Bot Token 后启用。微信频道基于 WxHub 协议，需要确保 WxHub 服务已运行并登录。

### 如何接入钉钉？

参考[钉钉开放平台文档](https://open.dingtalk.com/)创建应用，获取 Client ID 和 Client Secret，在管理台 **频道 → 钉钉** 页面填入并启用 Stream 模式。

### 如何接入飞书？

参考[飞书开放平台文档](https://open.feishu.cn/)创建应用，在管理台 **频道 → 飞书** 页面填入 App ID、App Secret 等信息。

---

## Agent 配置

### 如何修改 Agent 使用的模型？

在管理台 **Agent 配置 → 模型** 页面，或直接编辑 `~/.hubos/workspaces/<agent-id>/agent.json` 中的模型设置。

### 如何添加新技能（Skill）？

- **内置技能**：在管理台 **技能** 页面启用/禁用
- **自定义技能**：将技能目录放入 `~/.hubos/skill_pool/`，并在 `skill.json` 中注册

### 如何配置 MCP 工具？

在管理台 **MCP** 页面添加 MCP 客户端配置，包含 command、args 和所需的环境变量。

---

## 故障排查

### Agent 没有回复消息怎么办？

1. 检查 **频道** 配置是否启用
2. 在管理台 **会话** 页面确认是否有新会话创建
3. 查看 `~/.hubos/logs/` 目录下的日志文件
4. 检查 LLM 模型 API Key 是否正确配置

### 如何重置到默认配置？

删除或备份 `~/.hubos/workspaces/<agent-id>/agent.json`，重启服务后会生成默认配置。

---

## 更多帮助

- **GitHub Issues**：https://github.com/agentscope-ai/HubOS/issues
- **更新日志**：查看管理台顶部 **更新日志** 按钮
