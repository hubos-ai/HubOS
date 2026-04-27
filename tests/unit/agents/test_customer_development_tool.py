# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hubos.agents.tools.customer_development import find_customer_leads
from hubos.config.config import _default_builtin_tools


def _text(resp) -> str:
    block = resp.content[0]
    if isinstance(block, dict):
        return block.get("text", "")
    return block.text


async def _collect_text(resp_stream) -> str:
    chunks = []
    async for resp in resp_stream:
        chunks.append(_text(resp))
    return "\n".join(chunks)


@pytest.mark.asyncio
async def test_find_customer_leads_with_fake_pipeline(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    config = tmp_path / "search_terms_v2.json"
    config.write_text(
        json.dumps(
            {
                "products": ["educational equipment"],
                "countries": {
                    "US": {"name": "United States", "language": "en"},
                },
            },
        ),
        encoding="utf-8",
    )

    (scripts / "phase1_generate_terms.py").write_text(
        """
import argparse, json
p = argparse.ArgumentParser()
p.add_argument('--countries')
p.add_argument('--config')
p.add_argument('--output')
a = p.parse_args()
open(a.output, 'w', encoding='utf-8').write(json.dumps({'countries': {'US': {'terms': ['educational equipment distributor US']}}}))
""".strip(),
        encoding="utf-8",
    )
    (scripts / "phase2_xcrawl_search.py").write_text(
        """
import argparse, json
p = argparse.ArgumentParser()
p.add_argument('--search-terms')
p.add_argument('--output')
a = p.parse_args()
open(a.output, 'w', encoding='utf-8').write(json.dumps([{'url': 'https://alpha.example.com', 'score': 1.2}]))
""".strip(),
        encoding="utf-8",
    )
    (scripts / "phase3_4_super_crawler.py").write_text(
        """
import json, sys
json.dump([{'company_name': 'Alpha Science Supplies', 'country_code': 'US', 'source_url': 'https://alpha.example.com', 'email_pattern': 'sales@alpha.example.com', 'buyer_role': 'distributor', 'grade': 'A', 'effective_score': 9, 'match_reason': 'education equipment distributor'}], open(sys.argv[2], 'w', encoding='utf-8'))
""".strip(),
        encoding="utf-8",
    )

    text = await _collect_text(
        find_customer_leads(
            countries="US",
            max_leads=5,
            scripts_dir=str(scripts),
            config_path=str(config),
            output_dir=str(tmp_path / "run"),
            timeout=60,
        ),
    )

    assert "客户线索查找完成" in text
    assert "Phase 1/3" in text
    assert "Phase 2/3" in text
    assert "Phase 3/3" in text
    assert "Alpha Science Supplies" in text
    assert "不会写邮件或发送邮件" in text
    assert (tmp_path / "run" / "top_customer_leads.json").exists()


@pytest.mark.asyncio
async def test_find_customer_leads_reports_missing_scripts(
    tmp_path: Path,
) -> None:
    text = await _collect_text(
        find_customer_leads(
            scripts_dir=str(tmp_path / "missing"),
            config_path=str(tmp_path / "missing_config.json"),
            output_dir=str(tmp_path / "run"),
        ),
    )

    assert "客户开发工具未配置完整" in text
    assert "HUBOS_CUSTOMER_DEV_SCRIPTS_DIR" in text


def test_find_customer_leads_registered_as_builtin_tool() -> None:
    tools = _default_builtin_tools()
    assert "find_customer_leads" in tools
    assert tools["find_customer_leads"].enabled is True


@pytest.mark.asyncio
async def test_find_customer_leads_adds_missing_country_config(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    config = tmp_path / "search_terms_v2.json"
    config.write_text(
        json.dumps({"products": ["educational equipment"], "countries": {}}),
        encoding="utf-8",
    )

    (scripts / "phase1_generate_terms.py").write_text(
        """
import argparse, json
p = argparse.ArgumentParser()
p.add_argument('--countries')
p.add_argument('--config')
p.add_argument('--output')
a = p.parse_args()
config = json.load(open(a.config, encoding='utf-8'))
assert 'UZ' in config['countries']
assert config['countries']['UZ']['name'] == 'Uzbekistan'
open(a.output, 'w', encoding='utf-8').write(json.dumps({'countries': {'UZ': {'terms': ['educational equipment distributor Uzbekistan']}}}))
""".strip(),
        encoding="utf-8",
    )
    (scripts / "phase2_xcrawl_search.py").write_text(
        """
import argparse, json
p = argparse.ArgumentParser()
p.add_argument('--search-terms')
p.add_argument('--output')
a = p.parse_args()
open(a.output, 'w', encoding='utf-8').write(json.dumps([]))
""".strip(),
        encoding="utf-8",
    )
    (scripts / "phase3_4_super_crawler.py").write_text(
        """
import json, sys
json.dump([], open(sys.argv[2], 'w', encoding='utf-8'))
""".strip(),
        encoding="utf-8",
    )

    text = await _collect_text(
        find_customer_leads(
            countries="乌兹别克斯坦",
            max_leads=5,
            scripts_dir=str(scripts),
            config_path=str(config),
            output_dir=str(tmp_path / "run"),
            timeout=60,
        ),
    )

    assert "国家：UZ" in text
    effective_config = tmp_path / "run" / "effective_search_terms_config.json"
    assert effective_config.exists()
    assert (
        json.loads(effective_config.read_text(encoding="utf-8"))["countries"][
            "UZ"
        ]["name"]
        == "Uzbekistan"
    )
