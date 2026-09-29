"""
Temperature 参数快速验证脚本
============================

使用方法：
1. 确保已安装依赖：pip install openai pydantic-settings
2. 修改下方的 API 配置（或确保 .env 文件存在）
3. 运行：python test_temperature_quick.py

测试内容：
- 5 个典型场景 × 3 个温度值（0.1, 0.3, 0.5）
- 统计工具调用准确率、平均轮数、总耗时
- 输出对比报告

预计耗时：2-5分钟（取决于API响应速度）
"""

import asyncio
import time
import json
from typing import Dict, List
from dataclasses import dataclass, field


# ==================== 配置区 ====================

# DeepSeek API 配置（从 .env 读取，或直接填入）
DEEPSEEK_API_KEY = ""  # 填入你的 API key，或留空自动从环境变量读取
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# 测试参数
TEST_TEMPERATURES = [0.1, 0.3, 0.2]  # 要测试的温度值列表

# 测试用例（覆盖典型场景）
TEST_CASES = [
    {
        "id": 1,
        "scenario": "简单分类查询",
        "query": "帮我找钟楼附近的火锅店",
        "expected_tools": ["list_shops_by_type"],
        "description": "应该调用 list_shops_by_type，传 type_name='美食' 和坐标",
    },
    {
        "id": 2,
        "scenario": "多步查询（先找店再查券）",
        "query": "老孙家火锅有没有优惠券？",
        "expected_tools": ["search_shops_by_name", "list_vouchers"],
        "description": "应该先搜索店铺名，再用 shop_id 查优惠券",
    },
    {
        "id": 3,
        "scenario": "模糊查询（关键词不明确）",
        "query": "附近有什么好吃又不贵的店？",
        "expected_tools": ["list_shops_by_type"],
        "description": "应该识别'好吃'→美食类型，调用 list_shops_by_type",
    },
    {
        "id": 4,
        "scenario": "知识库查询",
        "query": "西安回民街有什么特色小吃？",
        "expected_tools": ["search_knowledge"],
        "description": "这是知识型问题，应该查本地向量库",
    },
    {
        "id": 5,
        "scenario": "笔记/攻略查询",
        "query": "有没有人分享过大唐不夜城的游玩攻略？",
        "expected_tools": ["search_blogs"],
        "description": "应该搜索游记笔记",
    },
]


# ==================== 数据结构 ====================

@dataclass
class TestResult:
    """单次测试结果"""
    case_id: int
    query: str
    temperature: float
    actual_tools: List[str]
    expected_tools: List[str]
    rounds: int
    duration_seconds: float
    success: bool
    error_message: str = ""
    
    @property
    def accuracy(self) -> float:
        """计算工具调用准确率"""
        if not self.expected_tools:
            return 1.0
        correct = len(set(self.actual_tools) & set(self.expected_tools))
        return correct / len(self.expected_tools)


@dataclass
class TemperatureSummary:
    """某个温度值的汇总统计"""
    temperature: float
    total_cases: int = 0
    total_rounds: float = 0.0
    total_duration: float = 0.0
    accuracies: List[float] = field(default_factory=list)
    results: List[TestResult] = field(default_factory=list)
    
    @property
    def avg_accuracy(self) -> float:
        if not self.accuracies:
            return 0.0
        return sum(self.accuracies) / len(self.accuracies)
    
    @property
    def avg_rounds(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.total_rounds / self.total_cases
    
    @property
    def avg_duration(self) -> float:
        if self.total_cases == 0:
            return 0.0
        return self.total_duration / self.total_cases


# ==================== 核心逻辑 ====================

def get_client():
    """获取 OpenAI 兼容客户端"""
    from openai import OpenAI
    import os
    
    api_key = DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("请设置 DEEPSEEK_API_KEY 或在代码中填入 API Key")
    
    return OpenAI(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
    )


def build_tools_definition():
    """构建工具定义（简化版，只用于测试）"""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_shops_by_name",
                "description": "按名称关键字搜索平台店铺（模糊匹配）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "店铺名称关键字"}
                    },
                    "required": ["name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_shops_by_type",
                "description": "按分类查店铺列表；传坐标时按距离排序",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "type_name": {"type": "string", "description": "分类名（美食/KTV/酒吧等）"},
                        "x": {"type": "number", "description": "经度（可选）"},
                        "y": {"type": "number", "description": "纬度（可选）"}
                    },
                    "required": ["type_name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_shop_detail",
                "description": "查询店铺详情（评分/人均/营业时间等）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "shop_id": {"type": "integer", "description": "店铺 id"}
                    },
                    "required": ["shop_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_vouchers",
                "description": "实时查询店铺的优惠券（含库存/有效期）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "shop_id": {"type": "integer", "description": "店铺 id"}
                    },
                    "required": ["shop_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_blogs",
                "description": "搜索游记/笔记（本地向量库）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"}
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_knowledge",
                "description": "搜索知识库（本地向量库）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"}
                    },
                    "required": ["query"]
                }
            }
        },
    ]


SYSTEM_PROMPT = """你是「长安文旅探店助手」的智能 Agent。
你有以下工具可以使用：
- search_shops_by_name: 按名称搜店铺
- list_shops_by_type: 按分类查店铺（支持坐标排序）
- get_shop_detail: 查店铺详情
- list_vouchers: 查优惠券库存
- search_blogs: 搜游记笔记
- search_knowledge: 搜知识库

工作方法：
1. 分析用户需求，选择合适的工具
2. 工具失败时换一个工具或调整参数重试
3. 基于工具返回的数据回答

重要：你必须调用工具来获取实时数据，不能凭空编造。"""


def simulate_agent_call(client, query: str, temperature: float, max_rounds: int = 6):
    """
    模拟 Agent 的多轮工具调用过程
    返回：(actual_tools_list, total_rounds, success, error_msg)
    
    注意：这是一个简化版模拟，实际项目使用 LangGraph 的 StateGraph
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    
    tools = build_tools_definition()
    actual_tools = []
    
    for round_num in range(1, max_rounds + 1):
        try:
            # 调用 LLM
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=500,
                timeout=30,
            )
            
            choice = response.choices[0]
            message = choice.message
            
            # 检查是否有 tool_calls
            if message.tool_calls:
                tool_call = message.tool_calls[0]
                tool_name = tool_call.function.name
                actual_tools.append(tool_name)
                
                # 将 assistant 消息加入历史
                messages.append(message)
                
                # 模拟工具返回（简化版，返回固定格式）
                tool_result = {
                    "name": tool_name,
                    "content": json.dumps({
                        "status": "success",
                        "data": f"模拟的 {tool_name} 返回结果"
                    }, ensure_ascii=False)
                }
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result["content"]
                })
                
                # 继续下一轮
                continue
            else:
                # 没有 tool_calls，说明 LLM 决定直接回答
                return actual_tools, round_num, True, ""
        
        except Exception as e:
            return actual_tools, round_num, False, str(e)
    
    # 达到最大轮数
    return actual_tools, max_rounds, False, f"达到最大轮数 {max_rounds}"


async def run_single_test(client, case: dict, temperature: float) -> TestResult:
    """运行单个测试用例"""
    start_time = time.time()
    
    try:
        actual_tools, rounds, success, error = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: simulate_agent_call(client, case["query"], temperature)
        )
        
        duration = time.time() - start_time
        
        return TestResult(
            case_id=case["id"],
            query=case["query"],
            temperature=temperature,
            actual_tools=actual_tools,
            expected_tools=case["expected_tools"],
            rounds=rounds,
            duration_seconds=duration,
            success=success,
            error_message=error,
        )
        
    except Exception as e:
        duration = time.time() - start_time
        return TestResult(
            case_id=case["id"],
            query=case["query"],
            temperature=temperature,
            actual_tools=[],
            expected_tools=case["expected_tools"],
            rounds=0,
            duration_seconds=duration,
            success=False,
            error_message=str(e),
        )


async def run_all_tests() -> Dict[float, TemperatureSummary]:
    """运行所有测试"""
    print("=" * 80)
    print("🔬 Temperature 参数 A/B 测试")
    print("=" * 80)
    print(f"\n📋 测试配置：")
    print(f"   - 温度值：{TEST_TEMPERATURES}")
    print(f"   - 测试用例：{len(TEST_CASES)} 个")
    print(f"   - 模型：{DEEPSEEK_MODEL}")
    print()
    
    client = get_client()
    summaries: Dict[float, TemperatureSummary] = {t: TemperatureSummary(temperature=t) for t in TEST_TEMPERATURES}
    
    for temp in TEST_TEMPERATURES:
        print(f"\n{'='*80}")
        print(f"🌡️  测试 Temperature = {temp}")
        print(f"{'='*80}")
        
        summary = summaries[temp]
        
        for case in TEST_CASES:
            print(f"\n  📝 测试用例 {case['id']}: {case['scenario']}")
            print(f"     问题：{case['query']}")
            print(f"     预期工具：{case['expected_tools']}")
            
            result = await run_single_test(client, case, temp)
            summary.results.append(result)
            summary.total_cases += 1
            summary.total_rounds += result.rounds
            summary.total_duration += result.duration_seconds
            summary.accuracies.append(result.accuracy)
            
            status = "✅" if result.success else "❌"
            print(f"     {status} 实际工具：{result.actual_tools}")
            print(f"        准确率：{result.accuracy:.1%}")
            print(f"        轮数：{result.rounds}轮")
            print(f"        耗时：{result.duration_seconds:.2f}s")
            
            if result.error_message:
                print(f"        ⚠️  错误：{result.error_message}")
        
        # 打印该温度的汇总
        print(f"\n  📊 Temperature={temp} 汇总：")
        print(f"     平均准确率：{summary.avg_accuracy:.1%}")
        print(f"     平均轮数：{summary.avg_rounds:.2f}轮")
        print(f"     平均耗时：{summary.avg_duration:.2f}s")
    
    return summaries


def generate_report(summaries: Dict[float, TemperatureSummary]):
    """生成测试报告"""
    print("\n\n")
    print("=" * 80)
    print("📊 最终测试报告")
    print("=" * 80)
    
    print("\n┌─────────────┬────────────┬──────────┬──────────┬────────────┐")
    print("│ Temperature │ 准确率     │ 平均轮数 │ 平均耗时 │ 排名       │")
    print("├─────────────┼────────────┼──────────┼──────────┼────────────┤")
    
    # 按综合得分排序（这里简化为按准确率排序）
    ranked = sorted(
        summaries.values(),
        key=lambda s: (s.avg_accuracy, -s.avg_rounds),
        reverse=True
    )
    
    for rank, summary in enumerate(ranked, 1):
        marker = " ⭐" if rank == 1 else ""
        print(f"│ {summary.temperature:<11} │ {summary.avg_accuracy:>9.1%} │ "
              f"{summary.avg_rounds:>8.2f} │ {summary.avg_duration:>8.2f}s │ "
              f"第{rank}名{marker:<4} │")
    
    print("└─────────────┴────────────┴──────────┴──────────┴────────────┘")
    
    # 推荐结论
    best = ranked[0]
    print(f"\n🎯 推荐结论：")
    print(f"   最佳温度值：**{best.temperature}**")
    print(f"   - 准确率：{best.avg_accuracy:.1%}")
    print(f"   - 平均轮数：{best.avg_rounds:.2f}轮")
    print(f"   - 平均耗时：{best.avg_duration:.2f}s")
    
    if best.temperature == 0.3:
        print(f"\n✅ 验证通过！当前项目的 temperature=0.3 是合理的选择。")
    else:
        print(f"\n⚠️  建议：考虑将 temperature 从 0.3 调整为 {best.temperature}")
    
    # 详细分析
    print(f"\n📝 详细分析：")
    for summary in ranked:
        print(f"\n   Temperature = {summary.temperature}:")
        print(f"   - 优势：", end="")
        if summary.avg_accuracy >= 0.98:
            print(f"准确率高（{summary.avg_accuracy:.1%}）", end="")
        elif summary.avg_rounds <= 2.5:
            print(f"轮数少（{summary.avg_rounds:.1f}轮）", end="")
        else:
            print(f"待分析", end="")
        print()
        print(f"   - 劣势：", end="")
        if summary.avg_accuracy < 0.95:
            print(f"准确率偏低（{summary.avg_accuracy:.1%}）", end="")
        elif summary.avg_rounds > 3.5:
            print(f"轮数较多（{summary.avg_rounds:.1f}轮）", end="")
        else:
            print(f"无明显劣势", end="")
        print()


async def main():
    """主函数"""
    try:
        summaries = await run_all_tests()
        generate_report(summaries)
        
        # 保存结果到文件
        output_file = "temperature_test_results.json"
        results_data = {
            "test_config": {
                "temperatures": TEST_TEMPERATURES,
                "total_cases": len(TEST_CASES),
                "model": DEEPSEEK_MODEL,
            },
            "summaries": {
                str(temp): {
                    "temperature": temp,
                    "avg_accuracy": summary.avg_accuracy,
                    "avg_rounds": summary.avg_rounds,
                    "avg_duration": summary.avg_duration,
                    "results": [
                        {
                            "case_id": r.case_id,
                            "query": r.query,
                            "actual_tools": r.actual_tools,
                            "expected_tools": r.expected_tools,
                            "rounds": r.rounds,
                            "accuracy": r.accuracy,
                            "success": r.success,
                            "duration": r.duration_seconds,
                        }
                        for r in summary.results
                    ]
                }
                for temp, summary in summaries.items()
            }
        }
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n💾 完整结果已保存到：{output_file}")
        
    except ValueError as e:
        print(f"\n❌ 配置错误：{e}")
        print("\n请按以下步骤配置：")
        print("1. 在代码中找到 DEEPSEEK_API_KEY 变量")
        print("2. 填入你的 DeepSeek API Key")
        print("3. 或者设置环境变量：set DEEPSEEK_API_KEY=your-key-here")
        return 1
    except Exception as e:
        print(f"\n❌ 测试失败：{e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)