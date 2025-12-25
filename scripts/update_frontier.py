import json
import re
import hashlib
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import feedparser
from dateutil import parser as dateparser

# =====================
# Output
# =====================

OUT_PATH = Path("data/frontier.json")

# =====================
# Tuning knobs
# =====================

DAYS_BACK = 14
MAX_ITEMS = 40
MIN_HOT_SCORE = 5              # 筛选分数阈值，过滤“普通资讯”
MAX_PER_SOURCE = 120

MAX_JIQI = 3                   # ✅ 机器之心限流：最多 3 条
MAX_ARXIV = 10                 # 可选：arXiv 也限流，避免论文刷屏

# =====================
# Feeds（少而精）
# =====================

FEEDS: List[Tuple[str, str]] = [
    # 主热源：推荐/精选
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("HF Trending Papers", "https://jamesg.blog/hf-papers.xml"),

    # 中文解读：放大器（不能单独制造热点）
    ("机器之心", "https://www.jiqizhixin.com/rss"),

    # 事实源：论文补充（不负责“热”）
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI"),
    ("arXiv cs.LG", "http://export.arxiv.org/rss/cs.LG"),
    ("arXiv cs.CL", "http://export.arxiv.org/rss/cs.CL"),
    ("arXiv cs.CV", "http://export.arxiv.org/rss/cs.CV"),
]

# =====================
# Source type & weight
# =====================

SOURCE_TYPE = {
    "Hugging Face Blog": "hf",
    "HF Trending Papers": "hf",
    "机器之心": "cn",

    "arXiv cs.AI": "arxiv",
    "arXiv cs.LG": "arxiv",
    "arXiv cs.CL": "arxiv",
    "arXiv cs.CV": "arxiv",
}

# ✅ 机器之心降权；HF 是主热源
SOURCE_WEIGHT = {
    "Hugging Face Blog": 7,
    "HF Trending Papers": 8,
    "机器之心": 3,

    "arXiv cs.AI": 2,
    "arXiv cs.LG": 2,
    "arXiv cs.CL": 2,
    "arXiv cs.CV": 2,
}

# =====================
# Utils
# =====================

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def truncate(s: str, n: int = 320) -> str:
    s = clean_text(s)
    return s if len(s) <= n else s[:n].rstrip() + "…"

def parse_date(entry) -> dt.date:
    for k in ("published", "updated", "created"):
        v = getattr(entry, k, None)
        if v:
            try:
                return dateparser.parse(v).date()
            except Exception:
                pass
    return dt.date.today()

def normalize_title(title: str) -> str:
    t = clean_text(title).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def extract_arxiv_id(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"arxiv\.org/(abs|pdf)/(\d{4}\.\d{4,5})", text, re.I)
    return m.group(2) if m else None

def hash_key(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

# =====================
# Hot score
# =====================

def recency_bonus(published: dt.date, today: dt.date) -> int:
    d = (today - published).days
    if d <= 1:
        return 3
    if d <= 3:
        return 2
    if d <= 7:
        return 1
    return 0

def cross_source_bonus(sources: set) -> int:
    """
    ✅ 真热点原则：
    - HF（推荐系统）可以“制造热点”
    - arXiv 是事实源，不单独“热”
    - 机器之心是放大器/解读源，不允许单源变热
    """
    types = {SOURCE_TYPE.get(s, "other") for s in sources}

    has_hf = "hf" in types
    has_arxiv = "arxiv" in types
    has_cn = "cn" in types

    # HF + arXiv：社区推荐 + 论文事实，最可信热点
    if has_hf and has_arxiv:
        return 6

    # HF 单独：也算热（HF 的推荐系统本身就是热度信号）
    if has_hf:
        return 4

    # ❌ 机器之心单独出现：不加热（防 UC 刷屏）
    # 机器之心只有在“有 HF 或 arXiv 佐证”时才加分
    if has_cn and has_arxiv:
        return 2  # 中文解读 + 论文，算一点热

    return 0

def compute_hot_score(source: str, published: dt.date, today: dt.date, sources: set) -> int:
    score = SOURCE_WEIGHT.get(source, 2)
    score += recency_bonus(published, today)
    score += cross_source_bonus(sources)
    return score

# =====================
# Main
# =====================

def main():
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=DAYS_BACK)

    raw_items = []

    # 1) Fetch feeds
    for source, url in FEEDS:
        feed = feedparser.parse(url)
        entries = getattr(feed, "entries", [])[:MAX_PER_SOURCE]

        for e in entries:
            title = clean_text(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            published = parse_date(e)

            if not title or not link:
                continue
            if published < cutoff:
                continue

            summary = truncate(
                getattr(e, "summary", "") or
                getattr(e, "description", "")
            )

            arxiv_id = extract_arxiv_id(link) or extract_arxiv_id(summary)

            raw_items.append({
                "title": title,
                "url": link,
                "source": source,
                "published_at": published,
                "raw_snippet": summary,
                "arxiv_id": arxiv_id,
            })

    # 2) Group by arXiv id or normalized title
    groups: Dict[str, Dict] = {}

    for it in raw_items:
        key = it["arxiv_id"] or normalize_title(it["title"])
        if not key:
            key = hash_key(it["url"] or it["title"])

        g = groups.setdefault(key, {"items": [], "sources": set()})
        g["items"].append(it)
        g["sources"].add(it["source"])

    # 3) Select best item per group
    results = []

    for g in groups.values():
        items = g["items"]
        sources = g["sources"]

        # Prefer: HF > CN > arXiv, and newer date
        items.sort(
            key=lambda x: (
                SOURCE_WEIGHT.get(x["source"], 0),
                x["published_at"]
            ),
            reverse=True
        )

        best = items[0]
        published = max(x["published_at"] for x in items)

        hot = compute_hot_score(
            source=best["source"],
            published=published,
            today=today,
            sources=sources,
        )

        if hot < MIN_HOT_SCORE:
            continue

        results.append({
            "title": best["title"],
            "url": best["url"],
            "source": best["source"],
            "published_at": published.isoformat(),

            # 原始素材（RSS / arXiv）
            "raw_snippet": best["raw_snippet"],

            # AI 生成（你现在关着也 OK）
            "ai_summary": "",
            "ai_generated": False,

            "tags": [],
            "hot_score": hot,
            "sources": sorted(list(sources)),
        })

    # 4) Sort by hotness and recency
    results.sort(key=lambda x: (x["hot_score"], x["published_at"]), reverse=True)

    # 5) Limit by source (prevent any one feed dominating)
    filtered = []
    cnt_jiqi = 0
    cnt_arxiv = 0

    for x in results:
        if x["source"] == "机器之心":
            if cnt_jiqi >= MAX_JIQI:
                continue
            cnt_jiqi += 1

        if x["source"].startswith("arXiv"):
            if cnt_arxiv >= MAX_ARXIV:
                continue
            cnt_arxiv += 1

        filtered.append(x)

        if len(filtered) >= MAX_ITEMS:
            break

    filtered = filtered[:MAX_ITEMS]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"[OK] wrote {len(filtered)} hot frontier items")
    print(f"[INFO] machine-heart kept: {cnt_jiqi}, arXiv kept: {cnt_arxiv}")

if __name__ == "__main__":
    main()
