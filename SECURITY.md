# Security Policy

If you believe you've found a security issue in HubOS, please report it privately.

## Reporting

Please report security vulnerabilities by emailing **security@hubos.dev** or
using [GitHub Security Advisories](https://github.com/hubos-ai/HubOS/security/advisories/new).

### Required in Reports

1. **Title**
2. **Severity assessment**
3. **Impact**
4. **Affected component** (e.g. channel adapter, skill, config loading)
5. **Technical reproduction steps**
6. **Demonstrated impact** (how it crosses a trust boundary, not just theoretical)
7. **Environment** (Python version, OS, how HubOS is run)
8. **Remediation advice** (if you have suggestions)

Reports without reproduction steps, demonstrated impact, and remediation advice will be deprioritized. Given the volume of scanner or AI-generated findings, we need vetted reports from researchers who understand the issues.

### Report Acceptance Gate

For fastest triage, include all of the following:

- Exact vulnerable path (file, function, and line range) on a current revision.
- Tested version details (HubOS version and/or commit SHA).
- Reproducible PoC against latest `main` or latest released version.
- Demonstrated impact tied to HubOS's documented trust boundaries (see below).
- For exposed-secret reports: proof the credential is HubOS-owned or grants access to HubOS-operated infrastructure/services.
- Scope check explaining why the report is **not** covered by the Out of Scope section below.

Reports that miss these requirements may be closed as `invalid` or `no-action`.

### Common False-Positive Patterns

- Prompt-injection-only chains without a boundary bypass (prompt injection is out of scope).
- Operator-intended local features (e.g. skills or commands the operator explicitly enabled) presented as remote injection.
- Authorized user–triggered actions presented as privilege escalation (e.g. an allowed sender triggering a skill that writes to an allowed path). In this trust model, authorized user actions are trusted unless you demonstrate an auth/sandbox/boundary bypass.
- Reports that only show a malicious skill executing privileged actions after a trusted operator installs/enables it.
- Reports that assume per-user multi-tenant authorization on a shared HubOS instance/config.
- ReDoS/DoS claims that require trusted operator configuration input without a trust-boundary bypass.
- Scanner-only claims against stale or nonexistent paths, or claims without a working repro.

### Duplicate Report Handling

- Search existing advisories and issues before filing.
- Include likely duplicate advisory IDs in your report when applicable.
- Maintainers may close lower-quality or later duplicates in favor of the earliest high-quality canonical report.

## Alignment with `hubos init`

The security model and recommended baseline in this document are **aligned with** what users see and accept during **`hubos init`**. The init flow presents a security summary and requires explicit acceptance before the agent gains tool access. If you change security-relevant settings after init, the recommendations here still apply.

## Trust Boundaries

HubOS runs as a local process under the operator's user account. The primary trust boundaries are:

1. **Network ↔ HubOS**: inbound messages from channels (Feishu, WeChat, etc.) and outbound tool calls (shell, browser, file I/O).
2. **User ↔ Agent**: authorized senders can instruct the agent to use tools; unauthorized senders are rejected by channel-level authentication.
3. **Agent ↔ Filesystem**: agents read/write within their workspace and any paths granted via tool guard / file guard policies.
4. **Agent ↔ Network**: agents make outbound requests (LLM APIs, web crawls, email); they do not listen on any port.

## Out of Scope

The following are **not** considered security vulnerabilities in HubOS:

- **Prompt injection** from authorized senders: an authorized user telling the agent to do something (even unexpected) is the intended behavior. If the user has access, the agent has access.
- **Local privilege escalation** from HubOS to the host OS: HubOS intentionally runs arbitrary commands and scripts via skills. This is by design.
- **Dependency vulnerabilities** without a demonstrated attack path through HubOS code. Report these to the upstream dependency.
- **Issues in third-party LLM services** (e.g. model hallucination, bias, data handling by the LLM provider).
- **Information disclosure via LLM prompt/context**: the agent's system prompt, memory files, and session history are local files accessible to the operator by design.

## Supported Versions

| Version | Supported |
|---------|-----------|
| main (latest) | ✅ |
| Release tags | ✅ |
| Older branches | ❌ |

## Security Best Practices

When deploying HubOS:

1. **Enable authentication** (`HUBOS_AUTH_ENABLED=true`) for any non-local deployment.
2. **Restrict channel access** to trusted senders only.
3. **Review tool guard policies** before enabling new skills.
4. **Keep dependencies updated** (`pip install --upgrade hubos`).
5. **Run behind a reverse proxy** (nginx, Caddy) for TLS termination if exposing to a network.
6. **Back up `~/.hubos.secret/`** — this directory contains API keys and credentials.
