"""chunking 纯函数测试：无需网络/数据库，跑得最快的一层。"""
from app.services.chunking import (
    blog_to_text,
    chunk_markdown,
    shop_to_text,
    voucher_to_text,
)


def test_markdown_split_by_headings():
    md = "# 标题\n\n## 第一节\n内容一。\n\n## 第二节\n内容二。"
    chunks = chunk_markdown(md, doc_title="测试", file_name="t.md")
    # 首个 ## 之前的内容归入「开篇」小节（保留文章标题上下文）
    assert [c.metadata["section"] for c in chunks] == ["开篇", "第一节", "第二节"]
    assert all(c.metadata["source"] == "corpus" for c in chunks)


def test_long_section_split_with_overlap():
    body = "这是一句很长的话。" * 80  # 约 800 字，超过 CHUNK_MAX=600
    md = f"# t\n\n## 长节\n{body}"
    chunks = chunk_markdown(md, doc_title="t", file_name="t.md")
    assert len(chunks) >= 2  # 超长必须切块
    # Chunk 是 dataclass：text 属性而非字典键
    assert all(len(c.text) <= 750 for c in chunks)  # 块长有界（句子粒度 + overlap 余量）


def test_shop_to_text_fields():
    shop = {"name": "老孙家", "area": "回民街", "address": "北院门118号",
            "avgPrice": 45, "score": 47, "sold": 100, "comments": 20, "openHours": "08:00-21:30"}
    text = shop_to_text(shop, type_name="美食")
    assert "回民街" in text and "美食" in text
    assert "4.7分" in text  # score ×10 存 → 展示除 10
    assert "45" in text


def test_blog_to_text_strips_html():
    blog = {"title": "探店", "content": "好吃<br/>下次再来<b>!</b>", "name": "游客", "liked": 5, "comments": 2}
    text = blog_to_text(blog)
    assert "<br/>" not in text and "<b>" not in text
    assert "好吃" in text and "游客" in text


def test_voucher_to_text_stock_states():
    v = {"title": "50元代金券", "payValue": 4500, "actualValue": 5000}
    assert "库存未知" in voucher_to_text(v, "某店")
    v["stock"] = 0
    assert "已售罄" in voucher_to_text(v, "某店")
    v["stock"] = 10
    assert "库存10张" in voucher_to_text(v, "某店")
    v["payValue"] = 0
    assert "免费领取" in voucher_to_text(v, "某店")
