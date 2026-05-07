# HubOS FAQ

## Installation & Startup

### How do I install HubOS?

Install via pip:

```bash
pip install hubos
```

Then run:

```bash
hubos app
```

Open http://127.0.0.1:8088 in your browser to access the dashboard.

---

### How do I update HubOS?

Choose the method matching your installation type:

1. **pip install**:

```bash
pip install --upgrade hubos
```

2. **Source install**:

```bash
cd HubOS
git pull origin main
pip install -e .
```

3. **Docker**:

```bash
docker pull hubos/hubos:latest
docker run -p 127.0.0.1:8088:8088 -v hubos-data:/app/working hubos/hubos:latest
```

After upgrading, restart with `hubos app`.

---

### Why is the page blank after startup?

1. Confirm the backend started successfully (no errors in terminal)
2. Check if port 8088 is already in use: `lsof -i :8088`
3. Clear your browser cache and try again
4. Try http://127.0.0.1:8088 instead of localhost

---

## Channel Configuration

### How do I connect WeChat?

Go to **Channels → WeChat** in the dashboard and enter your Bot Token. WeChat requires WxHub service to be running and logged in.

### How do I connect DingTalk?

Create an app in the [DingTalk Open Platform](https://open.dingtalk.com/), get the Client ID and Client Secret, then enter them in **Channels → DingTalk** with Stream mode enabled.

### How do I connect Feishu (Lark)?

Create an app in the [Feishu Open Platform](https://open.feishu.cn/), then fill in the App ID, App Secret, and other details in **Channels → Feishu**.

---

## Agent Configuration

### How do I change the model used by an Agent?

Go to **Agent Config → Models** in the dashboard, or edit `~/.hubos/workspaces/<agent-id>/agent.json` directly.

### How do I add new Skills?

- **Built-in skills**: Enable/disable in the **Skills** page of the dashboard
- **Custom skills**: Place your skill directory in `~/.hubos/skill_pool/` and register it in `skill.json`

### How do I configure MCP tools?

Go to the **MCP** page in the dashboard to add MCP client configurations, including command, args, and required environment variables.

---

## Troubleshooting

### The agent isn't responding to messages. What should I do?

1. Check that the **Channel** is enabled
2. Confirm new sessions are being created in the **Sessions** page
3. Check log files in `~/.hubos/logs/`
4. Verify that your LLM model API Key is correctly configured

### How do I reset to default configuration?

Delete or back up `~/.hubos/workspaces/<agent-id>/agent.json`. A default configuration will be generated on next startup.

---

## More Help

- **GitHub Issues**: https://github.com/agentscope-ai/HubOS/issues
- **Changelog**: See the **Changelog** button at the top of the dashboard
