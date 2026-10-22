"""Shared lexical ranking; no model, embeddings, or persistent index.

两层，都不调模型：
- 词面：空白/标点切词，加中文双字组合；含虚词的组合（「养了」「什么」「一下」）不算——它们让
  「车子该保养了」去命中「养了一只猫」。
- 话题：同一件事的不同说法（老婆/妻子/爱人，我家猫/橘猫，晚饭吃什么/不吃香菜）记作同一个话题；
  提问和记忆落在同一话题就算相关。词面抓不到换个说法的问法，这一层补上。
话题表刻意保守：只收个人助理常被问到的生活面，词尽量不歧义（单字只收「猫」「狗」「吃」「睡」这类不易误伤的）。
"""
import re

_FUNCTION_CHARS = set("了的么吗呢吧啊呀个一是在我你他她它这那有和就都也还要会能把被给让")
_CONCEPTS = {
    "住处": ("住在", "住哪", "住址", "住处", "我家附近", "家附近", "搬家", "搬到", "定居", "老家", "小区"),
    "伴侣": ("妻子", "老婆", "爱人", "太太", "媳妇", "丈夫", "老公", "配偶", "对象", "女朋友", "男朋友", "女友", "男友"),
    "宠物": ("猫", "狗", "宠物"),
    "孩子": ("孩子", "儿子", "女儿", "小孩", "上学", "放学", "年级", "幼儿园"),
    "作息": ("睡", "起床", "几点起", "叫我起", "作息", "熬夜", "早起"),
    "饮食": ("吃", "饭", "餐厅", "忌口", "过敏", "口味", "外卖", "菜"),
    "出行": ("出差", "高铁", "火车", "飞机", "航班", "机票", "车票", "行程", "怎么去", "座位"),
    "车": ("车子", "开车", "汽车", "我的车", "开一辆", "保养", "加油", "停车", "车牌", "驾照"),
    "工作": ("公司", "上班", "工作", "职位", "同事", "老板", "入职"),
    "联系方式": ("邮箱", "电话", "手机号", "微信"),
    "汇报": ("汇报", "报告", "总结", "先说结论"),
    "饮品": ("咖啡", "奶茶", "喝茶"),
    "健康": ("过敏", "体检", "医院", "生病", "吃药"),
}
_CONCEPT_WEIGHT = 3


def _concepts(text):
    return {name for name, words in _CONCEPTS.items() if any(word in text for word in words)}


def _terms(q):
    terms = [term for term in re.split(r"[\s,，。;；、/：:()（）\[\]{}?？!！]+", q) if term] or [q]
    for run in re.findall(r"[㐀-鿿]{2,}", q):
        terms.extend(run[index:index + 2] for index in range(len(run) - 1)
                     if not _FUNCTION_CHARS & set(run[index:index + 2]))
    return list(dict.fromkeys(terms))


def rank_memories(rows, query, limit=10):
    """Rank hits, preserving the caller's importance/recency order for ties."""
    q = (query or "").strip().lower()
    if not q:
        return rows[:limit]
    terms, topics = _terms(q), _concepts(q)
    scored = []
    for row in rows:
        content = str(row.get("content", "")).lower()
        score = sum(1 for term in terms if term in content)
        if q in content:
            score += len(terms)
        score += _CONCEPT_WEIGHT * len(topics & _concepts(content))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [row for _, row in scored][:limit]
