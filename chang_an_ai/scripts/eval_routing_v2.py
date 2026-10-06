"""路由准确率评估 V2 —— 场景矩阵自动生成 300 条均衡测试用例。

用法: cd chang_an_ai && python scripts/eval_routing_v2.py

设计:
- 场景×变量矩阵系统生成，非拍脑袋硬编码
- 分布 RAG:Agent:Boundary ≈ 60:30:10，接近真实生产
- 生成逻辑与评估逻辑分离，可独立验证标注质量
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.routers.chat import _route, ChatRequest
from app.routing_config import AGENT_KEYWORDS, RAG_KEYWORDS

# ==============================================================
# 原始 62 条（保留，确保回归不退化）
# ==============================================================
ORIGINAL_CASES = [
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

# ==============================================================
# 场景矩阵生成器：系统构造均衡测试集
# ==============================================================

def generate_rag_cases() -> list[tuple[str, str]]:
    """RAG 类型：静态知识问答，目标 ~180 条。

    覆盖：景点知识 / 美食文化 / 交通住宿 / 门票信息 / 历史文化 / 行程规划
    """
    cases = []
    scenery = ["大雁塔", "兵马俑", "华清池", "钟楼", "城墙", "大唐不夜城", "回民街", "华山"]
    foods = ["肉夹馍", "泡馍", "凉皮", "biangbiang面", "冰峰", "灌汤包", "胡辣汤", "柿子饼", "甑糕", "酸梅汤"]

    # 1) 景点知识：8景点 × 6模板 = 48
    tpl_scenery = [
        "{s}有什么历史故事",
        "{s}的建筑特色是什么",
        "{s}的最佳游览时间是什么时候",
        "{s}适合玩多久",
        "{s}附近有什么景点可以一起玩",
        "{s}的传说或民间故事",
    ]
    for s in scenery:
        for t in tpl_scenery:
            cases.append((t.format(s=s), "rag"))

    # 2) 美食文化：10美食 × 3模板 = 30
    tpl_food = [
        "{f}的制作工艺是什么",
        "{f}的历史由来",
        "{f}和别的城市做法有什么不同",
    ]
    for f in foods:
        for t in tpl_food:
            cases.append((t.format(f=f), "rag"))

    # 3) 交通住宿：6模板 × 5变量 = 30
    transport_vars = ["钟楼", "大雁塔", "兵马俑", "咸阳机场", "回民街"]
    tpl_transport = [
        "从{v}到火车站怎么走",
        "{v}附近有什么酒店推荐吗",
        "西安{v}坐地铁方便吗",
        "自驾去{v}好停车吗",
        "{v}周边住宿条件怎么样",
        "去{v}需要预约吗",
    ]
    for v in transport_vars:
        for t in tpl_transport:
            cases.append((t.format(v=v), "rag"))

    # 4) 门票信息：8景点 × 4模板 = 32
    tpl_ticket = [
        "{s}门票价格是多少",
        "{s}有学生票优惠吗",
        "{s}开放时间是几点到几点",
        "{s}需要提前预约吗",
    ]
    for s in scenery:
        for t in tpl_ticket:
            cases.append((t.format(s=s), "rag"))

    # 5) 历史文化：8景点 × 2模板 = 16
    tpl_history = [
        "{s}在唐朝时期是什么样子",
        "{s}有哪些名人去过",
    ]
    for s in scenery:
        for t in tpl_history:
            cases.append((t.format(s=s), "rag"))

    # 6) 行程规划：6模板 × 4变量 = 24
    plan_vars = ["两天", "三天", "四天", "一天"]
    tpl_plan = [
        "西安{n}游最适合的季节是什么",
        "西安{n}游怎么分配时间比较合理",
        "带父母的西安{n}游攻略",
        "穷游西安{n}的预算大概多少",
        "西安{n}必打卡的地方有哪些",
        "西安{n}游推荐住在哪个区",
    ]
    for v in plan_vars:
        n = v
        for t in tpl_plan:
            cases.append((t.format(n=n), "rag"))

    return cases  # 48+30+30+32+16+24 = 180

def generate_agent_cases() -> list[tuple[str, str]]:
    """Agent 类型：实时数据查询，目标 ~90 条。

    覆盖：找店推荐 / 价格查询 / 优惠券 / 营业信息 / 评分口碑
    """
    cases = []
    locations = ["钟楼", "大雁塔", "回民街", "城墙", "小寨"]
    shop_types = ["泡馍店", "面馆", "饺子馆", "肉夹馍店", "凉皮店", "小吃店"]

    # 1) 找店推荐：5地点 × 6店型 × 2模板 = 60
    tpl_find = [
        "{loc}附近的{typ}推荐一下",
        "{loc}有什么好吃的{typ}",
    ]
    for loc in locations:
        for typ in shop_types:
            for t in tpl_find:
                cases.append((t.format(loc=loc, typ=typ), "agent"))

    # 2) 价格查询：6店型 × 2模板 = 12
    tpl_price = [
        "{typ}人均多少钱",
        "{typ}贵不贵",
    ]
    for typ in shop_types:
        for t in tpl_price:
            cases.append((t.format(typ=typ), "agent"))

    # 3) 优惠券：5模板 = 5
    tpl_coupon = [
        "这家店有优惠券吗",
        "有什么团购活动",
        "有没有折扣券可以领",
        "最近的优惠活动有哪些",
        "促销券在哪里领",
    ]
    for t in tpl_coupon:
        cases.append((t, "agent"))

    # 4) 营业信息：6店型 × 2模板 = 12
    tpl_biz = [
        "{typ}营业时间是几点",
        "{typ}几点开门几点关门",
    ]
    for typ in shop_types:
        for t in tpl_biz:
            cases.append((t.format(typ=typ), "agent"))

    # 5) 评分口碑：5模板 = 5
    tpl_score = [
        "这家店评分高吗",
        "用户口碑怎么样",
        "好评多不多",
        "有没有差评",
        "这家店星级多少",
    ]
    for t in tpl_score:
        cases.append((t, "agent"))

    return cases  # 60+12+5+12+5 = 94

def generate_boundary_cases() -> list[tuple[str, str]]:
    """Boundary 类型：超纲拒绝，目标 ~30 条。

    覆盖：其他城市 / 天气 / 数学 / 政治敏感 / 其他领域 / 无意义
    """
    cases = []

    # 1) 其他城市：8条
    cities = ["北京", "上海", "成都", "杭州", "丽江", "三亚", "重庆", "南京"]
    for city in cities:
        cases.append((f"{city}有什么好玩的景点", "boundary"))

    # 2) 天气：5条
    cases.append(("今天天气怎么样", "boundary"))
    cases.append(("明天下不下雨", "boundary"))
    cases.append(("温度多少度", "boundary"))
    cases.append(("空气质量好不好", "boundary"))
    cases.append(("会不会刮风", "boundary"))

    # 3) 数学：3条
    cases.append(("2+3等于几", "boundary"))
    cases.append(("123乘以456是多少", "boundary"))
    cases.append(("根号2等于多少", "boundary"))

    # 4) 其他领域：7条
    cases.append(("推荐一部好看的电影", "boundary"))
    cases.append(("怎么学习Python编程", "boundary"))
    cases.append(("股票最近怎么样", "boundary"))
    cases.append(("帮我写一首诗", "boundary"))
    cases.append(("哪个牌子的手机好", "boundary"))
    cases.append(("怎么做红烧肉", "boundary"))
    cases.append(("世界杯冠军是谁", "boundary"))

    # 5) 无意义：5条
    cases.append(("啊", "boundary"))
    cases.append(("哈哈哈", "boundary"))
    cases.append(("???", "boundary"))
    cases.append(("嗯嗯嗯", "boundary"))
    cases.append(("。。。", "boundary"))

    return cases  # 8+5+3+7+5 = 28

def build_full_test_set() -> list[tuple[str, str]]:
    """拼接原始 62 条 + 场景矩阵生成的所有用例。"""
    rag = generate_rag_cases()       # ~180
    agent = generate_agent_cases()   # ~94
    boundary = generate_boundary_cases()  # ~28

    # 去重：如果生成的 case 和原始 case 完全重复，跳过
    seen = set(q for q, _ in ORIGINAL_CASES)
    for gen_cases in [rag, agent, boundary]:
        gen_cases[:] = [(q, e) for q, e in gen_cases if q not in seen]
        seen.update(q for q, _ in gen_cases)

    all_cases = list(ORIGINAL_CASES) + rag + agent + boundary
    return all_cases

# ==============================================================
# 评估逻辑
# ==============================================================

def _explain(msg):
    ah = [kw for kw in AGENT_KEYWORDS if kw in msg or re.search(kw, msg)]
    rh = [kw for kw in RAG_KEYWORDS if kw in msg]
    return ah, rh

async def main():
    CASES = build_full_test_set()
    tp = fn = fp = tn = bnd_ok = 0
    errors = []

    ag = sum(1 for _, e in CASES if e == "agent")
    rg = sum(1 for _, e in CASES if e == "rag")
    bd = sum(1 for _, e in CASES if e == "boundary")
    valid = ag + rg + bd

    print("\n" + "=" * 72)
    print("  路由准确率评估 V2 (场景矩阵自动生成)")
    print("=" * 72)
    print(f"  总样本: {len(CASES)}")
    print(f"  分布: RAG={rg} ({rg/valid*100:.0f}%) | Agent={ag} ({ag/valid*100:.0f}%) | "
          f"Boundary={bd} ({bd/valid*100:.0f}%)")
    print(f"  目标分布: RAG 60% | Agent 30% | Boundary 10%")
    print("-" * 72)

    for i, (q, expected) in enumerate(CASES, 1):
        req = ChatRequest(session_id="eval", message=q, mode="auto")
        actual = await _route(req)

        if expected == "boundary":
            bnd_ok += 1 if actual == "rag" else 0
            mark = "OK" if actual == "rag" else "WRN"
        elif expected == actual:
            if expected == "agent":
                tp += 1
            else:
                tn += 1
            mark = "OK"
        else:
            mark = "FAIL"
            if expected == "agent":
                fn += 1
            else:
                fp += 1
            errors.append((q, expected, actual))

        # 只打印错误，正常的不打印（样本多了全打印太冗长）
        if mark != "OK":
            icon = "??" if mark == "WRN" else "FAIL"
            print(f"  [{i:3d}] {icon:4s} [{actual.upper():5s}] {q[:55]:55s} (期望:{expected.upper()})")

    error_valid = tp + fn + fp + tn
    acc = (tp + tn) / error_valid * 100 if error_valid else 0
    pa = tp / (tp + fp) * 100 if (tp + fp) else 0
    ra = tp / (tp + fn) * 100 if (tp + fn) else 0
    f1a = 2 * pa * ra / (pa + ra) if (pa + ra) else 0
    pr = tn / (tn + fn) * 100 if (tn + fn) else 0
    rr = tn / (tn + fp) * 100 if (tn + fp) else 0

    print(f"\n  正确数: {tp+tn+bnd_ok}/{len(CASES)}  无错误打印")
    print(f"  (仅显示 {len(errors)} 个错误 + {bd-bnd_ok} 个边界警告)")

    print("\n" + "=" * 72)
    print("  评估结果汇总")
    print("=" * 72)

    print(f"\n  混淆矩阵 ({error_valid} 个有效样本):")
    print(f"  {'':14s} | {'预测Agent':>10s} | {'预测RAG':>10s}")
    print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*10}")
    print(f"  {'实际Agent':12s} | {tp:>10d} | {fn:>10d}")
    print(f"  {'实际RAG':12s} | {fp:>10d} | {tn:>10d}")

    print(f"\n  总体准确率: {acc:.1f}% ({tp+tn}/{error_valid})")
    print(f"  Agent 精确率: {pa:.1f}% ({tp}/{tp+fp})")
    print(f"  Agent 召回率: {ra:.1f}% ({tp}/{tp+fn})")
    print(f"  Agent F1:    {f1a:.1f}%")
    print(f"  RAG 精确率:  {pr:.1f}% ({tn}/{tn+fn})")
    print(f"  RAG 召回率:  {rr:.1f}% ({tn}/{tn+fp})")
    if bd:
        print(f"  边界拒绝率:  {bnd_ok}/{bd} ({bnd_ok/bd*100:.0f}%)")

    # 错误分类展示
    if errors:
        fp_list = [(q, e, a) for q, e, a in errors if e == "rag"]
        fn_list = [(q, e, a) for q, e, a in errors if e == "agent"]

        print(f"\n  {'─' * 68}")
        if fp_list:
            print(f"  RAG→Agent 误判 ({len(fp_list)} 个):")
            for q, e, a in fp_list[:10]:
                ah, rh = _explain(q)
                print(f"    [{e.upper()}→{a.upper()}] {q}")
                print(f"      命中: Agent={ah} RAG={rh}")
            if len(fp_list) > 10:
                print(f"    ... 还有 {len(fp_list)-10} 个")

        if fn_list:
            print(f"  Agent→RAG 漏召 ({len(fn_list)} 个):")
            for q, e, a in fn_list[:10]:
                ah, rh = _explain(q)
                print(f"    [{e.upper()}→{a.upper()}] {q}")
                print(f"      命中: Agent={ah} RAG={rh}")
            if len(fn_list) > 10:
                print(f"    ... 还有 {len(fn_list)-10} 个")

    # 诊断
    print(f"\n  {'─' * 68}")
    print(f"  诊断:")
    if acc >= 95:
        print(f"    优秀 (>=95%), 路由系统稳定可靠")
    elif acc >= 90:
        print(f"    良好 (90-95%), 基本满足生产要求")
    elif acc >= 85:
        print(f"    一般 (85-90%), 有明确优化空间")
    else:
        print(f"    偏低 (<85%), 需优先优化")

    if fn:
        print(f"    Agent 漏召 FN={fn}: 补充 AGENT_KEYWORDS 覆盖这些口语化表达")
    if fp:
        print(f"    RAG 误判 FP={fp}: 收紧冲突规则或调整 Agent 关键词")

    print("=" * 72)
    print()

if __name__ == "__main__":
    asyncio.run(main())