"""文本分块 + 结构化数据模板化。

两种数据两种切法（面试必考）：
- 语料 md：按 ## 二级标题切（语义边界），目标 400-600 字一块，超长续切并带 80 字
  overlap。为什么按标题不按固定字数？固定字数会把"大雁塔历史"和"大雁塔开放时间"
  切开重组，命中的块语义不完整；标题是作者标好的语义边界，优先信任它。
- 结构化记录（店铺/笔记/券）：一条记录 = 一个 chunk，不硬切。一条店铺信息是原子
  语义单元（几百字以内），切碎了反而丢信息。用模板把字段拼成自然中文——向量模型
  在自然语言上效果最好，字段裸拼（"name=xxx avgPrice=50"）会稀释语义。
"""
from __future__ import annotations # 延迟类型注解求值，允许前向引用

import re # 正则：HTML 标签清洗、句子边界切分
from dataclasses import dataclass, field

# ---- 分块参数 ----
CHUNK_MAX = 600   # 超过此长度的小节续切
OVERLAP = 80      # 续切时相邻块重叠字数，防止关键句被切断

_HTML_TAG_RE = re.compile(r"<[^>]+>")  # 笔记 是markdown形式 是富文本（含 <br/> 等标签）


@dataclass
class Chunk:
    """待入库的最小单元：文本 + 元数据（metadata 用于过滤、统计、引用跳转）。"""

    text: str
    metadata: dict = field(default_factory=dict)


# ==================== 语料 md 分块 ====================

def _split_by_headings(md_text: str) -> list[tuple[str, str]]:
    """按 ## 二级标题切分，返回 [(小节标题, 正文)]；首个标题之前的内容归入「开篇」。"""
    lines = md_text.splitlines()
    sections: list[tuple[str, str]] = []
    current_title, current_body = "开篇", []
    for line in lines:
        if line.startswith("## "):
            if current_body:
                sections.append((current_title, "\n".join(current_body).strip()))
            current_title = line[3:].strip()
            current_body = []
        else:
            current_body.append(line)
    if current_body:
        sections.append((current_title, "\n".join(current_body).strip()))
    return [s for s in sections if s[1]]  # 丢掉空小节


def _split_long(text: str) -> list[str]:
    """超长文本按句子边界（句号/问号/感叹号/换行）切到 CHUNK_MAX 以内，
    相邻块重叠 OVERLAP 字。先按句子切碎再贪心装箱，避免把句子拦腰截断。"""
    sentences = re.split(r"(?<=[。！？\n])", text)
    blocks: list[str] = []
    buf = ""
    for sent in sentences:
        if len(buf) + len(sent) > CHUNK_MAX and buf:#and buf防止第一轮就超限
            blocks.append(buf.strip())
            buf = buf[-OVERLAP:] + sent  # 重叠：上一块尾部 80 字带进下一块
        else:
            buf += sent
    if buf.strip():
        blocks.append(buf.strip())
    return blocks


def chunk_markdown(md_text: str, doc_title: str, file_name: str = "") -> list[Chunk]:
    """语料 md → 若干 Chunk。块内首行保留小节标题，检索命中时自带上下文。"""
    chunks: list[Chunk] = []
    for section_title, body in _split_by_headings(md_text):
        pieces = _split_long(body) if len(body) > CHUNK_MAX else [body]
        for i, piece in enumerate(pieces):
            chunks.append(
                Chunk(
                    text=f"{section_title}\n{piece}",
                    metadata={
                        "source": "corpus",
                        "doc": doc_title,
                        "file": file_name,
                        "section": section_title,
                        "part": i,
                    },
                )
            )
    return chunks


# ==================== 结构化数据模板（一条记录 = 一个 chunk） ====================

def _score_text(score) -> str:
    """Java 端 score ×10 存整数（4.7 分存 47），展示时除 10。"""
    return f"{score / 10:.1f}分" if score else "暂无评分"


def shop_to_text(shop: dict, type_name: str = "") -> str:
    """店铺 → 自然中文。type_name 由 ingest 传入（分类表 id→name 映射）。"""
    return (
        f"【{shop.get('name', '')}】是一家位于西安{shop.get('area', '')}商圈的"
        f"{type_name or '商铺'}店，地址：{shop.get('address', '')}。"
        f"人均消费约{shop.get('avgPrice') or '暂无'}元，"
        f"评分{_score_text(shop.get('score'))}，"
        f"月销量{shop.get('sold') or 0}单，累计评论{shop.get('comments') or 0}条。"
        f"营业时间：{shop.get('openHours') or '暂无'}。"
    )


def blog_to_text(blog: dict) -> str:
    """笔记 → 自然中文。content 去 HTML 标签；name 是 /blog/hot 填充的作者昵称。"""
    content = _HTML_TAG_RE.sub(" ", blog.get("content") or "").strip()
    return (
        f"用户{blog.get('name') or '网友'}发布探店笔记《{blog.get('title', '')}》："
        f"{content}"
        f"（点赞{blog.get('liked') or 0}，评论{blog.get('comments') or 0}）"
    )


def voucher_to_text(voucher: dict, shop_name: str = "") -> str:
    """优惠券 → 自然中文。

    库存三态（None/0/>0）区分；payValue=0 时表达为"免费领取"；
    rules 按 list 展开或按字符串原样输出；时间字段可选拼接。
    """

    pay, actual, stock = voucher.get("payValue"), voucher.get("actualValue"), voucher.get("stock")

    if stock is None:
        stock_text = "库存未知"
    elif stock == 0:
        stock_text = "已售罄"
    else:
        stock_text = f"库存{stock}张"

    begin_time = voucher.get("beginTime")
    end_time = voucher.get("endTime")
    time_parts = []
    if begin_time:
        time_parts.append(f"生效时间：{begin_time}")
    if end_time:
        time_parts.append(f"失效时间：{end_time}")
    time_text = "，".join(time_parts) if time_parts else "有效期暂无"

    if pay is not None and actual is not None:
        if pay == 0:
            deal = "免费领取"
        else:
            deal = f"{pay}元抵{actual}元"
    else:
        deal = ""

    rules_raw = voucher.get("rules")
    if rules_raw:
        if isinstance(rules_raw, list):
            rules_text = "；".join(rules_raw)
        else:
            rules_text = str(rules_raw)
    else:
        rules_text = "详见门店"

    return (
        f"{shop_name}店铺优惠券【{voucher.get('title', '')}】"
        f"{'，' + deal if deal else ''}"
        f"{'，' + voucher.get('subTitle', '') if voucher.get('subTitle') else ''}"
        f"。{stock_text}。{time_text}。"
        f"使用规则：{rules_text}。"
    )