"""用 DeepSeek 批量生成西安文旅语料 md（扩充知识库用）。

用法（在 changan_ai 目录下）：
    .venv\\Scripts\\python scripts/gen_corpus.py --count 10
前置：.env 里配好 DEEPSEEK_API_KEY。

设计要点（阶段 6 生成西安 SQL 同款思路，面试考点"AI 生成内容的质量保障"）：
1. 提示词硬约束：输出格式（必须 N 个 ## 小节）、字数范围、真实性要求
   （票价/时间不确定的写"以现场为准"，防 AI 编造具体数字）；
2. 生成后程序校验：小节数不足/字数不达标的自动重生成一次；
3. 已存在的文件跳过——脚本可断点续跑，失败不覆盖已有成果。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 让 scripts/ 下能 import app 包

from openai import OpenAI

from app.config import BASE_DIR, settings

# 目录 → 待生成主题（生成后人工过一遍再入库，AI 内容必须有人把关）
TOPICS = {
    "attractions": ["西安碑林博物馆", "华清宫与骊山", "大唐芙蓉园", "小雁塔与西安博物院"],
    "food": ["西安面食地图", "永兴坊美食街区", "西安特色饮品盘点", "西安夜宵地图"],
    "guides": ["西安亲子游攻略", "西安一日游速览", "华山一日游攻略", "西安冬季旅行指南"],
}

SYSTEM_PROMPT = """你是西安本地文旅内容编辑，为"长安文旅探店助手"知识库撰写真实、实用的西安旅游文章。

硬性要求：
1. 输出标准 Markdown：以 # 一级标题（文章名）开头，正文用至少 3 个 ## 二级小节组织；
2. 全文 800-1200 字；内容必须基于真实情况，票价、开放时间等不确定的写"以现场为准"；
3. 面向年轻游客：具体、实用、有信息密度，不要空话套话；
4. 只输出文章本身，不要任何解释、前言或客套回复。"""

MIN_CHARS = 400
MIN_SECTIONS = 2  # 至少 2 个 ## 小节


def generate_article(client: OpenAI, topic: str) -> str:
    resp = client.chat.completions.create(
        model=settings.deepseek_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"文章主题：《{topic}》"},
        ],
        temperature=0.8,
    )
    return resp.choices[0].message.content


def validate(article: str) -> bool:
    """程序校验：小节数 + 字数达标才算合格（不合格自动重生成一次）。"""
    return article.count("## ") >= MIN_SECTIONS and len(article) >= MIN_CHARS


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek 批量生成西安文旅语料")
    parser.add_argument("--count", type=int, default=8, help="本次最多生成篇数")
    args = parser.parse_args()

    if not settings.deepseek_api_key:
        sys.exit("请先在 .env 配置 DEEPSEEK_API_KEY（复制 .env.example 改名 .env）")

    client = OpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key)
    tasks = [(folder, topic) for folder, topics in TOPICS.items() for topic in topics]

    generated = 0
    for folder, topic in tasks:
        if generated >= args.count:
            break
        out_path = BASE_DIR / "app" / "data" / "corpus" / folder / f"{topic}.md"
        if out_path.exists():  # 断点续跑：已有成果不覆盖
            print(f"[跳过] 已存在 {out_path.name}")
            continue
        article = generate_article(client, topic)
        if not validate(article):  # 质量不达标重生成一次
            print(f"[重试] 《{topic}》首次生成不合格")
            article = generate_article(client, topic)
            if not validate(article):
                print(f"[放弃] 《{topic}》两次生成均不合格，人工处理")
                continue
        out_path.write_text(article, encoding="utf-8")
        generated += 1
        print(f"[完成] {folder}/{out_path.name}（{len(article)} 字）")

    print(f"本次生成 {generated} 篇。人工审阅后重跑 scripts/ingest.py 入库。")


if __name__ == "__main__":
    main()
