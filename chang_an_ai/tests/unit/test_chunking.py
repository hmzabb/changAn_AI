"""chunking 纯函数测试：无需网络/数据库，跑得最快的一层。"""
from app.services.chunking import (
    blog_to_text,
    chunk_markdown,
    chunk_plain_text,
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
    # OVERLAP=120, CHUNK_MAX=600, 所以最大块长 ≤ 600+120=720
    assert all(len(c.text) <= 720 for c in chunks)  # 块长有界（句子粒度 + overlap 余量）


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


# ==================== chunk_plain_text 测试（PDF/Word 用） ====================

def test_plain_text_split_by_paragraphs():
    """空行分隔的段落各自成为一个 chunk。"""
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = chunk_plain_text(text, doc_title="测试", file_name="t.docx")
    assert len(chunks) == 3
    assert [c.metadata["section"] for c in chunks] == ["正文", "正文", "正文"]
    assert all(c.metadata["source"] == "corpus" for c in chunks)
    assert all(c.metadata["doc"] == "测试" for c in chunks)
    assert chunks[1].metadata["part"] == 1


def test_plain_text_long_paragraph_split():
    """超长段落应被 _split_long 续切。"""
    long_para = "这是一句很长的话。" * 80  # ~800 字, 超过 CHUNK_MAX=600
    text = f"短段落。\n\n{long_para}"
    chunks = chunk_plain_text(text, doc_title="长文", file_name="long.pdf")
    # 短段落 1 个 + 长段落至少切 2 个
    assert len(chunks) >= 3
    assert all(c.metadata["source"] == "corpus" for c in chunks)
    assert all(len(c.text) <= 720 for c in chunks)  # CHUNK_MAX + OVERLAP 余量


def test_plain_text_empty_input():
    """空文本返回空列表。"""
    assert chunk_plain_text("", doc_title="空", file_name="e.pdf") == []
    assert chunk_plain_text("\n\n\n", doc_title="空", file_name="e.pdf") == []


def test_plain_text_single_paragraph():
    """单段落文本产生一个 chunk。"""
    chunks = chunk_plain_text("只有一段话。", doc_title="单段", file_name="s.docx")
    assert len(chunks) == 1
    assert chunks[0].text == "只有一段话。"
    assert chunks[0].metadata["file"] == "s.docx"


def test_plain_text_trim_whitespace():
    """段落前后空白应被 strip。"""
    text = "  前面有空格  \n\n  后面也有  "
    chunks = chunk_plain_text(text, doc_title="空格", file_name="w.pdf")
    assert chunks[0].text == "前面有空格"
    assert chunks[1].text == "后面也有"