"""java_client 测试：respx 拦截 httpx 传输层 mock Java 8081（不花钱、无真实依赖）。

覆盖三条路径：正常解包信封 / 业务失败抛异常 / 网络失败重试后抛异常 / 分页终止。
"""
import httpx
import pytest
import respx

from app.repositories.java_client import JavaClient, JavaClientError


@respx.mock
def test_list_shop_types_ok():
    respx.get("http://127.0.0.1:8081/shop-type/list").mock(
        return_value=httpx.Response(200, json={"success": True, "data": [{"id": 1, "name": "美食"}]})
    )
    with JavaClient() as jc:
        assert jc.list_shop_types() == [{"id": 1, "name": "美食"}]


@respx.mock
def test_business_fail_raises():
    respx.get("http://127.0.0.1:8081/shop-type/list").mock(
        return_value=httpx.Response(200, json={"success": False, "errorMsg": "系统繁忙"})
    )
    with JavaClient() as jc:
        with pytest.raises(JavaClientError, match="系统繁忙"):
            jc.list_shop_types()


@respx.mock
def test_network_error_retries_then_raises():
    # 所有尝试都失败：连接被拒 → 重试 2 次后抛 JavaClientError
    respx.get("http://127.0.0.1:8081/shop-type/list").mock(side_effect=httpx.ConnectError("refused"))
    with JavaClient() as jc:
        with pytest.raises(JavaClientError, match="重试"):
            jc.list_shop_types()


@respx.mock
def test_pagination_terminates_on_short_page():
    # 第 1 页满 5 条，第 2 页 3 条 → 循环在第 2 页终止
    respx.get("http://127.0.0.1:8081/shop/of/type", params={"typeId": 1, "current": 1}).mock(
        return_value=httpx.Response(200, json={"success": True, "data": [{"id": i} for i in range(5)]})
    )
    respx.get("http://127.0.0.1:8081/shop/of/type", params={"typeId": 1, "current": 2}).mock(
        return_value=httpx.Response(200, json={"success": True, "data": [{"id": i} for i in range(3)]})
    )
    with JavaClient() as jc:
        assert len(jc._fetch_all_pages(lambda p: jc.list_shops_by_type(1, p), page_size=5)) == 8
