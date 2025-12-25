import json
import re
import hashlib
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import feedparser
from dateutil import parser as dateparser

# =====================
# Config
# =====================

OUT_PATH = Path("data/frontier.json")

DAYS_BACK = 7
MAX_ITEMS = 40
MIN_HOT_SCORE = 6
MAX_PER_SOURCE = 80

# =====================
# Feeds（少而精）
# =====================

FEEDS: List[Tuple[str, str]] = [
    # Curated / 推荐源
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("HF Trending Papers", "https://jamesg.blog/hf-papers.xml"),
    ("机器之心", "https://www.jiqizhixin.com/rss"),

    # arXiv（仅补充）
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI"),
    ("arXiv cs.LG", "http://export.arxiv.org/rss/cs.LG"),
    ("arXiv cs.CL", "http://export.arxiv.org/rss/cs.CL"),
    ("arXiv cs.CV", "http://export.arxiv.org/rss/cs.CV"),
]

# =====================
# Source metadata
# =====================

SOURCE_TYPE = {
    "Hugging Face Blog": "curated",
    "HF Trending Papers": "curated",
    "机器之心": "curated",

    "arXiv cs.AI": "arxiv",
    "arXiv cs.LG": "arxiv",
    "arXiv cs.CL": "arxiv",
    "arXiv cs.CV": "arxiv",
}

SOURCE_WEIGHT = {
    "Hugging Face Blog": 6,
    "HF Trending Papers": 7,
    "机器之心": 6,

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

def truncate(s: str, n: int = 300) -> str:
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
    types = {SOURCE_TYPE.get(s, "other") for s in sources}

    # 真·热点规则
    if "curated" in types and "arxiv" in types:
        return 6
    if "curated" in types:
        return 4

    # 纯 arXiv：不加热
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

        g = groups.setdefault(key, {
            "items": [],
            "sources": set(),
        })

        g["items"].append(it)
        g["sources"].add(it["source"])

    # 3) Select best item per group
    results = []

    for g in groups.values():
        items = g["items"]
        sources = g["sources"]

        # best source priority
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

            # AI 生成（现在你关着也 OK）
            "ai_summary": "",
            "ai_generated": False,

            "tags": [],
            "hot_score": hot,
            "sources": sorted(list(sources)),
        })

    # 4) Sort & limit
    results.sort(key=lambda x: (x["hot_score"], x["published_at"]), reverse=True)
    results = results[:MAX_ITEMS]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"[OK] wrote {len(results)} hot frontier items")

if __name__ == "__main__":
    main()
