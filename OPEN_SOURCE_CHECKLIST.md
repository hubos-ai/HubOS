# HubOS 开源准备清单

## 🔴 必须完成（开源前阻断项）

### A. 代码中的硬编码个人路径
- [ ] A1. `src/hubos/agents/tools/customer_dev/scripts/phase3_4_super_crawler.py:24` — `/Users/allen/projects/super-crawler` → 环境变量 `SUPER_CRAWLER_DIR`
- [ ] A2. `src/hubos/agents/tools/customer_dev/scripts/phase3_4_super_crawler.py:169` — `/Users/allen/.openclaw/...` → 删除或改为配置
- [ ] A3. `src/hubos/agents/tools/customer_development.py:492` — `/Users/allen/projects/super-crawler` → 同 A1 环境变量
- [ ] A4. `src/hubos/agents/tools/file_io.py:90` — 注释中 `rd=/Users/allen/HubOS` → 改为通用示例
- [ ] A5. `scripts/test_local_memory_store.py:28` — `/Users/allen/HubOS/src` → 相对路径
- [ ] A6. `scripts/test_memory_protocol.py:21` — 同上
- [ ] A7. `scripts/e2e_inprocess_delegate.py:67` — 同上
- [ ] A8. `scripts/probe_inprocess_runtime.py:23` — 同上

### B. 阿里云/agentscope.io 域名引用
- [ ] B1. `SECURITY.md:9` — Alibaba Security Response Center (ASRC) → 改为 GitHub Security Advisory 或邮箱
- [ ] B2. `src/hubos/app/routers/console.py:78` — `runtime.agentscope.io` → 移除或改为通用说明
- [ ] B3. `src/hubos/local_models/manager.py:19` — `download.hubos.agentscope.io` → 移除或改为 HuggingFace
- [ ] B4. `.github/workflows/deploy-website.yml` — `hubos.agentscope.io` 域名 → 改为你自己的域名或注释掉
- [ ] B5. `.github/workflows/issue-welcome.yml:111,179` — `hubos.agentscope.io/copaw_ip.svg` → 移除或替换
- [ ] B6. `.github/workflows/pr-welcome.yml:149,206` — 同 B5

### C. Docker/镜像引用
- [ ] C1. `deploy/Dockerfile` — 阿里云容器镜像 `agentscope-registry.*.cr.aliyuncs.com` → 标准基础镜像
- [ ] C2. `.github/workflows/docker-release.yml:23` — `agentscope/hubos` → 新镜像名
- [ ] C3. `docker-compose.yml:11` — `agentscope/hubos:latest` → 同 C2
- [ ] C4. `console/public/faq.zh.md:44-45` — `docker pull agentscope/hubos` → 同 C2
- [ ] C5. `console/public/faq.en.md:44-45` — 同 C4
- [ ] C6. `console/dist/faq.zh.md:44-45` — 同 C4（dist 是构建产物，可能需重新 build）
- [ ] C7. `console/dist/faq.en.md:44-45` — 同 C6

### D. OpenClaw 业务残留
- [ ] D1. `src/hubos/agents/tools/customer_development.py:4` — 注释 "wraps the Hunter/OpenClaw pipeline" → 改为 HubOS 描述
- [ ] D2. `src/hubos/agents/tools/customer_dev/scripts/phase3_4_super_crawler.py:284` — `openclaw-tools.js` 路径 → 环境变量或配置
- [ ] D3. `docs/COPAW_API_COMPATIBILITY_MATRIX.md` — 整文件删除

---

## 🟡 应该完成（提升开源质量）

### E. 开源社区文件
- [ ] E1. 创建 `CONTRIBUTING.md` — 开发环境搭建、PR 流程、代码规范（pre-commit）
- [ ] E2. 创建 `CODE_OF_CONDUCT.md` — Contributor Covenant v2.1
- [ ] E3. 创建 `CHANGELOG.md` — 初始版本记录
- [ ] E4. 更新 `SECURITY.md` — 完整重写，去掉所有阿里引用

### F. pyproject.toml 补全
- [ ] F1. 添加 `license = {text = "Apache-2.0"}`
- [ ] F2. 添加 `authors` 字段
- [ ] F3. 添加 `classifiers` 列表
- [ ] F4. 添加 `[project.urls]` Homepage / Repository / Issues

### G. README 更新
- [ ] G1. 移除私有仓库 clone URL → 改为公开仓库 URL（占位符，发布时更新）
- [ ] G2. 添加英文版 README（或 README 开头加英文简介）
- [ ] G3. 添加截图/GIF 展示（可选，但大大提升第一印象）
- [ ] G4. 添加 "Quick Start" 30 秒上手指南

### H. CI Workflows 清理
- [ ] H1. `publish-pypi.yml` — 确认 PyPI 发布配置正确
- [ ] H2. `desktop-release.yml` — 评估是否保留（需 macOS/Windows runner）
- [ ] H3. `first-time-contributor-welcome.yml` — 检查品牌引用，基本 OK

### I. 文档清理
- [ ] I1. `docs/HUBOS_UI_ADAPTATION_PLAN.md` — 删除（内部计划，已过时）
- [ ] I2. `docs/REMAINING_PAGES_DECISION_MATRIX.md` — 删除（内部决策）
- [ ] I3. `docs/STAGE1_UI_ACCEPTANCE_REPORT.md` — 删除（内部报告）
- [ ] I4. `docs/HUBOS_SESSIONS_PRODUCT_AND_API_PLAN.md` — 评估保留或删除
- [ ] I5. 保留的文档加英文头部说明

---

## 🟢 可选（锦上添花）

### J. 发布前最终检查
- [ ] J1. `git log --all -p | grep -i "api_key\|password\|secret"` 确认历史无泄露
- [ ] J2. 新用户空仓库测试：`git clone → pip install → hubos init → hubos app`
- [ ] J3. 确认 GitHub repo Settings → 代码所有者、分支保护、Issue 模板
- [ ] J4. 打 tag v0.1.0，发布 Release

---

**总计：8(必须) + 10(必须) + 15(建议) + 4(可选) = 37 项**
**预计：Day 1 完成 A-D，Day 2 完成 E-I，Day 3 完成 J**
