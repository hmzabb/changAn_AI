"""阶段 6 数据落地执行器：备份 → 执行 changan_data.sql → 清理重建 Redis。

用法（changan_ai 目录下）：
    .venv/python.exe scripts/apply_xian_data.py            # 正常执行（先自动备份）
    .venv/python.exe scripts/apply_xian_data.py --skip-backup   # 重跑时跳过备份

三步各自可独立重跑（幂等）：
1. 备份：backup_tb_shop / backup_tb_blog 等表（DROP + CREATE AS SELECT）
2. 执行 SQL：TRUNCATE + INSERT（changan_data.sql 全量重建业务数据）
3. Redis：清理旧业务缓存（shop:geo/cache:shop/seckill:stock 等，保留用户 token）
   + 重建 shop:geo:{typeId} GEO key（Java 距离排序依赖）

三层质量保障之二（脚本校验）：执行后校验
- 店铺覆盖全部 10 个分类
- 坐标落在西安范围（108.6-109.3, 34.0-34.6）
- blog.shop_id 外键存在、score≤50、type_id∈1..10
"""
import argparse
import sys
from pathlib import Path

import pymysql
import redis

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 同 ingest.py 的 import 姿势

MYSQL_CFG = dict(
    host="172.30.237.211", port=3306,
    user="root", password="123456", database="hmdp",
    charset="utf8mb4", connect_timeout=10,
)
REDIS_CFG = dict(host="127.0.0.1", port=6379, password="123456", decode_responses=True)
SQL_FILE = Path(__file__).resolve().parent.parent / "app" / "data" / "sql" / "changan_data.sql"

BACKUP_TABLES = ("tb_shop", "tb_blog", "tb_voucher", "tb_seckill_voucher", "tb_voucher_order", "tb_blog_comments")
# 清理的缓存前缀（保留 login:token/code、sign、follow 等用户数据）
CLEAN_PATTERNS = ("shop:geo:*", "cache:shop:*", "cache:shopType:list", "seckill:stock:*", "blog:liked:*", "feed:*", "lock:shop:*")


def backup(conn: pymysql.connections.Connection) -> None:
    print("[1/4] 备份原表 → backup_*")
    with conn.cursor() as cur:
        for t in BACKUP_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS backup_{t}")
            cur.execute(f"CREATE TABLE backup_{t} AS SELECT * FROM {t}")
            cur.execute(f"SELECT COUNT(*) FROM backup_{t}")
            print(f"  backup_{t}: {cur.fetchone()[0]} 行")
    conn.commit()


def execute_sql(conn: pymysql.connections.Connection) -> None:
    print("[2/4] 执行 changan_data.sql")
    text = SQL_FILE.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        for raw in text.split(";"):
            # 过滤纯注释行后执行（-- 注释）
            stmt = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--")).strip()
            if stmt:
                cur.execute(stmt)
    conn.commit()


def clean_and_rebuild_redis(conn: pymysql.connections.Connection, r: redis.Redis) -> None:
    print("[3/4] 清理旧缓存 + 重建 GEO")
    deleted = 0
    for pattern in CLEAN_PATTERNS:
        for key in r.scan_iter(match=pattern):
            r.delete(key)
            deleted += 1
    print(f"  清理 {deleted} 个旧 key（保留 login:token 等用户数据）")

    with conn.cursor() as cur:
        cur.execute("SELECT id, type_id, x, y FROM tb_shop")
        rows = cur.fetchall()
    for sid, tid, x, y in rows:
        # member 用店铺 id 字符串——Java ShopServiceImpl 解析 geo 结果时
        # 从 member name 转 Long 拿店铺 id
        r.geoadd(f"shop:geo:{tid}", (x, y, str(sid)))
    print(f"  重建 shop:geo:{{typeId}} × {len(rows)} 个坐标点")


def validate(conn: pymysql.connections.Connection) -> None:
    print("[4/4] 质量校验（三层保障之二）")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tb_shop")
        print(f"  店铺总数: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(DISTINCT type_id) FROM tb_shop")
        print(f"  覆盖分类: {cur.fetchone()[0]}/10")
        cur.execute("SELECT COUNT(*) FROM tb_blog")
        print(f"  笔记总数: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM tb_voucher")
        print(f"  券总数: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM tb_seckill_voucher")
        print(f"  秒杀券: {cur.fetchone()[0]}")
        # 外键一致性
        cur.execute("SELECT COUNT(*) FROM tb_blog b LEFT JOIN tb_shop s ON b.shop_id = s.id WHERE s.id IS NULL")
        print(f"  笔记外键失效: {cur.fetchone()[0]}（应为 0）")
        # 用户外键（2026-08-22 踩坑补上：blog.user_id 指向不存在的用户会让
        # Java queryBlogUser 里 user.getNickName() NPE，/blog/hot 整个接口 500）
        cur.execute("SELECT COUNT(*) FROM tb_blog b LEFT JOIN tb_user u ON b.user_id = u.id WHERE u.id IS NULL")
        print(f"  笔记用户外键失效: {cur.fetchone()[0]}（应为 0）")
        # 坐标范围：西安 108.6-109.3 / 34.0-34.6
        cur.execute("SELECT COUNT(*) FROM tb_shop WHERE x < 108.6 OR x > 109.3 OR y < 34.0 OR y > 34.6")
        print(f"  坐标越界: {cur.fetchone()[0]}（应为 0）")
        cur.execute("SELECT COUNT(*) FROM tb_shop WHERE score > 50 OR type_id NOT BETWEEN 1 AND 10")
        print(f"  字段越界: {cur.fetchone()[0]}（应为 0）")


def main() -> None:
    parser = argparse.ArgumentParser(description="西安版数据落地：备份→替换→Redis 重建")
    parser.add_argument("--skip-backup", action="store_true", help="重跑时跳过备份")
    args = parser.parse_args()

    conn = pymysql.connect(**MYSQL_CFG)
    r = redis.Redis(**REDIS_CFG)
    try:
        if not args.skip_backup:
            backup(conn)
        execute_sql(conn)
        clean_and_rebuild_redis(conn, r)
        validate(conn)
        print("\n✅ 数据落地完成。下一步：重跑 ingest（python scripts/ingest.py --source java）")
        print("   回滚：backup_* 表数据 INSERT 回原表，或重导 hmdp.sql")
    finally:
        conn.close()
        r.close()


if __name__ == "__main__":
    main()
