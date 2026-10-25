"""所有数字都在这里用 pandas 算好，模型只负责解释，不做计算。"""
import calendar
from datetime import date

import pandas as pd

import config

C = config.CURRENCY


# ---------------- 存储 ----------------
COLUMNS = ["date", "description", "amount", "type", "source", "merchant", "category"]


def load_store():
    if config.TX_FILE.exists():
        df = pd.read_csv(config.TX_FILE, encoding="utf-8-sig")
        df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS).astype({"amount": float, "date": "datetime64[ns]"})


def save_store(df):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = df[COLUMNS].sort_values("date")
    out.to_csv(config.TX_FILE, index=False, encoding="utf-8-sig")  # utf-8-sig：Excel 打开不乱码


def merge_into_store(store, new):
    """合并新导入的交易，按 日期+描述+金额+类型 去重（重复导入同一份账单不会重复计算）。
    同一天两笔一模一样的消费（比如两杯咖啡）是真实的两笔，用"第几次出现"区分，不会被合并掉。"""
    if new.empty:
        return store, 0
    key = ["date", "description", "amount", "type"]
    new = new[COLUMNS].copy()
    # 空 store 的 date 列是 object，concat 后整列会变成 object，这里统一转成日期类型
    for df in (store, new):
        df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    old_keys = set(map(tuple, store[key].assign(n=store.groupby(key, dropna=False).cumcount()).values))
    new_n = new.groupby(key, dropna=False).cumcount()
    is_new = [tuple(r) not in old_keys for r in new[key].assign(n=new_n).values]
    added = new[is_new]
    merged = pd.concat([store, added], ignore_index=True) if len(store) else added.reset_index(drop=True)
    return merged, len(added)


# ---------------- 计算 ----------------
def spending(df, excluded):
    sp = df[(df["type"] == "支出") & (~df["category"].isin(excluded))].copy()
    sp["month"] = sp["date"].dt.to_period("M").astype(str)
    return sp


def months(df):
    return sorted(df["date"].dt.to_period("M").astype(str).unique())


def days_counted(month: str, df_month) -> int:
    """当月计入日均的天数：当前月份按已过天数算。"""
    y, m = map(int, month.split("-"))
    total = calendar.monthrange(y, m)[1]
    today = date.today()
    if (y, m) == (today.year, today.month):
        return today.day
    return total


def month_report(sp, month, lookback=3):
    """某个月的核心指标，以及和上月、前几个月平均值的对比。"""
    all_months = sorted(sp["month"].unique())
    prior = [m for m in all_months if m < month][-lookback:]
    prev = prior[-1] if prior else None
    cur = sp[sp["month"] == month]

    total = cur["amount"].sum()
    prev_total = sp.loc[sp["month"] == prev, "amount"].sum() if prev else None
    avg_prior = (sp[sp["month"].isin(prior)].groupby("month")["amount"].sum().mean()
                 if prior else None)

    # 分类对比
    by_cat = sp[sp["month"].isin(prior + [month])].pivot_table(
        index="category", columns="month", values="amount", aggfunc="sum", fill_value=0)
    cat = pd.DataFrame(index=by_cat.index)
    cat["本月"] = by_cat[month] if month in by_cat else 0
    cat["上月"] = by_cat[prev] if prev and prev in by_cat else 0
    cat["前几月平均"] = by_cat[prior].mean(axis=1) if prior else 0
    cat["比平均多"] = cat["本月"] - cat["前几月平均"]
    cat["比平均变化%"] = (cat["比平均多"] / cat["前几月平均"].where(cat["前几月平均"] > 0) * 100)
    cat["占本月%"] = cat["本月"] / total * 100 if total else 0
    cat["本月笔数"] = cur.groupby("category").size().reindex(cat.index).fillna(0).astype(int)
    cat = cat.sort_values("本月", ascending=False).round(2)

    # 商户
    merchants = (cur.groupby("merchant")["amount"].agg(["sum", "count"])
                 .sort_values("sum", ascending=False).rename(columns={"sum": "金额", "count": "笔数"}))
    seen_before = set(sp.loc[sp["month"] < month, "merchant"])
    new_merchants = merchants[~merchants.index.isin(seen_before)] if seen_before else merchants.iloc[0:0]

    # 大额交易：本月前 5 大，以及超过本月单笔中位数 5 倍的
    biggest = cur.nlargest(5, "amount")[["date", "description", "category", "amount"]]

    return {
        "month": month,
        "prev_month": prev,
        "prior_months": prior,
        "total": round(total, 2),
        "prev_total": None if prev_total is None else round(prev_total, 2),
        "avg_prior": None if avg_prior is None else round(avg_prior, 2),
        "count": int(len(cur)),
        "daily_avg": round(total / max(days_counted(month, cur), 1), 2),
        "days": days_counted(month, cur),
        "categories": cat,
        "merchants": merchants,
        "new_merchants": new_merchants,
        "biggest": biggest,
    }


def pct(a, b):
    if b in (None, 0) or pd.isna(b):
        return None
    return (a - b) / b * 100


# ---------------- 给模型的上下文 ----------------
def _money(x):
    return f"{C}{x:,.2f}"


def build_context(df, sp, month, question="", max_tx=40):
    r = month_report(sp, month)
    lines = [f"# 账单数据（{month}，货币 {C}，以下数字均已由程序精确计算）"]
    lines.append(f"- 本月支出合计：{_money(r['total'])}，共 {r['count']} 笔，按 {r['days']} 天算日均 {_money(r['daily_avg'])}")
    if r["prev_total"] is not None:
        p = pct(r["total"], r["prev_total"])
        lines.append(f"- 上月（{r['prev_month']}）支出：{_money(r['prev_total'])}，本月变化 {p:+.1f}%")
    else:
        lines.append("- 没有上月数据可对比")
    if r["avg_prior"] is not None:
        p = pct(r["total"], r["avg_prior"])
        lines.append(f"- 前 {len(r['prior_months'])} 个月（{', '.join(r['prior_months'])}）月均：{_money(r['avg_prior'])}，本月相比 {p:+.1f}%")
    today = date.today()
    if month == f"{today.year}-{today.month:02d}":
        lines.append(f"- 注意：{month} 还没结束（今天是 {today.day} 号），本月数据不完整，和整月对比时要考虑这一点")

    lines.append("\n## 分类对比（本月 / 上月 / 前几月平均 / 差额）")
    lines.append("| 分类 | 本月 | 占比 | 上月 | 前几月平均 | 比平均多 | 变化% | 笔数 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, row in r["categories"].iterrows():
        ch = "" if pd.isna(row["比平均变化%"]) else f"{row['比平均变化%']:+.0f}%"
        lines.append(f"| {name} | {row['本月']:,.2f} | {row['占本月%']:.0f}% | {row['上月']:,.2f} | "
                     f"{row['前几月平均']:,.2f} | {row['比平均多']:+,.2f} | {ch} | {int(row['本月笔数'])} |")

    lines.append("\n## 本月花费最多的商户（前 10）")
    for name, row in r["merchants"].head(10).iterrows():
        lines.append(f"- {name}：{_money(row['金额'])}（{int(row['笔数'])} 笔）")

    lines.append("\n## 本月最大的 5 笔")
    for _, row in r["biggest"].iterrows():
        lines.append(f"- {row['date']:%m-%d} {row['description']}（{row['category']}）{_money(row['amount'])}")

    if not r["new_merchants"].empty:
        lines.append("\n## 本月新出现的商户（以前月份没有）")
        for name, row in r["new_merchants"].head(8).iterrows():
            lines.append(f"- {name}：{_money(row['金额'])}")

    # 问题里提到了某个分类，就附上该分类本月的明细
    cur = sp[sp["month"] == month]
    mentioned = [c for c in config.CATEGORIES if c in question]
    for c in mentioned:
        sub = cur[cur["category"] == c].sort_values("amount", ascending=False).head(max_tx)
        if not sub.empty:
            lines.append(f"\n## 「{c}」本月明细")
            for _, row in sub.iterrows():
                lines.append(f"- {row['date']:%m-%d} {row['description']} {_money(row['amount'])}")

    # 近 6 个月趋势
    trend = sp.groupby("month")["amount"].sum().tail(6)
    lines.append("\n## 近几个月总支出")
    lines.append("、".join(f"{m}: {_money(v)}" for m, v in trend.items()))

    inc = df[(df["type"] == "收入") & (df["date"].dt.to_period("M").astype(str) == month)]
    if not inc.empty:
        lines.append(f"\n## 本月入账/还款/退款合计：{_money(inc['amount'].sum())}（不计入支出）")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是用户的个人记账分析助手，用中文回答，语气像一个懂理财的朋友，直接、具体、不说教。
规则：
1. 只根据下面提供的账单数据回答。所有数字必须直接来自数据，不要自己加减乘除，不要编造。
2. 判断"花多了/花少了"时，主要和"前几月平均"比较，其次和上月比较；变化不大（±15% 以内或金额很小）就说基本持平。
3. 指出花多的地方时，尽量点出具体是哪些商户或哪几笔大额交易造成的。
4. 如果数据不足以回答（比如只有一个月数据），直接说明。
5. 回答简洁，先给结论，再给 2-4 个要点。"""

REPORT_PROMPT = "请根据数据写一段本月消费小结：先一句话总体结论，然后列出花得明显比平时多的分类及原因、明显比平时少的分类、值得注意的大额或新出现的消费，最后给一条具体可执行的小建议。"
