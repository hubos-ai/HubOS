#!/usr/bin/env python3
"""
phase2_xcrawl_search.py - Hunter Agent
使用 xcrawl Search API 搜索候选公司。
增强点：
1. 兼容新的 search_terms_batch.json 结构
2. 每国保底结果配额，避免单一国家挤占全部结果
3. 弱国家使用更多搜索词，提高覆盖率
4. xcrawl/Tavily 不可用或空结果时 → webReader + DuckDuckGo 兜底
"""
import json
import argparse
import subprocess
import time
import sys
import os
import re
import asyncio
from collections import defaultdict
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

XCRAWL_API_KEY = ""
XCRAWL_BASE_URL = "https://run.xcrawl.com"
PRIORITY_COUNTRIES = {"BR", "MX", "US", "DE", "JP"}
PER_COUNTRY_GUARANTEE = 5
FINAL_LIMIT = 50

# HubOS agent config for MCP client
HUBOS_AGENT_CONFIG = os.path.expanduser(
    os.environ.get(
        "HUBOS_MCP_AGENT_CONFIG",
        "~/.hubos/workspaces/default/agent.json",
    )
)

LOW_VALUE_DOMAINS = {
    'amazon.', 'alibaba.', 'made-in-china.', 'globalsources.', 'ec21.',
    'kompass.', 'yellowpages', 'linkedin.', 'facebook.', 'instagram.',
    'youtube.', 'wikipedia.', 'trade.gov', 'volza.', 'trademo.',
    'go4worldbusiness.', 'b2brazil.', 'developmentaid.',
}

# Government / school / institutional domains — NOT potential customers
NON_COMMERCIAL_DOMAINS = {
    '.gov.', '.gob.', '.edu.', '.k12.', '.ac.uk', '.sch.', '.edu.',
    'deped.gov', 'deped.', 'wordpress.com', 'blogspot.', 'medium.com',
    'reddit.com', 'quora.com', 'slideshare.', 'scribd.',
    'researchgate.', 'academia.edu',
}

LOW_VALUE_TEXT = {
    'top 10', 'best suppliers', 'directory', 'yellow pages', 'marketplace',
    'news', 'blog', 'article', 'expo', 'fair', 'exhibition', 'tender notice',
    'find a distributor', 'dealer locator', 'product details',
}

BUYER_SIGNAL_TEXT = {
    'distributor', 'importer', 'wholesaler', 'dealer', 'reseller',
    'school supplier', 'laboratory supplier', 'educational equipment',
    'science equipment', 'teaching equipment', 'medical teaching',
}


# ─── webReader + DuckDuckGo fallback ───────────────────────────────────

_webreader_client = None
_webreader_callable = None


def _extract_text_from_mcp_result(result) -> str:
    if not result:
        return ""
    if isinstance(result, str):
        text = result
    elif isinstance(result, dict):
        text = result.get("content") or result.get("text") or json.dumps(result, ensure_ascii=False)
    elif hasattr(result, "content"):
        blocks = getattr(result, "content", [])
        texts = []
        for block in blocks if isinstance(blocks, list) else [blocks]:
            if isinstance(block, str):
                texts.append(block)
            elif hasattr(block, "text"):
                texts.append(block.text)
            elif isinstance(block, dict):
                texts.append(block.get("text") or block.get("content") or "")
        text = "\n".join(t for t in texts if t)
    else:
        text = str(result)

    # Zhipu MCP may return a JSON string containing another text/content field.
    for _ in range(2):
        stripped = text.strip()
        if not stripped.startswith(("{", "[")):
            break
        try:
            data = json.loads(stripped)
        except Exception:
            break
        if isinstance(data, dict):
            text = data.get("content") or data.get("text") or data.get("result") or text
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


def _clean_duckduckgo_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target else ""
    return unquote(url)


async def _get_webreader():
    """Lazy-init a callable webReader tool from HubOS MCP config."""
    global _webreader_client, _webreader_callable
    if _webreader_callable is not None:
        return _webreader_callable

    config_path = HUBOS_AGENT_CONFIG
    if not os.path.exists(config_path):
        print(f"    ℹ️  webReader: agent.json not found at {config_path}", flush=True)
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"    ℹ️  webReader: failed to read config: {e}", flush=True)
        return None

    mcp_clients = cfg.get("mcp", {}).get("clients", {})
    reader_cfg = mcp_clients.get("zhipu_reader")
    if not reader_cfg:
        # Try alternate names
        for name in ("zhipu_reader", "webReader", "zhipu_web_reader"):
            reader_cfg = mcp_clients.get(name)
            if reader_cfg:
                break
    if not reader_cfg:
        print("    ℹ️  webReader: no zhipu_reader in MCP config", flush=True)
        return None

    try:
        from agentscope.mcp import HttpStatefulClient

        client = HttpStatefulClient(
            name=reader_cfg.get("name") or "zhipu_reader",
            transport=reader_cfg.get("transport") or "sse",
            url=reader_cfg.get("url", ""),
            headers=reader_cfg.get("headers") or None,
        )
        await client.connect()
        tool = await client.get_callable_function(
            "webReader",
            wrap_tool_result=False,
            execution_timeout=60,
        )
        _webreader_client = client
        _webreader_callable = tool
        return tool
    except Exception as e:
        print(f"    ⚠️  webReader MCP init failed: {e}", flush=True)
        return None


async def _webreader_fetch(url: str) -> str:
    """Fetch a URL using zhipu_reader MCP webReader. Returns text or empty."""
    tool = await _get_webreader()
    if tool is None:
        return ""
    try:
        result = await tool(url=url)
        return _extract_text_from_mcp_result(result)
    except Exception as e:
        print(f"    ⚠️  webReader fetch error: {e}", flush=True)
        return ""


def _parse_ddg_html(html: str, max_results: int = 10) -> list[dict]:
    """Parse DuckDuckGo HTML lite results, extracting URLs and titles."""
    results = []
    seen = set()

    # webReader returns rendered Markdown for DDG, not raw HTML.
    for match in re.finditer(r"\[([^\]\n]{3,200})\]\((https?://[^)\s]+)\)", html):
        title = match.group(1).strip()
        url = _clean_duckduckgo_url(match.group(2).strip())
        if not url or "duckduckgo.com" in url or url in seen:
            continue
        seen.add(url)
        results.append({
            "url": url,
            "title": title,
            "content": "",
            "description": "",
            "position": len(results) + 1,
            "score": max(0.1, 1.0 - len(results) * 0.08),
        })
        if len(results) >= max_results:
            return results

    # DDG lite uses <a class="result__a" href="//duckduckgo.com/l/?uddg=<url>">
    # Pattern 1: uddg= parameter in href
    for match in re.finditer(r'uddg=([^&"]+)', html):
        raw_url = _clean_duckduckgo_url(unquote(match.group(1)))
        if "duckduckgo.com" in raw_url:
            continue
        if raw_url in seen:
            continue

        # Extract title from nearby link text
        title = ""
        chunk = html[max(0, match.start() - 500):match.start()]
        titles = re.findall(r'>([^<]{3,150})</a>', chunk)
        if titles:
            title = titles[-1].strip()

        seen.add(raw_url)
        results.append({"url": raw_url, "title": title, "content": ""})
        if len(results) >= max_results:
            break

    # Pattern 2: if no uddg found, try result__a links
    if not results:
        for match in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)', html
        ):
            href = match.group(1)
            title = match.group(2).strip()
            # DDG redirect URLs
            if "uddg=" in href:
                url_part = href.split("uddg=")[-1].split("&")[0]
                url = unquote(url_part)
            elif href.startswith("//duckduckgo.com"):
                continue
            else:
                url = href

            if url in seen or "duckduckgo.com" in url:
                continue
            seen.add(url)
            results.append({"url": url, "title": title, "content": ""})
            if len(results) >= max_results:
                break

    # Pattern 3: generic <a href="https://..."> fallback
    if not results:
        for match in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]{3,})</a>', html):
            url = match.group(1)
            title = match.group(2).strip()
            if any(skip in url for skip in ("duckduckgo.com", "bing.com", "google.com")):
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append({"url": url, "title": title, "content": ""})
            if len(results) >= max_results:
                break

    return results


def search_webreader_duckduckgo(query, max_results=5):
    """Synchronous wrapper: use webReader to fetch DDG page, then parse."""
    ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — create a new loop in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                html = pool.submit(
                    asyncio.run, _webreader_fetch(ddg_url)
                ).result(timeout=30)
        else:
            html = loop.run_until_complete(_webreader_fetch(ddg_url))
    except Exception as e:
        print(f"    ⚠️  webReader+DDG error: {e}", flush=True)
        return [], "webreader_error"

    if not html:
        return [], "webreader_empty"

    results = _parse_ddg_html(html, max_results=max_results)
    if not results:
        return [], "parse_empty"

    return results, "ok"


# ─── xcrawl search ─────────────────────────────────────────────────────

def load_xcrawl_key():
    global XCRAWL_API_KEY
    config_path = os.path.expanduser('~/.xcrawl/config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            cfg = json.load(f)
            XCRAWL_API_KEY = cfg.get('XCRAWL_API_KEY', '')


def search_xcrawl(query, max_results=5):
    payload = {
        "query": query,
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False
    }

    try:
        result = subprocess.run(
            ['curl', '-s', '-X', 'POST', f'{XCRAWL_BASE_URL}/v1/search',
             '-H', 'Content-Type: application/json',
             '-H', f'Authorization: Bearer {XCRAWL_API_KEY}',
             '-d', json.dumps(payload)],
            capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            return None, 'curl_error'

        resp = json.loads(result.stdout)
        if resp.get('status') == 'error' or resp.get('code'):
            return None, resp.get('message', 'unknown_error')

        data = resp.get('data', {})
        results = data.get('data', [])
        return results or [], 'ok'
    except Exception as e:
        return None, str(e)


# ─── common helpers ─────────────────────────────────────────────────────

def load_search_blocks(path):
    with open(path, 'r', encoding='utf-8') as f:
        search_data = json.load(f)

    if isinstance(search_data, list):
        return search_data

    countries = search_data.get('countries', {})
    blocks = []
    for code, block in countries.items():
        blocks.append({
            'country_code': code,
            'country_name': block.get('name', code),
            'search_terms': block.get('terms', [])
        })
    return blocks


def select_terms(country_code, terms):
    if country_code in PRIORITY_COUNTRIES:
        return terms[:8]
    return terms[:6]


def stable_score(item):
    return (
        float(item.get('score', 0) or 0),
        -int(item.get('position', 999) or 999)
    )


def is_low_value_search_result(item):
    url = (item.get('url') or '').lower()
    title = (item.get('title') or '').lower()
    snippet = (item.get('snippet') or item.get('description') or '').lower()
    text = f'{url} {title} {snippet}'

    if url.endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx')):
        return True
    if any(domain in url for domain in LOW_VALUE_DOMAINS):
        return True
    # Filter out government, school, and institutional domains
    if any(domain in url for domain in NON_COMMERCIAL_DOMAINS):
        return True
    if any(marker in text for marker in LOW_VALUE_TEXT):
        return True
    return False


def buyer_signal_count(item):
    title = (item.get('title') or '').lower()
    snippet = (item.get('snippet') or item.get('description') or '').lower()
    term = (item.get('search_term') or '').lower()
    text = f'{title} {snippet} {term}'
    return sum(1 for signal in BUYER_SIGNAL_TEXT if signal in text)


def balance_results(results):
    by_country = defaultdict(list)
    for r in results:
        by_country[r.get('country_code', '?')].append(r)

    for code in by_country:
        by_country[code].sort(key=stable_score, reverse=True)

    final = []
    seen_urls = set()

    # 先给每国保底名额
    for code, items in by_country.items():
        picked = 0
        for item in items:
            url = item.get('url')
            if not url or url in seen_urls:
                continue
            final.append(item)
            seen_urls.add(url)
            picked += 1
            if picked >= PER_COUNTRY_GUARANTEE:
                break

    # 再按总分补满
    remaining = []
    for items in by_country.values():
        for item in items:
            url = item.get('url')
            if url and url not in seen_urls:
                remaining.append(item)

    remaining.sort(key=stable_score, reverse=True)
    for item in remaining:
        if len(final) >= FINAL_LIMIT:
            break
        url = item.get('url')
        if not url or url in seen_urls:
            continue
        final.append(item)
        seen_urls.add(url)

    return final[:FINAL_LIMIT]


# ─── main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--search-terms', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    load_xcrawl_key()
    has_xcrawl = bool(XCRAWL_API_KEY)

    search_blocks = load_search_blocks(args.search_terms)

    all_results = []
    total_searches = 0
    total_credits = 0
    webreader_calls = 0
    webreader_results = 0

    for country_block in search_blocks:
        country_code = country_block['country_code']
        country_name = country_block['country_name']
        terms = select_terms(country_code, country_block.get('search_terms', []))

        country_results = []

        for term in terms:
            total_searches += 1
            print(f"  🔍 [{country_code}] {term[:55]}...", flush=True)

            results, status = None, "skipped"
            used_xcrawl = False

            # --- Try xcrawl first ---
            if has_xcrawl:
                results, status = search_xcrawl(term, max_results=5)
                used_xcrawl = True

                if results is not None and len(results) > 0:
                    # xcrawl succeeded with results
                    pass
                elif results is not None and len(results) == 0:
                    # xcrawl returned empty — try webReader fallback
                    print(f"    🔄 xcrawl 空结果，启用 webReader 兜底...", flush=True)
                    results, status = search_webreader_duckduckgo(term, max_results=5)
                    webreader_calls += 1
                    used_xcrawl = False
                else:
                    # xcrawl failed — try webReader fallback
                    print(f"    🔄 xcrawl 失败({status})，启用 webReader 兜底...", flush=True)
                    results, status = search_webreader_duckduckgo(term, max_results=5)
                    webreader_calls += 1
                    used_xcrawl = False
            else:
                # No xcrawl key — go straight to webReader
                print(f"    🔄 无 xcrawl key，使用 webReader + DuckDuckGo...", flush=True)
                results, status = search_webreader_duckduckgo(term, max_results=5)
                webreader_calls += 1
                used_xcrawl = False

            if results is None:
                results = []
                print(f"    ⚠️  搜索失败: {status}", flush=True)
                time.sleep(1)
                continue

            for r in results:
                url = r.get('url', '')
                if not url:
                    continue
                if is_low_value_search_result(r):
                    continue
                if any(existing['url'] == url for existing in country_results):
                    continue

                description = r.get('description', r.get('snippet', r.get('content', '')))
                signal_count = buyer_signal_count({
                    **r,
                    'search_term': term,
                    'snippet': description or '',
                })
                country_results.append({
                    'url': url,
                    'title': r.get('title', ''),
                    'snippet': description[:500] if description else '',
                    'search_term': term,
                    'country_code': country_code,
                    'country_name': country_name,
                    'position': r.get('position', 0),
                    'score': float(r.get('score', 0) or 0) + signal_count * 0.2,
                    'buyer_signal_count': signal_count,
                    'search_engine': 'xcrawl' if used_xcrawl else 'webreader_ddg',
                })

            engine_label = "xcrawl" if used_xcrawl else "webReader+DDG"
            print(f"    ✅ 找到 {len(results)} 个结果 ({engine_label})", flush=True)

            if used_xcrawl:
                total_credits += 2
            else:
                webreader_results += len(results)
            time.sleep(0.5)

        all_results.extend(country_results)
        print(f"  📊 {country_name}: {len(country_results)} 个独立URL", flush=True)

    top_results = balance_results(all_results)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(top_results, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 阶段2完成：执行 {total_searches} 次搜索，找到 {len(top_results)} 个候选URL（平衡后）")
    print(f"💰 约消耗 {total_credits} xcrawl credits")
    if webreader_calls > 0:
        print(f"🔧 webReader 兜底：{webreader_calls} 次调用，找到 {webreader_results} 个结果")
    print(f"📁 输出：{args.output}")


if __name__ == '__main__':
    main()
