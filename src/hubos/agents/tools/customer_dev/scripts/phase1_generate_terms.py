#!/usr/bin/env python3
"""
phase1_generate_terms.py v4 (HubOS)
基于 keyword_rules 动态生成精准产品搜索词：
1. 从产品线中选取具体产品词（不是泛词）
2. 用角色修饰词（supplier/distributor/importer）× 地理模式组合
3. 保留 local_search_terms 作为高优先级补充
4. 每个国家生成 10-15 个精准词，替代之前的 20 个泛词
"""

import argparse
import json
import random
from pathlib import Path


def load_config(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def dedupe_keep_order(items):
    seen = set()
    out = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def build_country_terms(country_code, country, rules):
    """Generate precise search terms using product keywords × roles × geo patterns."""
    country_name_en = country.get('english_name', country.get('name', country_code))
    cities = country.get('cities', [])
    procurement = country.get('procurement_agencies', [])
    local_terms = country.get('local_search_terms', [])

    # Load rules
    product_lines = rules.get('product_lines', {})
    role_modifiers = rules.get('role_modifiers', ['supplier', 'distributor', 'importer'])
    geo_patterns = rules.get('geo_patterns', [
        '{product} {role} {country}',
    ])
    gen_rules = rules.get('generation_rules', {})
    max_terms = gen_rules.get('max_terms_per_country', 15)
    products_per_line = gen_rules.get('products_per_line', 3)
    must_include_cities = gen_rules.get('must_include_cities', True)

    # Collect all products with their line-specific modifiers
    all_products = []  # (product, modifiers)
    for line_name, line_cfg in product_lines.items():
        line_products = line_cfg.get('products', [])
        line_modifiers = line_cfg.get('modifiers', role_modifiers)
        # Pick top N products from each line (most specific first)
        for p in line_products[:products_per_line]:
            all_products.append((p, line_modifiers))

    terms = []

    # 1) Highest priority: pre-configured local language terms
    if local_terms:
        terms.extend(local_terms[:4])

    # 2) Product × role × country patterns
    used_products = set()
    for product, product_modifiers in all_products:
        if len(terms) >= max_terms:
            break
        if product.lower() in used_products:
            continue
        used_products.add(product.lower())

        # Pick a modifier from this product line's modifiers
        role = product_modifiers[len(terms) % len(product_modifiers)]

        # Pattern: "{product} {role} {country}"
        terms.append(f"{product} {role} {country_name_en}")

        # Add a city-based variant if cities available
        if cities and must_include_cities and len(terms) < max_terms:
            city = cities[len(terms) % len(cities)]
            terms.append(f"{product} {role} {city}")

    # 3) Procurement agency terms (if configured)
    if procurement:
        for agency in procurement[:2]:
            if len(terms) >= max_terms:
                break
            # Use a high-value product for procurement context
            terms.append(f"laboratory equipment {agency}")
            terms.append(f"science equipment supplier {agency}")

    # 4) Import-specific terms (find importers/customers)
    if len(terms) < max_terms:
        top_products = [p for p, _ in all_products[:3]]
        for product in top_products:
            if len(terms) >= max_terms:
                break
            terms.append(f"import {product} {country_name_en}")

    # 5) Fallback: if still too few terms, add broader but targeted terms
    if len(terms) < 8:
        fallback = [
            f"school science equipment {country_name_en}",
            f"medical teaching model {country_name_en}",
            f"laboratory furniture supplier {country_name_en}",
        ]
        for t in fallback:
            if len(terms) >= max_terms:
                break
            if t.lower() not in {x.lower() for x in terms}:
                terms.append(t)

    terms = dedupe_keep_order(terms)
    return terms[:max_terms]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--countries', required=True, help='Comma-separated country codes, e.g. BR,MX,US')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    rules = config.get('keyword_rules', {})
    countries_cfg = config.get('countries', {})

    # Fallback if keyword_rules not found (v3 compat)
    if not rules or 'product_lines' not in rules:
        rules = {
            'product_lines': {
                'general': {
                    'products': config.get('products', ['educational equipment']),
                    'modifiers': ['supplier', 'distributor'],
                }
            },
            'role_modifiers': ['supplier', 'distributor', 'importer'],
            'geo_patterns': ['{product} {role} {country}'],
            'generation_rules': {'max_terms_per_country': 15},
        }

    country_codes = [c.strip().upper() for c in args.countries.split(',') if c.strip()]

    result = {
        'generated_by': 'hubos-keyword-rules-v4',
        'total_countries': 0,
        'total_terms': 0,
        'countries': {}
    }

    for code in country_codes:
        if code in countries_cfg:
            country = countries_cfg[code]
        else:
            country = {
                'name': code,
                'english_name': code,
                'language': 'en',
                'country_code': code,
                'cities': [],
                'procurement_agencies': [],
                'local_search_terms': [],
            }
        terms = build_country_terms(code, country, rules)
        result['countries'][code] = {
            'name': country.get('english_name', code),
            'name_local': country.get('name', code),
            'language': country.get('language', 'en'),
            'terms': terms,
            'count': len(terms),
        }

    result['total_countries'] = len(result['countries'])
    result['total_terms'] = sum(v['count'] for v in result['countries'].values())

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"✅ 已生成 {result['total_countries']} 个国家，共 {result['total_terms']} 个搜索词")


if __name__ == '__main__':
    main()
