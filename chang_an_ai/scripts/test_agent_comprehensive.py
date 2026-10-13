"""
Agent 六维度综合评测：任务完成度 → 过程合理性 → 系统稳定性

============================================================
维度设计对照用户需求：
============================================================
  维度一：任务完成度 —— 是否真的完成用户目标
  维度二：规划能力评测 —— 复杂任务拆解是否合理，步骤顺序是否正确
  维度三：工具调用能力 —— 是否选择正确工具，参数是否准确
  维度四：执行轨迹评测 —— 关键区分点，是否存在冗余
  维度五：结果质量评测 —— 准确/完整/可执行（LLM judge）
  维度六：稳定性和鲁棒性 —— 面对模糊表达/异常情况能否主动追问或异常恢复

============================================================
运行方式：
  cd chang_an_ai
  python scripts/test_agent_comprehensive.py           # 默认1轮
  python scripts/test_agent_comprehensive.py --full    # 2轮（含缓存验证）
  python scripts/test_agent_comprehensive.py --rounds 3

统计数据有效性：
  - P50 需要至少 5 样本
  - P95 需要至少 20 样本
  - P99 需要至少 100 样本
  本测试默认 36 条 × 1 轮 = 36 条查询，满足基础统计要求
============================================================
"""
import asyncio
import sys
import time
import json
import statistics
import math
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv
load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ============================================================================
#  测试用例定义：按评测维度分类
# ============================================================================

class TestDimensionType(Enum):
    """评测维度枚举"""
    TASK_COMPLETION = "dim1_task_completion"      # 任务完成度
    PLANNING = "dim2_planning"                     # 规划能力
    TOOL_CALLING = "dim3_tool_calling"              # 工具调用能力
    EXECUTION_TRAJECTORY = "dim4_trajectory"        # 执行轨迹
    RESULT_QUALITY = "dim5_result_quality"          # 结果质量
    STABILITY = "dim6_stability"                    # 稳定性和鲁棒性


@dataclass
class ComprehensiveTestCase:
    """六维度测试用例"""
    id: str
    query: str
    dimension: TestDimensionType
    sub_category: str
    expected_tools: List[str]          # 预期调用的工具
    min_tools: int = 1
    description: str = ""
    # 规划能力相关
    expected_min_steps: int = 0         # 最小合理步骤数
    expected_step_order: List[str] = field(default_factory=list)  # 预期步骤顺序
    # 结果质量相关
    expected_keywords: List[str] = field(default_factory=list)    # 回答应包含的关键信息
    forbidden_keywords: List[str] = field(default_factory=list)   # 回答不应包含的内容
    # 稳定性相关
    is_ambiguous: bool = False          # 是否为模糊表达
    should_ask_question: bool = False    # 是否应该主动追问
    is_error_scenario: bool = False      # 是否为异常场景


# ============================================================================
#  维度一：任务完成度 (15条) —— 真实用户目标是否完成
# ============================================================================
DIM1_CASES = [
    # D1-1: 单工具单步查询 (3条)
    ComprehensiveTestCase("D1-01", "帮我找一下回民街附近有什么好吃的泡馍店", TestDimensionType.TASK_COMPLETION,
             "单工具查询", ["search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["泡馍", "评分", "人均"], forbidden_keywords=["不知道", "无法查询"],
             description="典型店铺搜索：一次工具调用完成"),
    ComprehensiveTestCase("D1-02", "大雁塔的门票多少钱？开放时间是什么时候？", TestDimensionType.TASK_COMPLETION,
             "单工具查询", ["search_knowledge"], min_tools=1, expected_min_steps=1,
             expected_keywords=["门票", "开放", "时间"], forbidden_keywords=["不知道", "我无法"],
             description="知识查询：一次工具调用完成"),
    ComprehensiveTestCase("D1-03", "有没有人去回民街探店写过笔记？", TestDimensionType.TASK_COMPLETION,
             "单工具查询", ["search_blogs"], min_tools=1, expected_min_steps=1,
             expected_keywords=["笔记", "探店"], forbidden_keywords=[],
             description="笔记搜索：一次工具调用完成"),

    # D1-2: 多工具并行查询 (5条)
    ComprehensiveTestCase("D1-04", "推荐几家回民街评分高的餐厅，顺便看看有没有优惠券",
             TestDimensionType.TASK_COMPLETION, "多工具查询",
             ["search_shops_by_name", "list_vouchers", "search_knowledge"], min_tools=2, expected_min_steps=2,
             expected_step_order=["search", "voucher"],
             expected_keywords=["回民街", "评分", "优惠", "推荐"],
             forbidden_keywords=["不知道"],
             description="三工具混合：店铺+优惠券+知识"),
    ComprehensiveTestCase("D1-05", "我想去小寨吃烤肉，帮我看看有什么店，有没有优惠",
             TestDimensionType.TASK_COMPLETION, "多工具查询",
             ["search_shops_by_name", "list_shops_by_type", "list_vouchers"], min_tools=2, expected_min_steps=2,
             expected_step_order=["search", "voucher"],
             expected_keywords=["小寨", "烤肉", "优惠", "推荐"],
             forbidden_keywords=[],
             description="位置+品类+优惠券三合一"),
    ComprehensiveTestCase("D1-06", "帮我全面了解一下回民街：有什么店、有什么优惠、有哪些笔记推荐、有什么攻略",
             TestDimensionType.TASK_COMPLETION, "多工具查询",
             ["search_shops_by_name", "list_vouchers", "search_blogs", "search_knowledge"], min_tools=3, expected_min_steps=3,
             expected_step_order=["shop", "voucher", "blog", "knowledge"],
             expected_keywords=["回民街", "店铺", "优惠", "笔记", "攻略"],
             forbidden_keywords=[],
             description="全量信息查询：四工具覆盖"),
    ComprehensiveTestCase("D1-07", "看看美容SPA有什么好的店，顺便查一下有没有人写过体验笔记",
             TestDimensionType.TASK_COMPLETION, "多工具查询",
             ["list_shops_by_type", "search_blogs"], min_tools=2, expected_min_steps=2,
             expected_keywords=["美容", "SPA", "笔记", "体验"],
             description="分类+笔记混合查询"),
    ComprehensiveTestCase("D1-08", "给我查老潼关肉夹馍店的详情和优惠券信息",
             TestDimensionType.TASK_COMPLETION, "多工具查询",
             ["search_shops_by_name", "get_shop_detail", "list_vouchers"], min_tools=3, expected_min_steps=3,
             expected_step_order=["search_shops_by_name", "get_shop_detail", "list_vouchers"],
             expected_keywords=["老潼关", "详情", "优惠"],
             description="完整链路：名称→详情→优惠券"),

    # D1-3: 分类查询 (4条)
    ComprehensiveTestCase("D1-09", "西安有没有评分高的火锅店推荐？", TestDimensionType.TASK_COMPLETION,
             "分类查询", ["list_shops_by_type"], min_tools=1, expected_min_steps=1,
             expected_keywords=["火锅", "评分", "推荐"],
             description="美食分类搜索"),
    ComprehensiveTestCase("D1-10", "帮我看看有什么KTV可以订？", TestDimensionType.TASK_COMPLETION,
             "分类查询", ["list_shops_by_type"], min_tools=1, expected_min_steps=1,
             expected_keywords=["KTV"],
             description="KTV分类搜索"),
    ComprehensiveTestCase("D1-11", "最近想健身，有没有评分高的健身房？", TestDimensionType.TASK_COMPLETION,
             "分类查询", ["list_shops_by_type"], min_tools=1, expected_min_steps=1,
             expected_keywords=["健身"],
             description="健身运动分类搜索"),
    ComprehensiveTestCase("D1-12", "回民街有什么特色的西安小吃值得一试？", TestDimensionType.TASK_COMPLETION,
             "分类查询", ["search_knowledge", "search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["回民街", "小吃", "推荐"],
             description="美食推荐查询"),

    # D1-4: 链路完成度 (3条) —— 验证完整业务链路
    ComprehensiveTestCase("D1-13", "帮我找一下小寨赛格附近的火锅店，挑一家评分最高的，看看有什么优惠",
             TestDimensionType.TASK_COMPLETION, "链路完成度",
             ["list_shops_by_type", "get_shop_detail", "list_vouchers"], min_tools=3, expected_min_steps=3,
             expected_step_order=["list_shops_by_type", "get_shop_detail", "list_vouchers"],
             expected_keywords=["火锅", "评分", "优惠", "小寨"],
             description="完整商业链路：分类→筛选最佳→详情→优惠券"),
    ComprehensiveTestCase("D1-14", "回民街有什么泡馍馆？帮我看看评价最好的那家的详情",
             TestDimensionType.TASK_COMPLETION, "链路完成度",
             ["search_shops_by_name", "get_shop_detail"], min_tools=2, expected_min_steps=2,
             expected_step_order=["search_shops_by_name", "get_shop_detail"],
             expected_keywords=["泡馍", "回民街", "评分", "人均", "地址"],
             forbidden_keywords=["不知道"],
             description="搜索→筛选→详情链路"),
    ComprehensiveTestCase("D1-15", "有没有华山一日游的攻略？顺便帮我查查华山附近有住宿推荐吗",
             TestDimensionType.TASK_COMPLETION, "链路完成度",
             ["search_knowledge"], min_tools=1, expected_min_steps=1,
             expected_keywords=["华山", "攻略", "住宿"],
             forbidden_keywords=["不知道"],
             description="知识查询覆盖多个子需求"),
]


# ============================================================================
#  维度二：规划能力 (10条) —— 复杂任务拆解是否合理，步骤顺序是否正确
# ============================================================================
DIM2_CASES = [
    # D2-1: 需要先查店铺再查优惠券 (3条)
    ComprehensiveTestCase("D2-01", "帮我查一下老潼关肉夹馍店有没有优惠券或折扣",
             TestDimensionType.PLANNING, "先店铺后优惠券",
             ["search_shops_by_name", "list_vouchers"], min_tools=2, expected_min_steps=2,
             expected_step_order=["search_shops_by_name", "list_vouchers"],
             expected_keywords=["老潼关", "优惠"],
             description="必须步骤：先搜店铺名获取ID → 再查优惠券"),
    ComprehensiveTestCase("D2-02", "有没有火锅店的优惠券？告诉我具体是哪些店",
             TestDimensionType.PLANNING, "先分类后优惠券",
             ["list_shops_by_type", "list_vouchers"], min_tools=2, expected_min_steps=2,
             expected_step_order=["list_shops_by_type", "list_vouchers"],
             expected_keywords=["火锅", "优惠券", "店铺"],
             description="必须步骤：先查火锅分类获取店铺列表 → 再查各店铺优惠券"),
    ComprehensiveTestCase("D2-03", "美睫美甲的店有优惠券吗？帮我推荐几家",
             TestDimensionType.PLANNING, "先分类后优惠券",
             ["list_shops_by_type", "list_vouchers"], min_tools=2, expected_min_steps=2,
             expected_step_order=["list_shops_by_type", "list_vouchers"],
             expected_keywords=["美睫", "美甲", "优惠"],
             description="必须步骤：先查美睫美甲分类 → 再查优惠券"),

    # D2-2: 多实体并行查询 (3条)
    ComprehensiveTestCase("D2-04", "大雁塔附近有什么好玩的？门票怎么买？附近有没有好吃的店？",
             TestDimensionType.PLANNING, "多实体并行",
             ["search_knowledge", "search_shops_by_name"], min_tools=2, expected_min_steps=2,
             expected_keywords=["大雁塔", "门票", "餐厅", "好玩"],
             description="知识+店铺两个独立任务应并行查询"),
    ComprehensiveTestCase("D2-05", "钟楼有什么历史故事？以及附近有哪些店可以吃饭？",
             TestDimensionType.PLANNING, "多实体并行",
             ["search_knowledge", "search_shops_by_name"], min_tools=2, expected_min_steps=2,
             expected_keywords=["钟楼", "历史", "餐厅", "吃饭"],
             description="历史文化+附近店铺两独立任务应并行"),
    ComprehensiveTestCase("D2-06", "小寨有哪些火锅店？大雁塔有什么好玩的？",
             TestDimensionType.PLANNING, "多实体并行",
             ["list_shops_by_type", "search_knowledge"], min_tools=2, expected_min_steps=2,
             expected_keywords=["小寨", "火锅", "大雁塔"],
             description="两个独立实体应并行查询"),

    # D2-3: 逐级深入查询 (2条)
    ComprehensiveTestCase("D2-07", "帮我找一家评分最高的泡馍店，然后查它详细的营业信息和优惠券",
             TestDimensionType.PLANNING, "逐级深入",
             ["search_shops_by_name", "get_shop_detail", "list_vouchers"], min_tools=3, expected_min_steps=3,
             expected_step_order=["search_shops_by_name", "get_shop_detail", "list_vouchers"],
             expected_keywords=["泡馍", "评分", "营业", "优惠"],
             description="三级深入：搜索→详情→优惠券，顺序不可乱"),
    ComprehensiveTestCase("D2-08", "有没有好的烤肉店？帮我挑一家评价最好的，看看它的优惠信息",
             TestDimensionType.PLANNING, "逐级深入",
             ["list_shops_by_type", "get_shop_detail", "list_vouchers"], min_tools=3, expected_min_steps=3,
             expected_step_order=["list_shops_by_type", "get_shop_detail", "list_vouchers"],
             expected_keywords=["烤肉", "评价", "详情", "优惠"],
             description="三级深入：分类筛选→店铺详情→优惠券"),

    # D2-4: 复杂依赖场景 (2条)
    ComprehensiveTestCase("D2-09", "帮我查一下大唐不夜城有什么好玩的，然后看看附近步行10分钟内的餐厅有哪些",
             TestDimensionType.PLANNING, "复杂依赖",
             ["search_knowledge", "search_shops_by_name", "list_shops_by_type"], min_tools=2, expected_min_steps=2,
             expected_step_order=["search_knowledge", "search_shops_by_name"],
             expected_keywords=["大唐不夜城", "餐厅", "步行"],
             description="知识定位→附近店铺搜索，有先后依赖"),
    ComprehensiveTestCase("D2-10", "先帮我查一下西安城墙上能骑车吗，如果能的话附近有没有租车后吃饭的肉夹馍店",
             TestDimensionType.PLANNING, "复杂依赖",
             ["search_knowledge", "search_shops_by_name"], min_tools=2, expected_min_steps=2,
             expected_step_order=["search_knowledge", "search_shops_by_name"],
             expected_keywords=["城墙", "骑车", "肉夹馍"],
             description="先知识确认→后店铺搜索，有条件分支"),
]


# ============================================================================
#  维度三：工具调用能力 (10条) —— 是否选择正确工具，参数是否准确
# ============================================================================
DIM3_CASES = [
    # D3-1: 正确工具选择 (3条)
    ComprehensiveTestCase("D3-01", "搜一下名字里带'凉皮'的店", TestDimensionType.TOOL_CALLING,
             "正确工具选择", ["search_shops_by_name"], min_tools=1,
             description="应选search_shops_by_name而非list_shops_by_type"),
    ComprehensiveTestCase("D3-02", "给我推荐一些精油开背的SPA店", TestDimensionType.TOOL_CALLING,
             "正确工具选择", ["list_shops_by_type"], min_tools=1,
             description="SPA=美容SPA分类，应选list_shops_by_type('美容SPA')"),
    ComprehensiveTestCase("D3-03", "华山一日游攻略", TestDimensionType.TOOL_CALLING,
             "正确工具选择", ["search_knowledge"], min_tools=1,
             description="攻略类用search_knowledge而非search_blogs"),

    # D3-2: 参数准确性 (3条)
    ComprehensiveTestCase("D3-04", "钟楼附近有哪些美食店？", TestDimensionType.TOOL_CALLING,
             "参数准确性", ["list_shops_by_type"], min_tools=1,
             expected_keywords=["钟楼", "美食"],
             description="应传type_name='美食'且带坐标(x,y)参数"),
    ComprehensiveTestCase("D3-05", "帮我搜一下'老西安饭庄'这家店", TestDimensionType.TOOL_CALLING,
             "参数准确性", ["search_shops_by_name"], min_tools=1,
             expected_keywords=["老西安", "饭庄"],
             description="应传name='老西安饭庄'进行模糊匹配"),
    ComprehensiveTestCase("D3-06", "查一下店铺ID为1的这家店有什么优惠", TestDimensionType.TOOL_CALLING,
             "参数准确性", ["list_vouchers", "get_shop_detail"], min_tools=1,
             expected_keywords=["优惠", "详情"],
             description="应直接调用get_shop_detail(1)或list_vouchers(1)，跳过搜索步骤"),

    # D3-3: 工具区分能力 (4条) —— 容易混淆的工具选择
    ComprehensiveTestCase("D3-07", "老孙家泡馍的口碑怎么样？值不值得去？", TestDimensionType.TOOL_CALLING,
             "工具区分", ["search_shops_by_name", "get_shop_detail"], min_tools=1,
             expected_keywords=["老孙家", "泡馍", "评分"],
             forbidden_keywords=[],
             description="评价口碑→search_shops_by_name查评分，非search_blogs"),
    ComprehensiveTestCase("D3-08", "有没有人去过大唐不夜城写过游记？", TestDimensionType.TOOL_CALLING,
             "工具区分", ["search_blogs"], min_tools=1,
             expected_keywords=["游记", "大唐不夜城"],
             description="游记笔记→search_blogs而非search_knowledge"),
    ComprehensiveTestCase("D3-09", "小寨附近有哪些吃喝玩乐的好去处？攻略推荐一下", TestDimensionType.TOOL_CALLING,
             "工具区分", ["search_shops_by_name", "search_knowledge", "list_shops_by_type"], min_tools=1,
             expected_keywords=["小寨", "推荐", "攻略"],
             description="综合性查询，需正确选择工具组合"),
    ComprehensiveTestCase("D3-10", "有没有日式料理的优惠券？", TestDimensionType.TOOL_CALLING,
             "工具区分", ["list_shops_by_type", "list_vouchers"], min_tools=2,
             expected_step_order=["list_shops_by_type", "list_vouchers"],
             expected_keywords=["日式", "料理", "优惠"],
             description="日式料理分类查询→优惠券，两步工具配合"),
]


# ============================================================================
#  维度四：执行轨迹 (9条) —— 是否存在冗余工具调用
# ============================================================================
DIM4_CASES = [
    # D4-1: 潜在冗余场景 (3条)
    ComprehensiveTestCase("D4-01", "帮我找找泡馍店，比如老孙家泡馍这种有名的",
             TestDimensionType.EXECUTION_TRAJECTORY, "避免冗余调用",
             ["search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["泡馍"],
             description="只需一次search_shops_by_name('泡馍')，不应重复搜索"),
    ComprehensiveTestCase("D4-02", "有没有好的餐厅？火锅、烤肉、川菜都行",
             TestDimensionType.EXECUTION_TRAJECTORY, "避免冗余调用",
             ["list_shops_by_type", "search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["火锅", "烤肉", "推荐"],
             description="泛品类查询不应每种类型都单独调一次工具"),
    ComprehensiveTestCase("D4-03", "钟楼附近有好吃的吗？就钟楼那边",
             TestDimensionType.EXECUTION_TRAJECTORY, "避免冗余调用",
             ["search_shops_by_name", "list_shops_by_type"], min_tools=1, expected_min_steps=1,
             expected_keywords=["钟楼", "推荐"],
             description="语义重复不应触发重复工具调用"),

    # D4-2: 高效调用场景 (2条)
    ComprehensiveTestCase("D4-04", "搜一下肉夹馍店和泡馍店，都要评分的",
             TestDimensionType.EXECUTION_TRAJECTORY, "高效调用",
             ["search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["肉夹馍", "泡馍", "评分"],
             description="两个搜索词应并行调用，不应串行等待"),
    ComprehensiveTestCase("D4-05", "帮我查探店笔记和旅游攻略，关于大雁塔的",
             TestDimensionType.EXECUTION_TRAJECTORY, "高效调用",
             ["search_blogs", "search_knowledge"], min_tools=2, expected_min_steps=1,
             expected_keywords=["大雁塔", "笔记", "攻略"],
             description="两个向量库查询应在一轮内并行完成"),

    # D4-3: 冗余模式检测 (4条)
    ComprehensiveTestCase("D4-06", "帮我找找火锅店。再帮我找找火锅店，最好评分高一点的",
             TestDimensionType.EXECUTION_TRAJECTORY, "重复查询检测",
             ["list_shops_by_type"], min_tools=1, expected_min_steps=1,
             expected_keywords=["火锅", "评分"],
             description="同一问题重复询问，不应触发两次相同的工具调用"),
    ComprehensiveTestCase("D4-07", "大雁塔附近有什么？我是说大雁塔周边有什么好玩的？大雁塔那边有什么美食？",
             TestDimensionType.EXECUTION_TRAJECTORY, "重复查询检测",
             ["search_knowledge", "search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["大雁塔", "美食"],
             description="同义词反复强调（附近/周边/那边），不应重复调用"),
    ComprehensiveTestCase("D4-08", "帮我搜个店，名字是'长安大排档'，再搜一下'长安大'开头的所有店",
             TestDimensionType.EXECUTION_TRAJECTORY, "重复查询检测",
             ["search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["长安大排档", "长安大"],
             description="前缀重叠的两个搜索可合并为一次模糊搜索"),
    ComprehensiveTestCase("D4-09", "给我推荐美食、火锅、小吃、烤肉、泡馍，每样都要",
             TestDimensionType.EXECUTION_TRAJECTORY, "批量查询优化",
             ["list_shops_by_type", "search_shops_by_name"], min_tools=1, expected_min_steps=1,
             expected_keywords=["美食", "火锅", "小吃", "烤肉", "泡馍"],
             description="多品类查询应合并调用，每种品类不应单独调一次"),
]


# ============================================================================
#  维度五：结果质量 (10条) —— LLM judge 评估准确/完整/可执行
# ============================================================================
DIM5_CASES = [
    # D5-1: 准确性验证 (2条)
    ComprehensiveTestCase("D5-01", "大雁塔是什么朝代建的？", TestDimensionType.RESULT_QUALITY,
             "准确性", ["search_knowledge"], min_tools=1,
             expected_keywords=["唐代", "唐朝", "652", "永徽"],
             forbidden_keywords=["明代", "清代", "汉代", "宋代"],
             description="准确回答大雁塔建于唐代永徽三年(652年)"),
    ComprehensiveTestCase("D5-02", "西安有几个世界遗产？分别是什么？", TestDimensionType.RESULT_QUALITY,
             "准确性", ["search_knowledge"], min_tools=1,
             expected_keywords=["兵马俑", "大雁塔", "丝绸之路"],
             forbidden_keywords=[],
             description="准确列举西安世界遗产"),

    # D5-2: 完整性验证 (2条)
    ComprehensiveTestCase("D5-03", "推荐几家回民街评分高的餐厅，顺便看看有没有优惠券",
             TestDimensionType.RESULT_QUALITY, "完整性",
             ["search_shops_by_name", "list_vouchers"], min_tools=2,
             expected_keywords=["回民街", "评分", "优惠", "餐厅"],
             forbidden_keywords=[],
             description="应包含店铺推荐+优惠券信息两个部分"),
    ComprehensiveTestCase("D5-04", "帮我查一下泡馍店：有哪些店、评分、人均、有没有优惠",
             TestDimensionType.RESULT_QUALITY, "完整性",
             ["search_shops_by_name", "get_shop_detail", "list_vouchers"], min_tools=2,
             expected_keywords=["泡馍", "评分", "人均", "优惠"],
             forbidden_keywords=[],
             description="应包含店名/评分/人均/优惠四个维度的信息"),

    # D5-3: 可执行性验证 (2条)
    ComprehensiveTestCase("D5-05", "我想去回民街吃小吃，具体帮我推荐一条路线",
             TestDimensionType.RESULT_QUALITY, "可执行性",
             ["search_knowledge", "search_shops_by_name"], min_tools=1,
             expected_keywords=["回民街", "推荐", "路线"],
             forbidden_keywords=["我不知道", "无法推荐"],
             description="回答应具有可执行性：给出具体路线和店铺"),
    ComprehensiveTestCase("D5-06", "怎么从钟楼去大雁塔最方便？大概多久？",
             TestDimensionType.RESULT_QUALITY, "可执行性",
             ["search_knowledge"], min_tools=1,
             expected_keywords=["地铁", "公交", "分钟", "号线"],
             forbidden_keywords=["不知道"],
             description="回答应给出具体交通方式和预估时间"),

    # D5-4: 幻觉检测 (4条) —— 防止编造
    ComprehensiveTestCase("D5-07", "西安钟楼是在明代哪个皇帝在位期间修建的？",
             TestDimensionType.RESULT_QUALITY, "幻觉检测",
             ["search_knowledge"], min_tools=1,
             expected_keywords=["洪武", "朱元璋"],
             forbidden_keywords=["永乐", "康熙", "乾隆"],
             description="钟楼始建于明洪武十七年(1384年)，不能编造为其他朝代"),
    ComprehensiveTestCase("D5-08", "大雁塔有多少层？里面有没有电梯？",
             TestDimensionType.RESULT_QUALITY, "幻觉检测",
             ["search_knowledge"], min_tools=1,
             expected_keywords=["七层", "层"],
             forbidden_keywords=["有电梯", "电梯", "九层"],
             description="大雁塔七层，无电梯，不能编造不存在的设施"),
    ComprehensiveTestCase("D5-09", "回民街推荐几家米其林餐厅",
             TestDimensionType.RESULT_QUALITY, "幻觉检测",
             ["search_shops_by_name", "list_shops_by_type"], min_tools=1, is_error_scenario=True,
             expected_keywords=["米其林"],
             forbidden_keywords=[],
             description="回民街可能无米其林，应如实告知而非编造"),
    ComprehensiveTestCase("D5-10", "兵马俑距离西安市区多远？怎么去？现在开放吗？",
             TestDimensionType.RESULT_QUALITY, "幻觉检测",
             ["search_knowledge"], min_tools=1,
             expected_keywords=["兵马俑", "公里", "临潼"],
             forbidden_keywords=[],
             description="事实性知识回答，应准确提供距离和交通信息"),
]


# ============================================================================
#  维度六：稳定性和鲁棒性 (10条) —— 模糊表达/异常情况/主动追问
# ============================================================================
DIM6_CASES = [
    # D6-1: 模糊表达需追问 (3条)
    ComprehensiveTestCase("D6-01", "那个店有优惠吗？", TestDimensionType.STABILITY,
             "模糊表达",
             [], min_tools=0, is_ambiguous=True, should_ask_question=True,
             expected_keywords=["哪家", "哪个", "具体"],
             description="模糊指代'那个店'，应主动追问"),
    ComprehensiveTestCase("D6-02", "给我推荐一下", TestDimensionType.STABILITY,
             "模糊表达",
             [], min_tools=0, is_ambiguous=True, should_ask_question=True,
             expected_keywords=["什么", "类型", "需求"],
             description="无上下文推荐，应追问用户需求"),
    ComprehensiveTestCase("D6-03", "这附近有什么好吃的", TestDimensionType.STABILITY,
             "模糊表达",
             [], min_tools=0, is_ambiguous=True, should_ask_question=True,
             expected_keywords=["位置", "哪里", "附近"],
             description="缺少位置信息，应追问具体位置"),

    # D6-2: 异常场景 (2条)
    ComprehensiveTestCase("D6-04", "搜一下'xyz不存在的店12345678'", TestDimensionType.STABILITY,
             "空结果处理", ["search_shops_by_name"], min_tools=1, is_error_scenario=True,
             expected_keywords=["找不到", "没有"],
             forbidden_keywords=[],
             description="不存在店铺，应友好提示而非报错"),
    ComprehensiveTestCase("D6-05", "帮我查店铺id=99999的优惠券", TestDimensionType.STABILITY,
             "无效ID处理", ["list_vouchers", "get_shop_detail"], min_tools=1, is_error_scenario=True,
             expected_keywords=["找不到", "不存在", "无"],
             forbidden_keywords=[],
             description="不存在的店铺ID，应优雅处理"),

    # D6-3: 边界输入 (2条)
    ComprehensiveTestCase("D6-06", "火祸店", TestDimensionType.STABILITY,
             "错别字处理", ["list_shops_by_type"], min_tools=1, is_error_scenario=True,
             description="错别字'祸→锅'，应能容错或提示"),
    ComprehensiveTestCase("D6-07", "今天天气怎么样？", TestDimensionType.STABILITY,
             "超范围查询", [], min_tools=0, is_error_scenario=True,
             expected_keywords=["天气", "无法"],
             description="超出能力范围的问题，应明确告知而非编造"),

    # D6-4: 极端输入 (3条)
    ComprehensiveTestCase("D6-08", "", TestDimensionType.STABILITY,
             "空输入", [], min_tools=0, is_error_scenario=True,
             expected_keywords=[""],
             description="空字符串输入，不应崩溃"),
    ComprehensiveTestCase("D6-09", "abcdefghijklmnopqrstuvwxyz1234567890!@#$%^&*()", TestDimensionType.STABILITY,
             "乱码输入", [], min_tools=0, is_error_scenario=True,
             expected_keywords=["无法", "理解"],
             description="无意义随机字符串，应友好提示而非崩溃"),
    ComprehensiveTestCase("D6-10", "大雁塔大雁塔大雁塔大雁塔大雁塔大雁塔大雁塔大雁塔大雁塔大雁塔大雁塔",
             TestDimensionType.STABILITY, "重复输入",
             ["search_knowledge"], min_tools=1, is_error_scenario=True,
             expected_keywords=["大雁塔"],
             description="极度重复输入，应正常去重查询而非崩溃"),
]


# 合并全部测试用例（按维度组织）
ALL_CASES = DIM1_CASES + DIM2_CASES + DIM3_CASES + DIM4_CASES + DIM5_CASES + DIM6_CASES
DIMENSION_MAP = {
    TestDimensionType.TASK_COMPLETION: ("维度一：任务完成度", DIM1_CASES),
    TestDimensionType.PLANNING: ("维度二：规划能力", DIM2_CASES),
    TestDimensionType.TOOL_CALLING: ("维度三：工具调用能力", DIM3_CASES),
    TestDimensionType.EXECUTION_TRAJECTORY: ("维度四：执行轨迹", DIM4_CASES),
    TestDimensionType.RESULT_QUALITY: ("维度五：结果质量", DIM5_CASES),
    TestDimensionType.STABILITY: ("维度六：稳定性与鲁棒性", DIM6_CASES),
}


# ============================================================================
#  六维度评测数据结构
# ============================================================================

@dataclass
class ToolCallRecord:
    """单次工具调用记录"""
    round_num: int
    call_index: int
    tool_name: str
    tool_args: dict
    result_preview: str = ""
    is_error: bool = False
    latency_ms: float = 0.0


@dataclass
class SingleCaseResult:
    """单个用例的完整评测结果"""
    case_id: str
    query: str
    dimension: str
    sub_category: str
    round_results: List[dict] = field(default_factory=list)
    tool_call_records: List[ToolCallRecord] = field(default_factory=list)

    # 六维度评分
    dim1_score: float = 0.0   # 任务完成度 0-1
    dim2_score: float = 0.0   # 规划能力 0-1
    dim3_score: float = 0.0   # 工具调用 0-1
    dim4_score: float = 0.0   # 执行轨迹 0-1
    dim5_score: float = 0.0   # 结果质量 0-1
    dim6_score: float = 0.0   # 稳定性 0-1

    # 综合指标
    success: bool = False
    avg_latency_ms: float = 0.0
    total_tool_calls: int = 0
    redundant_calls: int = 0
    final_answer: str = ""
    llm_judge_faithfulness: float = 0.0
    llm_judge_completeness: float = 0.0


@dataclass
class ComprehensiveReport:
    """六维度综合评测报告"""
    test_time: str
    env: dict
    total_cases: int
    total_queries: int

    # 维度汇总
    dim1_summary: dict = field(default_factory=dict)
    dim2_summary: dict = field(default_factory=dict)
    dim3_summary: dict = field(default_factory=dict)
    dim4_summary: dict = field(default_factory=dict)
    dim5_summary: dict = field(default_factory=dict)
    dim6_summary: dict = field(default_factory=dict)

    # 全局延迟
    all_latencies: List[float] = field(default_factory=list)
    all_tool_counts: List[int] = field(default_factory=list)

    # 综合评分
    overall_score: float = 0.0
    case_results: List[SingleCaseResult] = field(default_factory=list)

    # 缓存统计
    cache_stats: dict = field(default_factory=dict)


# ============================================================================
#  LLM Judge 提示词 —— 用于维度五结果质量评估
# ============================================================================

FAITHFULNESS_JUDGE_PROMPT = """你是一个严格的AI回答质量评估者。

请评估以下AI回答是否**忠于工具返回的数据**，不存在编造。

【用户问题】
{question}

【AI回答】
{answer}

【工具返回的数据摘要】
{tool_results}

评分标准（1-5分）：
- 1分：回答完全编造，与工具数据毫无关系
- 2分：回答大部分编造，只有零星信息来自工具数据
- 3分：回答部分基于工具数据，但存在明显添油加醋
- 4分：回答基本忠于工具数据，有极少数轻微不准确
- 5分：回答完全忠实于工具数据，没有任何编造或歪曲

请严格只输出一个数字（1-5），不要输出其他任何内容："""

COMPLETENESS_JUDGE_PROMPT = """你是一个严格的AI回答完整性评估者。

请评估以下AI回答是否**完整覆盖了用户问题的所有方面**。

【用户问题】
{question}

【AI回答】
{answer}

用户问题的需求要点：
{requirements}

评分标准（1-5分）：
- 1分：回答与问题完全无关
- 2分：回答只涉及了极少部分需求，遗漏了大量关键信息
- 3分：回答覆盖了部分需求，但遗漏了一些重要信息
- 4分：回答基本完整，只遗漏了少量次要信息
- 5分：回答完全覆盖了用户的所有需求，信息完整

请严格只输出一个数字（1-5），不要输出其他任何内容："""


# ============================================================================
#  核心执行引擎
# ============================================================================

def percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return sorted_values[f]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


async def call_llm_judge(prompt: str) -> float:
    """调用LLM打分，返回1-5的浮点数"""
    import re
    from app.services.llm import chat_sync
    try:
        resp = chat_sync(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=60.0,
        )
        match = re.search(r"([1-5])", resp.strip())
        if match:
            return float(match.group(1))
        return 0.0
    except Exception:
        return 0.0


async def run_single_case(tc: ComprehensiveTestCase, round_num: int) -> SingleCaseResult:
    """执行单个测试用例，返回完整的六维度评测结果"""
    from app.agent.graph import get_agent, RECURSION_LIMIT
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

    result = SingleCaseResult(
        case_id=tc.id,
        query=tc.query,
        dimension=tc.dimension.value,
        sub_category=tc.sub_category,
    )

    messages = [HumanMessage(content=tc.query)]
    t0 = time.perf_counter()

    try:
        import asyncio as _asyncio
        agent_result = await _asyncio.wait_for(
            get_agent().ainvoke(
                {"messages": messages},
                config={"recursion_limit": RECURSION_LIMIT},
            ),
            timeout=300.0,  # 单用例最多5分钟
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        result.avg_latency_ms = elapsed_ms

        msgs = agent_result.get("messages", [])
        tool_call_records = []
        round_idx = 0
        call_idx = 0
        final_answer = ""

        for m in msgs:
            if isinstance(m, AIMessage) and hasattr(m, "tool_calls") and m.tool_calls:
                round_idx += 1
                for tc_data in m.tool_calls:
                    call_idx += 1
                    tool_call_records.append(ToolCallRecord(
                        round_num=round_idx,
                        call_index=call_idx,
                        tool_name=tc_data.get("name", "unknown"),
                        tool_args=tc_data.get("args", {}),
                    ))
            elif isinstance(m, AIMessage) and m.content:
                final_answer = m.content if isinstance(m.content, str) else str(m.content)
            elif isinstance(m, ToolMessage):
                if tool_call_records:
                    tool_call_records[-1].result_preview = (m.content or "")[:200]
                    if '"error"' in str(m.content or ""):
                        tool_call_records[-1].is_error = True

        result.tool_call_records = tool_call_records
        result.final_answer = final_answer
        result.total_tool_calls = len(tool_call_records)
        result.success = bool(final_answer)

        # ============================================
        # 维度一：任务完成度评分
        # ============================================
        dim1 = _score_task_completion(tc, tool_call_records, result.final_answer)
        result.dim1_score = dim1

        # ============================================
        # 维度二：规划能力评分
        # ============================================
        dim2 = _score_planning(tc, tool_call_records)
        result.dim2_score = dim2

        # ============================================
        # 维度三：工具调用能力评分
        # ============================================
        dim3 = _score_tool_calling(tc, tool_call_records)
        result.dim3_score = dim3

        # ============================================
        # 维度四：执行轨迹评分
        # ============================================
        dim4, redundant = _score_execution_trajectory(tc, tool_call_records)
        result.dim4_score = dim4
        result.redundant_calls = redundant

        # ============================================
        # 维度五：结果质量评分（LLM judge）
        # ============================================
        dim5 = await _score_result_quality_llm(tc, tool_call_records, result.final_answer)
        result.dim5_score = dim5

        # ============================================
        # 维度六：稳定性评分
        # ============================================
        dim6 = _score_stability(tc, tool_call_records, result.final_answer)
        result.dim6_score = dim6

        result.round_results.append({
            "round": round_num,
            "success": result.success,
            "latency_ms": result.avg_latency_ms,
            "tool_calls": result.total_tool_calls,
            "redundant_calls": result.redundant_calls,
            "dim1": dim1,
            "dim2": dim2,
            "dim3": dim3,
            "dim4": dim4,
            "dim5": dim5,
            "dim6": dim6,
        })

    except Exception as e:
        import traceback
        result.avg_latency_ms = (time.perf_counter() - t0) * 1000
        result.success = False
        result.final_answer = f"[Error] {str(e)}"
        result.round_results.append({
            "round": round_num,
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "latency_ms": result.avg_latency_ms,
        })
        print(f"\n    !!! CASE ERROR: {tc.id} - {type(e).__name__}: {e}", flush=True)

    return result


# ============================================================================
#  六维度打分函数
# ============================================================================

def _score_task_completion(tc: ComprehensiveTestCase, calls: List[ToolCallRecord], answer: str) -> float:
    """维度一：任务完成度 0-1 分

    评分逻辑：
    - 是否至少调用了工具？不要空口回答
    - 是否至少调用了 min_tools 个工具？
    - 最终回答是否非空？
    - 是否包含预期关键词（如果有要求）？
    - 是否不包含禁止关键词（如果有要求）？
    """
    if tc.min_tools == 0 and tc.is_ambiguous and tc.should_ask_question:
        # 模糊表达场景：不出错且有回应即可
        return 1.0 if answer else 0.0

    score = 0.0
    total_checks = 4.0

    # 检查1：有工具调用（非闲聊场景）
    if tc.min_tools > 0:
        if len(calls) > 0:
            score += 1.0
        # 检查2：最少工具数
        if len(calls) >= tc.min_tools:
            score += 1.0
    else:
        # 不需要工具的场景（如模糊表达追问），给满分
        score += 2.0

    # 检查3：有最终回答
    if answer:
        score += 1.0

    # 检查4：包含关键信息
    if tc.expected_keywords:
        matched = sum(1 for kw in tc.expected_keywords if kw.lower() in answer.lower())
        score += min(1.0, matched / max(1, len(tc.expected_keywords)))
    else:
        score += 1.0

    # 减分项：包含禁止关键词
    if tc.forbidden_keywords:
        penalty = sum(1 for kw in tc.forbidden_keywords if kw.lower() in answer.lower())
        score = max(0, score - penalty * 0.5)

    return min(1.0, score / total_checks)


def _score_planning(tc: ComprehensiveTestCase, calls: List[ToolCallRecord]) -> float:
    """维度二：规划能力 0-1 分

    评分逻辑：
    - 实际步骤数是否 >= 预期最小步骤数？
    - 步骤顺序是否正确（如果指定了 expected_step_order）？
    - 是否有必要的依赖步骤（如先搜店铺再查优惠券）？
    """
    if not tc.expected_step_order and tc.expected_min_steps == 0:
        return 0.8  # 无规划要求，默认良好

    score = 0.0
    total_checks = 3.0

    actual_tools = [c.tool_name for c in calls]
    unique_rounds = len(set(c.round_num for c in calls)) if calls else 0

    # 检查1：步骤数足够
    if unique_rounds >= tc.expected_min_steps:
        score += 1.0
    elif unique_rounds > 0:
        score += unique_rounds / max(1, tc.expected_min_steps)

    # 检查2：步骤顺序正确
    if tc.expected_step_order:
        expected = tc.expected_step_order
        order_correct = True
        last_idx = -1
        for tool_name in expected:
            if tool_name in actual_tools:
                idx = actual_tools.index(tool_name)
                if idx < last_idx:
                    order_correct = False
                    break
                last_idx = idx
        if order_correct:
            score += 1.0
    else:
        score += 0.5  # 无顺序要求

    # 检查3：依赖关系正确（先店铺后优惠券的逻辑）
    has_shop_search = any(t in actual_tools for t in ["search_shops_by_name", "list_shops_by_type"])
    has_voucher = "list_vouchers" in actual_tools
    if has_shop_search and has_voucher:
        # 店铺搜索应在优惠券查询之前（同一轮或更早的轮）
        shop_rounds = [c.round_num for c in calls if c.tool_name in ["search_shops_by_name", "list_shops_by_type"]]
        voucher_rounds = [c.round_num for c in calls if c.tool_name == "list_vouchers"]
        if shop_rounds and voucher_rounds:
            if min(shop_rounds) <= min(voucher_rounds):
                score += 1.0
            else:
                score += 0.0
        else:
            score += 0.5
    else:
        score += 1.0

    return min(1.0, score / total_checks)


def _score_tool_calling(tc: ComprehensiveTestCase, calls: List[ToolCallRecord]) -> float:
    """维度三：工具调用能力 0-1 分

    评分逻辑：
    - 是否选择了正确的工具？
    - 参数是否合理（非空/格式正确）？
    - 是否避免了调用不相关的工具？
    """
    if not calls:
        if tc.min_tools == 0:
            return 1.0
        return 0.0

    score = 0.0
    total_checks = 3.0

    actual_tools = [c.tool_name for c in calls]

    # 检查1：正确工具被调用
    if tc.expected_tools:
        matched = sum(1 for t in tc.expected_tools if t in actual_tools)
        score += min(1.0, matched / max(1, len(tc.expected_tools)))

    # 检查2：参数非空
    valid_args = sum(1 for c in calls if c.tool_args and len(c.tool_args) > 0)
    if calls:
        score += valid_args / len(calls)

    # 检查3：避免调用完全无关的工具
    KNOWN_TOOLS = ["search_shops_by_name", "list_shops_by_type", "get_shop_detail",
                   "list_vouchers", "search_blogs", "search_knowledge"]
    irrelevant = sum(1 for t in actual_tools if t not in KNOWN_TOOLS)
    score += max(0, 1.0 - irrelevant * 0.3)

    return min(1.0, score / total_checks)


def _score_execution_trajectory(tc: ComprehensiveTestCase, calls: List[ToolCallRecord]) -> Tuple[float, int]:
    """维度四：执行轨迹 0-1 分 + 冗余调用数

    评分逻辑：
    - 是否存在完全相同的重复工具调用？
    - 是否存在语义重复的工具调用？
    - 同一轮内重复/冗余调用越多分越低
    """
    if not calls:
        return (1.0 if tc.min_tools == 0 else 0.3, 0)

    redundant = 0
    score = 1.0

    # 检测1：完全相同的工具+参数
    seen = set()
    for c in calls:
        key = (c.tool_name, json.dumps(c.tool_args, sort_keys=True))
        if key in seen:
            redundant += 1
            score -= 0.2
        seen.add(key)

    # 检测2：同一工具被调用超过合理次数（如 search_shops_by_name 被调3次以上）
    tool_counts = {}
    for c in calls:
        tool_counts[c.tool_name] = tool_counts.get(c.tool_name, 0) + 1
    for name, count in tool_counts.items():
        if name in ["search_shops_by_name", "list_shops_by_type"] and count > 2:
            redundant += (count - 2)
            score -= 0.1 * (count - 2)
        elif count > 3:
            redundant += (count - 3)
            score -= 0.1 * (count - 3)

    # 检测3：同一轮内多次调用同一工具（可能冗余）
    round_tools = {}
    for c in calls:
        key = (c.round_num, c.tool_name)
        round_tools[key] = round_tools.get(key, 0) + 1
    for (r, name), count in round_tools.items():
        if count > 1:
            redundant += (count - 1)
            score -= 0.15 * (count - 1)

    return (max(0.0, min(1.0, score)), redundant)


async def _score_result_quality_llm(tc: ComprehensiveTestCase, calls: List[ToolCallRecord], answer: str) -> float:
    """维度五：结果质量 —— LLM judge 综合评分 0-1 分

    评分逻辑：
    - 调用LLM judge评估忠实度（faithfulness）
    - 调用LLM judge评估完整性（completeness）
    - 综合两者得分
    """
    if not answer:
        return 0.0

    # 汇总工具返回数据作为judge参考
    tool_results = []
    for c in calls:
        if c.result_preview:
            tool_results.append(f"[{c.tool_name}] {c.result_preview[:300]}")

    if not tool_results:
        # 没有工具调用但有回答（AI直接回答），不需要LLM judge
        return 0.3  # 基线：无工具调用但有回答

    tool_results_str = "\n\n".join(tool_results)

    # 忠实度judge
    faith_prompt = FAITHFULNESS_JUDGE_PROMPT.format(
        question=tc.query,
        answer=answer[:2000],
        tool_results=tool_results_str[:3000],
    )
    faithfulness = await call_llm_judge(faith_prompt)

    # 完整性judge
    requirements = "、".join(tc.expected_keywords) if tc.expected_keywords else "回答应覆盖用户问题的核心需求"
    comp_prompt = COMPLETENESS_JUDGE_PROMPT.format(
        question=tc.query,
        answer=answer[:2000],
        requirements=requirements,
    )
    completeness = await call_llm_judge(comp_prompt)

    # 综合：忠实度60% + 完整性40%
    if faithfulness > 0 and completeness > 0:
        return (faithfulness / 5.0 * 0.6 + completeness / 5.0 * 0.4)
    elif faithfulness > 0:
        return faithfulness / 5.0
    elif completeness > 0:
        return completeness / 5.0
    return 0.3


def _score_stability(tc: ComprehensiveTestCase, calls: List[ToolCallRecord], answer: str) -> float:
    """维度六：稳定性与鲁棒性 0-1 分

    评分逻辑：
    - 模糊表达场景：是否主动追问？
    - 错误场景：是否优雅处理而非崩溃？
    - 是否出现异常（tool error）？
    """
    score = 1.0

    if tc.is_ambiguous and tc.should_ask_question:
        # 模糊表达：检查是否主动追问
        question_kws = ["哪家", "哪个", "什么", "具体", "位置", "需求", "请问"]
        has_question = any(kw in answer for kw in question_kws) if answer else False
        # 如果回答很短且有追问意向（问号结尾）
        has_question_mark = answer.strip().endswith("？") or answer.strip().endswith("?") if answer else False
        return 1.0 if (has_question or has_question_mark) else 0.4

    if tc.is_error_scenario:
        # 异常场景：检查是否优雅处理
        crash_kws = ["error", "exception", "错误", "异常", "崩溃"]
        has_crash = any(kw in answer.lower() for kw in crash_kws) if answer else False
        if has_crash:
            return 0.2
        # 检查是否给出了友好提示
        friendly_kws = ["暂时", "建议", "试试", "其他", "找不到", "没有", "不存在"]
        has_friendly = any(kw in answer for kw in friendly_kws) if answer else False
        if has_friendly:
            return 1.0
        # 至少没有崩溃，给了回答
        return 0.7 if answer else 0.0

    # 正常场景：检查是否有工具错误
    tool_errors = sum(1 for c in calls if c.is_error)
    if tool_errors > 0:
        score -= 0.2 * tool_errors
    # 有回答且无错误
    return max(0.0, score) if answer else 0.0


# ============================================================================
#  报告生成
# ============================================================================

def collect_cache_stats() -> dict:
    """收集三级缓存的命中率统计。

    注意：缓存实例在 app.agent.cache 中为模块级全局变量，
    每次 agent 调用都会在其中累积 hits/misses。
    需要在所有测试执行完毕后调用，确保统计完整。
    """
    try:
        from app.agent.cache import voucher_cache, shop_cache, vector_cache
        stats = {
            "voucher_cache": voucher_cache.get_stats(),
            "shop_cache": shop_cache.get_stats(),
            "vector_cache": vector_cache.get_stats(),
        }

        total_hits = sum(s["hits"] for s in stats.values())
        total_misses = sum(s["misses"] for s in stats.values())
        total_requests = total_hits + total_misses
        total_hit_rate = (total_hits / total_requests * 100) if total_requests > 0 else 0

        stats["_summary"] = {
            "total_hits": total_hits,
            "total_misses": total_misses,
            "total_requests": total_requests,
            "overall_hit_rate": round(total_hit_rate, 1),
        }

        return stats
    except Exception as e:
        return {"error": str(e)}


def generate_report(results: List[SingleCaseResult], verbose: bool = True) -> ComprehensiveReport:
    """生成六维度综合评测报告"""
    report = ComprehensiveReport(
        test_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        env={
            "model": os.getenv("DEEPSEEK_MODEL", "unknown"),
            "total_tools": 6,
            "total_cases": len(results),
        },
        total_cases=len(results),
        total_queries=len(results),
    )

    # 按维度分类汇总
    dim_map = {d.value: [] for d in TestDimensionType}
    for r in results:
        dim_map[r.dimension].append(r)
        report.all_latencies.append(r.avg_latency_ms)
        report.all_tool_counts.append(r.total_tool_calls)

    for dim_type, (dim_name, dim_cases) in DIMENSION_MAP.items():
        cases = dim_map[dim_type.value]
        if not cases:
            continue

        dim1_scores = [c.dim1_score for c in cases]
        dim2_scores = [c.dim2_score for c in cases]
        dim3_scores = [c.dim3_score for c in cases]
        dim4_scores = [c.dim4_score for c in cases]
        dim5_scores = [c.dim5_score for c in cases]
        dim6_scores = [c.dim6_score for c in cases]

        success_rate = sum(1 for c in cases if c.success) / len(cases) * 100 if cases else 0
        avg_latency = statistics.mean([c.avg_latency_ms for c in cases]) if cases else 0
        total_redundant = sum(c.redundant_calls for c in cases)

        summary = {
            "name": dim_name,
            "total": len(cases),
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "avg_dim1": statistics.mean(dim1_scores) if dim1_scores else 0,
            "avg_dim2": statistics.mean(dim2_scores) if dim2_scores else 0,
            "avg_dim3": statistics.mean(dim3_scores) if dim3_scores else 0,
            "avg_dim4": statistics.mean(dim4_scores) if dim4_scores else 0,
            "avg_dim5": statistics.mean(dim5_scores) if dim5_scores else 0,
            "avg_dim6": statistics.mean(dim6_scores) if dim6_scores else 0,
            "total_redundant_calls": total_redundant,
            "cases": cases,
        }

        if dim_type == TestDimensionType.TASK_COMPLETION:
            report.dim1_summary = summary
        elif dim_type == TestDimensionType.PLANNING:
            report.dim2_summary = summary
        elif dim_type == TestDimensionType.TOOL_CALLING:
            report.dim3_summary = summary
        elif dim_type == TestDimensionType.EXECUTION_TRAJECTORY:
            report.dim4_summary = summary
        elif dim_type == TestDimensionType.RESULT_QUALITY:
            report.dim5_summary = summary
        elif dim_type == TestDimensionType.STABILITY:
            report.dim6_summary = summary

    report.case_results = results

    # 综合评分
    all_dim_scores = []
    for summary in [report.dim1_summary, report.dim2_summary, report.dim3_summary,
                    report.dim4_summary, report.dim5_summary, report.dim6_summary]:
        if summary:
            dim_avg = (summary.get("avg_dim1", 0) + summary.get("avg_dim2", 0) +
                       summary.get("avg_dim3", 0) + summary.get("avg_dim4", 0) +
                       summary.get("avg_dim5", 0) + summary.get("avg_dim6", 0)) / 6
            all_dim_scores.append(dim_avg)

    report.overall_score = statistics.mean(all_dim_scores) * 100 if all_dim_scores else 0

    return report


def print_comprehensive_report(report: ComprehensiveReport):
    """打印六维度综合评测报告"""
    print("\n")
    print("█" * 80)
    print("█  Agent 六维度综合评测报告 V1.0")
    print("█  任务完成度 → 过程合理性 → 系统稳定性")
    print("█" * 80)
    print(f"█  测试时间: {report.test_time}  |  模型: {report.env['model']}")
    print(f"█  总用例数: {report.total_cases}  |  总查询数: {report.total_queries}")
    print("█" * 80)

    summaries = [
        ("维度一：任务完成度", report.dim1_summary),
        ("维度二：规划能力", report.dim2_summary),
        ("维度三：工具调用能力", report.dim3_summary),
        ("维度四：执行轨迹", report.dim4_summary),
        ("维度五：结果质量", report.dim5_summary),
        ("维度六：稳定性与鲁棒性", report.dim6_summary),
    ]

    all_scores = []

    for dim_name, summary in summaries:
        if not summary:
            continue

        cases = summary.get("cases", [])
        if not cases:
            continue

        # 计算该维度的综合得分（各维度该维度关心的主要指标）
        if "任务完成度" in dim_name:
            main_score = summary["avg_dim1"] * 100
        elif "规划能力" in dim_name:
            main_score = summary["avg_dim2"] * 100
        elif "工具调用" in dim_name:
            main_score = summary["avg_dim3"] * 100
        elif "执行轨迹" in dim_name:
            main_score = summary["avg_dim4"] * 100
        elif "结果质量" in dim_name:
            main_score = summary["avg_dim5"] * 100
        elif "稳定性" in dim_name:
            main_score = summary["avg_dim6"] * 100
        else:
            main_score = 0

        all_scores.append(main_score)

        bar = "█" * int(main_score / 5) + "░" * (20 - int(main_score / 5))
        print(f"\n  ┌─ {dim_name} ─{'─'*50}")
        print(f"  │  得分: {main_score:5.1f}/100 [{bar}]")
        print(f"  │  用例数: {summary['total']}  |  成功率: {summary['success_rate']:.1f}%  |  平均延迟: {summary['avg_latency_ms']:.0f}ms")
        print(f"  │  冗余调用: {summary['total_redundant_calls']} 次")

        # 各子维度得分
        print(f"  │  子维度: D1={summary['avg_dim1']*100:.0f} D2={summary['avg_dim2']*100:.0f} "
              f"D3={summary['avg_dim3']*100:.0f} D4={summary['avg_dim4']*100:.0f} "
              f"D5={summary['avg_dim5']*100:.0f} D6={summary['avg_dim6']*100:.0f}")

        # 详细用例结果
        print(f"  │  {'─'*62}")
        for c in cases:
            status = "✓" if c.success else "✗"
            print(f"  │  {status} {c.case_id} {c.query[:36]:36s} "
                  f"D1={c.dim1_score*100:3.0f} D2={c.dim2_score*100:3.0f} D3={c.dim3_score*100:3.0f} "
                  f"D4={c.dim4_score*100:3.0f} D5={c.dim5_score*100:3.0f} D6={c.dim6_score*100:3.0f} "
                  f"冗余={c.redundant_calls}")

        # 维度评价
        print(f"  │  ")
        if main_score >= 85:
            print(f"  │  评价: ✓ 卓越 —— 该维度表现优秀")
        elif main_score >= 70:
            print(f"  │  评价: △ 良好 —— 该维度基本达标，偶有不足")
        elif main_score >= 50:
            print(f"  │  评价: ⚠ 需改进 —— 该维度存在明显短板")
        else:
            print(f"  │  评价: ✗ 不合格 —— 该维度需要重点修复")
        print(f"  └{'─'*63}")

    # 全局延迟统计
    sorted_lats = sorted(report.all_latencies)
    p50 = percentile(sorted_lats, 0.50)
    p95 = percentile(sorted_lats, 0.95)
    avg_lat = statistics.mean(report.all_latencies) if report.all_latencies else 0
    avg_tools = statistics.mean(report.all_tool_counts) if report.all_tool_counts else 0

    print(f"\n  {'█'*80}")
    print(f"  █  全局统计")
    print(f"  {'█'*80}")
    print(f"  P50延迟: {p50:.0f}ms  |  P95延迟: {p95:.0f}ms  |  平均延迟: {avg_lat:.0f}ms")
    print(f"  平均工具调用: {avg_tools:.1f}次/查询  |  总冗余调用: {sum(s['total_redundant_calls'] for _, s in summaries if s)}次")

    # 缓存统计
    cache_stats = report.cache_stats or {}
    if cache_stats and "_summary" in cache_stats:
        cs = cache_stats["_summary"]
        print(f"\n  {'█'*80}")
        print(f"  █  缓存统计（三级 TTL+LRU：voucher 5s / shop 30s / vector 5min）")
        print(f"  {'█'*80}")
        print(f"  总缓存请求: {cs['total_requests']}  |  命中: {cs['total_hits']}  |  未命中: {cs['total_misses']}")
        print(f"  🔥 缓存命中率: {cs['overall_hit_rate']}%")
        for cache_name, cache_info in cache_stats.items():
            if cache_name.startswith("_"):
                continue
            print(f"    {cache_name}: hit_rate={cache_info['hit_rate']} hits={cache_info['hits']} misses={cache_info['misses']} size={cache_info['size']} ttl={cache_info['ttl']}s")

    # 综合评分
    print(f"\n  {'█'*80}")
    print(f"  █  综合评分")
    print(f"  {'█'*80}")
    for i, (dim_name, summary) in enumerate(summaries):
        if not summary:
            continue
        score = all_scores[i] if i < len(all_scores) else 0
        bar = "█" * int(score / 5) + "░" * (20 - int(score / 5))
        print(f"  {dim_name}: {score:5.1f}/100 [{bar}]")

    overall = statistics.mean(all_scores) if all_scores else 0
    print(f"\n  {'─'*50}")
    print(f"  🎯 综合评分: {overall:.1f}/100")

    if overall >= 85:
        print(f"  🏆 总评: 卓越 —— 六维度全部达标，生产就绪")
    elif overall >= 70:
        print(f"  👍 总评: 良好 —— 核心指标达标，建议持续优化")
    elif overall >= 55:
        print(f"  ⚠️  总评: 一般 —— 存在明显短板需改进")
    else:
        print(f"  ❌ 总评: 不合格 —— 多维度不达标，需重点修复")

    # 改进建议
    print(f"\n  {'─'*50}")
    print(f"  📋 改进建议:")
    suggestions = []
    for i, (dim_name, summary) in enumerate(summaries):
        if not summary:
            continue
        score = all_scores[i] if i < len(all_scores) else 0
        if score < 60:
            if "任务完成度" in dim_name:
                suggestions.append(f"    - 任务完成度低：检查工具绑定是否正确，prompt是否强制要求调工具")
            elif "规划能力" in dim_name:
                suggestions.append(f"    - 规划能力不足：增强prompt中的步骤拆解引导，添加few-shot示例")
            elif "工具调用" in dim_name:
                suggestions.append(f"    - 工具选择不准：优化工具描述，添加更精确的触发条件")
            elif "执行轨迹" in dim_name:
                suggestions.append(f"    - 冗余调用过多：在prompt中增加\"避免重复查询\"规则")
            elif "结果质量" in dim_name:
                suggestions.append(f"    - 回答质量差：参考LLM judge反馈优化回答模板")
            elif "稳定性" in dim_name:
                suggestions.append(f"    - 鲁棒性不足：完善模糊表达追问逻辑，增强异常处理prompt")
    if suggestions:
        for s in suggestions:
            print(s)
    else:
        print("    ✓ 各维度均无明显短板，保持当前质量即可")

    print(f"  {'█'*80}\n")


def export_json_report(report: ComprehensiveReport, filepath: str):
    """导出JSON格式详细报告"""
    
    def _summary_without_cases(s: dict) -> dict:
        """排除 cases 字段避免 JSON 序列化错误"""
        if not s:
            return {}
        return {k: v for k, v in s.items() if k != "cases"}
    
    data = {
        "meta": {
            "test_time": report.test_time,
            "version": "1.0",
            "framework": "六维度综合评测：任务完成度→过程合理性→系统稳定性",
        },
        "environment": report.env,
        "global_stats": {
            "total_cases": report.total_cases,
            "total_queries": report.total_queries,
            "overall_score": report.overall_score,
        },
        "dimensions": {
            "dim1_task_completion": _summary_without_cases(report.dim1_summary),
            "dim2_planning": _summary_without_cases(report.dim2_summary),
            "dim3_tool_calling": _summary_without_cases(report.dim3_summary),
            "dim4_execution_trajectory": _summary_without_cases(report.dim4_summary),
            "dim5_result_quality": _summary_without_cases(report.dim5_summary),
            "dim6_stability": _summary_without_cases(report.dim6_summary),
        },
        "cache_stats": report.cache_stats,
        "case_details": [
            {
                "case_id": c.case_id,
                "query": c.query,
                "dimension": c.dimension,
                "sub_category": c.sub_category,
                "success": c.success,
                "avg_latency_ms": c.avg_latency_ms,
                "total_tool_calls": c.total_tool_calls,
                "redundant_calls": c.redundant_calls,
                "scores": {
                    "dim1_task_completion": c.dim1_score,
                    "dim2_planning": c.dim2_score,
                    "dim3_tool_calling": c.dim3_score,
                    "dim4_execution_trajectory": c.dim4_score,
                    "dim5_result_quality": c.dim5_score,
                    "dim6_stability": c.dim6_score,
                },
                "final_answer": c.final_answer[:500] if c.final_answer else "",
            }
            for c in report.case_results
        ],
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  📄 JSON详细报告已导出: {filepath}")


# ============================================================================
#  主入口
# ============================================================================

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent 六维度综合评测")
    parser.add_argument("--full", action="store_true", help="2轮测试（含缓存验证），默认1轮")
    parser.add_argument("--rounds", type=int, default=1, help="自定义轮数")
    parser.add_argument("--skip-judge", action="store_true", help="跳过LLM judge（加速测试）")
    parser.add_argument("--dimension", type=str, default=None,
                        help="只测试指定维度: 1/2/3/4/5/6")
    args = parser.parse_args()

    # 根据参数选择测试用例
    if args.dimension:
        dim_map = {
            "1": DIM1_CASES, "2": DIM2_CASES, "3": DIM3_CASES,
            "4": DIM4_CASES, "5": DIM5_CASES, "6": DIM6_CASES,
        }
        test_cases = dim_map.get(args.dimension, ALL_CASES)
        print(f"\n只测试维度{args.dimension}：{len(test_cases)} 条用例")
    else:
        test_cases = ALL_CASES

    rounds = args.rounds if args.rounds > 1 else (2 if args.full else 1)

    print(f"\n{'='*80}")
    print(f"  Agent 六维度综合评测 V1.0")
    print(f"  设计原则: 任务完成度 → 过程合理性 → 系统稳定性")
    print(f"  总用例: {len(test_cases)} 条 × {rounds} 轮 = {len(test_cases)*rounds} 次查询")
    print(f"  预计耗时: {len(test_cases)*rounds*10//60} 分钟")
    print(f"  提示: --full   跑2轮验证缓存")
    print(f"        --dimension N  只测试第N维度")
    print(f"        --skip-judge   跳过LLM judge加速")
    print(f"{'='*80}")

    all_results = []

    for r in range(rounds):
        if rounds > 1:
            print(f"\n{'─'*80}")
            print(f"  ◈ 第 {r+1}/{rounds} 轮（{'冷启动' if r==0 else '缓存验证'}）")
            print(f"  {'─'*80}")

        for i, tc in enumerate(test_cases):
            progress = f"[{r+1}/{rounds}][{i+1:02d}/{len(test_cases):02d}]"
            print(f"  {progress} {tc.id} {tc.query[:48]:48s} ", end="", flush=True)

            result = await run_single_case(tc, r + 1)
            all_results.append(result)

            status = "✓" if result.success else "✗"
            dims = f"D1={result.dim1_score*100:.0f} D2={result.dim2_score*100:.0f} D3={result.dim3_score*100:.0f} " \
                   f"D4={result.dim4_score*100:.0f} D5={result.dim5_score*100:.0f} D6={result.dim6_score*100:.0f}"
            print(f"{status} {result.avg_latency_ms:7.0f}ms | 工具({result.total_tool_calls}) | {dims} | 冗余={result.redundant_calls}")

            await asyncio.sleep(0.5)

    # 生成报告
    report = generate_report(all_results, verbose=True)

    # 收集缓存统计
    report.cache_stats = collect_cache_stats()

    print_comprehensive_report(report)

    # 导出JSON报告
    json_path = Path(__file__).parent / f"agent_comprehensive_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    export_json_report(report, str(json_path))


if __name__ == "__main__":
    asyncio.run(main())