# -*- coding: utf-8 -*-
from hubos.agents.dispatcher_policy import should_inject_dispatcher_policy


def test_dispatcher_policy_injected_for_default():
    assert should_inject_dispatcher_policy(
        agent_id="default",
        workspace_dir="/tmp/default",
        request_context={"channel": "console"},
    )


def test_dispatcher_policy_injected_for_feishu_workspace():
    assert should_inject_dispatcher_policy(
        agent_id="feishu_ou_123",
        workspace_dir="/Users/allen/.hubos/workspaces/feishu_ou_123",
        request_context={"channel": "feishu"},
    )


def test_dispatcher_policy_not_injected_for_department_agent():
    assert not should_inject_dispatcher_policy(
        agent_id="research",
        workspace_dir="/Users/allen/.hubos/workspaces/research",
        request_context={"channel": "hubos_core_subagent"},
    )
