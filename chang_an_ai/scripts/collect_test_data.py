"""收集项目中的所有真实测试数据"""
import subprocess
import sys
import json
from pathlib import Path
from datetime import datetime


def run_test(test_file, test_name=None):
    """运行单个测试并返回结果"""
    cmd = [sys.executable, '-m', 'pytest', test_file, '-v', '--tb=short', '-q']
    if test_name:
        cmd.append(f'::{test_name}')
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            timeout=120
        )
        
        output = result.stdout + result.stderr
        
        # 解析测试结果
        passed = output.count('PASSED')
        failed = output.count('FAILED')
        errors = output.count('ERROR')
        total = passed + failed + errors
        
        return {
            'file': test_file,
            'test': test_name or 'all',
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'total': total,
            'exit_code': result.returncode,
            'output': output[-2000:] if len(output) > 2000 else output,
            'success': result.returncode == 0
        }
    except Exception as e:
        return {
            'file': test_file,
            'test': test_name,
            'error': str(e),
            'success': False
        }


def collect_code_based_data():
    """从代码中提取硬编码的测试数据"""
    data = {}
    
    # 1. 阈值测试数据
    data['threshold_analysis'] = {
        'test_cases_count': 18,
        'should_pass': 13,
        'should_fail': 5,
        'threshold_value': 0.35,
        'test_similarities_pass': [0.82, 0.76, 0.71, 0.68, 0.65, 0.62, 0.58, 0.55, 0.52, 0.48, 0.42, 0.38, 0.36],
        'test_similarities_fail': [0.28, 0.22, 0.18, 0.15, 0.12],
        'boundary_tests': [(0.349, False), (0.350, True), (0.351, True)],
        'expected_metrics': {
            'min_f1': 0.70,
            'min_precision': 0.65,
            'min_recall': 0.65
        }
    }
    
    # 2. 路由测试数据
    data['routing'] = {
        'agent_test_cases': 37,
        'rag_test_cases': 7,
        'total_routing_tests': 44,
        'edge_cases': 4,
        'agent_keywords': [
            '帮我找', '附近', '人均', '优惠券', '券', '有什么店', '哪家店',
            '店铺', '探店', '推荐', '评分', '星级', '好评', '口碑',
            '开门', '关门', '营业时间', '地址', '电话', '联系方式',
            '团购', '折扣/活动', '价格'
        ],
        'rag_keywords': ['历史文化', '特色', '怎么走', '攻略', '方言', '旅游建议']
    }
    
    # 3. RAG服务测试数据
    data['rag_service'] = {
        'test_scenarios': 3,
        'scenarios': [
            {'name': 'threshold_fallback', 'description': '低于阈值→fallback，不调LLM'},
            {'name': 'citation_prompt', 'description': '有效命中→sources事件+prompt带编号'},
            {'name': 'shop_id_jump', 'description': '店铺来源带shop_id用于前端跳转'}
        ],
        'mock_similarity_values': {
            'low': [0.1, 0.2],
            'high': [0.8, 0.7],
            'single_shop': [0.7]
        }
    }
    
    # 4. 性能测试配置
    data['performance'] = {
        'temperature_test': {
            'temperatures': [0.1, 0.3, 0.2],
            'test_cases': 5,
            'scenarios': [
                '简单分类查询',
                '多步查询（先找店再查券）',
                '模糊查询（关键词不明确）',
                '知识库查询',
                '笔记/攻略查询'
            ]
        },
        'overlap_test': {
            'current_overlap': 120,
            'chunk_max': 600,
            'test_overlaps': [0, 40, 80, 120, 150, 200],
            'test_sentence_length': 124,
            'keywords_to_check': ['玄奘法师', '652年', '丝绸之路', '64.5米', '西安的象征']
        },
        'tool_performance': {
            'test_types': ['serial_execution', 'parallel_execution', 'cache_effectiveness'],
            'cache_repeat_count': 10
        }
    }
    
    # 5. 系统配置参数
    data['system_config'] = {
        'rag': {
            'recall_top_k': 8,
            'rerank_top_k': 4,
            'min_score': 0.35,
            'max_context_chars': 6000
        },
        'models': {
            'embedding': 'BAAI/bge-m3',
            'reranker': 'BAAI/bge-reranker-v2-m3',
            'llm': 'deepseek-chat'
        },
        'infrastructure': {
            'milvus_uri': 'http://127.0.0.1:19530',
            'redis_url': 'redis://127.0.0.1:6379/0',
            'session_ttl_seconds': 1800
        }
    }
    
    # 6. 单元测试统计
    data['unit_tests'] = {
        'vector_store': {
            'total_tests': 20,
            'categories': ['id_structure', 'initialization', 'filters', 'crud_operations', 'query', 'edge_cases']
        },
        'reranker': {
            'total_tests': 4,
            'tests': ['keyword_overlap_boost', 'area_match_boost', 'mmr_dedup', 'top_k_bounds']
        },
        'query_rewrite': {
            'total_tests': 6,
            'tests': ['pronoun_detection', 'rewrite_logic', 'fallback']
        },
        'embedding': {'total_tests': 'unknown'},
        'chunking': {'total_tests': 'unknown'},
        'java_client': {'total_tests': 'unknown'}
    }
    
    # 7. 集成测试统计
    data['integration_tests'] = {
        'rag_service': 3,
        'assistant_service': 'unknown',
        'summary_memory': 'unknown'
    }
    
    return data


def main():
    print("=" * 80)
    print("📊 收集长安旅游AI项目真实测试数据")
    print(f"⏰ 收集时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    all_results = {
        'collection_time': datetime.now().isoformat(),
        'code_based_data': collect_code_based_data(),
        'test_execution_results': {}
    }
    
    # 要运行的测试列表
    tests_to_run = [
        ('tests/performance/test_threshold_analysis.py', 'TestThreshold'),
        ('tests/integration/test_rag_service.py', None),
        ('tests/routing/test_routing.py', None),
        ('tests/unit/test_vector_store.py', None),
        ('tests/unit/test_reranker.py', None),
        ('tests/unit/test_query_rewrite.py', None),
    ]
    
    print("\n🧪 开始运行测试...")
    print("-" * 80)
    
    for test_file, test_name in tests_to_run:
        print(f"\n▶ 运行: {test_file}" + (f"::{test_name}" if test_name else ""))
        result = run_test(test_file, test_name)
        
        test_key = f"{test_file.replace('/', '_').replace('.py', '')}"
        if test_name:
            test_key += f"_{test_name}"
        
        all_results['test_execution_results'][test_key] = result
        
        if result.get('success'):
            print(f"  ✅ 通过: {result.get('passed', 0)} 个测试")
        else:
            print(f"  ❌ 失败: {result.get('failed', 0)} 个失败, {result.get('errors', 0)} 个错误")
            if result.get('error'):
                print(f"     错误: {result['error']}")
    
    # 保存结果
    output_file = Path(__file__).parent.parent / 'test_data_report.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print("\n" + "=" * 80)
    print(f"✅ 数据收集完成！结果已保存到: {output_file}")
    print("=" * 80)
    
    # 打印摘要
    print("\n📋 数据摘要:")
    print("-" * 80)
    
    code_data = all_results['code_based_data']
    
    print(f"\n1️⃣ 阈值分析测试:")
    print(f"   测试案例总数: {code_data['threshold_analysis']['test_cases_count']}")
    print(f"   应该通过: {code_data['threshold_analysis']['should_pass']} 个")
    print(f"   应该拦截: {code_data['threshold_analysis']['should_fail']} 个")
    print(f"   当前阈值: {code_data['threshold_analysis']['threshold_value']}")
    print(f"   预期 F1-score: > {code_data['threshold_analysis']['expected_metrics']['min_f1']}")
    
    print(f"\n2️⃣ 路由测试:")
    print(f"   Agent路由测试: {code_data['routing']['agent_test_cases']} 个")
    print(f"   RAG路由测试: {code_data['routing']['rag_test_cases']} 个")
    print(f"   总计: {code_data['routing']['total_routing_tests']} 个")
    
    print(f"\n3️⃣ RAG服务集成测试:")
    for scenario in code_data['rag_service']['scenarios']:
        print(f"   - {scenario['name']}: {scenario['description']}")
    
    print(f"\n4️⃣ 系统配置参数:")
    config = code_data['system_config']
    print(f"   召回 Top-K: {config['rag']['recall_top_k']}")
    print(f"   重排 Top-K: {config['rag']['rerank_top_k']}")
    print(f"   相似度阈值: {config['rag']['min_score']}")
    print(f"   Embedding模型: {config['models']['embedding']}")
    print(f"   Reranker模型: {config['models']['reranker']}")
    
    print(f"\n5️⃣ 测试执行结果:")
    exec_results = all_results['test_execution_results']
    total_passed = sum(r.get('passed', 0) for r in exec_results.values() if isinstance(r, dict))
    total_failed = sum(r.get('failed', 0) for r in exec_results.values() if isinstance(r, dict))
    print(f"   总通过: {total_passed} 个")
    print(f"   总失败: {total_failed} 个")
    
    return all_results


if __name__ == '__main__':
    main()