import subprocess
import sys

test_files = [
    'tests/test_query_rewrite.py',
    'tests/test_embedding.py',
    'tests/test_vector_store.py',
]

print("=" * 80)
print("运行新创建的测试文件...")
print("=" * 80)

for test_file in test_files:
    print(f"\n{'=' * 80}")
    print(f"运行: {test_file}")
    print('=' * 80)
    
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', test_file, '-v'],
        capture_output=True,
        text=True,
        cwd=r'd:\zmz\project\chang_an_travel\chang_an_ai'
    )
    
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    print(f"\n返回码: {result.returncode}")

print("\n" + "=" * 80)
print("测试完成！")
print("=" * 80)