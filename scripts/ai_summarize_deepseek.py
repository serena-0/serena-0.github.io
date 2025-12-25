import os
import json
import time
import re
from typing import List, Dict
import urllib.request

AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() == "true"


# DeepSeek OpenAI-compatible endpoint
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")  # or deepseek-reasoner

TAGS = [
    ("Agent", ["agent", "tool", "function calling", "mcp", "planner", "workflow", "autonomous"]),
    ("RAG", ["rag", "retrieval", "rerank", "graph", "graphrag", "vector", "embedding"]),
    ("LLM", ["llm", "language model", "transformer", "instruction", "sft", "rlhf", "dpo"]),
    ("Multimodal", ["multimodal", "vision-language", "vlm", "image", "video", "audio"]),
    ("Vision", ["diffusion", "segmentation", "detection", "restormer", "denoise", "super-resolution"]),
    ("Reasoning", ["reasoning", "math", "theorem", "planning"]),
    ("Systems", ["inference", "serving", "throughput", "latency", "quantization", "kernel", "cuda"]),
    ("Robotics", ["robot", "slam", "manipulation", "control"]),
    ("Finance", ["finance", "stock", "trading", "valuation"]),
]

def simple_tags(text: str) -> List[str]:
    t = (text or "").lower()
    out = []
    for tag, kws in TAGS:
        if any(kw in t for kw in kws):
            out.append(tag)
    return sorted(set(out))

def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def call_deepseek_chat(messages: List[Dict], model: str = DEEPSEEK_MODEL, temperature: float = 0.2) -> str:
    """
    DeepSeek Chat Completions (OpenAI-compatible).
    Endpoint: POST {base_url}/v1/chat/completions
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is empty")

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        f"{DEEPSEEK_BASE_URL}/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        obj = json.loads(raw)
        return obj["choices"][0]["message"]["content"]

def summarize_one(item: Dict) -> Dict:
    title = item.get("title", "")
    source = item.get("source", "")
    url = item.get("url", "")
    published_at = item.get("published_at", "")
    snippet = item.get("ai_summary", "")  # v1/v1.5 的截断摘要当作素材

    sys = "你是资深 AI/ML 技术编辑。输出简洁、准确、不过度推断的中文摘要。"
    user = f"""
请为下面这条前沿内容生成“简要说明”，要求：
- 中文 2~4 句
- 第一句：它是什么（模型/论文/工具/产品）
- 第二句：亮点/贡献（只基于给定信息，不要编造）
- 可选第三句：为什么重要/可能影响
- 若信息不足，明确写“信息有限，仅基于标题/摘要”
- 不要加链接（页面已有链接）

素材：
Title: {title}
Source: {source}
Date: {published_at}
URL: {url}
Snippet: {snippet}
""".strip()

    text = call_deepseek_chat(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        model=DEEPSEEK_MODEL,
        temperature=0.2,
    )

    item["ai_summary"] = clean_ws(text)
    item["tags"] = sorted(set((item.get("tags") or []) + simple_tags(title + " " + item["ai_summary"])))
    return item

def main():

    if not AI_ENABLED:
        print("[SKIP] AI summarization disabled by AI_ENABLED=false")
        return

    path = "data/frontier.json"
    items = json.loads(open(path, "r", encoding="utf-8").read() or "[]")
    if not items:
        print("[INFO] frontier.json empty, skip summarize")
        return

    updated = 0
    for i, it in enumerate(items):
        s = (it.get("ai_summary") or "").strip()
        # 只对“明显是 RSS 截断/空”的条目做 AI（省钱）
        if len(s) >= 80 and "信息有限" not in s:
            continue
        try:
            items[i] = summarize_one(it)
            updated += 1
            time.sleep(0.6)
        except Exception as e:
            print(f"[WARN] summarize failed: {it.get('title','')[:60]} -> {e}")
            continue

    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"[OK] deepseek summarized {updated} items")

if __name__ == "__main__":
    main()
