"""离线建库脚本：python scripts/ingest.py [--source corpus|java|all]

为什么建库是离线脚本而不是服务启动时自动跑？
1. 建库要全量拉数 + embedding，耗时几十秒到几分钟，放服务里会拖慢启动；
2. 建库是低频运维动作，手动触发更可控。Milvus 是独立服务（不像 Chroma 的
   SQLite 有单写者限制），脚本与 FastAPI 并行跑也不冲突。
"""
import argparse
import sys
from pathlib import Path

# scripts/ 不是包（没有 __init__.py），直接 python scripts/ingest.py 运行时
# sys.path 里只有 scripts/ 自己，手动把项目根目录加进去才能 import app 包。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingest_service import run_ingest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="重建长安知识库（离线运行，可与 FastAPI 并行）")
    parser.add_argument(
        "--source",
        action="append",
        choices=["corpus", "java", "all"],
        default=None,
        help="可多次指定；不指定默认全部",
    )
    args = parser.parse_args()
    sources = args.source or ["all"]
    if "all" in sources:
        sources = ["corpus", "java"]

    report = run_ingest(sources, progress=print)
    print("\n===== 建库报告 =====")
    print(f"各源 chunk 数：{report['sources']}")
    print(f"向量库统计：{report['stats']}")
    if report["errors"]:
        print(f"失败：{report['errors']}")
        sys.exit(1)
    print("建库完成 ✅")


if __name__ == "__main__":
    main()
