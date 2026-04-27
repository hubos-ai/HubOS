# -*- coding: utf-8 -*-
"""Customer development lead discovery tool.

This tool wraps the Hunter/OpenClaw lead-discovery pipeline for HubOS,
then enriches results with HubOS-only tools (Tavily search, Zhipu webReader,
super-crawler hunter_domain).

Pipeline:
    Phase 1 (openclaw): Generate search terms
    Phase 2 (openclaw): xcrawl search for candidate URLs
    Phase 2.5 (HubOS): Tavily search fallback when xcrawl results are poor
    Phase 3 (openclaw): super_crawler deep crawl + email discovery
    Phase 3.5 (HubOS): Zhipu webReader enrichment + hunter_domain retry
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)

# HubOS 自带的客户开发脚本和配置（独立于 openclaw）
_TOOLS_DIR = Path(__file__).parent / "customer_dev"
DEFAULT_SCRIPTS_DIR = Path(
    os.environ.get(
        "HUBOS_CUSTOMER_DEV_SCRIPTS_DIR",
        str(_TOOLS_DIR / "scripts"),
    ),
).expanduser()
DEFAULT_CONFIG_PATH = Path(
    os.environ.get(
        "HUBOS_CUSTOMER_DEV_CONFIG_PATH",
        str(_TOOLS_DIR / "config" / "search_terms_v2.json"),
    ),
).expanduser()
DEFAULT_OUTPUT_ROOT = Path(
    os.environ.get(
        "HUBOS_CUSTOMER_DEV_OUTPUT_DIR",
        "~/.hubos/customer_development/runs",
    ),
).expanduser()
HUBOS_AGENT_CONFIG_PATH = Path(
    os.environ.get(
        "HUBOS_MCP_AGENT_CONFIG",
        "~/.hubos/workspaces/default/agent.json",
    ),
).expanduser()

# ---- Country mappings (27 countries) ----

COUNTRY_ALIASES: dict[str, tuple[str, str, str]] = {
    "BR": ("Brazil", "pt-BR", "巴西"),
    "MX": ("Mexico", "es", "墨西哥"),
    "PE": ("Peru", "es-PE", "秘鲁"),
    "CO": ("Colombia", "es-CO", "哥伦比亚"),
    "CL": ("Chile", "es-CL", "智利"),
    "US": ("United States", "en", "美国"),
    "RU": ("Russia", "ru", "俄罗斯"),
    "UZ": ("Uzbekistan", "uz", "乌兹别克斯坦"),
    "KZ": ("Kazakhstan", "kk-KZ", "哈萨克斯坦"),
    "MY": ("Malaysia", "ms-MY", "马来西亚"),
    "PH": ("Philippines", "en-PH", "菲律宾"),
    "VN": ("Vietnam", "vi", "越南"),
    "TH": ("Thailand", "th-TH", "泰国"),
    "ID": ("Indonesia", "id-ID", "印尼"),
    "IN": ("India", "en-IN", "印度"),
    "JP": ("Japan", "ja", "日本"),
    "KR": ("South Korea", "ko", "韩国"),
    "TR": ("Turkey", "tr-TR", "土耳其"),
    "AE": ("United Arab Emirates", "ar", "阿联酋"),
    "SA": ("Saudi Arabia", "ar", "沙特阿拉伯"),
    "EG": ("Egypt", "ar-EG", "埃及"),
    "NG": ("Nigeria", "en-NG", "尼日利亚"),
    "ZA": ("South Africa", "en-ZA", "南非"),
    "DE": ("Germany", "de", "德国"),
    "FR": ("France", "fr-FR", "法国"),
    "GB": ("United Kingdom", "en-GB", "英国"),
    "ES": ("Spain", "es-ES", "西班牙"),
}

COUNTRY_NAME_TO_CODE: dict[str, str] = {
    "uzbekistan": "UZ",
    "uzbekiston": "UZ",
    "乌兹别克斯坦": "UZ",
    "乌兹": "UZ",
    "russia": "RU",
    "俄罗斯": "RU",
    "brazil": "BR",
    "巴西": "BR",
    "mexico": "MX",
    "墨西哥": "MX",
    "peru": "PE",
    "秘鲁": "PE",
    "colombia": "CO",
    "哥伦比亚": "CO",
    "chile": "CL",
    "智利": "CL",
    "malaysia": "MY",
    "马来西亚": "MY",
    "philippines": "PH",
    "菲律宾": "PH",
    "vietnam": "VN",
    "越南": "VN",
    "thailand": "TH",
    "泰国": "TH",
    "indonesia": "ID",
    "印尼": "ID",
    "印度尼西亚": "ID",
    "india": "IN",
    "印度": "IN",
    "turkey": "TR",
    "土耳其": "TR",
    "kazakhstan": "KZ",
    "哈萨克斯坦": "KZ",
    "egypt": "EG",
    "埃及": "EG",
    "nigeria": "NG",
    "尼日利亚": "NG",
    "south africa": "ZA",
    "南非": "ZA",
    "germany": "DE",
    "德国": "DE",
    "france": "FR",
    "法国": "FR",
    "united kingdom": "GB",
    "uk": "GB",
    "英国": "GB",
    "spain": "ES",
    "西班牙": "ES",
    "united states": "US",
    "usa": "US",
    "美国": "US",
    "japan": "JP",
    "日本": "JP",
    "south korea": "KR",
    "韩国": "KR",
    "uae": "AE",
    "阿联酋": "AE",
    "saudi": "SA",
    "沙特": "SA",
    "沙特阿拉伯": "SA",
}

# ---- API Key loading ----


def _load_tavily_keys() -> list[str]:
    """Load all available Tavily API keys (multi-key rotation).

    Sources:
    1) hunter workspace .env — TAVILY_API_KEY, TAVILY_API_KEY_2, ...
    2) hubos config.json — "TAVILY_API_KEY": "..."
    3) env var TAVILY_API_KEY
    """
    keys: list[str] = []
    seen: set[str] = set()

    # 1) hunter workspace .env — collect TAVILY_API_KEY* (all suffixed variants)
    env_path = Path("~/.openclaw/agents/hunter/workspace/.env").expanduser()
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("TAVILY_API_KEY") and "=" in line:
                val = line.split("=", 1)[1].strip().strip("\"'")
                if val and val not in seen:
                    keys.append(val)
                    seen.add(val)

    # 2) hubos config
    config_path = Path("~/.hubos/config.json").expanduser()
    if config_path.exists():
        m = re.search(
            r'"TAVILY_API_KEY":\s*"([^"]+)"',
            config_path.read_text(),
        )
        if m and m.group(1) and m.group(1) not in seen:
            keys.append(m.group(1))
            seen.add(m.group(1))

    # 3) env var
    env_val = os.environ.get("TAVILY_API_KEY", "")
    if env_val and env_val not in seen:
        keys.append(env_val)

    return keys


def _load_tavily_key() -> str:
    """Return first available Tavily key (backward compat)."""
    return (_load_tavily_keys() or [""])[0]


def _load_zhipu_key() -> str:
    """Load Zhipu API key from hubos secret."""
    secret_path = Path(
        "~/.hubos.secret/providers/custom/zhipuai.json",
    ).expanduser()
    if secret_path.exists():
        try:
            cfg = json.loads(secret_path.read_text())
            return cfg.get("api_key", "")
        except Exception:
            pass
    return os.environ.get("ZHIPU_API_KEY", "")


def _extract_mcp_text(result: Any) -> str:
    """Normalize AgentScope MCP tool results into plain text."""
    if not result:
        return ""
    if isinstance(result, str):
        text = result
    elif isinstance(result, dict):
        text = (
            result.get("content")
            or result.get("text")
            or json.dumps(result, ensure_ascii=False)
        )
    elif hasattr(result, "content"):
        blocks = getattr(result, "content", [])
        parts: list[str] = []
        for block in blocks if isinstance(blocks, list) else [blocks]:
            if isinstance(block, str):
                parts.append(block)
            elif hasattr(block, "text"):
                parts.append(block.text)
            elif isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
        text = "\n".join(p for p in parts if p)
    else:
        text = str(result)

    for _ in range(2):
        stripped = text.strip()
        if not stripped.startswith(("{", "[")):
            break
        try:
            data = json.loads(stripped)
        except Exception:
            break
        if isinstance(data, dict):
            text = (
                data.get("content")
                or data.get("text")
                or data.get("result")
                or text
            )
        elif isinstance(data, list):
            parts = []
            for item in data:
                if isinstance(item, dict):
                    parts.append(item.get("text") or item.get("content") or "")
                elif isinstance(item, str):
                    parts.append(item)
            text = "\n".join(p for p in parts if p) or text
        else:
            break
    return text


async def _zhipu_webreader_mcp(url: str) -> str:
    """Fetch page content through the zhipu_reader MCP configured for HubOS."""
    if not HUBOS_AGENT_CONFIG_PATH.exists():
        return ""
    try:
        cfg = json.loads(HUBOS_AGENT_CONFIG_PATH.read_text(encoding="utf-8"))
        reader_cfg = (cfg.get("mcp", {}).get("clients", {}) or {}).get(
            "zhipu_reader",
        )
        if not reader_cfg or not reader_cfg.get("enabled", True):
            return ""

        from agentscope.mcp import HttpStatefulClient

        client = HttpStatefulClient(
            name=reader_cfg.get("name") or "zhipu_reader",
            transport=reader_cfg.get("transport") or "sse",
            url=reader_cfg.get("url") or "",
            headers=reader_cfg.get("headers") or None,
        )
        await client.connect()
        try:
            tool = await client.get_callable_function(
                "webReader",
                wrap_tool_result=False,
                execution_timeout=60,
            )
            return _extract_mcp_text(await tool(url=url))
        finally:
            try:
                await client.close()
            except Exception:
                pass
    except Exception:
        return ""


# ---- HubOS enrichment functions ----


async def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Search using Tavily API with multi-key rotation.

    Tries each available key in order; skips exhausted ones automatically.
    Returns list of {url, title, content}.
    """
    keys = _load_tavily_keys()
    if not keys:
        return []

    payload = json.dumps(
        {
            "query": query,
            "max_results": max_results,
            "include_answer": False,
            "search_depth": "basic",
        },
    )

    for api_key in keys:
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl",
                "-s",
                "-X",
                "POST",
                "https://api.tavily.com/search",
                "-H",
                "Content-Type: application/json",
                "-d",
                payload,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            resp = json.loads(stdout)

            # Check for quota exceeded — try next key
            if isinstance(resp, dict) and "detail" in resp:
                err = str(resp.get("detail", {}))
                if "usage limit" in err.lower() or "exceeds" in err.lower():
                    logger.warning(
                        "Tavily key %s… quota exceeded, trying next",
                        api_key[:12],
                    )
                    continue

            results = []
            for r in resp.get("results", []):
                results.append(
                    {
                        "url": r.get("url", ""),
                        "title": r.get("title", ""),
                        "content": r.get("content", "")[:500],
                    },
                )
            return results
        except Exception:
            continue

    return []


async def _duckduckgo_search(query: str, max_results: int = 10) -> list[dict]:
    """Search via DuckDuckGo HTML + parse results. Free, no API key, no quota.

    Uses webReader (Zhipu) to fetch DDG HTML page, then extracts URLs.
    """
    from urllib.parse import quote_plus, unquote

    ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    # Use zhipu webReader to fetch the page
    html = await _zhipu_webreader(ddg_url)
    if not html:
        # Fallback: try curl directly
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl",
                "-s",
                "-L",
                ddg_url,
                "-H",
                "User-Agent: Mozilla/5.0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            html = stdout.decode("utf-8", errors="ignore")
        except Exception:
            return []

    if not html:
        return []

    results = []
    # DDG HTML results have links like:uddg=<encoded_url>
    for match in re.finditer(r'uddg=([^&"]+)', html):
        raw_url = unquote(match.group(1))
        # Skip DDG internal URLs
        if "duckduckgo.com" in raw_url or "duckduck" in raw_url:
            continue
        # Extract title: look for link text near the URL
        title = ""
        title_match = re.search(
            r">([^<]{5,200})</a>\s*</[^>]*>\s*<[^>]*uddg="
            + re.escape(match.group(1)),
            html,
        )
        if not title_match:
            # Alternative: grab the link text right before the uddg
            chunk = html[max(0, match.start() - 500) : match.start()]
            tm = re.findall(r">([^<]{3,150})</a>", chunk)
            if tm:
                title = tm[-1].strip()
        else:
            title = title_match.group(1).strip()

        # Deduplicate by URL
        if any(r["url"] == raw_url for r in results):
            continue

        results.append(
            {
                "url": raw_url,
                "title": title,
                "content": "",
            },
        )
        if len(results) >= max_results:
            break

    return results


async def _zhipu_webreader(url: str) -> str:
    """Fetch page content using HubOS zhipu_reader MCP, with REST as fallback."""
    mcp_content = await _zhipu_webreader_mcp(url)
    if mcp_content:
        return mcp_content

    api_key = _load_zhipu_key()
    if not api_key:
        return ""

    payload = json.dumps({"url": url, "return_format": "text"})

    try:
        proc = await asyncio.create_subprocess_exec(
            "curl",
            "-s",
            "-X",
            "POST",
            "https://open.bigmodel.cn/api/paas/v4/tools",
            "-H",
            "Content-Type: application/json",
            "-H",
            f"Authorization: Bearer {api_key}",
            "-d",
            payload,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        resp = json.loads(stdout)
        # Try multiple response formats
        content = resp.get("content", resp.get("text", ""))
        if not content and "choices" in resp:
            choices = resp["choices"]
            if choices:
                content = choices[0].get("message", {}).get("content", "")
        return content or ""
    except Exception:
        return ""


async def _hunter_domain(domain: str) -> list[dict]:
    """Call super-crawler hunter_domain to find emails for a domain."""
    super_crawler_dir = "/Users/allen/projects/super-crawler"
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            f"{super_crawler_dir}/src/openclaw-tools.js",
            "call",
            "hunter_domain",
            json.dumps({"domain": domain, "found_only": True}),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=super_crawler_dir,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        result = json.loads(stdout.strip())
        if result and result.get("success"):
            return result.get("data", {}).get("emails", []) or []
        return []
    except Exception:
        return []


def _extract_domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().replace("www.", "")
    except Exception:
        return ""


def _domain_matches(target: str, email_domain: str) -> bool:
    if not target or not email_domain:
        return False
    t = target.lower().strip(".")
    e = email_domain.lower().strip(".")
    return t == e or e.endswith("." + t) or t.endswith("." + e)


def _extract_emails_from_text(text: str, domain: str) -> list[str]:
    """Extract emails matching domain from text."""
    if not text:
        return []
    text = unescape(text)
    emails = re.findall(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        text,
    )
    return [
        e.lower() for e in emails if _domain_matches(domain, e.split("@")[-1])
    ]


# ---- Phase 2.5: Tavily search enrichment ----


async def _phase2_5_tavily_enrich(
    config_path: Path,
    search_path: Path,
    run_dir: Path,
    country_codes: list[str],
) -> int:
    """Supplement xcrawl results with Tavily + DuckDuckGo search.

    Strategy: Tavily first (best quality) → DuckDuckGo fallback (free, no quota).
    """
    existing = _load_json(search_path, [])
    if not isinstance(existing, list):
        existing = []

    # If xcrawl already found enough, skip
    if len(existing) >= 20:
        return 0

    # Read config directly to get english_name + local_search_terms
    config = _load_json(config_path, {})
    if not isinstance(config, dict):
        return 0
    countries_cfg = config.get("countries", {})

    new_results = []
    seen_urls = {r.get("url", "") for r in existing}

    for code in country_codes:
        country = countries_cfg.get(code, {})
        english_name = country.get("english_name", country.get("name", code))
        local_terms = country.get("local_search_terms", [])

        # Build high-quality search queries: local language first, then English
        search_terms = list(local_terms[:6])
        for product in [
            "educational equipment",
            "school laboratory equipment",
            "laboratory instruments",
        ][:2]:
            search_terms.append(f"{product} distributor {english_name}")
            search_terms.append(f"{product} supplier {english_name}")

        for term in search_terms[:8]:
            # Try Tavily first
            results = await _tavily_search(term, max_results=5)
            engine = "tavily"

            # If Tavily returned nothing, try DuckDuckGo
            if not results:
                results = await _duckduckgo_search(term, max_results=10)
                engine = "duckduckgo"

            for r in results:
                url = r.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                new_results.append(
                    {
                        "url": url,
                        "title": r.get("title", ""),
                        "snippet": r.get("content", "")[:500],
                        "search_term": term,
                        "country_code": code,
                        "country_name": english_name,
                        "position": 0,
                        "score": 0.5,
                        "buyer_signal_count": 0,
                        "search_engine": engine,
                    },
                )

    if new_results:
        combined = existing + new_results
        search_path.write_text(
            json.dumps(combined, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        enrich_path = run_dir / "tavily_enrichment.json"
        enrich_path.write_text(
            json.dumps(new_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return len(new_results)


# ---- Phase 3.5: HubOS enrichment (webReader + hunter_domain) ----


async def _phase3_5_enrich_emails(
    leads_path: Path,
    run_dir: Path,
) -> int:
    """Enrich leads without emails using Zhipu webReader + hunter_domain."""
    leads = _load_json(leads_path, [])
    if not isinstance(leads, list) or not leads:
        return 0

    enriched = 0
    needs_email = [
        lead
        for lead in leads
        if not lead.get("email_pattern") and not lead.get("emails")
    ]

    if not needs_email:
        return 0

    # Process up to 10 leads without emails
    semaphore = asyncio.Semaphore(3)

    async def _enrich_one(lead: dict) -> int:
        async with semaphore:
            url = (
                lead.get("source_url")
                or lead.get("url")
                or lead.get("website", "")
            )
            domain = _extract_domain(url)
            if not domain:
                return 0

            found = 0

            # Try Zhipu webReader
            content = await _zhipu_webreader(url)
            if content:
                emails = _extract_emails_from_text(content, domain)
                if emails:
                    lead["email_pattern"] = emails[0]
                    lead["email_source"] = "zhipu_webreader"
                    lead["email_confidence"] = 0.80
                    lead.setdefault("emails", []).extend(emails)
                    lead["match_reason"] = (
                        lead.get("match_reason", "")
                        + "; zhipu_webreader=enriched"
                    )
                    found = 1

            # If still no email, try hunter_domain
            if not found:
                hunter_emails = await _hunter_domain(domain)
                if hunter_emails:
                    best = hunter_emails[0]
                    email = best.get("email", "")
                    if email:
                        lead["email_pattern"] = email
                        lead["email_source"] = "hunter_domain_v2"
                        lead["email_confidence"] = best.get("confidence", 0.85)
                        lead.setdefault("emails", []).append(email)
                        lead["match_reason"] = (
                            lead.get("match_reason", "")
                            + "; hunter_domain_v2=enriched"
                        )
                        found = 1

            return found

    tasks = [_enrich_one(lead) for lead in needs_email[:15]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    enriched = sum(r for r in results if isinstance(r, int))

    if enriched > 0:
        leads_path.write_text(
            json.dumps(leads, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Save enrichment report
    report_path = run_dir / "hubos_enrichment_report.json"
    report_path.write_text(
        json.dumps(
            {
                "leads_without_email": len(needs_email),
                "enriched": enriched,
                "tavily_key_available": bool(_load_tavily_key()),
                "zhipu_key_available": bool(_load_zhipu_key()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return enriched


# ---- Utility functions ----


def _compact_lead(lead: dict[str, Any]) -> dict[str, Any]:
    """Return a small, chat-safe lead record."""
    return {
        "company": lead.get("company_name") or lead.get("company") or "",
        "country_code": lead.get("country_code") or lead.get("country") or "",
        "website": lead.get("source_url") or lead.get("website") or "",
        "email": lead.get("email_pattern") or lead.get("email") or "",
        "buyer_role": lead.get("buyer_role") or "",
        "grade": lead.get("grade") or "",
        "score": _lead_score(lead),
        "email_source": lead.get("email_source") or "",
        "email_confidence": lead.get("email_confidence") or 0,
        "reason": str(lead.get("match_reason") or lead.get("reason") or "")[
            :240
        ],
    }


def _tool_response(
    text: str,
    *,
    stream: bool = False,
    is_last: bool = True,
) -> ToolResponse:
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
        stream=stream,
        is_last=is_last,
    )


def _progress_response(text: str) -> ToolResponse:
    return _tool_response(text, stream=True, is_last=False)


def _normalize_country_codes(countries: str) -> list[str]:
    codes: list[str] = []
    for raw in (countries or "").split(","):
        item = raw.strip()
        if not item:
            continue
        code = COUNTRY_NAME_TO_CODE.get(
            item.lower(),
        ) or COUNTRY_NAME_TO_CODE.get(item)
        if not code:
            code = item.upper()
        if code not in codes:
            codes.append(code)
    return codes or ["BR", "MX", "US"]


def _default_country_entry(code: str) -> dict[str, Any]:
    english_name, language, local_name = COUNTRY_ALIASES.get(
        code,
        (code, "en", code),
    )
    return {
        "name": english_name,
        "language": language,
        "keywords": [
            "educational equipment",
            "school laboratory equipment",
            "laboratory instruments",
            "teaching models",
            "medical training simulators",
            "science equipment",
        ],
        "search_modifiers": [
            f"educational equipment distributor {english_name}",
            f"school laboratory equipment importer {english_name}",
            f"laboratory instruments supplier {english_name}",
            f"teaching models distributor {english_name}",
            f"medical education equipment procurement {english_name}",
            f"science equipment wholesaler {english_name}",
            f"educational equipment distributor {local_name}",
            f"laboratory equipment supplier {local_name}",
        ],
    }


def _build_effective_config(
    config: Path,
    country_codes: list[str],
    run_dir: Path,
) -> Path:
    """Copy config and add missing countries dynamically for this run."""
    data = _load_json(config, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault(
        "products",
        [
            "educational equipment",
            "school laboratory equipment",
            "laboratory instruments",
            "teaching models",
            "medical training simulators",
        ],
    )
    countries_cfg = data.setdefault("countries", {})
    added: list[str] = []
    for code in country_codes:
        if code not in countries_cfg:
            countries_cfg[code] = _default_country_entry(code)
            added.append(code)

    effective_config = run_dir / "effective_search_terms_config.json"
    data["_hubos_dynamic_countries_added"] = added
    effective_config.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return effective_config


async def _run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return (
            -1,
            "",
            f"Command timed out after {timeout} seconds: {' '.join(cmd)}",
        )
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _run_command_events(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> AsyncGenerator[tuple[str, Any], None]:
    """Run a command and yield stdout/stderr lines as they arrive."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    started = time.monotonic()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    async def _pump(kind: str, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            if kind == "stdout":
                stdout_chunks.append(text)
            else:
                stderr_chunks.append(text)
            await queue.put((kind, text))

    pumps = [
        asyncio.create_task(_pump("stdout", proc.stdout)),
        asyncio.create_task(_pump("stderr", proc.stderr)),
    ]
    timed_out = False
    try:
        while True:
            if (
                proc.returncode is not None
                and all(t.done() for t in pumps)
                and queue.empty()
            ):
                break
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                timed_out = True
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                break
            try:
                kind, text = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield kind, text
            except asyncio.TimeoutError:
                continue

        await proc.wait()
        await asyncio.gather(*pumps, return_exceptions=True)
        while not queue.empty():
            yield queue.get_nowait()
    finally:
        for task in pumps:
            if not task.done():
                task.cancel()

    if timed_out:
        timeout_msg = (
            f"Command timed out after {timeout} seconds: {' '.join(cmd)}"
        )
        stderr_chunks.append(timeout_msg)
        yield "done", {
            "code": -1,
            "stdout": "".join(stdout_chunks),
            "stderr": "".join(stderr_chunks),
        }
    else:
        yield "done", {
            "code": proc.returncode or 0,
            "stdout": "".join(stdout_chunks),
            "stderr": "".join(stderr_chunks),
        }


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _lead_score(lead: dict[str, Any]) -> float:
    for key in (
        "effective_score",
        "buyer_score",
        "customer_fit_score",
        "score",
    ):
        try:
            value = float(lead.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value:
            return value
    return 0.0


def _summarize_leads(leads: list[dict[str, Any]], max_leads: int) -> str:
    if not leads:
        return "未找到合格客户线索。"

    lines = []
    for idx, lead in enumerate(leads[:max_leads], 1):
        company = (
            lead.get("company_name")
            or lead.get("company")
            or "Unknown company"
        )
        country = lead.get("country_code") or lead.get("country") or ""
        website = lead.get("source_url") or lead.get("website") or ""
        email = lead.get("email_pattern") or lead.get("email") or ""
        role = lead.get("buyer_role") or ""
        grade = lead.get("grade") or ""
        reason = lead.get("match_reason") or lead.get("reason") or ""
        score = _lead_score(lead)
        line = f"{idx}. {company}"
        meta = []
        if country:
            meta.append(country)
        if grade:
            meta.append(f"grade={grade}")
        if score:
            meta.append(f"score={score:g}")
        if role:
            meta.append(role)
        if meta:
            line += " — " + " | ".join(meta)
        if website:
            line += f"\n   website: {website}"
        if email:
            line += f"\n   email: {email}"
        if reason:
            line += f"\n   reason: {str(reason)[:220]}"
        lines.append(line)
    return "\n".join(lines)


# ---- Main tool function ----


async def find_customer_leads(
    countries: str = "BR,MX,US",
    max_leads: int = 8,
    output_dir: Optional[str] = None,
    scripts_dir: Optional[str] = None,
    config_path: Optional[str] = None,
    timeout: int = 900,
) -> AsyncGenerator[ToolResponse, None]:
    """Find potential customer leads without drafting or sending emails.

    Pipeline (all self-contained in HubOS):
        Phase 1: Generate search terms (english_name + local_search_terms)
        Phase 2: xcrawl search for candidate URLs
        Phase 2.5: Tavily search enrichment when xcrawl results are poor
        Phase 3: super_crawler deep crawl + email discovery + scoring
        Phase 3.5: Zhipu webReader + hunter_domain email enrichment

    Args:
        countries: Comma-separated country codes, e.g. "BR,MX,US".
        max_leads: Maximum number of top leads to return in the response.
        output_dir: Optional directory for run artifacts.
        scripts_dir: Optional scripts directory (defaults to HubOS built-in).
        config_path: Optional search terms config path.
        timeout: Timeout in seconds for each pipeline phase.

    Returns:
        ToolResponse with a concise summary and artifact paths.
    """

    scripts = (
        Path(scripts_dir).expanduser() if scripts_dir else DEFAULT_SCRIPTS_DIR
    )
    config = (
        Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    )
    if output_dir:
        run_dir = Path(output_dir).expanduser()
    else:
        run_dir = DEFAULT_OUTPUT_ROOT / datetime.now().strftime(
            "%Y%m%d_%H%M%S",
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    required = {
        "phase1": scripts / "phase1_generate_terms.py",
        "phase2": scripts / "phase2_xcrawl_search.py",
        "phase3": scripts / "phase3_4_super_crawler.py",
    }
    missing = [str(path) for path in required.values() if not path.exists()]
    if missing:
        yield _tool_response(
            "客户开发工具未配置完整，缺少脚本：\n"
            + "\n".join(f"- {p}" for p in missing)
            + "\n可设置 HUBOS_CUSTOMER_DEV_SCRIPTS_DIR 指向 Hunter 脚本目录。",
        )
        return
    if not config.exists():
        yield _tool_response(
            f"客户开发工具缺少搜索配置：{config}\n"
            "可设置 HUBOS_CUSTOMER_DEV_CONFIG_PATH 指向 search_terms_v2.json。",
        )
        return

    country_codes = _normalize_country_codes(countries)
    countries_str = ",".join(country_codes)
    effective_config = _build_effective_config(config, country_codes, run_dir)
    try:
        max_leads = max(1, min(int(max_leads), 25))
    except (TypeError, ValueError):
        max_leads = 20
    try:
        timeout = max(60, int(timeout))
    except (TypeError, ValueError):
        timeout = 900

    terms_path = run_dir / "search_terms.json"
    search_path = run_dir / "search_results.json"
    leads_path = run_dir / "customer_leads.json"
    log_path = run_dir / "pipeline.log"

    phases = [
        [
            sys.executable,
            str(required["phase1"]),
            "--countries",
            countries_str,
            "--config",
            str(effective_config),
            "--output",
            str(terms_path),
        ],
        [
            sys.executable,
            str(required["phase2"]),
            "--search-terms",
            str(terms_path),
            "--output",
            str(search_path),
        ],
        [
            sys.executable,
            str(required["phase3"]),
            str(search_path),
            str(leads_path),
        ],
    ]
    phase_names = {
        1: "生成搜索词",
        2: "搜索候选客户网页",
        3: "深度爬取并评分客户线索",
    }

    yield _progress_response(
        "客户线索查找已开始。\n"
        f"- 国家：{countries_str}\n"
        f"- 输出目录：{run_dir}\n"
        f"- 说明：会完整搜索和爬取，不会写邮件或发送邮件。",
    )

    log_chunks: list[str] = []

    # ---- Phase 1-3: OpenClaw pipeline ----
    for idx, cmd in enumerate(phases, 1):
        yield _progress_response(
            f"Phase {idx}/3：{phase_names[idx]}，开始执行。",
        )
        phase_result: dict[str, Any] | None = None
        async for event, payload in _run_command_events(
            cmd,
            cwd=scripts,
            timeout=timeout,
        ):
            if event == "done":
                phase_result = payload
                continue

            line = str(payload).strip()
            if not line:
                continue

            should_show = (
                idx == 1
                or "🔍" in line
                or "✅" in line
                or "📊" in line
                or "阶段2完成" in line
                or "待处理" in line
                or line.startswith("[")
                or "完成：" in line
            )
            if should_show:
                yield _progress_response(line)

        if phase_result is None:
            phase_result = {
                "code": -1,
                "stdout": "",
                "stderr": "Command ended without status.",
            }
        code = int(phase_result.get("code", -1))
        stdout = str(phase_result.get("stdout") or "")
        stderr = str(phase_result.get("stderr") or "")
        log_chunks.append(
            f"$ {' '.join(cmd)}\nexit={code}\n[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
        )
        log_path.write_text("\n".join(log_chunks), encoding="utf-8")

        if code != 0:
            # Phase 2 失败（xcrawl 额度用完）时尝试 tavily 兜底
            if idx == 2:
                yield _progress_response(
                    "⚠️ xcrawl 搜索失败，启动 HubOS Tavily 兜底搜索...",
                )
                break
            yield _tool_response(
                f"客户线索查找失败：phase{idx} 退出码 {code}\n"
                f"日志：{log_path}\n"
                f"错误摘要：{(stderr or stdout).strip()[:1200]}",
            )
            return
        yield _progress_response(f"Phase {idx}/3：{phase_names[idx]}，完成。")

    # ---- Phase 2.5: HubOS Tavily enrichment ----
    # Trigger when phase2 produced few results or xcrawl failed
    search_results = _load_json(search_path, [])
    if isinstance(search_results, list) and len(search_results) < 15:
        yield _progress_response(
            f"Phase 2.5：xcrawl 结果不足（{len(search_results)}个），"
            f"使用 Tavily 补充搜索...",
        )
        tavily_count = await _phase2_5_tavily_enrich(
            config,
            search_path,
            run_dir,
            country_codes,
        )
        yield _progress_response(
            f"Phase 2.5：Tavily 补充了 {tavily_count} 个候选URL。",
        )

        # Re-run phase3 if we added tavily results
        if tavily_count > 0:
            # Only if phase3 hasn't run yet or we need to re-run
            existing_leads = _load_json(leads_path, [])
            if not existing_leads:
                yield _progress_response(
                    "Phase 3：使用补充后的搜索结果重新爬取...",
                )
                cmd = phases[2]  # phase3
                phase_result = None
                async for event, payload in _run_command_events(
                    cmd,
                    cwd=scripts,
                    timeout=timeout,
                ):
                    if event == "done":
                        phase_result = payload
                        continue
                    line = str(payload).strip()
                    if line and ("[" in line or "完成" in line):
                        yield _progress_response(line)

                if phase_result:
                    code = int(phase_result.get("code", -1))
                    stdout = str(phase_result.get("stdout") or "")
                    stderr = str(phase_result.get("stderr") or "")
                    log_chunks.append(
                        f"$ {' '.join(cmd)} (tavily-enriched)\nexit={code}\n[stdout]\n{stdout}\n[stderr]\n{stderr}\n",
                    )
                    log_path.write_text(
                        "\n".join(log_chunks),
                        encoding="utf-8",
                    )
    elif not isinstance(search_results, list) or len(search_results) == 0:
        # phase2 completely failed, try tavily-only search
        yield _progress_response(
            "Phase 2.5：xcrawl 完全失败，使用 Tavily 搜索所有候选...",
        )
        tavily_count = await _phase2_5_tavily_enrich(
            config,
            search_path,
            run_dir,
            country_codes,
        )
        yield _progress_response(
            f"Phase 2.5：Tavily 找到 {tavily_count} 个候选URL。",
        )

        if tavily_count > 0:
            yield _progress_response("Phase 3：爬取 Tavily 搜索结果...")
            cmd = phases[2]
            async for event, payload in _run_command_events(
                cmd,
                cwd=scripts,
                timeout=timeout,
            ):
                if event == "done":
                    phase_result = payload
                    continue
                line = str(payload).strip()
                if line and ("[" in line or "完成" in line):
                    yield _progress_response(line)

            if phase_result:
                code = int(phase_result.get("code", -1))
                stdout = str(phase_result.get("stdout") or "")
                log_chunks.append(
                    f"$ {' '.join(cmd)} (tavily-only)\nexit={code}\n[stdout]\n{stdout}\n",
                )
                log_path.write_text("\n".join(log_chunks), encoding="utf-8")

    # ---- Phase 3.5: HubOS email enrichment ----
    raw_leads = _load_json(leads_path, [])
    leads = raw_leads if isinstance(raw_leads, list) else []
    no_email_count = sum(
        1
        for lead in leads
        if not lead.get("email_pattern") and not lead.get("emails")
    )

    if no_email_count > 0:
        yield _progress_response(
            f"Phase 3.5：{no_email_count} 个客户缺少邮箱，"
            f"使用 HubOS 工具补充（智谱 webReader + hunter_domain）...",
        )
        enriched = await _phase3_5_enrich_emails(leads_path, run_dir)
        yield _progress_response(f"Phase 3.5：成功补充 {enriched} 个邮箱。")

    # ---- Final summary ----
    raw_leads = _load_json(leads_path, [])
    leads = raw_leads if isinstance(raw_leads, list) else []
    leads = sorted(leads, key=_lead_score, reverse=True)
    top_leads = [_compact_lead(lead) for lead in leads[:max_leads]]
    top_path = run_dir / "top_customer_leads.json"
    top_path.write_text(
        json.dumps(top_leads, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    has_email = sum(
        1 for lead in leads if lead.get("email_pattern") or lead.get("emails")
    )
    a_count = sum(1 for lead in leads if lead.get("grade") == "A")

    summary_path = run_dir / "summary.md"
    summary_path.write_text(
        "\n".join(
            [
                "# Customer Lead Discovery Summary",
                "",
                f"- Countries: {countries_str}",
                f"- Total leads: {len(leads)}",
                f"- Returned leads: {len(top_leads)}",
                f"- A-grade: {a_count}",
                f"- With email: {has_email}/{len(leads)}",
                f"- Effective config: {effective_config}",
                f"- Full result: {leads_path}",
                f"- Top result: {top_path}",
                f"- Enrichment report: {run_dir / 'hubos_enrichment_report.json'}",
                "",
                _summarize_leads(top_leads, max_leads),
            ],
        ),
        encoding="utf-8",
    )

    summary = [
        "客户线索查找完成。",
        f"国家：{countries_str}",
        f"候选线索：{len(leads)} 个（A级 {a_count} 个），返回前 {len(top_leads)} 个。",
        f"有邮箱：{has_email}/{len(leads)}",
        f"完整结果：{leads_path}",
        f"Top 精简结果：{top_path}",
        f"摘要：{summary_path}",
        f"日志：{log_path}",
        "",
        _summarize_leads(top_leads, max_leads),
    ]
    yield _tool_response("\n".join(summary))
