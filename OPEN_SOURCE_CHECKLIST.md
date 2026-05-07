# HubOS 开源准备清单

## ✅ 已完成

### A. 代码中的硬编码个人路径 (commit `e2b2871`)
- [x] A1-A8 全部替换为环境变量/通用路径

### B. 阿里云/agentscope.io 域名引用 (commit `a8151e8`)
- [x] B1. SECURITY.md → GitHub Security Advisory
- [x] B2. console.py → 移除 agentscope.io
- [x] B3. local_models/manager.py → 移除
- [x] B4. deploy-website.yml → 移除
- [x] B5. issue-welcome.yml → 替换图片
- [x] B6. pr-welcome.yml → 同上

### C. Docker/镜像引用 (commit `82e074b`)
- [x] C1. Dockerfile → node:20-slim
- [x] C2. docker-release.yml → hubos/hubos
- [x] C3. docker-compose.yml → hubos/hubos:latest
- [x] C4-C7. faq 文件 → hubos/hubos

### D. OpenClaw 业务残留 (commit `82e074b`)
- [x] D1. customer_development.py → 通用描述
- [x] D2. openclaw-tools.js → tools.js
- [x] D3. 删除 COPAW_API_COMPATIBILITY_MATRIX.md

### E. 开源社区文件 (commit `eac4dfb`)
- [x] E1. CONTRIBUTING.md
- [x] E2. CODE_OF_CONDUCT.md
- [x] E3. CHANGELOG.md
- [x] E4. SECURITY.md 重写

### F. pyproject.toml 补全 (commit `eac4dfb`)
- [x] F1. license
- [x] F2. authors
- [x] F3. classifiers
- [x] F4. project URLs

### G. README 更新 (commit `eac4dfb`)
- [x] G1. clone URL → hubos-ai/HubOS
- [x] G2. 英文头部 + Quick Start

### H. CI Workflows + 引用清理 (commit `eac4dfb`)
- [x] H1. publish-pypi.yml OK
- [x] H2. desktop-release.yml 删除 upload-oss job (220 行)
- [x] H3. dingtalk SKILL.md 移除 alicdn 图片
- [x] H4. docs/ + tests → hubos-ai
- [x] H5. customer_development + super_crawler → 移除 openclaw 路径
- [x] H6. skills_hub + skills_manager → 更新引用

---

## ⬜ 待完成

### I. 文档清理
- [ ] I1. `docs/HUBOS_UI_ADAPTATION_PLAN.md` — 删除（内部计划，已过时）
- [ ] I2. `docs/REMAINING_PAGES_DECISION_MATRIX.md` — 删除（内部决策）
- [ ] I3. `docs/STAGE1_UI_ACCEPTANCE_REPORT.md` — 删除（内部报告）
- [ ] I4. `docs/HUBOS_SESSIONS_PRODUCT_AND_API_PLAN.md` — 评估保留或删除
- [ ] I5. 保留的文档加英文头部说明

### J. 发布前最终检查
- [ ] J1. `git log --all -p | grep -i "api_key\|password\|secret"` 确认历史无泄露
- [ ] J2. 新用户空仓库测试：`git clone → pip install → hubos init → hubos app`
- [ ] J3. 确认 GitHub repo Settings → 代码所有者、分支保护、Issue 模板
- [ ] J4. 打 tag v0.1.0，发布 Release

---

**已完成：30/37 项 (81%)**
**剩余：I 类 5 项 + J 类 4 项 = 9 项**
