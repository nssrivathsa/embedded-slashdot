import asyncio
import datetime
import hashlib
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

DB_PATH = os.path.join(os.path.dirname(__file__), "embedded_slashdot.db")

SLASHDOT_EDITORS = ["CmdrTaco", "Hemos", "ScuttleMonkey", "Timothy", "Soulskill", "Samzenpus"]

# In-memory cache for RSS/Substack archive indexes so 3-year date lookups are fast
_NEWSLETTER_ARCHIVE_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "items": []}

CURATION_POLICY = {
    "name": "EmbeddedDot Multi-Source 3-Year Rolling Curation Policy v2.0",
    "max_lookback_days": 1095,  # 3 years
    "stories_per_day": 9,
    "sources": [
        {
            "id": "arxiv",
            "name": "arXiv Preprints (cs.AR, cs.LG, cs.CV, eess.SP, cs.PF)",
            "method": "Date-windowed query via arXiv API (submittedDate range around target day)",
            "focus": "TinyML, Microcontroller NPUs, Sub-4-bit Quantization, RISC-V Vector/Matrix Extensions, FPGA/ASIC Edge Accelerators",
        },
        {
            "id": "cnx_software",
            "name": "CNX Software – Embedded Systems News & Newsletter",
            "method": "WordPress REST API (/wp-json/wp/v2/posts?after=...&before=...) for exact historical dates",
            "focus": "Edge AI SBCs, Hailo/Axelera/Rockchip NPUs, RISC-V SoCs, ESP32/STM32 MCUs, Zephyr & Embedded Linux",
        },
        {
            "id": "hackaday",
            "name": "Hackaday Hardware & Firmware Dispatch",
            "method": "WordPress REST API (/wp-json/wp/v2/posts?after=...&before=...) filtered for AI/FPGA/MCU",
            "focus": "Bare-metal neural nets, FPGA accelerators, custom PCBs, neuromorphic & ultra-low-power sensor hacks",
        },
        {
            "id": "newsletters",
            "name": "Free Embedded & AI Newsletters (Interrupt by Memfault, SemiAnalysis, Import AI, Ahead of AI, Last Week in AI)",
            "method": "Substack Archive API (/api/v1/archive) & Atom/RSS historical feeds matched to target date window",
            "focus": "Firmware engineering, silicon supply chain & NPU architecture, small language models, edge inference benchmarks",
        },
        {
            "id": "openalex_ieee",
            "name": "OpenAlex Peer-Reviewed IEEE / ACM Embedded Systems Papers",
            "method": "Date-filtered query (from_publication_date / to_publication_date) across IEEE TCAD, IEEE Micro, ACM TECS, IoT-J",
            "focus": "Peer-reviewed silicon design, compute-in-memory (CIM), hardware-software co-design, real-time embedded ML",
        },
        {
            "id": "hf_daily",
            "name": "Hugging Face Daily Papers Archive",
            "method": "Date-indexed API (/api/daily_papers?date=YYYY-MM-DD) filtered for quantization, SLMs & edge inference",
            "focus": "On-device Small Language Models (SLMs), KV-cache compression, 1-bit/2-bit/4-bit quantization, mobile VLMs",
        },
        {
            "id": "hn_algolia",
            "name": "Hacker News Engineering Archive (Algolia Timestamp Search)",
            "method": "Exact Unix timestamp window (created_at_i) via Algolia HN API",
            "focus": "llama.cpp / ExecuTorch / LiteRT / microTVM releases, community hardware teardowns & benchmarks",
        },
    ],
    "scoring_weights": {
        "hardware_constraint_keywords": 3.0,
        "quantization_compression_keywords": 2.5,
        "silicon_architecture_keywords": 2.5,
        "quantitative_metrics_present": 2.0,
        "source_diversity_quota": "Balanced across Preprints, Peer-Reviewed IEEE/ACM, Embedded Newsletters, CNX/Hackaday Hardware Blogs & HN",
    },
}

TOPIC_CATEGORIES = [
    {"id": "tinyml", "name": "TinyML & MCUs", "icon": "microchip", "keywords": ["tinyml", "mcu", "microcontroller", "cortex-m", "stm32", "esp32", "rp2040", "rp2350", "sram", "kilobyte", "zephyr", "rtos", "arduino", "firmware", "memfault"]},
    {"id": "quantization", "name": "Quantization & Compression", "icon": "binary", "keywords": ["quantiz", "int8", "int4", "int2", "binary", "ternary", "bitnet", "pruning", "sparsity", "distillation", "low-bit", "fp8", "ptq", "qat", "compression"]},
    {"id": "npu_asic", "name": "NPUs, RISC-V & Silicon", "icon": "cpu", "keywords": ["npu", "risc-v", "asic", "systolic", "accelerator", "tops/w", "tops", "pim", "compute-in-memory", "cim", "analog", "neuromorphic", "spiking", "snn", "hailo", "rockchip", "qualcomm", "semiconductor", "silicon"]},
    {"id": "fpga", "name": "FPGA & Reconfigurable", "icon": "grid", "keywords": ["fpga", "zynq", "versal", "verilog", "vhdl", "hls", "lut", "reconfigurable", "bitstream", "lattice", "xilinx", "altera"]},
    {"id": "edge_llm", "name": "On-Device LLMs & VLMs", "icon": "terminal", "keywords": ["on-device", "edge llm", "small language model", "slm", "mobile", "prefill", "kv cache", "dram", "flash", "llama.cpp", "gguf", "executorch", "litert"]},
    {"id": "edge_vision", "name": "Embedded Vision & Sensors", "icon": "eye", "keywords": ["camera", "sensor", "radar", "lidar", "event-based", "dvs", "yolo", "object detection", "drone", "uav", "autonomous", "robotics", "imu", "audio", "keyword spotting", "sbc"]},
]

HIGH_SIGNAL_KEYWORDS = {
    "sram": 4.0,
    "tinyml": 4.0,
    "microcontroller": 4.0,
    "cortex-m": 4.0,
    "risc-v": 3.5,
    "npu": 3.5,
    "fpga": 3.0,
    "quantization": 3.0,
    "quantized": 2.5,
    "on-device": 3.0,
    "edge ai": 3.5,
    "edge inference": 3.5,
    "low-power": 3.0,
    "ultra-low-power": 3.5,
    "energy-efficient": 2.5,
    "compute-in-memory": 3.5,
    "in-memory computing": 3.5,
    "systolic": 3.0,
    "accelerator": 2.5,
    "int4": 3.0,
    "int8": 2.5,
    "bit-width": 2.5,
    "mixed-precision": 2.5,
    "pruning": 2.0,
    "sparsity": 2.0,
    "neuromorphic": 3.0,
    "spiking": 2.5,
    "asic": 3.0,
    "embedded": 3.0,
    "firmware": 2.5,
    "rtos": 3.0,
    "zephyr": 3.0,
    "tops/w": 4.0,
    "tops": 3.0,
    "esp32": 3.5,
    "stm32": 3.5,
    "hailo": 3.5,
    "mw": 2.5,
    "uj/op": 3.5,
    "kv cache": 2.0,
}


def strip_html_tags(raw_html: str) -> str:
    if not raw_html:
        return ""
    cleaned = re.sub(r"<script.*?>.*?</script>", " ", raw_html, flags=re.S | re.I)
    cleaned = re.sub(r"<style.*?>.*?</style>", " ", cleaned, flags=re.S | re.I)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_editions (
                date_str TEXT PRIMARY KEY,
                curated_at TEXT NOT NULL,
                summarizer_mode TEXT NOT NULL,
                stories_json TEXT NOT NULL,
                stats_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                story_id TEXT NOT NULL,
                date_str TEXT NOT NULL,
                author TEXT NOT NULL,
                uid INTEGER NOT NULL,
                title TEXT NOT NULL,
                score TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row:
            return row[0]
    return os.environ.get(key, default)


def set_setting(key: str, value: str) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def classify_topic(title: str, summary: str) -> Dict[str, str]:
    text = f"{title} {summary}".lower()
    best_cat = TOPIC_CATEGORIES[0]
    best_score = -1
    for cat in TOPIC_CATEGORIES:
        score = sum(2 if kw in title.lower() else 1 for kw in cat["keywords"] if kw in text)
        if score > best_score:
            best_score = score
            best_cat = cat
    return {"id": best_cat["id"], "name": best_cat["name"], "icon": best_cat["icon"]}


def score_candidate(title: str, abstract: str) -> float:
    text = f"{title} {abstract}".lower()
    title_lower = title.lower()
    score = 0.0
    for kw, weight in HIGH_SIGNAL_KEYWORDS.items():
        if kw in title_lower:
            score += weight * 1.6
        elif kw in text:
            score += weight

    quant_matches = re.findall(
        r"\b\d+(?:\.\d+)?\s*(?:kb|mb|mw|uw|ghz|mhz|tops|fps|ms|x\s+speedup|bit|-bit|nm)\b",
        text,
        flags=re.IGNORECASE,
    )
    score += min(len(quant_matches) * 1.5, 7.5)
    return round(score, 2)


# =====================================================================
# SOURCE 1: arXiv Technical Preprints (Date-Windowed)
# =====================================================================
async def fetch_arxiv_for_date(target_date: datetime.date) -> List[Dict[str, Any]]:
    start_dt = target_date - datetime.timedelta(days=4)
    end_dt = target_date

    start_str = start_dt.strftime("%Y%m%d0000")
    end_str = end_dt.strftime("%Y%m%d2359")

    search_query = (
        f"submittedDate:[{start_str} TO {end_str}] AND "
        f'(cat:cs.AR OR cat:eess.SP OR cat:cs.PF OR '
        f'(cat:cs.LG AND (all:embedded OR all:edge OR all:quantization OR all:microcontroller OR all:fpga OR all:accelerator OR all:tinyml OR all:"on-device" OR all:"low-power")) OR '
        f'(cat:cs.CV AND (all:embedded OR all:edge OR all:fpga OR all:microcontroller OR all:quantization OR all:"real-time")))'
    )

    url = "https://export.arxiv.org/api/query"
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": 35,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    candidates: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=14.0, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                candidates = parse_arxiv_xml(resp.text, target_date)
    except Exception as e:
        print(f"[arXiv fetch warning for {target_date}]: {e}")

    if len(candidates) < 4:
        wider_start = (target_date - datetime.timedelta(days=12)).strftime("%Y%m%d0000")
        wider_query = (
            f"submittedDate:[{wider_start} TO {end_str}] AND "
            f'(cat:cs.AR OR (all:embedded AND all:neural) OR all:tinyml OR all:microcontroller OR all:"edge ai" OR all:fpga)'
        )
        try:
            async with httpx.AsyncClient(timeout=14.0, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    params={
                        "search_query": wider_query,
                        "start": 0,
                        "max_results": 35,
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    },
                )
                if resp.status_code == 200:
                    more = parse_arxiv_xml(resp.text, target_date)
                    seen_urls = {c["url"] for c in candidates}
                    for item in more:
                        if item["url"] not in seen_urls:
                            candidates.append(item)
                            seen_urls.add(item["url"])
        except Exception as e:
            print(f"[arXiv fallback fetch warning for {target_date}]: {e}")

    return candidates


def parse_arxiv_xml(xml_text: str, target_date: datetime.date) -> List[Dict[str, Any]]:
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml_text)
    entries = []

    for entry in root.findall("atom:entry", ns):
        id_elem = entry.find("atom:id", ns)
        title_elem = entry.find("atom:title", ns)
        summary_elem = entry.find("atom:summary", ns)
        published_elem = entry.find("atom:published", ns)

        if id_elem is None or title_elem is None or summary_elem is None:
            continue

        raw_id = id_elem.text.strip()
        title = re.sub(r"\s+", " ", title_elem.text.strip())
        abstract = re.sub(r"\s+", " ", summary_elem.text.strip())
        published = published_elem.text.strip() if published_elem is not None else target_date.isoformat()

        authors = []
        for author in entry.findall("atom:author", ns):
            name_el = author.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        cats = []
        for cat in entry.findall("atom:category", ns):
            term = cat.attrib.get("term")
            if term:
                cats.append(term)

        arxiv_id_match = re.search(r"abs/([^\s/]+(?:/[^\s/]+)?)", raw_id)
        arxiv_id = arxiv_id_match.group(1) if arxiv_id_match else raw_id.split("/")[-1]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

        rel_score = score_candidate(title, abstract)
        day_hash = int(hashlib.md5(f"{target_date.isoformat()}:{arxiv_id}".encode()).hexdigest()[:6], 16) % 100 / 25.0
        exact_day_bonus = 6.0 if published[:10] == target_date.isoformat() else 0.0

        entries.append(
            {
                "id": f"arxiv-{arxiv_id}",
                "source_type": "arxiv",
                "source_family": "arXiv Preprint",
                "source_label": f"arXiv:{arxiv_id} [{cats[0] if cats else 'cs.AR'}]",
                "title": title,
                "url": raw_id.replace("http://", "https://"),
                "pdf_url": pdf_url,
                "authors": authors[:4],
                "published_at": published,
                "abstract": abstract,
                "categories": cats[:3],
                "relevance_score": round(rel_score + exact_day_bonus + day_hash, 2),
            }
        )

    entries.sort(key=lambda x: x["relevance_score"], reverse=True)
    return entries


# =====================================================================
# SOURCE 2: CNX Software Embedded News & Newsletter (Date-Windowed WP API)
# =====================================================================
async def fetch_cnx_software_for_date(target_date: datetime.date) -> List[Dict[str, Any]]:
    start_dt = target_date - datetime.timedelta(days=3)
    after_str = f"{start_dt.isoformat()}T00:00:00"
    before_str = f"{target_date.isoformat()}T23:59:59"

    url = "https://www.cnx-software.com/wp-json/wp/v2/posts"
    results: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True, headers={"User-Agent": "EmbeddedDot/2.0"}
        ) as client:
            resp = await client.get(
                url,
                params={
                    "after": after_str,
                    "before": before_str,
                    "per_page": 20,
                },
            )
            if resp.status_code == 200:
                posts = resp.json()
                for p in posts:
                    pid = p.get("id")
                    title = strip_html_tags(p.get("title", {}).get("rendered", ""))
                    excerpt = strip_html_tags(p.get("excerpt", {}).get("rendered", ""))
                    content_text = strip_html_tags(p.get("content", {}).get("rendered", ""))[:900]
                    summary = excerpt if len(excerpt) > 100 else content_text
                    link = p.get("link") or ""
                    pub_date = p.get("date") or target_date.isoformat()

                    if not title or not link:
                        continue

                    rel = score_candidate(title, summary) + 5.0
                    if pub_date[:10] == target_date.isoformat():
                        rel += 5.0

                    results.append(
                        {
                            "id": f"cnx-{pid}",
                            "source_type": "newsletter_blog",
                            "source_family": "CNX Software Newsletter",
                            "source_label": "CNX Software (Embedded Dispatch)",
                            "title": title,
                            "url": link,
                            "authors": ["Jean-Luc Aufranc (CNXSoft)"],
                            "published_at": pub_date,
                            "abstract": summary,
                            "categories": ["Embedded Hardware & SBCs"],
                            "relevance_score": round(rel, 2),
                        }
                    )
    except Exception as e:
        print(f"[CNX Software fetch warning for {target_date}]: {e}")

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results


# =====================================================================
# SOURCE 3: Hackaday Hardware & Firmware Blog (Date-Windowed WP API)
# =====================================================================
async def fetch_hackaday_for_date(target_date: datetime.date) -> List[Dict[str, Any]]:
    start_dt = target_date - datetime.timedelta(days=4)
    after_str = f"{start_dt.isoformat()}T00:00:00"
    before_str = f"{target_date.isoformat()}T23:59:59"

    url = "https://hackaday.com/wp-json/wp/v2/posts"
    results: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True, headers={"User-Agent": "EmbeddedDot/2.0"}
        ) as client:
            resp = await client.get(
                url,
                params={
                    "after": after_str,
                    "before": before_str,
                    "per_page": 25,
                },
            )
            if resp.status_code == 200:
                posts = resp.json()
                for p in posts:
                    pid = p.get("id")
                    title = strip_html_tags(p.get("title", {}).get("rendered", ""))
                    excerpt = strip_html_tags(p.get("excerpt", {}).get("rendered", ""))
                    link = p.get("link") or ""
                    pub_date = p.get("date") or target_date.isoformat()

                    if not title or not link:
                        continue

                    rel = score_candidate(title, excerpt)
                    # Filter Hackaday posts to those relevant to AI / MCU / FPGA / neural / edge / DSP / RISC-V
                    hay = f"{title} {excerpt}".lower()
                    if rel < 2.5 and not any(
                        w in hay
                        for w in ["ai", "neural", "machine learning", "fpga", "risc-v", "stm32", "esp32", "rp2040", "rp2350", "npu", "llm", "vision", "dsp", "microcontroller"]
                    ):
                        continue

                    if pub_date[:10] == target_date.isoformat():
                        rel += 4.0

                    results.append(
                        {
                            "id": f"had-{pid}",
                            "source_type": "newsletter_blog",
                            "source_family": "Hackaday",
                            "source_label": "Hackaday (Hardware Dispatch)",
                            "title": title,
                            "url": link,
                            "authors": ["Hackaday Editors"],
                            "published_at": pub_date,
                            "abstract": excerpt,
                            "categories": ["Hardware Hacks & Firmware"],
                            "relevance_score": round(rel + 4.0, 2),
                        }
                    )
    except Exception as e:
        print(f"[Hackaday fetch warning for {target_date}]: {e}")

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results


# =====================================================================
# SOURCE 4: Free Newsletters (Interrupt by Memfault, SemiAnalysis, Import AI, Ahead of AI, Last Week in AI)
# =====================================================================
async def _load_newsletter_archive_index() -> List[Dict[str, Any]]:
    now = time.time()
    if _NEWSLETTER_ARCHIVE_CACHE["items"] and (now - _NEWSLETTER_ARCHIVE_CACHE["loaded_at"] < 3600):
        return _NEWSLETTER_ARCHIVE_CACHE["items"]

    items: List[Dict[str, Any]] = []
    substack_sources = [
        ("SemiAnalysis Newsletter", "https://www.semianalysis.com/api/v1/archive?limit=50", "Dylan Patel (SemiAnalysis)"),
        ("Import AI Newsletter", "https://importai.substack.com/api/v1/archive?limit=50", "Jack Clark (Import AI)"),
        ("Ahead of AI Newsletter", "https://magazine.sebastianraschka.com/api/v1/archive?limit=50", "Sebastian Raschka (Ahead of AI)"),
        ("Last Week in AI Newsletter", "https://lastweekin.ai/api/v1/archive?limit=50", "Last Week in AI Editors"),
    ]

    async with httpx.AsyncClient(
        timeout=10.0, follow_redirects=True, headers={"User-Agent": "EmbeddedDot/2.0"}
    ) as client:
        # 1. Fetch Interrupt by Memfault Atom XML (contains full multi-year embedded engineering archive!)
        try:
            r = await client.get("https://interrupt.memfault.com/feed.xml")
            if r.status_code == 200:
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                root = ET.fromstring(r.text)
                for entry in root.findall("atom:entry", ns):
                    t_el = entry.find("atom:title", ns)
                    p_el = entry.find("atom:published", ns)
                    s_el = entry.find("atom:summary", ns)
                    c_el = entry.find("atom:content", ns)
                    link_el = entry.find("atom:link", ns)
                    author_el = entry.find("atom:author/atom:name", ns)

                    if t_el is None or p_el is None:
                        continue
                    title = strip_html_tags(t_el.text or "")
                    pub = (p_el.text or "").strip()[:10]
                    link = link_el.attrib.get("href", "https://interrupt.memfault.com") if link_el is not None else "https://interrupt.memfault.com"
                    raw_summary = (s_el.text if s_el is not None and s_el.text else (c_el.text if c_el is not None else "")) or ""
                    summary = strip_html_tags(raw_summary)[:650]
                    author = author_el.text.strip() if (author_el is not None and author_el.text) else "Memfault Interrupt"

                    items.append(
                        {
                            "id": f"memfault-{hashlib.md5(link.encode()).hexdigest()[:8]}",
                            "source_type": "newsletter",
                            "source_family": "Interrupt by Memfault",
                            "source_label": "Interrupt Newsletter (Memfault)",
                            "title": title,
                            "url": link,
                            "authors": [author],
                            "published_date": pub,
                            "published_at": pub,
                            "abstract": summary,
                            "categories": ["Firmware & MCU Newsletter"],
                        }
                    )
        except Exception as e:
            print(f"[Interrupt Memfault RSS warning]: {e}")

        # 2. Fetch Substack newsletter archives
        for label, url, default_author in substack_sources:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    posts = r.json()
                    if isinstance(posts, list):
                        for p in posts:
                            title = (p.get("title") or "").strip()
                            subtitle = (p.get("subtitle") or "").strip()
                            body_snip = (p.get("truncated_body_text") or p.get("description") or "").strip()
                            pub = (p.get("post_date") or "")[:10]
                            link = p.get("canonical_url") or ""
                            if not title or not pub or not link:
                                continue
                            abstract = f"{subtitle}. {body_snip}".strip(". ")
                            items.append(
                                {
                                    "id": f"substack-{p.get('id', hashlib.md5(link.encode()).hexdigest()[:8])}",
                                    "source_type": "newsletter",
                                    "source_family": label,
                                    "source_label": f"{label} (Substack)",
                                    "title": title,
                                    "url": link,
                                    "authors": [default_author],
                                    "published_date": pub,
                                    "published_at": pub,
                                    "abstract": abstract[:650],
                                    "categories": ["AI & Silicon Newsletter"],
                                }
                            )
            except Exception as e:
                print(f"[{label} fetch warning]: {e}")

    _NEWSLETTER_ARCHIVE_CACHE["loaded_at"] = now
    _NEWSLETTER_ARCHIVE_CACHE["items"] = items
    return items


async def fetch_newsletters_for_date(target_date: datetime.date) -> List[Dict[str, Any]]:
    all_items = await _load_newsletter_archive_index()
    matched: List[Dict[str, Any]] = []

    for item in all_items:
        try:
            pub_dt = datetime.date.fromisoformat(item["published_date"])
        except Exception:
            continue
        delta_days = abs((target_date - pub_dt).days)
        # Newsletters typically publish weekly or bi-weekly, so match within a 10-day window up to target_date
        if pub_dt <= target_date and delta_days <= 10:
            rel = score_candidate(item["title"], item["abstract"]) + max(0.0, (10 - delta_days) * 0.8) + 4.5
            copy_item = dict(item)
            copy_item["relevance_score"] = round(rel, 2)
            matched.append(copy_item)

    matched.sort(key=lambda x: x["relevance_score"], reverse=True)
    return matched


# =====================================================================
# SOURCE 5: OpenAlex Peer-Reviewed IEEE / ACM Embedded AI Papers
# =====================================================================
async def fetch_openalex_for_date(target_date: datetime.date) -> List[Dict[str, Any]]:
    start_dt = target_date - datetime.timedelta(days=5)
    url = "https://api.openalex.org/works"
    filter_str = (
        f"from_publication_date:{start_dt.isoformat()},"
        f"to_publication_date:{target_date.isoformat()},"
        f"default.search:embedded neural OR microcontroller OR fpga accelerator OR tinyml OR quantization"
    )
    results: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True, headers={"User-Agent": "EmbeddedDot/2.0 (mailto:nerd@localhost)"}
        ) as client:
            resp = await client.get(url, params={"filter": filter_str, "per_page": 15})
            if resp.status_code == 200:
                data = resp.json()
                for w in data.get("results", []):
                    wid = (w.get("id") or "").split("/")[-1]
                    title = (w.get("title") or "").strip()
                    if not title:
                        continue
                    pub_date = w.get("publication_date") or target_date.isoformat()
                    doi_url = w.get("doi") or (w.get("primary_location") or {}).get("landing_page_url") or w.get("id")
                    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "IEEE / ACM Proceedings"

                    # Reconstruct abstract from OpenAlex inverted index if available
                    inv = w.get("abstract_inverted_index")
                    abstract = ""
                    if isinstance(inv, dict):
                        pos_map = {}
                        for word, positions in inv.items():
                            for p in positions:
                                pos_map[p] = word
                        abstract = " ".join(pos_map[k] for k in sorted(pos_map.keys()))
                    if not abstract:
                        abstract = f"Peer-reviewed embedded systems & hardware architecture paper published in {venue} on {pub_date}."

                    authors = [
                        (a.get("author") or {}).get("display_name", "")
                        for a in (w.get("authorships") or [])[:4]
                        if (a.get("author") or {}).get("display_name")
                    ]

                    rel = score_candidate(title, abstract) + 3.5
                    if pub_date == target_date.isoformat():
                        rel += 4.0

                    results.append(
                        {
                            "id": f"openalex-{wid}",
                            "source_type": "ieee_acm",
                            "source_family": "IEEE / ACM Peer-Reviewed",
                            "source_label": f"{venue[:34]} ({pub_date})",
                            "title": title,
                            "url": doi_url,
                            "authors": authors or ["IEEE / ACM Researchers"],
                            "published_at": pub_date,
                            "abstract": abstract[:800],
                            "categories": [venue],
                            "relevance_score": round(rel, 2),
                        }
                    )
    except Exception as e:
        print(f"[OpenAlex fetch warning for {target_date}]: {e}")

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results


# =====================================================================
# SOURCE 6: Hugging Face Daily Papers (Date-Windowed API)
# =====================================================================
async def fetch_hf_daily_papers_for_date(target_date: datetime.date) -> List[Dict[str, Any]]:
    url = "https://huggingface.co/api/daily_papers"
    results: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, params={"date": target_date.isoformat()})
            if resp.status_code == 200:
                items = resp.json()
                if isinstance(items, list):
                    for entry in items:
                        paper = entry.get("paper") or {}
                        pid = paper.get("id") or ""
                        title = (paper.get("title") or entry.get("title") or "").strip()
                        summary = (paper.get("summary") or entry.get("summary") or "").strip()
                        upvotes = paper.get("upvotes") or entry.get("numComments") or 1
                        if not title or not pid:
                            continue

                        rel = score_candidate(title, summary)
                        # Keep HF Daily Papers that touch quantization, compression, efficient/small models, edge, vision, or hardware
                        if rel < 2.0 and not any(
                            kw in f"{title} {summary}".lower()
                            for kw in ["quantiz", "efficient", "small", "compact", "edge", "mobile", "prun", "sparse", "distill", "fast", "latency", "memory", "1-bit", "2-bit", "4-bit", "vision"]
                        ):
                            continue

                        authors = [
                            (a.get("name") or "")
                            for a in (paper.get("authors") or [])[:4]
                            if a.get("name")
                        ]
                        results.append(
                            {
                                "id": f"hf-{pid}",
                                "source_type": "hf_daily",
                                "source_family": "HF Daily Papers",
                                "source_label": f"HF Daily Papers (▲{upvotes})",
                                "title": title,
                                "url": f"https://huggingface.co/papers/{pid}",
                                "pdf_url": f"https://arxiv.org/pdf/{pid}",
                                "authors": authors,
                                "published_at": target_date.isoformat(),
                                "abstract": re.sub(r"\s+", " ", summary),
                                "categories": ["HF Daily Curated"],
                                "relevance_score": round(rel + min(upvotes / 15.0, 5.0) + 3.0, 2),
                            }
                        )
    except Exception as e:
        print(f"[HF Daily Papers fetch warning for {target_date}]: {e}")

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results


# =====================================================================
# SOURCE 7: Hacker News Algolia Timestamp Archive
# =====================================================================
async def fetch_hn_for_date(target_date: datetime.date) -> List[Dict[str, Any]]:
    start_dt = datetime.datetime.combine(
        target_date - datetime.timedelta(days=4), datetime.time.min, tzinfo=datetime.timezone.utc
    )
    end_dt = datetime.datetime.combine(target_date, datetime.time.max, tzinfo=datetime.timezone.utc)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    url = "https://hn.algolia.com/api/v1/search_by_date"
    queries = ["RISC-V", "FPGA", "microcontroller", "quantization", "NPU", "embedded"]

    results: List[Dict[str, Any]] = []
    seen_ids = set()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for q in queries:
            try:
                resp = await client.get(
                    url,
                    params={
                        "query": q,
                        "tags": "story",
                        "numericFilters": f"created_at_i>={start_ts},created_at_i<={end_ts}",
                        "hitsPerPage": 12,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for hit in data.get("hits", []):
                        obj_id = hit.get("objectID")
                        title = hit.get("title") or ""
                        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={obj_id}"
                        points = hit.get("points") or 1
                        num_comments = hit.get("num_comments") or 0
                        if not title or obj_id in seen_ids:
                            continue
                        seen_ids.add(obj_id)

                        rel = score_candidate(title, "") + min(points / 25.0, 5.0)
                        created_at = hit.get("created_at") or target_date.isoformat()
                        if created_at[:10] == target_date.isoformat():
                            rel += 4.0

                        domain = urllib.parse.urlparse(story_url).netloc.replace("www.", "") or "news.ycombinator.com"
                        results.append(
                            {
                                "id": f"hn-{obj_id}",
                                "source_type": "news",
                                "source_family": "Hacker News Dispatch",
                                "source_label": f"{domain} (HN +{points})",
                                "title": title,
                                "url": story_url,
                                "hn_url": f"https://news.ycombinator.com/item?id={obj_id}",
                                "authors": [hit.get("author", "anonymous_coward")],
                                "published_at": created_at,
                                "abstract": (
                                    f"Community-curated hardware & embedded engineering dispatch from {domain} "
                                    f"({points} points, {num_comments} comments on Hacker News). "
                                    f"Title: '{title}'."
                                ),
                                "categories": ["Hardware & Edge News"],
                                "relevance_score": round(rel, 2),
                                "points": points,
                                "num_comments": num_comments,
                            }
                        )
            except Exception as e:
                print(f"[HN fetch warning for {target_date}]: {e}")

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results


def make_deterministic_dept(title: str, topic_name: str) -> str:
    words = [
        re.sub(r"[^a-z0-9]", "", w.lower())
        for w in title.split()
        if len(w) > 3
        and w.lower()
        not in {
            "with",
            "from",
            "that",
            "this",
            "using",
            "based",
            "towards",
            "efficient",
            "neural",
            "network",
            "networks",
            "learning",
            "model",
            "models",
        }
    ]
    slug_words = words[:4] if len(words) >= 3 else (words + ["sram", "budget", "constraints"])[:4]
    return f"from the {'-'.join(slug_words)}-in-sram dept."


def extract_key_metrics(abstract: str) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    metric_sents = [
        s for s in sentences if re.search(r"\b\d+(?:\.\d+)?\s*(?:%|x|kb|mb|mw|uw|ms|fps|tops|bit|ghz|mhz)\b", s, re.I)
    ]
    return metric_sents[:2] if metric_sents else sentences[-2:]


def build_fallback_slashdot_story(raw: Dict[str, Any], index: int, target_date: datetime.date) -> Dict[str, Any]:
    topic = classify_topic(raw["title"], raw["abstract"])
    editor = SLASHDOT_EDITORS[int(hashlib.md5(raw["id"].encode()).hexdigest()[:4], 16) % len(SLASHDOT_EDITORS)]
    dept = make_deterministic_dept(raw["title"], topic["name"])

    sentences = re.split(r"(?<=[.!?])\s+", raw["abstract"].strip())
    lead_sents = " ".join(sentences[:2]) if len(sentences) >= 2 else raw["abstract"]
    quote_sents = " ".join(sentences[2:5]) if len(sentences) > 2 else raw["abstract"]
    metrics = extract_key_metrics(raw["abstract"])
    metrics_bullet = (
        " ".join(metrics)
        if metrics
        else "Demonstrates hardware-aware optimization for memory- and power-constrained edge targets."
    )

    authors_str = ", ".join(raw.get("authors", [])[:3])
    if len(raw.get("authors", [])) > 3:
        authors_str += " et al."
    if not authors_str:
        authors_str = "an EmbeddedDot contributor"

    hour = 8 + (index * 2) % 14
    minute = (int(hashlib.md5(raw["id"].encode()).hexdigest()[:2], 16)) % 60
    time_str = f"{target_date.strftime('%A %B %d, %Y')} @{hour:02d}:{minute:02d}"

    stype = raw["source_type"]
    if stype in ("arxiv", "ieee_acm", "hf_daily"):
        intro_html = (
            f"<p><strong>{authors_str}</strong> published new technical research via <strong>{raw['source_label']}</strong> "
            f"tackling embedded AI's core constraints: fitting high-accuracy inference into tight silicon area, "
            f"SRAM, and milliwatt power envelopes. {lead_sents}</p>"
        )
        blockquote_html = f"<blockquote><p>{quote_sents}</p></blockquote>"
        takeaway_html = f"<p><strong>Embedded Hardware Takeaway:</strong> {metrics_bullet}</p>"
    elif stype in ("newsletter", "newsletter_blog"):
        intro_html = (
            f"<p><strong>{authors_str}</strong> writes in the latest <strong>{raw['source_label']}</strong> dispatch "
            f"with a deep-dive on <em>\"{raw['title']}\"</em>:</p>"
        )
        blockquote_html = f"<blockquote><p>{raw['abstract']}</p></blockquote>"
        takeaway_html = (
            f"<p><strong>Embedded Engineering Takeaway:</strong> {metrics_bullet}</p>"
        )
    else:
        intro_html = (
            f"<p>An Anonymous Coward writes in with word from <strong>{raw['source_label']}</strong>: "
            f"<em>\"{raw['title']}\"</em> is sparking discussion among embedded systems and edge silicon engineers.</p>"
        )
        blockquote_html = f"<blockquote><p>{raw['abstract']}</p></blockquote>"
        takeaway_html = (
            f"<p><strong>Why It Matters at the Edge:</strong> Tooling and silicon advances that cut memory bandwidth "
            f"and host CPU overhead directly expand what can run locally without cloud round-trips.</p>"
        )

    comment_templates = [
        (
            "Re: Show me the SRAM footprint and DMA latency",
            "+5, Insightful",
            "wake_me_when_it_fits_in_256kb (148291)",
            "Paper numbers always look great until you account for activation buffer fragmentation and CMSIS-NN / custom ISA alignment overhead on a real Cortex-M55 or RISC-V RVV core. Still, the quantization/dataflow reduction here looks genuinely practical for bare-metal deployment.",
        ),
        (
            "Re: Memory bandwidth is still the real wall",
            "+5, Informative",
            "solder_fume_veteran (84920)",
            "Compute TOPS are cheap now; moving weights out of SPI Flash or LPDDR without blowing the thermal and milliwatt budget is 90% of the battle. Any architecture that keeps activations resident in on-chip SRAM deserves a close look.",
        ),
        (
            "Re: Linux vs. RTOS deployment",
            "+4, Interesting",
            "interrupt_handler_0x1f (512044)",
            "Would love to see benchmarks comparing deterministic worst-case execution time (WCET) under Zephyr RTOS versus an embedded Linux NPU driver stack. Jitter matters just as much as average throughput in closed-loop robotics.",
        ),
        (
            "Re: Finally, fewer FP32 layers on the edge",
            "+5, Funny",
            "int4_or_bust (31094)",
            "In 2023 everyone said you needed an 80GB datacenter GPU. Now we're arguing over whether 3-bit or 4-bit weights fit better inside a microcontroller cache line. What a time to be writing C and Verilog.",
        ),
    ]
    c_idx = int(hashlib.md5(raw["id"].encode()).hexdigest()[:4], 16) % len(comment_templates)
    c_title, c_score, c_author, c_body = comment_templates[c_idx]

    return {
        "id": raw["id"],
        "title": raw["title"],
        "url": raw["url"],
        "pdf_url": raw.get("pdf_url"),
        "hn_url": raw.get("hn_url"),
        "source_type": raw["source_type"],
        "source_family": raw.get("source_family", raw["source_type"]),
        "source_label": raw["source_label"],
        "authors": raw.get("authors", []),
        "editor": editor,
        "time_str": time_str,
        "department": dept,
        "topic": topic,
        "relevance_score": raw["relevance_score"],
        "summary_html": f"{intro_html}{blockquote_html}{takeaway_html}",
        "raw_abstract": raw["abstract"],
        "summarized_by": "deterministic-rule-engine",
        "comments": [
            {
                "id": f"c-{raw['id']}-1",
                "title": c_title,
                "score": c_score,
                "author": c_author,
                "body": c_body,
                "timestamp": f"@{min(hour + 1, 23):02d}:{(minute + 17) % 60:02d}",
            }
        ],
    }


async def summarize_stories_with_gemini(
    stories: List[Dict[str, Any]], api_key: str, target_date: datetime.date
) -> List[Dict[str, Any]]:
    if not api_key or not stories:
        return stories

    try:
        from google import genai

        client = genai.Client(api_key=api_key)

        payload_items = [
            {
                "id": s["id"],
                "title": s["title"],
                "source_label": s["source_label"],
                "authors": s.get("authors", []),
                "abstract": s.get("raw_abstract", ""),
            }
            for s in stories
        ]

        system_instruction = (
            "You are CmdrTaco and the senior technical editors at EmbeddedDot (a Slashdot-style publication dedicated "
            "to Embedded AI, TinyML, Edge NPUs, RISC-V accelerators, FPGA inference, and low-bit quantization). "
            "For each input paper, newsletter dispatch, or hardware article, produce a sharp, technically rigorous Slashdot-style writeup:\n"
            "1. `headline`: A punchy, accurate Slashdot-style headline (keep technical precision, e.g. mentioning bit-widths, SRAM, FPGA, NPU, SBC, or speedups if present).\n"
            "2. `department`: A witty lowercase Slashdot department string like 'from the squeezing-7b-into-sram dept.'\n"
            "3. `intro_paragraph`: 2-3 crisp sentences introducing who built/wrote it, the core embedded AI problem, and how their architecture/method works.\n"
            "4. `quote_excerpt`: A 2-3 sentence technical excerpt or synthesis of the quantitative results (latency, power in mW, TOPS/W, compression ratio, accuracy, hardware specs).\n"
            "5. `hardware_takeaway`: 1-2 sentences on what this practically means for firmware/hardware engineers targeting MCUs, FPGAs, or edge SoCs.\n"
            "6. `top_comment`: An authentic, deeply technical '+5, Insightful' or '+5, Funny' Slashdot reader comment discussing real-world embedded trade-offs."
        )

        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "headline": {"type": "string"},
                            "department": {"type": "string"},
                            "intro_paragraph": {"type": "string"},
                            "quote_excerpt": {"type": "string"},
                            "hardware_takeaway": {"type": "string"},
                            "top_comment_title": {"type": "string"},
                            "top_comment_score": {"type": "string"},
                            "top_comment_handle": {"type": "string"},
                            "top_comment_body": {"type": "string"},
                        },
                        "required": [
                            "id",
                            "headline",
                            "department",
                            "intro_paragraph",
                            "quote_excerpt",
                            "hardware_takeaway",
                            "top_comment_title",
                            "top_comment_score",
                            "top_comment_handle",
                            "top_comment_body",
                        ],
                    },
                }
            },
            "required": ["items"],
        }

        interaction = client.interactions.create(
            model="gemini-3.8-flash",
            system_instruction=system_instruction,
            input=f"Curate and summarize these {len(payload_items)} Embedded AI items for edition date {target_date.isoformat()}:\n\n{json.dumps(payload_items, indent=2)}",
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
        )

        output_text = interaction.output_text
        if output_text:
            parsed = json.loads(output_text)
            by_id = {item["id"]: item for item in parsed.get("items", []) if "id" in item}
            for s in stories:
                g = by_id.get(s["id"])
                if not g:
                    continue
                s["title"] = g.get("headline") or s["title"]
                dept = g.get("department", "").strip()
                if dept and not dept.startswith("from the"):
                    dept = f"from the {dept} dept."
                s["department"] = dept or s["department"]
                s["summary_html"] = (
                    f"<p>{g['intro_paragraph']}</p>"
                    f"<blockquote><p>{g['quote_excerpt']}</p></blockquote>"
                    f"<p><strong>Embedded Hardware Takeaway:</strong> {g['hardware_takeaway']}</p>"
                )
                s["summarized_by"] = "gemini-3.8-flash"
                s["comments"] = [
                    {
                        "id": f"c-{s['id']}-gemini",
                        "title": g.get("top_comment_title", "Re: Hardware implications"),
                        "score": g.get("top_comment_score", "+5, Insightful"),
                        "author": g.get("top_comment_handle", "sram_wizard (94812)"),
                        "body": g.get("top_comment_body", ""),
                        "timestamp": s["comments"][0]["timestamp"] if s.get("comments") else "@11:42",
                    }
                ]
    except Exception as e:
        print(f"[Gemini summarization warning]: {e}")

    return stories


def get_user_comments_for_date(date_str: str) -> Dict[str, List[Dict[str, Any]]]:
    init_db()
    by_story: Dict[str, List[Dict[str, Any]]] = {}
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, story_id, author, uid, title, score, body, created_at FROM user_comments WHERE date_str = ? ORDER BY id ASC",
            (date_str,),
        ).fetchall()
        for r in rows:
            by_story.setdefault(r[1], []).append(
                {
                    "id": f"user-{r[0]}",
                    "author": f"{r[2]} ({r[3]})",
                    "title": r[4],
                    "score": r[5],
                    "body": r[6],
                    "timestamp": r[7],
                }
            )
    return by_story


async def get_or_curate_day(
    date_str: str,
    force_refresh: bool = False,
    use_gemini: Optional[bool] = None,
) -> Dict[str, Any]:
    init_db()
    target_date = datetime.date.fromisoformat(date_str)
    today = datetime.date.today()
    min_date = today - datetime.timedelta(days=CURATION_POLICY["max_lookback_days"])

    if target_date > today:
        target_date = today
        date_str = target_date.isoformat()
    elif target_date < min_date:
        target_date = min_date
        date_str = target_date.isoformat()

    api_key = get_setting("GEMINI_API_KEY", "").strip()
    want_gemini = bool(api_key) if use_gemini is None else (use_gemini and bool(api_key))

    if not force_refresh:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT curated_at, summarizer_mode, stories_json, stats_json FROM daily_editions WHERE date_str = ?",
                (date_str,),
            ).fetchone()
            if row:
                curated_at, summarizer_mode, stories_json, stats_json = row
                stories = json.loads(stories_json)
                extra_comments = get_user_comments_for_date(date_str)
                for s in stories:
                    if s["id"] in extra_comments:
                        existing_ids = {c["id"] for c in s.get("comments", [])}
                        for ec in extra_comments[s["id"]]:
                            if ec["id"] not in existing_ids:
                                s.setdefault("comments", []).append(ec)
                return {
                    "date": date_str,
                    "display_date": target_date.strftime("%A, %B %d, %Y"),
                    "curated_at": curated_at,
                    "summarizer_mode": summarizer_mode,
                    "stories": stories,
                    "stats": json.loads(stats_json),
                    "cached": True,
                }

    # Fetch all 7 source streams concurrently!
    (
        arxiv_candidates,
        cnx_candidates,
        hackaday_candidates,
        newsletter_candidates,
        openalex_candidates,
        hf_candidates,
        hn_candidates,
    ) = await asyncio.gather(
        fetch_arxiv_for_date(target_date),
        fetch_cnx_software_for_date(target_date),
        fetch_hackaday_for_date(target_date),
        fetch_newsletters_for_date(target_date),
        fetch_openalex_for_date(target_date),
        fetch_hf_daily_papers_for_date(target_date),
        fetch_hn_for_date(target_date),
    )

    # Balanced multi-source quota selection (up to 9 stories per day across all source families)
    selected_raw: List[Dict[str, Any]] = []
    seen_titles = set()

    def add_up_to(pool: List[Dict[str, Any]], quota: int) -> None:
        added = 0
        for item in pool:
            norm = re.sub(r"[^a-z0-9]", "", item["title"].lower())[:42]
            if norm and norm not in seen_titles:
                selected_raw.append(item)
                seen_titles.add(norm)
                added += 1
                if added >= quota or len(selected_raw) >= CURATION_POLICY["stories_per_day"]:
                    break

    # 1. Free Newsletters & Embedded Hardware Blogs (CNX Software, Hackaday, Interrupt by Memfault, SemiAnalysis, Import AI, Ahead of AI)
    add_up_to(cnx_candidates, 2)
    add_up_to(newsletter_candidates, 2)
    add_up_to(hackaday_candidates, 1)
    # 2. Technical Papers (arXiv Preprints + OpenAlex IEEE/ACM + Hugging Face Daily Papers)
    add_up_to(arxiv_candidates, 3)
    add_up_to(hf_candidates, 1)
    add_up_to(openalex_candidates, 1)
    # 3. Hacker News Engineering Dispatches
    add_up_to(hn_candidates, 1)

    # Fill any remaining slots up to stories_per_day (9) from the highest-scoring remaining pool
    remaining_pool = (
        cnx_candidates
        + newsletter_candidates
        + hackaday_candidates
        + arxiv_candidates
        + hf_candidates
        + openalex_candidates
        + hn_candidates
    )
    remaining_pool.sort(key=lambda x: x["relevance_score"], reverse=True)
    add_up_to(remaining_pool, CURATION_POLICY["stories_per_day"] - len(selected_raw))

    selected_raw.sort(key=lambda x: x["relevance_score"], reverse=True)

    stories = [build_fallback_slashdot_story(raw, idx, target_date) for idx, raw in enumerate(selected_raw)]

    summarizer_mode = "deterministic-rule-engine"
    if want_gemini and stories:
        stories = await summarize_stories_with_gemini(stories, api_key, target_date)
        if any(s.get("summarized_by") == "gemini-3.8-flash" for s in stories):
            summarizer_mode = "gemini-3.8-flash"

    stats = {
        "arxiv_scanned": len(arxiv_candidates),
        "newsletters_blogs_scanned": len(cnx_candidates) + len(hackaday_candidates) + len(newsletter_candidates),
        "ieee_hf_scanned": len(openalex_candidates) + len(hf_candidates),
        "hn_scanned": len(hn_candidates),
        "selected_count": len(stories),
        "avg_relevance_score": round(
            sum(s["relevance_score"] for s in stories) / max(len(stories), 1), 2
        ),
    }
    curated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO daily_editions (date_str, curated_at, summarizer_mode, stories_json, stats_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date_str) DO UPDATE SET
                curated_at = excluded.curated_at,
                summarizer_mode = excluded.summarizer_mode,
                stories_json = excluded.stories_json,
                stats_json = excluded.stats_json
            """,
            (date_str, curated_at, summarizer_mode, json.dumps(stories), json.dumps(stats)),
        )
        conn.commit()

    extra_comments = get_user_comments_for_date(date_str)
    for s in stories:
        if s["id"] in extra_comments:
            s.setdefault("comments", []).extend(extra_comments[s["id"]])

    return {
        "date": date_str,
        "display_date": target_date.strftime("%A, %B %d, %Y"),
        "curated_at": curated_at,
        "summarizer_mode": summarizer_mode,
        "stories": stories,
        "stats": stats,
        "cached": False,
    }
