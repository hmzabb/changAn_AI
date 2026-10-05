"""路由准确率快速评估 —— 基于 eval_retrieval.py 62 个 QA 对。

用法: cd chang_an_ai && python scripts/eval_routing.py

输出: 准确率、混淆矩阵、精确率/召回率/F1、错误案例及命中关键词诊断
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routers.chat import _route, ChatRequest
from app.routing_config import AGENT_KEYWORDS, RAG_KEYWORDS

CASES = [
    # Layer 1: 核心事实 (20)
    ("大雁塔门票多少钱", "rag"),
    ("大雁塔是谁主持修建的", "rag"),
    ("大雁塔北广场喷泉什么时候有", "rag"),
    ("西安城墙可以骑车吗", "rag"),
    ("西安城墙有多长", "rag"),
    ("城墙哪个门最值得登", "rag"),
    ("钟楼和鼓楼相距多远", "rag"),
    ("钟楼鼓楼联票多少钱", "rag"),
    ("大唐不夜城什么时候去最好", "rag"),
    ("不夜城有什么表演", "rag"),
    ("回民街有哪些必吃小吃", "rag"),
    ("回民街怎么逛不踩坑", "rag"),
    ("西安三日游怎么安排", "rag"),
    ("兵马俑安排在第几天", "rag"),
    ("西安住宿选哪个商圈方便", "rag"),
    ("去西安坐高铁到哪个站", "rag"),
    ("肉夹馍有什么讲究", "rag"),
    ("西安凉皮有哪几种", "rag"),
    ("biangbiang面是什么", "rag"),
    ("冰峰是什么", "rag"),

    # Layer 2: 基础层 (16)
    ("大雁塔怎么去，地铁几号线", "rag"),
    ("大雁塔和大慈恩寺是什么关系", "rag"),
    ("西安城墙有几个门可以登", "rag"),
    ("城墙门票包含自行车吗", "rag"),
    ("钟楼鼓楼晚上亮灯吗", "rag"),
    ("钟楼鼓楼值得单独买票吗", "rag"),
    ("大唐不夜城需要门票吗", "rag"),
    ("不夜城适合带孩子去吗", "rag"),
    ("回民街什么时候人最少", "rag"),
    ("回民街有哪些坑要注意", "rag"),
    ("肉夹馍和汉堡有什么区别", "rag"),
    ("西安小吃哪个最辣", "rag"),
    ("三天时间够玩西安吗", "rag"),
    ("如果只有两天怎么压缩行程", "rag"),
    ("咸阳机场到市区怎么走", "rag"),
    ("西安住宿大概多少钱一晚", "rag"),

    # Layer 3: 进阶层 (15)
    ("从钟楼到大雁塔坐几号地铁，大概多长时间", "rag"),
    ("大雁塔附近有什么好吃的推荐", "agent"),
    ("城墙骑行结束后去哪里吃晚饭比较好", "agent"),
    ("预算500元以内，三天游西安怎么安排", "rag"),
    ("带老人和孩子，哪些景点不适合去", "rag"),
    ("冬天去西安，哪些户外景点要慎选", "rag"),
    ("西安和北京的古都特色有什么不同", "rag"),
    ("回民街的小吃和普通街头小吃区别在哪", "rag"),
    ("如果只有一天时间，优先去哪两个景点", "rag"),
    ("西安旅游最佳季节是什么时候", "rag"),
    ("景点之间怎么安排顺序最合理", "rag"),
    ("哪些小吃是西安独有的", "rag"),
    ("为什么西安被称为十三朝古都", "rag"),
    ("回民街的历史由来是什么", "rag"),
    ("城墙是中国现存最完整的古城墙吗", "rag"),

    # Layer 4: 鲁棒性层 (10)
    ("咋去大雁塔啊", "rag"),
    ("肉夹馍啥味儿的", "rag"),
    ("这地儿有啥好吃的没", "agent"),
    ("大雁它门票多少", "rag"),
    ("biangbiang面咋写", "rag"),
    ("北京故宫门票多少钱", "boundary"),
    ("我想去东京旅游", "boundary"),
    ("今天天气怎么样", "boundary"),
    ("1+1等于几", "boundary"),
]

def _explain(msg):
    ah = [kw for kw in AGENT_KEYWORDS if kw in msg or re.search(kw, msg)]
    rh = [kw for kw in RAG_KEYWORDS if kw in msg]
    return ah, rh

async def main():
    tp = fn = fp = tn = bnd_ok = 0
    errors = []

    ag = sum(1 for _, e in CASES if e == "agent")
    rg = sum(1 for _, e in CASES if e == "rag")
    bd = sum(1 for _, e in CASES if e == "boundary")

    print("\n" + "=" * 72)
    print("  路由准确率评估报告 (62 QA pairs)")
    print("=" * 72)
    print(f"  标注: Agent={ag}  RAG={rg}  Boundary={bd}")
    print("-" * 72)

    for i, (q, expected) in enumerate(CASES, 1):
        req = ChatRequest(session_id="eval", message=q, mode="auto")
        actual = await _route(req)

        if expected == "boundary":
            bnd_ok += 1 if actual == "rag" else 0
            mark = "OK" if actual == "rag" else "WRN"
        elif expected == actual:
            if expected == "agent": tp += 1
            else: tn += 1
            mark = "OK"
        else:
            mark = "FAIL"
            if expected == "agent": fn += 1
            else: fp += 1
            errors.append((q, expected, actual))

        icon = "OK" if mark == "OK" else ("??" if mark == "WRN" else "FAIL")
        print(f"  [{i:2d}] {icon:4s} [{actual.upper():5s}] {q}")

    valid = tp + fn + fp + tn
    acc = (tp + tn) / valid * 100 if valid else 0
    pa = tp / (tp + fp) * 100 if (tp + fp) else 0
    ra = tp / (tp + fn) * 100 if (tp + fn) else 0
    f1a = 2 * pa * ra / (pa + ra) if (pa + ra) else 0
    pr = tn / (tn + fn) * 100 if (tn + fn) else 0
    rr = tn / (tn + fp) * 100 if (tn + fp) else 0

    print("\n" + "=" * 72)
    print("  结果汇总")
    print("=" * 72)
    print(f"\n  混淆矩阵 ({valid} 有效样本):")
    print(f"  {'':14s} | {'预测Agent':>10s} | {'预测RAG':>10s}")
    print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*10}")
    print(f"  {'实际Agent':12s} | {tp:>10d} | {fn:>10d}")
    print(f"  {'实际RAG':12s} | {fp:>10d} | {tn:>10d}")
    print(f"\n  总体准确率: {acc:.1f}% ({tp+tn}/{valid})")
    print(f"  Agent 精确率: {pa:.1f}% ({tp}/{tp+fp})")
    print(f"  Agent 召回率: {ra:.1f}% ({tp}/{tp+fn})")
    print(f"  Agent F1:    {f1a:.1f}%")
    print(f"  RAG 精确率:  {pr:.1f}% ({tn}/{tn+fn})")
    print(f"  RAG 召回率:  {rr:.1f}% ({tn}/{tn+fp})")
    if bd:
        print(f"  边界拒绝率:  {bnd_ok}/{bd} ({bnd_ok/bd*100:.0f}%)")

    if errors:
        print(f"\n  错误({len(errors)}):")
        for q, e, a in errors:
            ah, rh = _explain(q)
            print(f"    [{e.upper()}->{a.upper()}] {q}")
            if ah or rh: print(f"      命中: Agent={ah} RAG={rh}")

    if fn: print(f"\n  Agent漏召FN={fn}: 口语化/歧义未被关键词覆盖")
    if fp: print(f"\n  RAG误判FP={fp}: 含Agent关键词的知识类问题")
    agent_pct = ag / valid * 100 if valid else 0
    if agent_pct < 20:
        print(f"  样本偏差: Agent仅占{agent_pct:.1f}%, 真实生产可能30-50%")
    print("=" * 72 + "\n")

if __name__ == "__main__":
    asyncio.run(main())