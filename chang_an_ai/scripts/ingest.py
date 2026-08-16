"""离线建库脚本：python scripts/ingest.py [--source corpus|java|all]

为什么建库是离线脚本而不是服务启动时自动跑？
1. Chroma 底层 SQLite 不允许两个进程同时写同一个库（Windows 上尤其严格），
   所以约定：先跑本脚本，再启动 FastAPI（服务里只读）；
2. 建库要全量拉数 + embedding，耗时几十秒到几分钟，放服务里会拖慢启动。
"""
import argparse
import sys
from pathlib import Path

# scripts/ 不是包（没有 __init__.py），直接 python scripts/ingest.py 运行时
# sys.path 里只有 scripts/ 自己，手动把项目根目录加进去才能 import app 包。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingest_service import run_ingest  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="重建长安知识库（离线运行，先停 FastAPI）")
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
    print("建库完成 ✅ 可以启动服务了：python run.py")


if __name__ == "__main__":
    main()
