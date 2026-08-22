"""检索质量评估：Hit@5 / Hit@3 / MRR。

用法（changan_ai 目录下）：
    .venv/python.exe scripts/eval_retrieval.py                # 全库评估
    .venv/python.exe scripts/eval_retrieval.py --source corpus  # 只评估语料源

面试必考（评估指标）：
- Hit@k：正确答案出现在 top-k 结果中的问题占比——衡量"找不找得到"（目标 ≥85%）；
- MRR：正确答案排名倒数的均值——衡量"排得靠不靠前"，对排序质量敏感；
- 为什么评估用真实 embedding/真实向量库，而测试用 fake？
  测试验证"代码逻辑对"，评估验证"系统效果好"——两者目的不同，
  用 fake 跑评估等于自欺欺人（这是"测试与评估的分界线"）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client

TOP_K = 5

# (查询, 期望命中的文档标题) —— 文档标题 = corpus md 文件名
QA_PAIRS = [
    ("大雁塔门票多少钱", "大雁塔与大慈恩寺"),
    ("大雁塔是谁主持修建的", "大雁塔与大慈恩寺"),
    ("大雁塔北广场喷泉什么时候有", "大雁塔与大慈恩寺"),
    ("西安城墙可以骑车吗", "西安城墙"),
    ("西安城墙有多长", "西安城墙"),
    ("城墙哪个门最值得登", "西安城墙"),
    ("钟楼和鼓楼相距多远", "钟楼与鼓楼"),
    ("钟楼鼓楼联票多少钱", "钟楼与鼓楼"),
    ("大唐不夜城什么时候去最好", "大唐不夜城"),
    ("不夜城有什么表演", "大唐不夜城"),
    ("回民街有哪些必吃小吃", "回民街美食指南"),
    ("回民街怎么逛不踩坑", "回民街美食指南"),
    ("西安三日游怎么安排", "西安三日游经典路线"),
    ("兵马俑安排在第几天", "西安三日游经典路线"),
    ("西安住宿选哪个商圈方便", "西安交通与住宿指南"),
    ("去西安坐高铁到哪个站", "西安交通与住宿指南"),
    ("肉夹馍有什么讲究", "西安小吃图鉴"),
    ("西安凉皮有哪几种", "西安小吃图鉴"),
    ("biangbiang面是什么", "西安小吃图鉴"),
    ("冰峰是什么", "西安小吃图鉴"),
]


def evaluate(store, embedder, source: str | None) -> dict:
    where = {"source": source} if source else None
    hit3 = hit5 = 0
    rr_sum = 0.0
    detail: list[str] = []
    for query, expected in QA_PAIRS:
        qv = embedder.embed_texts([query])[0]
        hits = store.query(qv, top_k=TOP_K, where=where)
        docs = [h["metadata"].get("doc") for h in hits]
        if expected in docs:
            hit5 += 1
            rank = docs.index(expected) + 1
            rr_sum += 1 / rank
            if rank <= 3:
                hit3 += 1
            detail.append(f"  ✅ {query} → #{rank}")
        else:
            detail.append(f"  ❌ {query} → top5: {docs[:3]}...")
    n = len(QA_PAIRS)
    return {
        "total": n,
        "hit@3": round(hit3 / n, 3),
        "hit@5": round(hit5 / n, 3),
        "mrr": round(rr_sum / n, 3),
        "detail": detail,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="检索质量评估（Hit@k / MRR）")
    parser.add_argument("--source", default=None, help="只评估某个来源（corpus / java）")
    args = parser.parse_args()

    store = get_store()
    embedder = get_embedding_client()
    print(f"评估范围：{'全部' if not args.source else args.source}（{store.count()} chunks）")
    report = evaluate(store, embedder, args.source)
    print("\n".join(report["detail"]))
    print(f"\n===== 检索评估报告 =====")
    print(f"样本数: {report['total']}")
    print(f"Hit@3 : {report['hit@3']:.1%}（目标 ≥85%）")
    print(f"Hit@5 : {report['hit@5']:.1%}（目标 ≥90%）")
    print(f"MRR   : {report['mrr']:.3f}")


if __name__ == "__main__":
    main()
