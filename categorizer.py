"""交易分类：你手动改过的 > 关键词规则 > Qwen 判断（结果缓存）> 其他。"""
import json
import re

import config

NOISE = re.compile(r"\b(SINGAPORE|SGP|SG|SGD|PTE|LTD|S\$)\b")
# 银行流水里常见的交易类型前缀，不是商户名
PREFIXES = re.compile(r"^(POINT-OF-SALE TRANSACTION|DEBIT CARD TRANSACTION|DEBIT PURCHASE|POS PURCHASE NETS|"
                      r"NETS DEBIT|NETS|ADVICE|MST|POS|VISA DEBIT|MASTERCARD DEBIT)\s+")
CARD_MASK = re.compile(r"\bXX-\d{4}\b")  # OCBC 流水里的卡号尾号，如 xx-1234


def merchant_key(desc: str) -> str:
    """把描述规整成商户名，用作缓存的 key（去掉数字、卡号、城市等噪音）。"""
    s = str(desc).upper()
    s = re.sub(r"X{4,}|\*+", " ", s)
    s = CARD_MASK.sub(" ", s)
    words = s.split()
    s = PREFIXES.sub("", " ".join(words))
    words = s.split()
    # 去掉带数字的词（订单号、卡号、行程编号），但保留第一个词（7-ELEVEN、M1 这类品牌名）
    s = " ".join(words[:1] + [w for w in words[1:] if not re.search(r"\d", w)])
    s = NOISE.sub(" ", s)
    s = re.sub(r"[^A-Z0-9&.\- ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = PREFIXES.sub("", s)
    return s[:40] or str(desc)[:40]


def load_cache():
    if config.CATEGORY_CACHE.exists():
        try:
            data = json.loads(config.CATEGORY_CACHE.read_text(encoding="utf-8"))
            return {"user": data.get("user", {}), "llm": data.get("llm", {})}
        except Exception:
            pass
    return {"user": {}, "llm": {}}


def save_cache(cache):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CATEGORY_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def rule_category(desc: str):
    up = " " + str(desc).upper() + " "
    for kw, cat in config.KEYWORD_RULES:
        if kw in up:
            return cat
    return None


CLASSIFY_PROMPT = """你在帮一位住在新加坡的用户整理信用卡/银行账单。
请把下面每一条商户描述归入以下类别之一：{cats}

判断提示：
- 餐厅、咖啡店、小贩中心、外卖 → 餐饮
- 超市、便利店、日用品 → 买菜日用
- 打车、公交地铁、加油、停车 → 交通
- 电商、服装、电子产品、家居 → 购物
- 按月扣费的软件或会员 → 订阅会员
- 电话费、水电、保险、物业 → 账单缴费
- 实在判断不了 → 其他

只输出 JSON 对象，key 是编号，value 是类别名称（必须是上面列表中的一个），例如 {{"1": "餐饮", "2": "交通"}}

{items}"""


def classify_with_llm(keys, llm, batch=20, progress=None):
    allowed = [c for c in config.CATEGORIES if c not in ("收入/还款", "信用卡还款")]
    result = {}
    for start in range(0, len(keys), batch):
        chunk = keys[start:start + batch]
        items = "\n".join(f"{i + 1}. {k}" for i, k in enumerate(chunk))
        prompt = CLASSIFY_PROMPT.format(cats="、".join(allowed), items=items)
        try:
            data = llm.chat_json([{"role": "user", "content": prompt}]) or {}
        except Exception:
            data = {}
        for i, k in enumerate(chunk):
            cat = str(data.get(str(i + 1), "")).strip() if isinstance(data, dict) else ""
            result[k] = cat if cat in allowed else "其他"
        if progress:
            progress(min(start + batch, len(keys)), len(keys))
    return result


def categorize(df, llm=None, progress=None):
    """给 df 加上 merchant 和 category 两列。"""
    if df.empty:
        df = df.copy()
        df["merchant"], df["category"] = [], []
        return df
    df = df.copy()
    cache = load_cache()
    df["merchant"] = df["description"].map(merchant_key)

    cats, pending = [], set()
    for desc, key, typ in zip(df["description"], df["merchant"], df["type"]):
        cat = cache["user"].get(key) or rule_category(desc)
        if cat is None and typ == "收入":
            cat = "收入/还款"
        if cat is None:
            cat = cache["llm"].get(key)
        if cat is None:
            pending.add(key)
        cats.append(cat)
    df["category"] = cats

    if pending and llm is not None:
        new = classify_with_llm(sorted(pending), llm, progress=progress)
        cache["llm"].update(new)
        save_cache(cache)
        mask = df["category"].isna()
        df.loc[mask, "category"] = df.loc[mask, "merchant"].map(new)

    df["category"] = df["category"].fillna("其他")
    return df


def set_user_category(merchant, category):
    cache = load_cache()
    cache["user"][merchant] = category
    save_cache(cache)
