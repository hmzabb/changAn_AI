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
# 总计62个QA对，分4层：
#   Layer1: 原始21个（核心事实型查询）
#   Layer2: 基础层+16个（补齐每文档到5个QA）
#   Layer3: 进阶层+15个（跨文档关联/组合约束/推理判断）
#   Layer4: 鲁棒性层+10个（口语化/错别字/边界case）
QA_PAIRS = [
    # ========== Layer 1: 核心事实型查询（原始21个）==========
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

    # ========== Layer 2: 基础层+16个（补齐每个文档到5个QA）==========
    # 大雁塔（+2个 → 共5个）
    ("大雁塔怎么去，地铁几号线", "大雁塔与大慈恩寺"),
    ("大雁塔和大慈恩寺是什么关系", "大雁塔与大慈恩寺"),

    # 西安城墙（+2个 → 共5个）
    ("西安城墙有几个门可以登", "西安城墙"),
    ("城墙门票包含自行车吗", "西安城墙"),

    # 钟楼鼓楼（+2个 → 共4个）
    ("钟楼鼓楼晚上亮灯吗", "钟楼与鼓楼"),
    ("钟楼鼓楼值得单独买票吗", "钟楼与鼓楼"),

    # 大唐不夜城（+2个 → 共4个）
    ("大唐不夜城需要门票吗", "大唐不夜城"),
    ("不夜城适合带孩子去吗", "大唐不夜城"),

    # 回民街（+2个 → 共4个）
    ("回民街什么时候人最少", "回民街美食指南"),
    ("回民街有哪些坑要注意", "回民街美食指南"),

    # 小吃图鉴（+2个 → 共6个）
    ("肉夹馍和汉堡有什么区别", "西安小吃图鉴"),
    ("西安小吃哪个最辣", "西安小吃图鉴"),

    # 三日游（+2个 → 共4个）
    ("三天时间够玩西安吗", "西安三日游经典路线"),
    ("如果只有两天怎么压缩行程", "西安三日游经典路线"),

    # 交通住宿（+2个 → 共4个）
    ("咸阳机场到市区怎么走", "西安交通与住宿指南"),
    ("西安住宿大概多少钱一晚", "西安交通与住宿指南"),

    # ========== Layer 3: 进阶层+15个（跨文档/组合约束/推理）==========
    # 跨文档关联
    ("从钟楼到大雁塔坐几号地铁，大概多长时间", "西安交通与住宿指南"),
    ("大雁塔附近有什么好吃的推荐", "回民街美食指南"),
    ("城墙骑行结束后去哪里吃晚饭比较好", "回民街美食指南"),

    # 多约束组合
    ("预算500元以内，三天游西安怎么安排", "西安三日游经典路线"),
    ("带老人和孩子，哪些景点不适合去", "西安城墙"),
    ("冬天去西安，哪些户外景点要慎选", "大唐不夜城"),

    # 推理判断
    ("西安和北京的古都特色有什么不同", "大雁塔与大慈恩寺"),
    ("回民街的小吃和普通街头小吃区别在哪", "回民街美食指南"),
    ("如果只有一天时间，优先去哪两个景点", "西安三日游经典路线"),

    # 实用信息深度
    ("西安旅游最佳季节是什么时候", "西安三日游经典路线"),
    ("景点之间怎么安排顺序最合理", "西安三日游经典路线"),
    ("哪些小吃是西安独有的", "西安小吃图鉴"),

    # 文化背景
    ("为什么西安被称为十三朝古都", "大雁塔与大慈恩寺"),
    ("回民街的历史由来是什么", "回民街美食指南"),
    ("城墙是中国现存最完整的古城墙吗", "西安城墙"),

    # ========== Layer 4: 鲁棒性层+10个（口语化/错别字/边界）==========
    # 口语化表达（方言/地域特色）
    ("咋去大雁塔啊", "大雁塔与大慈恩寺"),
    ("肉夹馍啥味儿的", "西安小吃图鉴"),
    ("这地儿有啥好吃的没", "回民街美食指南"),

    # 错别字/拼音/模糊输入
    ("大雁它门票多少", "大雁塔与大慈恩寺"),
    ("biangbiang面咋写", "西安小吃图鉴"),

    # 边界case（超出知识库范围）→ 期望返回None或fallback
    ("北京故宫门票多少钱", None),
    ("我想去东京旅游", None),
    ("今天天气怎么样", None),
    ("1+1等于几", None),
]


def evaluate(store, embedder, source: str | None) -> dict:
    where = {"source": source} if source else None
    hit3 = hit5 = 0
    rr_sum = 0.0
    valid_n = 0  # 有效样本数（排除边界case）
    boundary_pass = 0  # 边界case通过数（应返回空或低分）
    detail: list[str] = []
    for query, expected in QA_PAIRS:
        qv = embedder.embed_texts([query])[0]
        hits = store.query(qv, top_k=TOP_K, where=where)
        docs = [h["metadata"].get("doc") for h in hits]

        # 边界case处理（expected=None表示应在知识库外）
        if expected is None:
            valid_n += 1
            # 边界case：期望检索结果为空或相似度很低
            if not docs or all(h.get("similarity", 1.0) < 0.35 for h in hits):
                boundary_pass += 1
                detail.append(f"  🚫 {query} → 正确拒绝（超纲）")
            else:
                detail.append(f"  ⚠️  {query} → 意外命中: {docs[:2]}（可能误检）")
            continue

        valid_n += 1
        if expected in docs:
            hit5 += 1
            rank = docs.index(expected) + 1
            rr_sum += 1 / rank
            if rank <= 3:
                hit3 += 1
            detail.append(f"  ✅ {query} → #{rank}")
        else:
            detail.append(f"  ❌ {query} → top5: {docs[:3]}...")

    # 分层统计
    n = valid_n
    return {
        "total": n,
        "boundary_total": len([q for q, e in QA_PAIRS if e is None]),
        "boundary_pass": boundary_pass,
        "hit@3": round(hit3 / n, 3) if n > 0 else 0,
        "hit@5": round(hit5 / n, 3) if n > 0 else 0,
        "mrr": round(rr_sum / n, 3) if n > 0 else 0,
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
    print(f"\n{'='*50}")
    print(f"📊 检索质量评估报告（{report['total']}个有效样本）")
    print(f"{'='*50}")
    print(f"\n📈 核心指标:")
    print(f"  Hit@3 : {report['hit@3']:.1%}（目标 ≥85%）")
    print(f"  Hit@5 : {report['hit@5']:.1%}（目标 ≥90%）")
    print(f"  MRR   : {report['mrr']:.3f}（目标 >0.85）")

    # 分层统计
    layer1_count = 21
    layer2_count = 16
    layer3_count = 15
    layer4_count = report.get("boundary_total", 10)
    print(f"\n📚 样本分层:")
    print(f"  Layer1 核心事实型: {layer1_count}个")
    print(f"  Layer2 基础补全:   {layer2_count}个")
    print(f"  Layer3 进阶推理:   {layer3_count}个")
    print(f"  Layer4 鲁棒性:     {layer4_count}个（边界case）")

    if layer4_count > 0:
        boundary_rate = report.get("boundary_pass", 0) / layer4_count
        print(f"\n🛡️  边界case测试（超纲查询拒绝率）:")
        print(f"  通过: {report.get('boundary_pass', 0)}/{layer4_count} ({boundary_rate:.1%})")
        if boundary_rate >= 0.75:
            print("  ✅ 拒绝率良好（≥75%）")
        elif boundary_rate >= 0.5:
            print("  ⚠️  拒绝率一般（50-75%），可优化")
        else:
            print("  ❌ 拒绝率偏低（<50%），需检查阈值")

    # 统计学置信度说明
    n = report['total']
    if n > 0:
        import math
        p = report['hit@5']
        se = math.sqrt(p * (1-p) / n) if 0 < p < 1 else 0
        ci_width = 1.96 * se
        print(f"\n📐 统计学置信度（95% CI）:")
        print(f"  置信区间: [{max(0, p-ci_width):.1%}, {min(1, p+ci_width):.1%}]")
        print(f"  区间宽度: ±{ci_width:.1%}")
        if ci_width <= 0.08:
            print("  ✅✅ 样本量充足，结论高度可信")
        elif ci_width <= 0.12:
            print("  ✅ 样本量可接受，结论基本可信")
        else:
            print("  ⚠️  样本量偏少，建议扩充至80+以提升置信度")


if __name__ == "__main__":
    main()