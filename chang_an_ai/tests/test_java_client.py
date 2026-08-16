"""java_client 集成测试 —— 直连真实 Java 后端（http://127.0.0.1:8081 ）。

运行方式：
    cd chang_an_ai
    python -m pytest tests/test_java_client.py -v -s

注意：需要 Java 后端已启动在 8081 端口。
"""
import pytest

from app.repositories.java_client import JavaClient, JavaClientError


@pytest.fixture
def client():
    with JavaClient() as c:
        yield c


# ---------- 1. 分类接口 ----------
def test_list_shop_types(client):
    result = client.list_shop_types()
    assert isinstance(result, list)
    assert len(result) > 0, "分类列表不应为空"
    print(f"\n分类数量: {len(result)}")
    for t in result:
        print(f"  {t}")


# ---------- 2. 店铺分页 ----------
def test_list_shops_by_type(client):
    types = client.list_shop_types()
    if not types:
        pytest.skip("没有分类，跳过店铺测试")
    type_id = types[0]["id"]
    shops = client.list_shops_by_type(type_id=type_id, page=1)
    assert isinstance(shops, list)
    print(f"\n分类[{types[0]['name']}]第1页店铺数: {len(shops)}")


# ---------- 3. 全量店铺 ----------
def test_fetch_all_shops(client):
    shops = client.fetch_all_shops()
    assert isinstance(shops, list)
    print(f"\n全量店铺数: {len(shops)}")


# ---------- 4. 热门笔记 ----------
def test_list_hot_blogs(client):
    blogs = client.list_hot_blogs(page=1)
    assert isinstance(blogs, list)
    print(f"\n热门笔记数: {len(blogs)}")


# ---------- 5. 全量笔记 ----------
def test_fetch_all_blogs(client):
    blogs = client.fetch_all_blogs()
    assert isinstance(blogs, list)
    print(f"\n全量笔记数: {len(blogs)}")


# ---------- 6. 优惠券 ----------
def test_list_vouchers(client):
    shops = client.fetch_all_shops()
    if not shops:
        pytest.skip("没有店铺，跳过优惠券测试")
    shop_id = shops[0]["id"]
    vouchers = client.list_vouchers(shop_id=shop_id)
    assert isinstance(vouchers, list)
    print(f"\n店铺[{shops[0].get('name', shop_id)}]优惠券数: {len(vouchers)}")