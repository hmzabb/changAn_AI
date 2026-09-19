"""Java 后端数据拉取封装（repository 层）。

- Python 不直连 MySQL，所有数据经 Java REST 获取——Python 是"Java 之上的智能层"。
- 本层是项目里唯一允许出现 httpx 调用的地方：上层服务只看到"拉店铺/拉笔记"，
  将来换协议（如 gRPC）只需改这一个文件。
- 统一异常 JavaClientError：网络故障与业务失败（success=false）对上层是同一件事，
  但异常消息里带 URL 和 errorMsg，排障时能区分"Java 挂了"还是"数据有问题"。
"""
from __future__ import annotations

import time

import httpx

from app.config import settings


class JavaClientError(Exception):
    """调用 Java 失败（网络不可达 / 超时 / 业务 success=false）。"""


class JavaClient:
    """封装 Java 8081 的全部数据接口（全部免登录，见 README.md 5.3）。

    分页约定来自 SystemConstants.java：
    /shop/of/type（不传 x,y）每页 DEFAULT_PAGE_SIZE=5；/blog/hot 每页 MAX_PAGE_SIZE=10。
    """

    SHOP_PAGE_SIZE = 5
    BLOG_PAGE_SIZE = 10
    MAX_RETRIES = 2  # 网络异常最多重试 2 次（共 3 次尝试）

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        # httpx.Client 连接池复用：分页循环几十次请求只握手一次（连接复用）
        self._client = httpx.Client(
            base_url=(base_url or settings.java_base_url).rstrip("/"),
            timeout=timeout or settings.java_timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

#上下文管理器：实现了 __enter__ 和 __exit__，
#这意味着你可以使用 with JavaClient() as client: 的语法，退出 with 块时会自动关闭连接池，防止资源泄漏。
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- 私有：统一请求入口 ----------
    def _get_list(self, path: str, params: dict | None = None) -> list:
        """GET 并解包统一信封 {success, errorMsg, data}，data 必须是 list。

        网络异常做"指数退避重试"：Java 偶发抖动时自动重试 2 次（间隔 0.5s、1s），
        三次全失败才抛 JavaClientError，单个偶发抖动对上层完全透明。
        """
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self._client.get(path, params=params)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                # TimeoutException / ConnectError 都继承 HTTPError，catch 它即可；
                # 业务失败（success=false）在下方解包时才抛，【不】重试——重试改变
                # 不了数据问题，只会放大故障。
                if attempt == self.MAX_RETRIES:
                    raise JavaClientError(
                        f"Java 服务不可达 GET {path}（重试 {self.MAX_RETRIES} 次后仍失败）: {e}"
                    ) from e
                # ！GET 天然幂等，重复请求无副作用，所以敢重试；间隔 0.5s、1s 指数
                # 退避，给 Java 抖动留恢复时间，也避免重试风暴。
                time.sleep(0.5 * 2**attempt)
            else:
                break  # 无异常：拿到响应，跳出重试循环

        try:
            body = resp.json()
        except ValueError as e:
            raise JavaClientError(f"Java 返回非 JSON GET {path}: {resp.text[:200]}") from e
        if not body.get("success"):
            raise JavaClientError(f"Java 业务失败 GET {path}: {body.get('errorMsg')}")
        return body.get("data") or []

    def _fetch_all_pages(self, fetch_page, page_size: int) -> list:
        """分页循环：从第 1 页翻到空为止。

        终止条件为什么是"本页条数 < page_size"而不是 total？
        —— Java 端 Result.ok(records) 只返回当前页列表、没有 total 字段，
        翻页协议只能约定俗成：不足一页说明到了最后一页。
        """
        records: list = []
        page = 1
        while True:
            batch = fetch_page(page)
            records.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return records

    # ---------- 分类 ----------
    def list_shop_types(self) -> list[dict]:
        """GET /shop-type/list → [{id, name, icon, sort}]"""
        return self._get_list("/shop-type/list")

    # ---------- 店铺 ----------
    def list_shops_by_type(self, type_id: int, page: int = 1, x: float | None = None, y: float | None = None) -> list[dict]:
        """GET /shop/of/type?typeId=&current=（不传 x,y → DB 分页，每页 5 条；
        传 x,y → 走 Redis GEO 按距离排序（Agent 工具"钟楼附近的美食店"用）"""
        params: dict = {"typeId": type_id, "current": page}
        if x is not None and y is not None:
            params.update({"x": x, "y": y})
        return self._get_list("/shop/of/type", params=params)

    def list_shops_by_name(self, name: str, page: int = 1) -> list[dict]:
        """GET /shop/of/name?name=&current=（Agent 按名查店工具用）"""
        return self._get_list("/shop/of/name", params={"name": name, "current": page})

    def get_shop_detail(self, shop_id: int) -> dict:
        """GET /shop/{id}——注意此接口 data 是单个店铺对象而非列表（/shop/{id} 需登录吗？
        免登录，见 MvcConfig 白名单 /shop/**）"""
        return self._get_list(f"/shop/{shop_id}")

    def fetch_all_shops(self) -> list[dict]:
        """全量拉取：遍历所有分类，每个分类内翻页。

        注意 lambda 默认参数 tid=t["id"] 的写法：Python 闭包晚绑定，
        不写默认参数的话循环结束后 tid 全是最后一个分类的 id（经典坑）。
        """
        all_shops: list = []
        for t in self.list_shop_types():
            shops = self._fetch_all_pages(
                lambda p, tid=t["id"]: self.list_shops_by_type(tid, p),
                page_size=self.SHOP_PAGE_SIZE,
            )
            all_shops.extend(shops)
        return all_shops

    # ---------- 笔记 ----------
    def list_hot_blogs(self, page: int = 1) -> list[dict]:
        """GET /blog/hot?current=（每页 10 条，按点赞数倒序）"""
        return self._get_list("/blog/hot", params={"current": page})

    def fetch_all_blogs(self) -> list[dict]:
        return self._fetch_all_pages(self.list_hot_blogs, page_size=self.BLOG_PAGE_SIZE)

    # ---------- 优惠券 ----------
    def list_vouchers(self, shop_id: int) -> list[dict]:
        """GET /voucher/list/{shopId}"""
        return self._get_list(f"/voucher/list/{shop_id}")
