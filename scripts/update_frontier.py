import json
import re
import math
import hashlib
import datetime as dt
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import feedparser
from dateutil import parser as dateparser

OUT_PATH = Path("data/frontier.json")

# ---------- 可调参数 ----------
DAYS_BACK = 7         # 只看最近 N 天
MAX_ITEMS = 60        # 输出最多 N 条（按热度排序）
MIN_HOT_SCORE = 8     # 低于这个分数不展示（“新但不热”的会被过滤）
MAX_PER_SOURCE = 80   # 每个 RSS 最多取多少条做候选（防爆）

# ---------- 信息源（中外 + 热点倾向）----------
# 说明：
# - arXiv 全量权重低（只做补充+交叉验证）
# - “精选/Trending/Daily”权重高（更接近热）
FEEDS: List[Tuple[str, str]] = [
    # 国外：产品/模型发布（热源）
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),

    # 国外：论文热门精选（更“热”）
    # 这是社区整理的 HF Daily Papers RSS（如果偶尔不稳定，会自动跳过）
    ("HF Daily Papers (community)", "https://jamesg.blog/hf-papers.xml"),

    # 国外：arXiv（只做补充）
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI"),
    ("arXiv cs.LG", "http://export.arxiv.org/rss/cs.LG"),
    ("arXiv cs.CL", "http://export.arxiv.org/rss/cs.CL"),
    ("arXiv cs.CV", "http://export.arxiv.org/rss/cs.CV"),

    # 中文热点（如果你暂时没 RSS，就先注释；后面你给我源我帮你加）
    # ("机器之心", "RSS_URL_HERE"),
    # ("新智元", "RSS_URL_HERE"),
]

# ---------- 权重配置 ----------
# “热”的核心：被筛选过/Trending 的源权重大；全量源权重小
SOURCE_WEIGHT = {
    "Hugging Face Blog": 5,
    "HF Daily Papers (community)": 6,
    "arXiv cs.AI": 2,
    "arXiv cs.LG": 2,
    "arXiv cs.CL": 2,
    "arXiv cs.CV": 2,
    "机器之心": 5,
    "新智元": 5,
}

CURATION_KEYWORDS = [
    "daily", "trending", "top", "featured", "highlight", "highlights",
    "best", "hot", "榜", "精选", "推荐", "热点"
]

# ---------- 文本工具 ----------
def clean_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)      # strip HTML tags
    s = re.sub(r"\s+", " ", s).strip()
    return s

def truncate(s: str, n: int = 260) -> str:
    s = clean_text(s)
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"

def safe_parse_date(entry) -> dt.date:
    for key in ("published", "updated", "created"):
        val = getattr(entry, key, None)
        if val:
            try:
                return dateparser.parse(val).date()
            except Exception:
                pass
    return dt.date.today()

def extract_summary(entry) -> str:
    # try summary/description/content
    for key in ("summary", "description", "content"):
        val = getattr(entry, key, None)
        if isinstance(val, list) and val:
            val = val[0].get("value", "")
        if val:
            return str(val)
    return ""

def normalize_title(title: str) -> str:
    """
    用于“跨源合并”的轻量规范化：
    - 小写
    - 去标点
    - 去多余空格
    """
    t = clean_text(title).lower()
    t = re.sub(r"[\[\]\(\)\{\}<>:;,.!?\"'`~@#$%^&*_=+\\|/]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def extract_arxiv_id(text: str) -> Optional[str]:
    """
    从 link/summary/title 中抓 arXiv id（粗略版）
    支持：arxiv.org/abs/xxxx.xxxxx 或 arxiv:xxxx.xxxxx
    """
    if not text:
        return None
    m = re.search(r"arxiv\.org/(abs|pdf)/(\d{4}\.\d{4,5})(v\d+)?", text, re.IGNORECASE)
    if m:
        return m.group(2)
    m = re.search(r"\barxiv:\s*(\d{4}\.\d{4,5})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None

def stable_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

# ---------- 热度模型 ----------
def recency_bonus(published: dt.date, today: dt.date) -> int:
    delta = (today - published).days
    if delta <= 1:
        return 4
    if delta <= 3:
        return 3
    if delta <= 7:
        return 1
    return 0

def curation_bonus(title: str, source: str) -> int:
    hay = (title + " " + source).lower()
    for kw in CURATION_KEYWORDS:
        if kw.lower() in hay:
            return 3
    return 0

def cross_source_bonus(num_sources: int) -> int:
    if num_sources >= 3:
        return 7
    if num_sources >= 2:
        return 4
    return 0

def compute_hot_score(
    source: str,
    published: dt.date,
    today: dt.date,
    num_sources: int,
    title: str
) -> int:
    base = SOURCE_WEIGHT.get(source, 3)
    score = base
    score += recency_bonus(published, today)
    score += curation_bonus(title, source)
    score += cross_source_bonus(num_sources)
    return int(score)

# ---------- 聚合逻辑 ----------
def main():
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=DAYS_BACK)

    # 先抓取候选 items
    raw_items = []
    for source, url in FEEDS:
        d = feedparser.parse(url)

        # RSS 可能偶发失败：不让整个 workflow 挂
        if getattr(d, "bozo", 0) == 1 and not getattr(d, "entries", None):
            print(f"[WARN] feed parse failed: {source} -> {url}")
            continue

        entries = getattr(d, "entries", [])[:MAX_PER_SOURCE]
        for e in entries:
            title = clean_text(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            published = safe_parse_date(e)
            if published < cutoff:
                continue

            summary_raw = extract_summary(e)
            # 临时摘要：先用 RSS 自带 summary 截断；后面你接 AI 再覆盖
            summary = truncate(summary_raw, 260)

            # 尝试提取 arXiv id，用于更稳的跨源合并
            arxiv_id = (
                extract_arxiv_id(link)
                or extract_arxiv_id(title)
                or extract_arxiv_id(summary_raw)
            )

            raw_items.append({
                "title": title,
                "source": source,
                "published_at": published.isoformat(),
                "url": link,
                "ai_summary": summary,
                "tags": [],
                "arxiv_id": arxiv_id,
            })

    if not raw_items:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text("[]", encoding="utf-8")
        print("[INFO] No items fetched; wrote empty frontier.json")
        return

    # 2) 归并：同一个主题/论文在多个源出现 => 认为更“热”
    # 归并 key 优先级：arxiv_id > normalize_title
    groups: Dict[str, Dict] = {}
    for it in raw_items:
        key = it["arxiv_id"] or normalize_title(it["title"])
        key = key if key else stable_hash(it["url"] or it["title"])

        g = groups.get(key)
        if not g:
            groups[key] = {
                "key": key,
                "items": [it],
                "sources": {it["source"]},
                "best": it,  # 先用第一条当 best，后续会更新
            }
        else:
            g["items"].append(it)
            g["sources"].add(it["source"])

    # 3) 为每个 group 选“最佳展示条目”
    # 策略：优先权重高的源，其次更近的时间
    def item_rank(it):
        w = SOURCE_WEIGHT.get(it["source"], 3)
        d = it["published_at"]
        return (w, d)

    final_items = []
    for g in groups.values():
        items = g["items"]
        items.sort(key=item_rank, reverse=True)
        best = items[0]

        # group 层面的 “代表发布时间”：取最新日期（利于 recency）
        dates = [dt.date.fromisoformat(x["published_at"]) for x in items]
        published_group = max(dates)

        num_sources = len(g["sources"])

        # hot score 用 group 的 num_sources + 最新日期
        hot = compute_hot_score(
            source=best["source"],
            published=published_group,
            today=today,
            num_sources=num_sources,
            title=best["title"],
        )

        # 组装输出条目
        out = {
            "title": best["title"],
            "source": best["source"],
            "published_at": published_group.isoformat(),
            "url": best["url"],
            "ai_summary": best["ai_summary"],
            "tags": best.get("tags", []),
            "hot_score": hot,
            "sources": sorted(list(g["sources"])),
        }
        final_items.append(out)

    # 4) 过滤 “新但不热”
    final_items = [x for x in final_items if x["hot_score"] >= MIN_HOT_SCORE]

    # 5) 排序：先按 hot_score，再按日期
    final_items.sort(key=lambda x: (x["hot_score"], x["published_at"]), reverse=True)
    final_items = final_items[:MAX_ITEMS]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(final_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {len(final_items)} hot items to {OUT_PATH}")

if __name__ == "__main__":
    main()
