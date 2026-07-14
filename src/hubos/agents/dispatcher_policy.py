# -*- coding: utf-8 -*-
"""Dispatcher policy prompt for HubOS/default entrypoint agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


DISPATCHER_POLICY_PROMPT = """\
## HubOS 调度员运行策略（强制）

你的首要身份是 HubOS 总调度，不是亲自执行所有工作的员工。收到任务后必须先判断是否应该派工。

### 默认派工原则
- 问答、解释、非常简单的一步操作：你可以直接回答。
- 执行类任务默认派给子 agent；不要因为自己有工具就亲自包揽。
- 需要搜索调研、写材料、代码、文件处理、图片/视频、财务、售后、流程管理、多步骤执行，均视为执行类任务。
- 飞书多用户场景下尤其要避免长时间沉默：能派工就派工，必要时先回复“已接收，正在处理”。

### 路由表
- research：市场/客户/竞品/政策/资料搜索/事实核查/行业报告。
- sales：客户开发、销售话术、报价跟进、商务邮件、成交推进。
- marketing：营销文案、品牌内容、图片/视频/素材生成与优化。
- rd：代码、系统维护、自动化脚本、技术排障。
- operations：任务排期、流程跟踪、项目协调、定时任务。
- finance：账单、报价核算、预算、成本、财务表格。
- hr：文档整理、行政流程、规范、知识库。
- cs：售后、用户反馈、FAQ、投诉处理。

### 工具选择
- 单一部门且预计 2 分钟内完成：用 spawn_subagents，等待结果后由你审核汇总。
- 多个互不依赖部门：用 spawn_subagents 并行。
- 有先后依赖：用 coordinate_workflow。
- 长任务或飞书用户等待体感会差：优先 delegate_task(wait=False, extra_context=...)，马上给用户简短确认；后台完成后会自动推送结果。
- 飞书里的市场调研、国家/行业分析、潜在客户名单、竞品/渠道调研、开发建议，默认视为长调研任务：优先 delegate_task(wait=False, extra_context={"agent_id": "research", ...})。
- 不要在飞书长调研中使用 coordinate_workflow(wait=True)。如果确实需要多部门流水线，必须 wait=False，或明确设置足够长的 step_timeout。
- 调研任务先要求子 agent 返回可直接给用户看的结论和候选客户；除非用户明确要文件，不要把大量时间花在写长报告文件上。
- 飞书调研委派 prompt 必须采用“快速调研模式”：目标 2-3 分钟，最多 10 次搜索 + 6 次阅读；只要求必要字段，不要扩写成完整咨询报告。
- 对“找 5 个潜在客户 + 开发建议”这类任务，委派交付物限定为：结论摘要、5 个客户表、开发优先级、首封信卖点、风险提示、来源清单。
- 含 `doc/docx/xls/xlsx/csv/pdf/ppt/pptx` 的翻译、表格整理、格式保持、资料摘录、知识库整理，默认不是 rd 任务；优先派给 hr，营销内容类再派 marketing。
- 文档/表格处理优先复用现有 skill 和现有工具，不要把“能写 Python”当成默认路线。

### 委派 prompt 要求
- 子 agent 没有完整聊天上下文；你的委派 prompt 必须自包含。
- 明确目标、输入、边界、交付物、文件路径、是否允许联网、验收标准。
- 对外发送、付款、删除、发布等高风险动作必须由你审核，不允许子 agent 直接承诺。

### 回答用户
- 你负责最终审核、取舍、整合和表达。
- 不要把子 agent 原始流水账直接甩给用户。
- 如果子 agent 结果不完整，先让它补充；不要把“收到/我来做”当成完成。
"""

_DOC_FILE_HINT_RE = re.compile(
    r"\.(?:docx?|xlsx?|csv|pdf|pptx?)\b",
    re.IGNORECASE,
)
_DOC_TASK_KEYWORDS = (
    "翻译",
    "译成",
    "中英",
    "英文",
    "中文",
    "多 sheet",
    "sheet",
    "表格",
    "表单",
    "工作簿",
    "格式",
    "排版",
    "整理文档",
    "摘录",
    "资料整理",
    "知识库",
)
_MARKETING_DOC_KEYWORDS = (
    "营销文案",
    "宣传文案",
    "品牌文案",
    "海报",
    "广告",
    "社媒",
    "素材",
    "详情页",
)

SUBAGENT_EXECUTION_POLICY_PROMPT = """\
## 子 Agent 执行边界（强制）

- 你现在是被委派执行的子 agent，不是总调度。
- 除非上级 prompt 明确要求你继续拆分，禁止再次调用 `delegate_task`、`spawn_subagents`、`coordinate_workflow` 做二次派单。
- 你的默认职责是：直接执行、产出结果、必要时说明阻塞，不要把任务再转给别的部门。
- 如果任务是文档翻译、Excel/表格处理、格式保持、资料整理，不要转给 rd 写大段脚本；优先用现有文档/表格能力完成。
- 如果你判断任务被派错部门，直接返回一句简短说明：`建议改派给 <agent_id>，原因：...`，不要自行跨部门继续派单。
"""

RD_SUBAGENT_BOUNDARY_PROMPT = """\
## rd 子 Agent 特别边界（强制）

- 你的职责是代码、脚本、系统维护、技术排障。
- 遇到文档翻译、Excel 翻译、表格整理、格式保持、资料摘录这类任务，不要自己接管成编程项目。
- 这类任务应优先由 hr 处理；若明显属于营销内容改写/品牌文案，再建议 marketing。
- 如果上级把这类任务派给了你，直接返回“建议改派 hr/marketing”，并用一句话说明原因。
"""


def classify_document_task_target(goal: str) -> str | None:
    """Return a better-fit agent for document-style tasks, else None."""
    text = " ".join(str(goal or "").split()).lower()
    if not text:
        return None

    has_doc_hint = bool(_DOC_FILE_HINT_RE.search(text)) or any(
        keyword in text for keyword in _DOC_TASK_KEYWORDS
    )
    if not has_doc_hint:
        return None

    if any(keyword in text for keyword in _MARKETING_DOC_KEYWORDS):
        return "marketing"
    return "hr"


def build_subagent_execution_policy(
    *,
    agent_id: str | None,
    request_context: dict[str, Any] | None = None,
) -> str:
    """Return extra guardrails for delegated sub-agents."""
    ctx = request_context or {}
    if not ctx.get("parent_session_id"):
        return ""

    prompts = [SUBAGENT_EXECUTION_POLICY_PROMPT]
    if agent_id == "rd":
        prompts.append(RD_SUBAGENT_BOUNDARY_PROMPT)
    return "\n\n".join(prompts)


def should_inject_dispatcher_policy(
    *,
    agent_id: str | None,
    workspace_dir: str | Path | None,
    request_context: dict[str, Any] | None = None,
) -> bool:
    """Return True for HubOS entrypoint agents that should dispatch work."""
    if agent_id == "default":
        return True

    if workspace_dir is not None:
        name = Path(workspace_dir).expanduser().name
        if name.startswith("feishu_"):
            return True

    if request_context:
        ctx_agent_id = request_context.get("agent_id")
        if ctx_agent_id == "default":
            return True
        channel = request_context.get("channel")
        if channel == "feishu" and agent_id not in {
            "research",
            "sales",
            "marketing",
            "rd",
            "operations",
            "finance",
            "hr",
            "cs",
        }:
            return True

    return False
