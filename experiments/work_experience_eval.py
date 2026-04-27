#!/usr/bin/env -S uv run -p python --script
# -*- coding: utf-8 -*-
"""
Work Experience Layer — Real Task Experiment v2

Design: 10 tasks where model defaults are unreliable/unstable,
but experience tips should meaningfully improve output quality.

Categories:
  1. Channel format constraints (Discord, Slack, WeChat, Telegram)
  2. Tool usage sequencing (retry before fail, check before use)
  3. Error handling conventions (specific backoff, logging format)
  4. Organizational conventions (timezones, naming, no-API-keys-in-logs)
  5. Security/privacy policy (no PII in output, no creds exposure)

Each task runs with flag OFF, then flag ON. Differences in:
  - Retrieved cards
  - Injected hint text
  - Output quality (relevance to constraints)
  - hit_count / effective_count changes
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

LOG_FORMAT = "%(message)s"
logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT, stream=sys.stdout)
for noisy in ["PIL", "httpx", "httpcore", "openai", "urllib3", "werkzeug"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)


# =============================================================================
# Test Tasks
# =============================================================================


@dataclass
class ExperimentTask:
    input_text: str
    description: str
    category: str
    # What the experience card teaches
    card_title: str
    card_keywords: list[str]
    card_trigger_hint: str  # must match first 10+ chars of input first-key value
    card_guidance: str
    card_avoidance: str
    # What to check in output
    success_indicator: str  # text that should appear in ON output but not OFF
    failure_indicator: str  # text that might appear in OFF output


TASKS = [
    # ── 1. Discord Message Formatting ──────────────────────────────────────
    ExperimentTask(
        input_text="send a message to the #general channel saying the deployment succeeded",
        description="Discord notification message",
        category="Channel Format",
        card_title="Discord notifications: 2000-char limit + code block convention",
        card_keywords=[
            "discord",
            "notification",
            "2000",
            "codeblock",
            "markdown",
        ],
        card_trigger_hint="input_text:send",
        card_guidance="Discord messages max 2000 chars; use code blocks (```) for multi-line output; start with ✅/❌ emoji for status; channel name in #format",
        card_avoidance="Do not send plain text paragraphs over 500 chars; do not use HTML in Discord",
        success_indicator="```",
        failure_indicator="plain paragraph without code block",
    ),
    # ── 2. Slack Block Kit Formatting ──────────────────────────────────────
    ExperimentTask(
        input_text="post to #alerts channel a Slack message with the server uptime report",
        description="Slack Block Kit message",
        category="Channel Format",
        card_title="Slack Block Kit: sections, mrkdwn, 3000-char limit",
        card_keywords=["slack", "block", "section", "mrkdwn", "3000"],
        card_trigger_hint="input_text:post",
        card_guidance="Use Block Kit (section blocks, divider); max 3000 chars per message; use mrkdwn for formatting; put metric values in bold",
        card_avoidance="Do not use HTML tags in Slack; do not exceed 3000 chars; do not use legacy attachments",
        success_indicator="section",
        failure_indicator="plain text without block formatting",
    ),
    # ── 3. WeChat Work Notification ─────────────────────────────────────────
    ExperimentTask(
        input_text="send a WeChat work notification to the ops channel about the failed cron job",
        description="WeChat Work (企业微信) webhook format",
        category="Channel Format",
        card_title="WeChat Work webhook: XML template, markdown supported",
        card_keywords=["wechat", "企业微信", "webhook", "xml", "markdown"],
        card_trigger_hint="input_text:send",
        card_guidance="Use XML message format for WeChat Work webhook; markdown supported within Content tag; include MsgType tag; endpoint requires HTTPS",
        card_avoidance="Do not use plain JSON; do not send to personal chat without approval; do not include HTML entities",
        success_indicator="MsgType",
        failure_indicator="json payload",
    ),
    # ── 4. Telegram Bot Message Formatting ─────────────────────────────────
    ExperimentTask(
        input_text="send a Telegram message via bot about the daily report being ready",
        description="Telegram bot HTML formatting",
        category="Channel Format",
        card_title="Telegram: HTML formatting, 4096-char limit, parse_mode=HTML",
        card_keywords=["telegram", "bot", "html", "4096", "parse_mode"],
        card_trigger_hint="input_text:send",
        card_guidance="Use HTML formatting (<b>, <i>, <code>, <pre>); max 4096 chars per message; set parse_mode='HTML'; use <a href> for links; split long messages with ... continuation",
        card_avoidance="Do not use MarkdownV2; do not send messages over 4096 chars in one piece",
        success_indicator="<b>",
        failure_indicator="plain text or wrong parse_mode",
    ),
    # ── 5. Retry Before Fail Convention ─────────────────────────────────────
    ExperimentTask(
        input_text="call the /api/users endpoint and if it fails, report the error",
        description="API call with retry-before-fail convention",
        category="Tool Sequencing",
        card_title="API calls: retry 3x with exponential backoff before reporting failure",
        card_keywords=[
            "api",
            "retry",
            "backoff",
            "exponential",
            "401",
            "429",
            "timeout",
        ],
        card_trigger_hint="input_text:call",
        card_guidance="Retry failed API calls 3 times with exponential backoff (1s, 2s, 4s); handle 401 by refreshing token first; handle 429 by respecting Retry-After header; only report failure after all retries exhausted",
        card_avoidance="Do not immediately report failure on first error; do not retry indefinitely without backoff",
        success_indicator="retry",
        failure_indicator="immediately report error",
    ),
    # ── 6. Check robots.txt Before Crawling ─────────────────────────────────
    ExperimentTask(
        input_text="crawl https://example.com and extract all article titles and links",
        description="Web crawl with robots.txt check",
        category="Tool Sequencing",
        card_title="Web crawling: always check robots.txt first, set User-Agent",
        card_keywords=[
            "crawl",
            "robots.txt",
            "requests",
            "beautifulsoup",
            "user-agent",
        ],
        card_trigger_hint="input_text:crawl",
        card_guidance="Always check robots.txt before crawling; set identifiable User-Agent header; respect robots disallow directives; add 1s delay between requests; handle 403/robots blocked gracefully with user warning",
        card_avoidance="Do not crawl without checking robots.txt; do not set aggressive crawl rates",
        success_indicator="robots.txt",
        failure_indicator="skip robots.txt check",
    ),
    # ── 7. API Rate Limit Backoff ────────────────────────────────────────────
    ExperimentTask(
        input_text="fetch data from the rate-limited /api/data endpoint for 50 records",
        description="API with rate limiting and backoff",
        category="Error Handling",
        card_title="Rate-limited API: always read and respect Retry-After header on 429",
        card_keywords=[
            "rate",
            "limit",
            "429",
            "retry-after",
            "paginate",
            "backoff",
        ],
        card_trigger_hint="input_text:fetch",
        card_guidance="On 429: MUST read Retry-After header — wait exactly that many seconds before retry; also implement exponential backoff starting at 2s for cases without Retry-After; use pagination",
        card_avoidance="Do not ignore 429 responses; do not make parallel requests when rate limited; do not retry without waiting",
        success_indicator="Retry-After",
        failure_indicator="ignore 429 and retry immediately",
    ),
    # ── 8. No API Keys in Logs ──────────────────────────────────────────────
    ExperimentTask(
        input_text="log the API response from the user service for debugging",
        description="Security: prevent API key exposure in logs",
        category="Security Policy",
        card_title="Security: never log API keys, tokens, or credentials",
        card_keywords=[
            "log",
            "api",
            "key",
            "token",
            "credential",
            "secret",
            "debug",
        ],
        card_trigger_hint="input_text:log",
        card_guidance="Before logging any API response, redact all credential fields (api_key, api_key_id, secret, token, Authorization header values); use [REDACTED] placeholder; never log full request/response objects without sanitization",
        card_avoidance="Do not log raw API responses; do not print request headers containing Authorization; do not output credentials to stdout/stderr",
        success_indicator="[REDACTED]",
        failure_indicator="sk-cp- or Bearer or api_key",
    ),
    # ── 9. Timezone Convention ──────────────────────────────────────────────
    ExperimentTask(
        input_text="schedule a daily report to run at 9am and send results to the analytics channel",
        description="Timezone-aware scheduling convention",
        category="Organizational Convention",
        card_title="Scheduling: all times stored as UTC, displayed as UTC+8, cron in UTC",
        card_keywords=["schedule", "cron", "timezone", "utc", "9am", "daily"],
        card_trigger_hint="input_text:schedule",
        card_guidance="Store all timestamps in UTC; display times to users in UTC+8; configure cron expressions in UTC; document timezone in schedule comments; if user says '9am' without timezone, assume UTC+8",
        card_avoidance="Do not store or display times without timezone context; do not configure cron in local timezone",
        success_indicator="UTC+8",
        failure_indicator="assuming local timezone",
    ),
    # ── 10. Database Migration Ordering ────────────────────────────────────────
    ExperimentTask(
        input_text="create a new users table with email and created_at columns",
        description="Database migration with dependency ordering",
        category="Tool Sequencing",
        card_title="DB migrations: always add created_at/updated_at, run migrations in order, backup before",
        card_keywords=[
            "migration",
            "database",
            "created_at",
            "updated_at",
            "alter",
            "schema",
        ],
        card_trigger_hint="input_text:create",
        card_guidance="Every table must have created_at (UTC datetime) and updated_at columns; run migrations in sequence (no parallel migration on same table); take a backup snapshot before alter statements; use idempotent up/down migrations",
        card_avoidance="Do not skip created_at/updated_at audit columns; do not run migrations in parallel; do not alter tables without backup",
        success_indicator="created_at",
        failure_indicator="missing audit columns",
    ),
]


# =============================================================================
# Store Builder
# =============================================================================

from hubos.core.work_experience import (
    LocalWorkExperienceStore,
    WorkExperienceExtractor,
)
from hubos.core.work_experience.schemas import (
    WorkExperience,
    WorkExperienceScope,
    WorkExperienceStatus,
)
from hubos.core.orchestrator.reflection_engine import TaskContext
from hubos.core.schemas.memory import ReflectionReport
from hubos.core.schemas.tasks import TaskResult, TaskStatus as TaskStatusEnum


def build_store(root: Path) -> LocalWorkExperienceStore:
    store = LocalWorkExperienceStore(root=root / "we_store")
    (root / "we_store").mkdir(parents=True, exist_ok=True)

    for exp_task in TASKS:
        ctx = TaskContext(
            task_id=f"source-{uuid.uuid4().hex[:8]}",
            session_id=f"session-{uuid.uuid4().hex[:8]}",
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
            task_input={"input_text": exp_task.input_text[:20]},
            execution_trace=[],
            task_result=TaskResult(
                unit_id="u1",
                task_id="source",
                status=TaskStatusEnum.SUCCESS,
                confidence=0.85,
                output_data={},
                artifacts={},
                error_message=None,
                retry_count=0,
                executed_at=None,
            ),
            execution_time_ms=1000,
        )
        rep = ReflectionReport(
            report_id=None,  # type: ignore[arg-type]
            task_id=ctx.task_id,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            what_worked=[f"Applied: {exp_task.card_guidance[:60]}"],
            what_failed=[],
            root_cause="",
            next_time_strategy=exp_task.card_guidance,
            confidence=0.85,
            has_human_feedback=False,
            policy_suggestions=[],
        )
        extractor = WorkExperienceExtractor(store=store)
        card = extractor.extract(rep, ctx)
        if card:
            card.title = exp_task.card_title
            card.scope = WorkExperienceScope.GLOBAL
            card.trigger_keywords = exp_task.card_keywords
            card.trigger_hint = exp_task.card_trigger_hint
            card.what_happened = f"Task: {exp_task.description}"
            card.what_worked = [exp_task.card_guidance[:80]]
            card.what_failed = [exp_task.card_avoidance[:80]]
            card.guidance = exp_task.card_guidance
            card.avoidance = exp_task.card_avoidance
            card.applicability_tags = [
                exp_task.category.lower().replace(" ", "-"),
            ]
            card.confidence = 0.85
            card.status = WorkExperienceStatus.APPROVED
            card.effective_count = 0
            card.hit_count = 0
            store.save(card)

    return store


# =============================================================================
# Experiment Runner
# =============================================================================


def run_comparison(
    store: LocalWorkExperienceStore,
    task: ExperimentTask,
) -> dict[str, Any]:
    """Run flag OFF then flag ON, return structured comparison."""
    import time
    from hubos.core.execution.task_store import TaskStore
    from hubos.core.execution.orchestrator import ExecutionOrchestrator
    from hubos.core.execution.event_store import EventStore
    from hubos.core.work_experience.integration import (
        get_work_experience_interceptor,
    )
    from hubos.core.work_experience.retriever import WorkExperienceRetriever
    from hubos.core.llm.runtime import get_llm_runtime
    from hubos.core.infra.feature_flags import reload_feature_flags
    import hubos.core.work_experience.integration as integ_mod

    session_off = f"off-{uuid.uuid4().hex[:8]}"
    session_on = f"on-{uuid.uuid4().hex[:8]}"

    results = {}

    for label, enabled, session_id in [
        ("OFF", False, session_off),
        ("ON", True, session_on),
    ]:
        flags_env = {
            "ENABLE_WORK_EXPERIENCE_LAYER": "true" if enabled else "false",
            "ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true"
            if enabled
            else "false",
        }
        orig = {k: os.environ.get(k) for k in flags_env}
        for k, v in flags_env.items():
            os.environ[k] = v
        reload_feature_flags()
        integ_mod._interceptor = None

        try:
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._retriever = WorkExperienceRetriever(
                store=store,
                max_results=5,
            )

            # Create task
            ts = TaskStore()
            t = ts.create_task(
                input_text=task.input_text,
                session_id=session_id,
            )

            # Capture pre counts BEFORE pre_execute() — pre_execute() increments hit_count
            pre_counts = {}
            # We don't know which cards will be retrieved yet, so we snapshot ALL cards
            # and compare post to see which ones were actually hit
            all_pre = {
                str(c.experience_id): (c.hit_count, c.effective_count)
                for c in store.list_all()
            }

            # Retrieve cards
            cards = interceptor.pre_execute(t)

            # Map card ids from retrieved cards
            retrieved_ids = [c.get("experience_id") for c in cards]

            # Call LLM with/without cards
            rt = get_llm_runtime()
            ctx = {"task_id": t.task_id, "work_experience_cards": cards or []}
            result = rt.generate_for_stage(
                "info",
                task.input_text,
                context=ctx,
            )

            # Phase 5-B: record effective use after successful LLM generation
            # This mirrors what the orchestrator does in _execute_stage_real()
            if result.success and cards:
                try:
                    interceptor.record_effective_uses(cards)
                except Exception as exc:
                    print(f"  [record_effective_uses warning] {exc}")

            # Post counts — compare only retrieved cards
            post_counts = {}
            for cid in retrieved_ids:
                if cid:
                    try:
                        card = store.get(uuid.UUID(cid))
                        if card:
                            post_counts[cid] = (
                                card.hit_count,
                                card.effective_count,
                            )
                    except Exception:
                        pass

            # Compute deltas using all_pre as baseline for retrieved cards
            hit_delta = {}
            eff_delta = {}
            for cid in retrieved_ids:
                if cid:
                    pre = all_pre.get(cid, (0, 0))
                    post = post_counts.get(cid, pre)
                    hit_delta[cid] = post[0] - pre[0]
                    eff_delta[cid] = post[1] - pre[1]

            results[label] = {
                "cards_retrieved": len(cards),
                "card_titles": [c.get("title", "")[:60] for c in cards],
                "card_ids": [c.get("experience_id", "")[:8] for c in cards],
                "response_text": result.text,
                "response_chars": len(result.text),
                "hit_count_delta": hit_delta,
                "effective_delta": eff_delta,
                "success_indicator_found": task.success_indicator.lower()
                in result.text.lower(),
                "failure_indicator_found": task.failure_indicator.lower()
                in result.text.lower(),
            }
        finally:
            for k, v in orig.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            reload_feature_flags()

    return results


def check_trigger_hint_matching(store: LocalWorkExperienceStore) -> None:
    """Verify trigger_hint prefix matching vs keyword-only fallback."""
    from hubos.core.work_experience.retriever import WorkExperienceRetriever

    retriever = WorkExperienceRetriever(store=store, max_results=5)

    print("\n  [trigger_hint matching test]")
    for task in TASKS[:3]:
        # Build task_input as the interceptor does
        task_input = {
            "input_text": task.input_text,
            "session_id": "test",
            "channel": "api",
        }
        cards = retriever.retrieve_for_task(task_input)

        # Replicate trigger_hint building
        import re

        first_key = next(iter(task_input.keys()))
        first_val = str(task_input[first_key])[:10].lower().replace(" ", "_")
        task_hint = f"{first_key}:{first_val}"

        # Check if trigger_hint prefix matched
        trigger_match = (
            any(c.trigger_hint.startswith(task_hint) for c in cards)
            if cards
            else False
        )

        print(f"    Task: {task.description[:50]}")
        print(f"      task_hint = '{task_hint}'")
        print(f"      card hints = {[c.trigger_hint for c in cards]}")
        print(f"      trigger_prefix_match = {trigger_match}")
        print(f"      cards retrieved = {len(cards)}")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("Work Experience Layer — Real Task Experiment v2")
    print("=" * 80)

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        print(
            f"\n[Building store with {len(TASKS)} APPROVED experience cards]",
        )
        store = build_store(root)
        all_cards = store.list_all(include_disabled=False)
        print(f"  → {len(all_cards)} cards stored")

        # trigger_hint matching test
        check_trigger_hint_matching(store)

        print(f"\n[Running A/B experiments]")
        experiment_results = []
        for i, task in enumerate(TASKS):
            print(f"\n  Task {i+1}/{len(TASKS)}: {task.description[:60]}")
            try:
                r = run_comparison(store, task)
                experiment_results.append((task, r))

                off = r["OFF"]
                on = r["ON"]
                print(
                    f"    OFF: {off['cards_retrieved']} cards, {off['response_chars']} chars, "
                    f"success_indicator={off['success_indicator_found']}",
                )
                print(
                    f"    ON:  {on['cards_retrieved']} cards, {on['response_chars']} chars, "
                    f"success_indicator={on['success_indicator_found']}",
                )
                if on["card_titles"]:
                    print(f"         cards: {on['card_titles'][0][:60]}")

                delta_hit = sum(on["hit_count_delta"].values())
                delta_eff = sum(on["effective_delta"].values())
                print(f"         hit Δ={delta_hit}, effective Δ={delta_eff}")
            except Exception as e:
                print(f"    ERROR: {e}")
                experiment_results.append((task, None))

        # =====================================================================
        # Report
        # =====================================================================
        print("\n" + "=" * 80)
        print("STRUCTURED EXPERIMENT REPORT")
        print("=" * 80)

        for i, (task, r) in enumerate(experiment_results):
            print(f"\n{'─' * 80}")
            print(f"[{i+1}] {task.description}")
            print(f"    Category: {task.category}")
            print(f"    Input: {task.input_text[:70]}...")

            if r is None:
                print(f"    ERROR — skipped")
                continue

            off, on = r["OFF"], r["ON"]

            print(f"\n    CARD RETRIEVAL:")
            print(f"      OFF: {off['cards_retrieved']} cards")
            print(
                f"      ON:  {on['cards_retrieved']} cards — {[c[:50] for c in on['card_titles']]}",
            )

            print(f"\n    OUTPUT INDICATORS:")
            print(f"      Success indicator '{task.success_indicator}'")
            print(f"        OFF: {off['success_indicator_found']}")
            print(f"        ON:  {on['success_indicator_found']}")
            print(f"      Failure indicator '{task.failure_indicator}'")
            print(f"        OFF: {off['failure_indicator_found']}")
            print(f"        ON:  {on['failure_indicator_found']}")

            print(f"\n    COUNTERS:")
            if on["effective_delta"]:
                for cid, delta_eff in on["effective_delta"].items():
                    delta_hit = on["hit_count_delta"].get(cid, 0)
                    print(
                        f"      {cid[:8]}: hit {delta_hit:+d}, effective {delta_eff:+d}",
                    )
            else:
                print(f"      (no cards retrieved)")

            print(f"\n    INJECTION CONTENT (ON):")
            inj_text = on["response_text"]
            inj_start = inj_text.find("[Relevant Past Experience]")
            inj_end = inj_text.find("[/Relevant Past Experience]")
            if inj_start >= 0:
                raw_injection = inj_text[
                    inj_start : min(inj_end + 30, inj_start + 600)
                ]
                # Show just the hint lines
                for line in raw_injection.split("\n"):
                    if ":" in line and not line.startswith("["):
                        print(f"      {line[:120]}")
            else:
                print(f"      (injection marker not visible in response)")

            print(f"\n    COMPARISON:")
            improved = (
                on["success_indicator_found"]
                and not off["success_indicator_found"]
            ) or (
                not on["failure_indicator_found"]
                and off["failure_indicator_found"]
            )
            degraded = (
                not on["success_indicator_found"]
                and off["success_indicator_found"]
            ) or (
                on["failure_indicator_found"]
                and not off["failure_indicator_found"]
            )
            neutral = not improved and not degraded
            status = (
                "✅ IMPROVED"
                if improved
                else "❌ DEGRADED"
                if degraded
                else "➖ NEUTRAL"
            )
            print(f"      Result: {status}")

        # Summary
        print(f"\n{'═' * 80}")
        print("SUMMARY")
        print(f"{'═' * 80}")
        improved = sum(
            1
            for _, r in experiment_results
            if r is not None
            and (
                (
                    r["ON"]["success_indicator_found"]
                    and not r["OFF"]["success_indicator_found"]
                )
                or (
                    not r["ON"]["failure_indicator_found"]
                    and r["OFF"]["failure_indicator_found"]
                )
            )
        )
        degraded = sum(
            1
            for _, r in experiment_results
            if r is not None
            and (
                (
                    not r["ON"]["success_indicator_found"]
                    and r["OFF"]["success_indicator_found"]
                )
                or (
                    r["ON"]["failure_indicator_found"]
                    and not r["OFF"]["failure_indicator_found"]
                )
            )
        )
        neutral = len(experiment_results) - improved - degraded
        print(f"\n  Improved:  {improved}/{len(experiment_results)}")
        print(f"  Degraded:  {degraded}/{len(experiment_results)}")
        print(f"  Neutral:   {neutral}/{len(experiment_results)}")
        print(
            f"\n  Note: 'Neutral' means the model already knew the convention,",
        )
        print(
            f"  or the injected hint was absorbed by the model without visible change.",
        )
        print(f"  This is expected for well-known conventions.")
