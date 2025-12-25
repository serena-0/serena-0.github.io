import json
import re
import datetime as dt
from pathlib import Path

import feedparser
from dateutil import parser as dateparser

OUT_PATH = Path("data/frontier.json")
DAYS_BACK = 7
MAX_ITEMS = 40

FEEDS = [
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml"),
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI"),
    ("arXiv cs.LG", "http://export.arxiv.org/rss/cs.LG"),
]

def clean_text(s: str) -> str:
    if not s:
        return ""
    # remove HTML tags
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def truncate(s: str, n: int = 240) -> str:
    s = clean_text(s)
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"

def parse_date(entry) -> dt.date:
    # feedparser may provide published / updated
    for key in ("published", "updated", "created"):
        val = getattr(entry, key, None)
        if val:
            try:
                return dateparser.parse(val).date()
            except Exception:
                pass
    # fallback
    return dt.date.today()

def entry_id(entry) -> str:
    # stable id for dedupe
    for key in ("id", "guid", "link"):
        val = getattr(entry, key, None)
        if val:
            return str(val)
    return str(getattr(entry, "title", ""))

def main():
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=DAYS_BACK)

    items = []
    seen = set()

    for source, url in FEEDS:
        d = feedparser.parse(url)
        for e in getattr(d, "entries", [])[:100]:
            pid = entry_id(e)
            if pid in seen:
                continue
            seen.add(pid)

            title = clean_text(getattr(e, "title", ""))
            link = getattr(e, "link", "")
            published = parse_date(e)

            if published < cutoff:
                continue

            # try to get summary/description
            summary = ""
            for key in ("summary", "description", "content"):
                val = getattr(e, key, None)
                if isinstance(val, list) and val:
                    # feedparser content list
                    val = val[0].get("value", "")
                if val:
                    summary = str(val)
                    break

            items.append({
                "title": title,
                "source": source,
                "published_at": published.isoformat(),
                "url": link,
                "ai_summary": truncate(summary, 260),  # placeholder summary
                "tags": []  # optional
            })

    # sort by date desc, then title
    items.sort(key=lambda x: (x["published_at"], x["title"]), reverse=True)
    items = items[:MAX_ITEMS]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(items)} items to {OUT_PATH}")

if __name__ == "__main__":
    main()

