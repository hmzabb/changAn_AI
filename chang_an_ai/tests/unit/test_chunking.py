"""chunking 纯函数测试：无需网络/数据库，跑得最快的一层。"""
from app.services.chunking import (
    CHUNK_MAX,
    _is_table_block,
    _split_table_with_header,
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


# ==================== _is_table_block 表格检测 ====================

def test_is_table_block_typical_table():
    """典型管道符表格应被识别。"""
    text = "名称 | 价格 | 时间\n大雁塔 | 40元 | 8:00\n钟楼 | 30元 | 9:00"
    assert _is_table_block(text) is True


def test_is_table_block_two_line_table():
    """最小表格：2 行含 |。"""
    text = "名称 | 价格\n大雁塔 | 40"
    assert _is_table_block(text) is True


def test_is_table_block_single_line_not_table():
    """单行含 | 不应被当作表格。"""
    text = "大雁塔 | 位于西安"
    assert _is_table_block(text) is False


def test_is_table_block_plain_text_not_table():
    """纯文本不含管道符。"""
    text = "大雁塔位于西安市南郊，是唐代建筑。\n钟楼位于西安市中心。"
    assert _is_table_block(text) is False


def test_is_table_block_mixed_content_not_table():
    """如果管道行占比不足 50%，不应被当作表格。"""
    text = "以下是景点介绍。\n大雁塔 | 西安\n\n还有其他景点值得一看。\n\n更多信息请访问官网。"
    assert _is_table_block(text) is False


def test_is_table_block_majority_pipes_is_table():
    """管道行占多数且连续 ≥2 行，应被识别。"""
    text = "名称 | 价格\n大雁塔 | 40\n钟楼 | 30\n说明：以上价格仅供参考"
    assert _is_table_block(text) is True


# ==================== 表格保护：带表头重复拆分 ====================

def test_split_table_with_header_basic():
    """大表格应被拆分为多块，每块都带表头且 ≤ CHUNK_MAX。"""
    header = "景点名称 | 门票价格 | 开放时间 | 地址 | 简介"
    row_template = "景点{0} | {0}0元 | 08:00-18:00 | 西安市长安区第{0}号 | 这是一个非常著名的旅游景点值得一去"
    rows = [header] + [row_template.format(i) for i in range(1, 31)]  # 31 行表格
    table_text = "\n".join(rows)
    assert len(table_text) > CHUNK_MAX  # 确实超长

    pieces = _split_table_with_header(table_text)
    assert len(pieces) >= 2  # 大表格必然被拆分
    for piece in pieces:
        assert len(piece) <= CHUNK_MAX  # 每块 ≤ 600
        assert piece.startswith(header)  # 每块都以表头开头


def test_split_table_with_header_small_table_unchanged():
    """小表格（≤ CHUNK_MAX）不拆分，直接返回原样。"""
    text = "名称 | 价格\n大雁塔 | 40\n钟楼 | 30\n鼓楼 | 25"
    assert len(text) <= CHUNK_MAX
    pieces = _split_table_with_header(text)
    assert len(pieces) == 1
    assert pieces[0] == text


def test_split_table_with_header_header_preserved_in_all_chunks():
    """验证表头在每一个拆分块中都完整出现。"""
    header = "景点名称 | 门票价格 | 开放时间"
    rows = [header] + [f"景点{i} | {i*10}元 | 08:00-{8+i}:00" for i in range(1, 50)]
    table_text = "\n".join(rows)

    pieces = _split_table_with_header(table_text)
    for i, piece in enumerate(pieces):
        assert piece.startswith(header), f"Chunk {i} 缺少表头"


def test_plain_text_large_table_split_with_header():
    """超长表格段落 → chunk_plain_text 产生多块，每块都带表头。"""
    header = "景点名称 | 门票价格 | 开放时间 | 地址 | 简介"
    row_template = "景点{0} | {0}0元 | 08:00-18:00 | 西安市长安区第{0}号 | 这是一个非常著名的旅游景点值得一去"
    rows = [header] + [row_template.format(i) for i in range(1, 31)]
    table_text = "\n".join(rows)
    assert len(table_text) > CHUNK_MAX

    chunks = chunk_plain_text(table_text, doc_title="景点表", file_name="spots.pdf")
    assert len(chunks) >= 2  # 大表格被拆分
    for chunk in chunks:
        assert chunk.text.startswith(header)  # 每块都带表头
        assert len(chunk.text) <= CHUNK_MAX  # 每块大小合理


def test_plain_text_table_with_text_not_interfered():
    """表格拆分不影响普通长文本的正常续切。"""
    header = "名称 | 价格"
    rows = [header] + [f"景点{i} | {i * 10}元" for i in range(1, 50)]
    table_text = "\n".join(rows)
    long_text = "这是一句很长的话。" * 80
    combined = f"{long_text}\n\n{table_text}"

    chunks = chunk_plain_text(combined, doc_title="混合", file_name="mix.pdf")
    # 长文本至少切 2 块 + 表格至少 1 块（50 行短数据 ≈500 字，不超 CHUNK_MAX）
    assert len(chunks) >= 3
    table_chunks = [c for c in chunks if "名称 | 价格" in c.text]
    assert len(table_chunks) >= 1  # 表格可能不拆分（够小），也可能拆分（够大），但至少存在
    for tc in table_chunks:
        assert header in tc.text


def test_plain_text_small_table_unchanged():
    """小表格（≤ CHUNK_MAX）行为不变，仍作为一个 chunk。"""
    text = "名称 | 价格\n大雁塔 | 40\n钟楼 | 30\n鼓楼 | 25"
    assert len(text) <= CHUNK_MAX
    chunks = chunk_plain_text(text, doc_title="小表", file_name="small.pdf")
    assert len(chunks) == 1
    assert "大雁塔" in chunks[0].text