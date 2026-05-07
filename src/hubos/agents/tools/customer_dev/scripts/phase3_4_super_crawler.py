#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phase3_4_super_crawler.py - 候选客户抓取与清洗
目标：
1. 从候选URL提取公司、邮箱、描述
2. 做公司名清洗
3. 做买家角色判断与 buyer_score
4. 输出可供 phase5/phase7 使用的干净候选数据
"""
import json
import os
import subprocess
import sys
import re
from datetime import datetime
from pathlib import Path
from html import unescape
from urllib.parse import urlparse, urljoin, unquote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore

SUPER_CRAWLER_DIR = os.environ.get(
    "SUPER_CRAWLER_DIR",
    os.path.expanduser("~/projects/super-crawler"),
)
MAX_WORKERS = 5
SUB_TIMEOUT = 30

BUYER_KEYWORDS = {
    "distributor": 3,
    "importer": 3,
    "supplier": 2,
    "wholesaler": 2,
    "dealer": 2,
    "reseller": 2,
    "procurement": 3,
    "tender": 3,
    "bid": 2,
    "government": 2,
    "school": 2,
    "education": 2,
    "educational": 2,
    "laboratory": 2,
    "lab": 1,
    "science": 1,
    "teaching": 1,
    "equipment": 1,
    "ensino": 2,
    "educacional": 2,
    "laboratório": 2,
    "escolar": 2,
    "didático": 2,
}

BAD_SITE_KEYWORDS = {
    "blog": -2,
    "news": -2,
    "wikipedia": -4,
    "directory": -2,
    "listing": -2,
    "marketplace": -1,
    "wordpress": -1,
    "security checkpoint": -6,
    "top 10": -4,
    "10 best": -4,
    "trade.gov": -5,
    "irs.gov": -5,
    "ec21.com": -3,
    "alibaba.com": -2,
}

# Penalize government procurement pages and school websites
GOVERNMENT_KEYWORDS = {
    "procurement notice": -5,
    "invitation to bid": -5,
    "request for quotation": -4,
    "bid bulletin": -5,
    "awarded to": -4,
    "notice of award": -5,
    "procurement of goods": -5,
    "bidding requirements": -4,
    "approved budget": -4,
    "government procurement": -5,
    "deped order": -6,
    "department of education": -3,
    "ministry of education": -3,
    "school district": -4,
    "school board": -4,
    "public school": -3,
    "admission": -3,
    "enrollment": -3,
    "curriculum": -2,
    "grade level": -3,
    "lesson plan": -4,
    "teacher guide": -4,
    "student handbook": -4,
}

MANUFACTURER_KEYWORDS = {
    "manufacturer",
    "factory",
    "oem",
    "odm",
    "manufacturing",
    "producer",
    "made in",
    "fabricante",
    "производитель",
    "製造",
}

DIRECT_BUYER_KEYWORDS = {
    "procurement",
    "purchasing",
    "import",
    "importer",
    "distributor",
    "wholesale",
    "wholesaler",
    "dealer",
    "reseller",
    "supplier",
    "distribution",
    "school supplier",
    "laboratory supplier",
}

END_USER_ONLY_KEYWORDS = {
    "kindergarten",
    "primary school",
    "middle school",
    "high school",
    "university",
    "college",
    "academy",
    "institute",
    "campus",
}

GENERIC_LOCALS = {
    "info",
    "contact",
    "sales",
    "support",
    "admin",
    "hello",
    "office",
    "vendas",
    "comercial",
}

# Email domains that are government/institutional — NOT business contacts
NON_BUSINESS_EMAIL_DOMAINS = {
    ".gov",
    ".gob",
    ".edu",
    ".ac.uk",
    ".k12",
    "gov.ph",
    "gov.br",
    "gov.mx",
    "gob.mx",
    "gov.ru",
    "deped.gov",
    "education.gov",
    "edu.ph",
    "edu.br",
}

INDEX_DIR = Path(
    os.environ.get(
        "HUNTER_INDEX_DIR",
        os.path.expanduser("~/.hubos/hunter/data/index"),
    ),
)


def read_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def load_feedback_stats():
    rows = read_jsonl(INDEX_DIR / "delivery_feedback.jsonl")
    stats = {
        "bounced_emails": set(),
        "bounced_domains": set(),
        "replied_emails": set(),
        "replied_domains": set(),
    }
    for r in rows:
        email = (
            (r.get("matched_email") or r.get("from_addr") or "")
            .lower()
            .strip()
        )
        domain = email.split("@", 1)[1] if "@" in email else ""
        event = (r.get("event_type") or "").lower().strip()
        if event == "bounce":
            if email:
                stats["bounced_emails"].add(email)
            if domain:
                stats["bounced_domains"].add(domain)
        elif event == "reply":
            if email:
                stats["replied_emails"].add(email)
            if domain:
                stats["replied_domains"].add(domain)
    return stats


def append_jsonl(path: Path, records):
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_candidate_indexes(results):
    ts = datetime.now().isoformat()
    domains = []
    companies = []
    emails = []
    for r in results:
        domains.append(
            {
                "ts": ts,
                "domain": r.get("domain", ""),
                "source_url": r.get("source_url", ""),
                "country_code": r.get("country_code", ""),
                "buyer_role": r.get("buyer_role", ""),
                "buyer_score": r.get("buyer_score", 0),
                "grade": r.get("grade", ""),
            },
        )
        companies.append(
            {
                "ts": ts,
                "company_name": r.get("company_name", ""),
                "domain": r.get("domain", ""),
                "country_code": r.get("country_code", ""),
                "country_name": r.get("country_name", ""),
                "buyer_role": r.get("buyer_role", ""),
                "buyer_score": r.get("buyer_score", 0),
                "grade": r.get("grade", ""),
                "contact_name": r.get("contact_name", ""),
                "contact_title": r.get("contact_title", ""),
                "contact_confidence": r.get("contact_confidence", 0),
                "match_reason": r.get("match_reason", ""),
            },
        )
        for e in r.get("email_details", []) or []:
            emails.append(
                {
                    "ts": ts,
                    "company_name": r.get("company_name", ""),
                    "domain": r.get("domain", ""),
                    "country_code": r.get("country_code", ""),
                    "email": e.get("email", ""),
                    "email_type": e.get("type", ""),
                    "email_source": e.get("source", ""),
                    "email_confidence": e.get("confidence", 0),
                    "first_name": e.get("first_name", ""),
                    "last_name": e.get("last_name", ""),
                    "position": e.get("position", ""),
                },
            )

    append_jsonl(INDEX_DIR / "domains.jsonl", domains)
    append_jsonl(INDEX_DIR / "companies.jsonl", companies)
    append_jsonl(INDEX_DIR / "emails.jsonl", emails)


def call_super_crawler(tool, params):
    cmd = [
        "node",
        f"{SUPER_CRAWLER_DIR}/src/tools.js",
        "call",
        tool,
        json.dumps(params),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUB_TIMEOUT,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(\{[\s\S]*\})", result.stdout)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception:
        return None


def root_brand_token(domain):
    domain = (domain or "").lower().strip()
    ignored = {
        "www",
        "en",
        "eng",
        "kr",
        "jp",
        "de",
        "fr",
        "ru",
        "vn",
        "cn",
        "zh",
    }
    labels = [x for x in domain.split(".") if x and x not in ignored]
    if not labels:
        return ""
    root = re.sub(r"[^a-z0-9]", "", labels[0])
    return root


def common_prefix_len(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def domain_matches(candidate_domain, email_domain):
    candidate_domain = (candidate_domain or "").lower().strip()
    email_domain = (email_domain or "").lower().strip()
    if not candidate_domain or not email_domain:
        return False
    return (
        candidate_domain == email_domain
        or candidate_domain.endswith("." + email_domain)
        or email_domain.endswith("." + candidate_domain)
    )


def related_domain_matches(candidate_domain, email_domain):
    if domain_matches(candidate_domain, email_domain):
        return True
    candidate_root = root_brand_token(candidate_domain)
    email_root = root_brand_token(email_domain)
    if not candidate_root or not email_root:
        return False
    if candidate_root == email_root and len(candidate_root) >= 3:
        return True
    if len(candidate_root) >= 6 and candidate_root in email_domain:
        return True
    if len(email_root) >= 6 and email_root in candidate_domain:
        return True
    return common_prefix_len(candidate_root, email_root) >= 6


def normalize_crawled_email(email, source="website_crawl"):
    email = unquote((email or "").strip()).strip().lower()
    email = email.replace("mailto:", "").strip()
    # 修复 protocol-relative URL bug: //sales@example.com → sales@example.com
    email = re.sub(r"^//+", "", email)
    email = re.sub(r"^(?:%[0-9a-f]{2}|\s)+", "", email, flags=re.I)
    email = email.replace(" ", "")
    if "@" not in email:
        return None
    # 清理仍以 // 开头的邮箱
    email = re.sub(r"^//+", "", email)
    local = email.split("@")[0]
    etype = "generic" if local in GENERIC_LOCALS else "personal"
    base_confidence = 0.9 if etype == "personal" else 0.84
    if source.startswith("raw_html"):
        base_confidence -= 0.03
    return {
        "email": email,
        "type": etype,
        "source": source,
        "confidence": round(base_confidence, 2),
        "first_name": "",
        "last_name": "",
        "position": "",
    }


CONTACT_PATHS = [
    "/",
    "/contact",
    "/contact-us",
    "/contacts",
    "/support",
    "/about",
    "/contacto",
    "/contato",
    "/kontakt",
]


EMAIL_REGEX = re.compile(r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", re.I)
CFEMAIL_REGEX = re.compile(r'data-cfemail=["\']([0-9a-fA-F]+)["\']', re.I)


def fetch_html(target_url):
    req = Request(
        target_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; HunterBot/1.0; +https://www.wxyanyang.com)",
        },
    )
    try:
        req.add_header(
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        req.add_header("Accept-Language", "en-US,en;q=0.9")
        req.add_header("Cache-Control", "no-cache")
        with urlopen(req, timeout=6) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if (
                "text/html" not in content_type
                and "application/xhtml+xml" not in content_type
            ):
                return ""
            raw = resp.read(512000)
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="ignore")
    except (HTTPError, URLError, TimeoutError, ValueError):
        return ""
    except Exception:
        return ""


def extract_title_from_html(html):
    if not html:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", unescape(m.group(1))).strip()
    return title


def decode_cfemail(encoded):
    try:
        if len(encoded) < 2:
            return ""
        key = int(encoded[:2], 16)
        out = []
        for i in range(2, len(encoded), 2):
            out.append(chr(int(encoded[i : i + 2], 16) ^ key))
        return "".join(out)
    except Exception:
        return ""


def extract_emails_from_html(html, domain, source="raw_html"):
    if not html:
        return []
    normalized = unescape(html)
    normalized = re.sub(r"(?i)\[\s*at\s*\]|\(\s*at\s*\)", "@", normalized)
    normalized = re.sub(r"(?i)\[\s*dot\s*\]|\(\s*dot\s*\)", ".", normalized)

    emails = []
    seen = set()
    patterns = []
    patterns.extend(
        unquote(x)
        for x in re.findall(r'mailto:([^"\'\s>?#]+)', normalized, re.I)
    )
    patterns.extend(m.group(1) for m in EMAIL_REGEX.finditer(normalized))
    patterns.extend(
        decode_cfemail(m.group(1)) for m in CFEMAIL_REGEX.finditer(normalized)
    )

    for email in patterns:
        email_obj = normalize_crawled_email(email, source=source)
        if not email_obj:
            continue
        email_domain = extract_domain_from_email(email_obj["email"])
        if not related_domain_matches(domain, email_domain):
            continue
        if not domain_matches(domain, email_domain):
            email_obj["source"] = f"{source}_related_domain"
            email_obj["confidence"] = min(float(email_obj["confidence"]), 0.72)
        if email_obj["email"] in seen:
            continue
        seen.add(email_obj["email"])
        emails.append(email_obj)
    return emails


def crawl_single_url(target_url, domain, source="website_crawl"):
    emails = []
    seen = set()
    title = ""
    pages = []

    html = fetch_html(target_url)
    raw_source = (
        "raw_html_contact_page"
        if source == "website_contact_page"
        else "raw_html"
    )
    for email_obj in extract_emails_from_html(html, domain, source=raw_source):
        if email_obj["email"] in seen:
            continue
        seen.add(email_obj["email"])
        emails.append(email_obj)
    if not title:
        title = extract_title_from_html(html)

    # 联系页优先轻抓，只有 raw html 没拿到邮箱时才走重爬
    should_use_web_crawl = source == "website_crawl" or not emails
    if should_use_web_crawl:
        result = call_super_crawler(
            "web_crawl",
            {
                "url": target_url,
                "depth": 1,
                "spa": True,
            },
        )
        if result and result.get("success"):
            data = result.get("data", {}) or {}
            pages = data.get("results", []) or []
            for page in pages:
                if not title and page.get("title"):
                    title = page.get("title", "")
                for email in page.get("emails", []) or []:
                    email_obj = normalize_crawled_email(email, source=source)
                    if not email_obj:
                        continue
                    if not domain_matches(
                        domain,
                        extract_domain_from_email(email_obj["email"]),
                    ):
                        continue
                    if email_obj["email"] in seen:
                        continue
                    seen.add(email_obj["email"])
                    emails.append(email_obj)

    return {
        "emails": emails,
        "title": title,
        "pages": len(pages),
        "visited_url": target_url,
    }


def has_strong_email(emails):
    return any(
        (e.get("type") == "personal")
        or (float(e.get("confidence") or 0) >= 0.88)
        for e in emails
    )


def discover_contact_links(base_url, domain, limit=3):
    html = fetch_html(base_url)
    if not html:
        return []
    keywords = (
        "contact",
        "support",
        "about",
        "company",
        "office",
        "kontakt",
        "contato",
        "contacto",
    )
    links = []
    seen = set()
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
        href_low = href.lower()
        if href_low.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        if not any(k in href_low for k in keywords):
            continue
        full = urljoin(base_url, href)
        if extract_domain(full) and not domain_matches(
            domain,
            extract_domain(full),
        ):
            continue
        full = full.split("#", 1)[0]
        if full in seen:
            continue
        seen.add(full)
        links.append(full)
        if len(links) >= limit:
            break
    return links


def crawl_site_contact_data(url, domain, max_extra_targets=4):
    parsed = urlparse(url)
    base_url = (
        f'{parsed.scheme or "https"}://{parsed.netloc}'
        if parsed.netloc
        else url
    )
    targets = []
    seen_targets = set()
    discovered_targets = discover_contact_links(base_url, domain, limit=3)
    candidate_targets = (
        [url]
        + [
            urljoin(base_url, path)
            for path in CONTACT_PATHS[:max_extra_targets]
        ]
        + discovered_targets
    )
    for target in candidate_targets:
        if target in seen_targets:
            continue
        seen_targets.add(target)
        targets.append(target)

    all_emails = []
    seen_emails = set()
    title = ""
    total_pages = 0
    visited_urls = []

    for idx, target in enumerate(targets):
        source = "website_contact_page" if idx > 0 else "website_crawl"
        result = crawl_single_url(target, domain, source=source)
        visited_urls.append(result.get("visited_url", target))
        total_pages += result.get("pages", 0)
        if not title and result.get("title"):
            title = result.get("title", "")
        for item in result.get("emails", []):
            if item["email"] in seen_emails:
                continue
            seen_emails.add(item["email"])
            all_emails.append(item)
        if has_strong_email(all_emails):
            break

    return {
        "emails": all_emails,
        "title": title,
        "pages": total_pages,
        "visited_urls": visited_urls,
    }


def extract_domain_from_email(email):
    if "@" not in (email or ""):
        return ""
    return email.split("@", 1)[1].lower().strip()


def extract_domain(url):
    try:
        netloc = urlparse(url).netloc.lower().strip()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def title_from_domain(domain):
    base = root_brand_token(domain) or domain.split(".")[0]
    return base.replace("-", " ").replace("_", " ").title()


LOW_VALUE_DOMAINS = [
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "wikipedia.org",
    "volza.com",
    "b2brazil.com",
    "trade.gov",
    "ec21.com",
    "globalsources.com",
    "made-in-china.com",
    "yellowpages-uae.com",
    "developmentaid.org",
    "go4worldbusiness.com",
    "trademo.com",
    "kompass.com",
    "industrystock.com",
    "turkishexporter.net",
    "saudiyellowpagesonline.com",
]

BAD_COMPANY_EXACT = {
    "school lab equipment manufacturer",
    "science lab equipment",
    "educational equipment supplier",
    "laboratory equipment",
    "educational",
    "equipment",
    "home",
    "welcome",
    "something went wrong",
    "english",
    "distribution",
    "contact us",
    "about us",
    "dealers and resellers",
    "authorized distributor",
    "find your distributor",
    "find a distributor",
    "distributors",
    "dealer",
    "reseller",
    "supplier",
    "importer",
    "company",
    "companies",
    "english version",
    "main website",
    "scientific instruments",
    "laboratory & scientific equipment supplier",
    "school educational equipments",
    "school lab instruments and equipments suppliers brazil manufacturers",
    "en",
    "kr",
    "jp",
    "403",
    "brazil",
    "mexico",
    "germany",
    "japan",
    "united states",
    "usa",
    "portugal",
    "vietnam",
}

GENERIC_COMPANY_TOKENS = {
    "lab",
    "laboratory",
    "education",
    "educational",
    "equipment",
    "science",
    "school",
    "supplies",
    "supplier",
    "importer",
    "distributor",
    "dealer",
    "reseller",
    "distribution",
    "contact",
    "about",
    "home",
    "welcome",
    "english",
    "version",
    "company",
    "companies",
    "global",
    "international",
    "business",
    "industrial",
    "marketplace",
    "directory",
    "yellowpages",
    "trade",
    "world",
    "viet",
    "vietnam",
    "korea",
}

BAD_COMPANY_PHRASES = [
    "contact us",
    "about us",
    "find a distributor",
    "find your distributor",
    "dealers and resellers",
    "authorized distributor",
    "school and office stationery",
    "education facility equipment",
    "product details",
    "tender suppliers",
    "buyers",
    "company distributors",
    "distributor page",
    "english version",
    "discover quality",
    "main website",
    "scientific equipment supplier",
    "school supplies at",
    "suppliers brazil manufacturers",
    "the worldfolio",
]


def is_low_value_url(url):
    low = (url or "").lower()
    if (
        low.endswith(".pdf")
        or "/wp-content/" in low
        or "/blog/" in low
        or "/news/" in low
    ):
        return True
    if any(x in low for x in LOW_VALUE_DOMAINS):
        return True
    # Filter government, school, and institutional domains
    if any(
        d in low
        for d in [".gov.", ".gob.", ".edu.", ".k12.", ".ac.uk", ".sch."]
    ):
        return True
    # Filter known government procurement domains
    if any(
        d in low for d in ["depedrizal", "deped.gov", "lazada.com", "shopee."]
    ):
        return True
    low_value_paths = [
        "/buyers/",
        "/supplier",
        "/suppliers",
        "/distributor",
        "/distributors",
        "/dealers",
        "/resellers",
        "/yellowpages",
        "/product-details/",
        "/tender-suppliers",
    ]
    if any(p in low for p in low_value_paths):
        return True
    return False


def is_low_value_text(*texts):
    low = " ".join((t or "").lower() for t in texts)
    patterns = [
        "security checkpoint",
        "vercel security checkpoint",
        "10 best",
        "top 10",
        "best suppliers",
        "best science lab equipment suppliers",
        "blog",
        "news",
        "article",
        "directory",
        "marketplace",
        "yellow pages",
        "dealer locator",
        "find a distributor",
        "dealers and resellers",
        "authorized distributor",
    ]
    return any(p in low for p in patterns)


def is_invalid_company_name(name, domain=""):
    name = (name or "").strip()
    low = name.lower()
    if not name:
        return True
    if len(name) < 3 or len(name) > 80:
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?", low):
        return True
    if low in BAD_COMPANY_EXACT:
        return True
    if any(phrase in low for phrase in BAD_COMPANY_PHRASES):
        return True

    tokens = [t for t in re.split(r"[^a-z0-9]+", low) if t]
    token_set = set(tokens)
    if token_set and token_set.issubset(GENERIC_COMPANY_TOKENS):
        return True

    generic_count = sum(1 for t in tokens if t in GENERIC_COMPANY_TOKENS)
    if tokens and generic_count / len(tokens) >= 0.8:
        return True

    if domain:
        root = root_brand_token(domain)
        alpha_root = re.sub(r"[^a-z]", "", root)
        alpha_name = re.sub(r"[^a-z]", "", low)
        if any(
            phrase in low
            for phrase in [
                "discover quality",
                "main website",
                "the worldfolio",
            ]
        ):
            return True
        if (
            alpha_root
            and alpha_name
            and alpha_root not in alpha_name
            and len(tokens) >= 2
            and generic_count >= max(1, len(tokens) - 1)
        ):
            return True
        if low in {"en", "english", "kr", "jp", "de", "fr", "ru", "vn"}:
            return True
        if root in {
            "en",
            "english",
            "kr",
            "jp",
            "de",
            "fr",
            "ru",
            "vn",
            "vietnam",
        }:
            return True
        if (
            domain.startswith(
                ("en.", "kr.", "jp.", "de.", "fr.", "ru.", "vn."),
            )
            and len(tokens) <= 2
        ):
            return True
        if (
            any(bad in domain for bad in LOW_VALUE_DOMAINS)
            and len(tokens) <= 4
        ):
            return True
    return False


def clean_company_name(name, domain=""):
    name = (name or "").replace("\n", " ").replace("\r", " ").strip()
    for sep in [
        "|",
        " - ",
        " — ",
        " :: ",
        " • ",
        " — ",
        " is ",
        " everything for ",
    ]:
        if sep in name.lower():
            idx = name.lower().find(sep)
            name = name[:idx].strip()
    while ",," in name:
        name = name.replace(",,", ",")
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if parts:
        dedup = []
        seen = set()
        for part in parts:
            key = part.lower()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(part)
        name = dedup[0]

    if is_invalid_company_name(name, domain):
        name = ""
    if not name and domain:
        fallback = title_from_domain(domain)
        if not is_invalid_company_name(fallback, domain):
            name = fallback
    return name.strip()


def infer_role(text):
    text = (text or "").lower()
    role_map = [
        ("government_buyer", ["government", "tender", "procurement", "bid"]),
        ("importer", ["importer"]),
        ("distributor", ["distributor", "dealer", "reseller", "wholesaler"]),
        ("supplier", ["supplier"]),
        (
            "school_or_lab_buyer",
            [
                "school",
                "laboratory",
                "education",
                "educational",
                "escolar",
                "educacional",
            ],
        ),
    ]
    for role, keys in role_map:
        if any(k in text for k in keys):
            return role
    return "unknown"


def classify_customer_fit(text):
    """Return a business-fit score and reasons for outreach targeting."""
    low = (text or "").lower()
    score = 0
    reasons = []

    for key in DIRECT_BUYER_KEYWORDS:
        if key in low:
            score += 2
            reasons.append(f"buyer:{key}")

    for key in MANUFACTURER_KEYWORDS:
        if key in low:
            score -= 3
            reasons.append(f"manufacturer:{key}")

    end_user_hits = [key for key in END_USER_ONLY_KEYWORDS if key in low]
    if end_user_hits and not any(
        k in low for k in ("procurement", "purchase", "import", "supplier")
    ):
        score -= 2
        reasons.append("end_user_only")

    if "contact" in low or "about us" in low:
        score += 1
    if "catalog" in low or "products" in low:
        score += 1

    return score, reasons


def buyer_score(text):
    low = (text or "").lower()
    score = 0
    hits = []
    for k, v in BUYER_KEYWORDS.items():
        if k in low:
            score += v
            hits.append(k)
    for k, v in BAD_SITE_KEYWORDS.items():
        if k in low:
            score += v
    for k, v in GOVERNMENT_KEYWORDS.items():
        if k in low:
            score += v
    return score, hits


def normalize_email_details(emails_raw, domain):
    details = []
    seen = set()
    for record in emails_raw or []:
        email = (record.get("email") or "").strip().lower()
        if not email or "@" not in email or email in seen:
            continue
        seen.add(email)
        local, host = email.split("@", 1)
        if not domain_matches(domain, host):
            continue
        etype = record.get("type") or (
            "generic" if local in GENERIC_LOCALS else "personal"
        )
        confidence = 0.98 if etype == "personal" else 0.82
        if local in GENERIC_LOCALS:
            confidence = min(confidence, 0.78)
        # Penalize government/institutional emails
        is_non_business = any(
            suffix in host for suffix in NON_BUSINESS_EMAIL_DOMAINS
        )
        if is_non_business:
            confidence *= 0.3  # Heavy penalty
            etype = "institutional"
        details.append(
            {
                "email": email,
                "type": etype,
                "source": "hunter_domain",
                "confidence": confidence,
                "first_name": record.get("first_name", ""),
                "last_name": record.get("last_name", ""),
                "position": record.get("position", ""),
            },
        )
    details.sort(
        key=lambda x: (x["confidence"], x["type"] == "personal"),
        reverse=True,
    )
    return details


def choose_primary_email(details):
    if not details:
        return "", "", 0.0
    best = details[0]
    return best["email"], best["source"], float(best["confidence"])


def should_continue_email_enrichment(email_details):
    if not email_details:
        return True
    best = email_details[0]
    best_type = (best.get("type") or "").lower()
    best_conf = float(best.get("confidence") or 0)
    best_source = (best.get("source") or "").lower()
    if (
        best_source == "hunter_domain"
        and best_type == "personal"
        and best_conf >= 0.95
    ):
        return False
    if best_type == "generic" or best_conf < 0.88:
        return True
    return False


def choose_primary_contact(email_details):
    for item in email_details:
        first = (item.get("first_name") or "").strip()
        last = (item.get("last_name") or "").strip()
        position = (item.get("position") or "").strip()
        if first or last:
            full = " ".join(x for x in [first, last] if x).strip()
            confidence = 0.92 if item.get("type") == "personal" else 0.75
            return full, position, confidence
    return "", "", 0.0


def grade_candidate(buyer_score_value, email_confidence, feedback_bonus=0):
    total = buyer_score_value + feedback_bonus
    if total >= 8 and email_confidence >= 0.8:
        return "A"
    if total >= 4:
        return "B"
    return "C"


def needs_email_enrichment(candidate):
    email_pattern = (candidate.get("email_pattern") or "").strip()
    email_source = (candidate.get("email_source") or "").strip().lower()
    email_confidence = float(candidate.get("email_confidence") or 0)

    if not email_pattern or email_source in {"", "none"}:
        return True
    if email_source == "guessed_generic" and email_confidence < 0.8:
        return True
    return False


def process_url(candidate, semaphore, feedback_stats):
    with semaphore:
        url = candidate.get("url", "")
        domain = extract_domain(url)
        if not domain or is_low_value_url(url):
            return None

        hunter_result = call_super_crawler(
            "hunter_domain",
            {
                "domain": domain,
                "found_only": True,
            },
        )

        emails_raw = []
        company_name = ""
        company_desc = candidate.get("snippet", "") or ""
        crawl_data = {"emails": [], "title": "", "pages": 0}

        if hunter_result and hunter_result.get("success"):
            data = hunter_result.get("data", {}) or {}
            emails_raw = data.get("emails", []) or []
            company_info = data.get("company", {}) or {}
            company_name = company_info.get("name", "") or ""
            if company_info.get("description"):
                company_desc = company_info["description"]

        email_details = normalize_email_details(emails_raw, domain)
        if should_continue_email_enrichment(email_details) or not company_name:
            max_extra_targets = 6 if not email_details else 3
            crawl_data = crawl_site_contact_data(
                url,
                domain,
                max_extra_targets=max_extra_targets,
            )

        company_name = clean_company_name(
            company_name or crawl_data.get("title", ""),
            domain,
        )
        if not company_name:
            return None

        if crawl_data.get("emails"):
            seen_emails = {x["email"] for x in email_details}
            for item in crawl_data["emails"]:
                if item["email"] not in seen_emails:
                    email_details.append(item)
            email_details.sort(
                key=lambda x: (x["confidence"], x["type"] == "personal"),
                reverse=True,
            )
        primary_email, email_source, email_confidence = choose_primary_email(
            email_details,
        )
        (
            contact_name,
            contact_title,
            contact_confidence,
        ) = choose_primary_contact(email_details)

        feedback_bonus = 0
        if domain in feedback_stats["replied_domains"]:
            feedback_bonus += 2
        if domain in feedback_stats["bounced_domains"]:
            feedback_bonus -= 3
        if (
            primary_email
            and primary_email.lower() in feedback_stats["replied_emails"]
        ):
            feedback_bonus += 2
        if (
            primary_email
            and primary_email.lower() in feedback_stats["bounced_emails"]
        ):
            feedback_bonus -= 4

        combined_text = " ".join(
            [
                company_name,
                company_desc,
                candidate.get("title", ""),
                crawl_data.get("title", ""),
                candidate.get("search_term", ""),
                domain,
            ],
        )
        if is_low_value_text(
            company_name,
            candidate.get("title", ""),
            crawl_data.get("title", ""),
            company_desc,
        ):
            return None

        bscore, bhits = buyer_score(combined_text)
        fit_score, fit_reasons = classify_customer_fit(combined_text)
        bscore += fit_score
        role = infer_role(combined_text)
        effective_score = bscore + feedback_bonus

        # 太弱的站直接跳过
        if effective_score < 2:
            return None

        grade = grade_candidate(bscore, email_confidence, feedback_bonus)

        match_reason = []
        if bhits:
            match_reason.append("buyer_signals=" + ",".join(bhits[:6]))
        if fit_reasons:
            match_reason.append("fit=" + ",".join(fit_reasons[:6]))
        if email_details:
            match_reason.append(f"email_source={email_source}")
        else:
            match_reason.append("no_verified_email_yet")
        if crawl_data.get("emails"):
            match_reason.append(
                f'crawl_emails={len(crawl_data.get("emails", []))}',
            )

        result = {
            "url": url,
            "domain": domain,
            "company_name": company_name,
            "company_desc": company_desc,
            "country_code": candidate.get("country_code", ""),
            "country_name": candidate.get("country_name", ""),
            "search_term": candidate.get("search_term", ""),
            "emails": [x["email"] for x in email_details],
            "email_details": email_details,
            "phones": [],
            "website": url,
            "source_url": url,
            "business_description": company_desc,
            "email_pattern": primary_email,
            "email_source": email_source or "none",
            "email_confidence": email_confidence,
            "contact_name": contact_name,
            "contact_title": contact_title,
            "contact_confidence": contact_confidence,
            "buyer_role": role,
            "buyer_score": bscore,
            "customer_fit_score": fit_score,
            "customer_fit_reasons": fit_reasons,
            "feedback_bonus": feedback_bonus,
            "effective_score": effective_score,
            "buyer_signals": bhits,
            "match_reason": "; ".join(match_reason),
            "grade": grade,
            "crawl_pages": crawl_data.get("pages", 0),
            "crawl_title": crawl_data.get("title", ""),
            "extraction_method": "super_crawler_parallel_v3",
        }
        return result


def main():
    input_file = (
        sys.argv[1] if len(sys.argv) > 1 else "stage/tavily_results.json"
    )
    output_candidates = (
        sys.argv[2] if len(sys.argv) > 2 else "stage/candidates_graded.json"
    )

    with open(input_file, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    print(f"📋 待处理: {len(candidates)} 个候选URL (并行数: {MAX_WORKERS})")

    semaphore = Semaphore(MAX_WORKERS)
    feedback_stats = load_feedback_stats()
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_url, c, semaphore, feedback_stats): c
            for c in candidates
        }
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result:
                results.append(result)
                print(
                    f"[{done}/{len(candidates)}] ✅ {result['grade']} {result['buyer_role']}: {result['company_name']} | {result.get('email_pattern','-')}",
                )
            else:
                print(f"[{done}/{len(candidates)}] ⏭️ 跳过弱候选")

    results.sort(
        key=lambda x: (
            x.get("grade") == "A",
            x.get("buyer_score", 0),
            x.get("email_confidence", 0),
        ),
        reverse=True,
    )

    deduped = []
    seen_domains = set()
    for item in results:
        domain = item.get("domain", "")
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        deduped.append(item)
    results = deduped

    with open(output_candidates, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    enrichment_candidates = [r for r in results if needs_email_enrichment(r)]
    enrichment_path = Path(output_candidates).with_name(
        "candidates_needing_enrichment.json",
    )
    with enrichment_path.open("w", encoding="utf-8") as f:
        json.dump(enrichment_candidates, f, ensure_ascii=False, indent=2)

    write_candidate_indexes(results)

    a_count = len([r for r in results if r.get("grade") == "A"])
    b_count = len([r for r in results if r.get("grade") == "B"])
    c_count = len([r for r in results if r.get("grade") == "C"])

    print(f"\n✅ 完成：A级{a_count}个 / B级{b_count}个 / C级{c_count}个")
    print(f"📬 待补邮箱候选: {len(enrichment_candidates)} 个")
    print(f"📁 输出: {output_candidates}")


if __name__ == "__main__":
    main()
