"""账单分析 Dashboard —— 运行：python -m streamlit run app.py"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import analytics as an
import categorizer as cz
import config
from llm import OllamaClient
from bill_parser import parse_pdf

st.set_page_config(page_title="我的账单分析", page_icon="💳", layout="wide")
C = config.CURRENCY

# 兼容新旧版本 Streamlit：新版用 width="stretch"，旧版用 use_container_width=True
_ver = tuple(int(x) for x in st.__version__.split(".")[:2] if x.isdigit())
WIDE = {"width": "stretch"} if _ver >= (1, 50) else {"use_container_width": True}

# ================= 侧边栏 =================
with st.sidebar:
    st.header("⚙️ 设置")
    url = st.text_input("Ollama 地址", config.OLLAMA_URL)
    probe = OllamaClient(url)
    try:
        models = probe.list_models()
        online = True
    except Exception:
        models, online = [], False

    if online and models:
        default = next((m for m in models if m == config.DEFAULT_MODEL), None) \
            or next((m for m in models if "qwen" in m.lower()), models[0])
        model = st.selectbox("模型", models, index=models.index(default))
        st.success("Ollama 已连接")
    else:
        model = st.text_input("模型", config.DEFAULT_MODEL)
        st.error("连不上 Ollama，或还没有下载模型（命令行运行 ollama list 检查）。"
                 "没有模型时仍可用规则解析和看图表。")
    llm = OllamaClient(url, model) if online else None

    pdf_password = st.text_input("PDF 密码（如账单有密码）", type="password")
    excluded = st.multiselect("不计入支出的分类", config.CATEGORIES, default=config.DEFAULT_EXCLUDED,
                              help="比如银行账户里还信用卡的钱，已经在信用卡账单里算过一次了")

store = an.load_store()
sp = an.spending(store, excluded) if not store.empty else None
all_months = sorted(sp["month"].unique()) if sp is not None and not sp.empty else []

with st.sidebar:
    if all_months:
        month = st.selectbox("分析月份", all_months[::-1])
    else:
        month = None
    st.caption(f"已存 {len(store)} 笔交易，数据保存在本机 data 文件夹")

tab_import, tab_dash, tab_chat, tab_tx = st.tabs(["📥 导入账单", "📊 Dashboard", "💬 对话分析", "🧾 交易明细"])

# ================= 导入 =================
with tab_import:
    st.subheader("上传 PDF 账单")
    files = st.file_uploader("可以一次选多个月、多张卡的账单", type=["pdf"], accept_multiple_files=True)
    use_llm = st.checkbox("用 Qwen 给认不出的商户分类", value=online, disabled=not online)
    if files and st.button("解析并导入", type="primary"):
        new_all = []
        for f in files:
            with st.spinner(f"正在解析 {f.name} …"):
                res = parse_pdf(f.getvalue(), f.name, pdf_password, llm if online else None)
            if res.df.empty:
                st.error(f"{f.name}：{res.message or '没有解析到交易'}")
            else:
                st.write(f"✅ **{f.name}**：{len(res.df)} 笔（{res.method}）"
                         + (f" — ⚠️ {res.message}" if res.message else ""))
                new_all.append(res.df)
            with st.expander(f"查看 {f.name} 的原始文本（解析不对时用来排查）"):
                st.text(res.text[:4000] or "（无文本）")
        if new_all:
            new = pd.concat(new_all, ignore_index=True)
            bar = st.progress(0.0, text="正在分类…")
            new = cz.categorize(new, llm if (online and use_llm) else None,
                                progress=lambda d, t: bar.progress(d / t, text=f"Qwen 分类中 {d}/{t}"))
            bar.empty()
            store, added = an.merge_into_store(store, new)
            an.save_store(store)
            st.success(f"导入完成：新增 {added} 笔，{len(new) - added} 笔是重复的已跳过")
            st.dataframe(new[["date", "description", "amount", "type", "category"]],
                         **WIDE, hide_index=True)
            st.button("刷新 Dashboard")

    with st.expander("🗑️ 清空所有数据"):
        if st.checkbox("我确认要删除所有已导入的交易") and st.button("清空"):
            config.TX_FILE.unlink(missing_ok=True)
            st.rerun()

# ================= Dashboard =================
with tab_dash:
    if not month:
        st.info("还没有数据，先到「导入账单」上传 PDF。")
    else:
        r = an.month_report(sp, month)
        c1, c2, c3, c4 = st.columns(4)
        d_prev = an.pct(r["total"], r["prev_total"])
        d_avg = an.pct(r["total"], r["avg_prior"])
        c1.metric("本月支出", f"{C}{r['total']:,.2f}",
                  None if d_prev is None else f"{d_prev:+.1f}% 比上月", delta_color="inverse")
        c2.metric("前几月平均", "—" if r["avg_prior"] is None else f"{C}{r['avg_prior']:,.2f}",
                  None if d_avg is None else f"本月 {d_avg:+.1f}%", delta_color="inverse")
        c3.metric("交易笔数", r["count"])
        c4.metric("日均", f"{C}{r['daily_avg']:,.2f}")

        cat = r["categories"]
        left, right = st.columns(2)
        with left:
            pie = cat[cat["本月"] > 0].reset_index()
            fig = px.pie(pie, names="category", values="本月", hole=0.5, title="本月分类占比")
            fig.update_traces(textinfo="percent+label")
            st.plotly_chart(fig, **WIDE)
        with right:
            if r["prior_months"]:
                fig = go.Figure([
                    go.Bar(name="本月", x=cat.index, y=cat["本月"]),
                    go.Bar(name="前几月平均", x=cat.index, y=cat["前几月平均"]),
                ])
                fig.update_layout(barmode="group", title="各分类：本月 vs 平时")
                st.plotly_chart(fig, **WIDE)
            else:
                st.info("导入更多月份的账单后，这里会显示和平时的对比。")

        # 花多 / 花少
        if r["prior_months"]:
            st.subheader("哪里花多了，哪里花少了")
            diff = cat[(cat["比平均多"].abs() >= 20)].sort_values("比平均多", ascending=False)
            up, down = st.columns(2)
            with up:
                st.markdown("**🔺 比平时多**")
                for n, row in diff[diff["比平均多"] > 0].iterrows():
                    ch = "" if pd.isna(row["比平均变化%"]) else f"（{row['比平均变化%']:+.0f}%）"
                    st.write(f"{n}：多 {C}{row['比平均多']:,.2f}{ch}")
            with down:
                st.markdown("**🔻 比平时少**")
                for n, row in diff[diff["比平均多"] < 0].iloc[::-1].iterrows():
                    ch = "" if pd.isna(row["比平均变化%"]) else f"（{row['比平均变化%']:+.0f}%）"
                    st.write(f"{n}：少 {C}{-row['比平均多']:,.2f}{ch}")

        trend = sp.groupby(["month", "category"])["amount"].sum().reset_index()
        fig = px.bar(trend, x="month", y="amount", color="category", title="每月支出趋势")
        fig.update_layout(xaxis_type="category", yaxis_title=C)
        st.plotly_chart(fig, **WIDE)

        a, b = st.columns(2)
        with a:
            st.markdown("**花钱最多的商户**")
            st.dataframe(r["merchants"].head(10), **WIDE)
        with b:
            st.markdown("**最大的 5 笔**")
            st.dataframe(r["biggest"], **WIDE, hide_index=True)

        st.divider()
        if st.button("🤖 让 Qwen 写本月小结", disabled=not online):
            ctx = an.build_context(store, sp, month)
            msgs = [{"role": "system", "content": an.SYSTEM_PROMPT + "\n\n" + ctx},
                    {"role": "user", "content": an.REPORT_PROMPT}]
            st.write_stream(llm.chat_stream(msgs))

# ================= 对话 =================
with tab_chat:
    if not month:
        st.info("还没有数据，先到「导入账单」上传 PDF。")
    elif not online:
        st.warning("需要连上 Ollama 才能对话。")
    else:
        st.caption(f"正在分析 **{month}**（左侧栏可切换月份）。可以问：这个月哪里花多了？餐饮为什么比上个月贵？订阅有哪些？")
        if "chat" not in st.session_state or st.session_state.get("chat_month") != month:
            st.session_state.chat, st.session_state.chat_month = [], month
        for m in st.session_state.chat:
            st.chat_message(m["role"]).markdown(m["content"])

        q = st.chat_input("问问你的账单…")
        if q:
            st.chat_message("user").markdown(q)
            ctx = an.build_context(store, sp, month, question=q)
            msgs = [{"role": "system", "content": an.SYSTEM_PROMPT + "\n\n" + ctx}]
            msgs += st.session_state.chat[-6:]  # 保留最近几轮，避免上下文过长
            msgs.append({"role": "user", "content": q})
            with st.chat_message("assistant"):
                try:
                    ans = st.write_stream(llm.chat_stream(msgs))
                except Exception as e:
                    ans = f"调用模型出错：{e}"
                    st.error(ans)
            st.session_state.chat += [{"role": "user", "content": q},
                                      {"role": "assistant", "content": ans if isinstance(ans, str) else str(ans)}]
        if st.session_state.chat and st.button("清空对话"):
            st.session_state.chat = []
            st.rerun()

# ================= 明细 =================
with tab_tx:
    if store.empty:
        st.info("还没有数据。")
    else:
        st.caption("分类不对可以直接改，保存后同一商户以后都会按你改的来分。")
        view_month = st.selectbox("月份", ["全部"] + an.months(store)[::-1], key="tx_month")
        view = store if view_month == "全部" else \
            store[store["date"].dt.to_period("M").astype(str) == view_month]
        edited = st.data_editor(
            view.sort_values("date", ascending=False),
            column_config={
                "category": st.column_config.SelectboxColumn("分类", options=config.CATEGORIES),
                "amount": st.column_config.NumberColumn("金额", format="%.2f"),
                "date": st.column_config.DateColumn("日期"),
            },
            disabled=["date", "description", "amount", "type", "source", "merchant"],
            **WIDE, hide_index=True, key="editor",
        )
        if st.button("保存分类修改", type="primary"):
            changed = edited[edited["category"] != view.loc[edited.index, "category"]]
            for merchant, cat in changed.groupby("merchant")["category"].last().items():
                cz.set_user_category(merchant, cat)
                store.loc[store["merchant"] == merchant, "category"] = cat
            an.save_store(store)
            st.success(f"已更新 {changed['merchant'].nunique()} 个商户的分类")
            st.rerun()
        st.download_button("导出 CSV", store.to_csv(index=False).encode("utf-8-sig"),
                           "transactions.csv", "text/csv")
