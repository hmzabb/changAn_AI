"""运行所有测试并收集真实数据"""
import subprocess
import sys
import json
from datetime import datetime
from pathlib import Path


def run_pytest(test_path, extra_args=[]):
    """运行pytest并返回完整输出"""
    cmd = [sys.executable, '-m', 'pytest', test_path] + extra_args + [
        '-v', '--tb=short', '-q'
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
            timeout=180
        )
        
        return {
            'success': result.returncode == 0,
            'exit_code': result.returncode,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'output': result.stdout + '\n' + result.stderr
        }
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Timeout',
            'output': 'Test execution timed out'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'output': str(e)
        }


def parse_test_output(output):
    """解析pytest输出，提取通过/失败/错误数量"""
    import re
    
    passed = len(re.findall(r'PASSED', output))
    failed = len(re.findall(r'FAILED', output))
    errors = len(re.findall(r'ERROR', output))
    
    # 尝试提取总结行
    summary_match = re.search(r'(\d+) passed.*?(\d+) failed.*?(\d+) errors?', output)
    if summary_match:
        passed = int(summary_match.group(1))
        failed = int(summary_match.group(2))
        errors = int(summary_match.group(3))
    
    return {
        'passed': passed,
        'failed': failed,
        'errors': errors,
        'total': passed + failed + errors
    }


def main():
    print("=" * 80)
    print("🧪 运行长安旅游AI项目 - 全部测试")
    print(f"⏰ 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    results = {}
    
    # 定义要运行的测试模块
    test_modules = [
        ('unit/vector_store', 'tests/unit/test_vector_store.py'),
        ('unit/reranker', 'tests/unit/test_reranker.py'),
        ('unit/query_rewrite', 'tests/unit/test_query_rewrite.py'),
        ('unit/embedding', 'tests/unit/test_embedding.py'),
        ('unit/chunking', 'tests/unit/test_chunking.py'),
        ('integration/rag_service', 'tests/integration/test_rag_service.py'),
        ('routing/routing', 'tests/routing/test_routing.py'),
        ('performance/threshold', 'tests/performance/test_threshold_analysis.py'),
    ]
    
    all_output = []
    total_passed = 0
    total_failed = 0
    total_errors = 0
    success_count = 0
    fail_count = 0
    
    for name, path in test_modules:
        print(f"\n{'='*80}")
        print(f"▶ 运行: {name}")
        print(f"   路径: {path}")
        print("-" * 80)
        
        result = run_pytest(path)
        stats = parse_test_output(result['output'])
        
        results[name] = {
            **result,
            **stats
        }
        
        # 打印关键信息
        status = "✅ 通过" if result['success'] else "❌ 失败"
        print(f"\n{status} | 通过: {stats['passed']} | 失败: {stats['failed']} | 错误: {stats['errors']}")
        
        # 打印部分输出（最后2000字符）
        output_preview = result.get('output', '')[-1500:]
        if output_preview.strip():
            print(f"\n📋 输出预览:")
            print("-" * 80)
            print(output_preview)
        
        total_passed += stats['passed']
        total_failed += stats['failed']
        total_errors += stats['errors']
        
        if result['success']:
            success_count += 1
        else:
            fail_count += 1
        
        all_output.append(f"\n{'='*80}\n{name}\n{'='*80}\n{result.get('output', '')}")
    
    # 汇总报告
    print("\n" + "=" * 80)
    print("📊 测试执行汇总报告")
    print("=" * 80)
    
    print(f"\n✅ 成功模块: {success_count}/{len(test_modules)}")
    print(f"❌ 失败模块: {fail_count}/{len(test_modules)}")
    print(f"\n📈 总计:")
    print(f"   ✅ 通过: {total_passed} 个测试")
    print(f"   ❌ 失败: {total_failed} 个测试")
    print(f"   ⚠️  错误: {total_errors} 个测试")
    print(f"   📊 总计: {total_passed + total_failed + total_errors} 个测试")
    
    if total_passed + total_failed + total_errors > 0:
        pass_rate = (total_passed / (total_passed + total_failed + total_errors)) * 100
        print(f"   🎯 通过率: {pass_rate:.1f}%")
    
    # 保存详细结果
    report = {
        'run_time': datetime.now().isoformat(),
        'summary': {
            'total_modules': len(test_modules),
            'successful_modules': success_count,
            'failed_modules': fail_count,
            'total_tests': total_passed + total_failed + total_errors,
            'total_passed': total_passed,
            'total_failed': total_failed,
            'total_errors': total_errors,
            'pass_rate': f"{(total_passed / (total_passed + total_failed + total_errors) * 100):.1f}%" if (total_passed + total_failed + total_errors) > 0 else "N/A"
        },
        'module_results': results,
        'full_output': '\n'.join(all_output)
    }
    
    output_file = Path(__file__).parent.parent / 'test_execution_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 详细结果已保存: {output_file}")
    print(f"⏰ 完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    return report


if __name__ == '__main__':
    main()