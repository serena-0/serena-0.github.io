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
    ("机器之心
