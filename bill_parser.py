"""PDF 账单解析：先用规则解析文字，解析不出来时再让 Qwen 帮忙提取。"""
import io
import re
from datetime import date, datetime, timedelta

import pandas as pd
import pdfplumber

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}

# 日期格式：01 SEP / 01 Sep 2026 / 01SEP / 01/09 / 01/09/2026 / 2026-09-01 / Sep 01
_MON = r"(?i:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
DATE_PAT = (
    r"(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    rf"|\d{{1,2}}\s?{_MON}(?:\s+\d{{4}})?"
    rf"|{_MON}\s+\d{{1,2}}(?:,?\s+\d{{4}})?)"
)
# 金额：1,234.56 / -12.50 / (12.50) / 12.50 CR / S$12.50
AMT_PAT = r"(?:-?\(?(?:S?\$)?\d{1,3}(?:,\d{3})*\.\d{2}\)?(?:\s?(?:CR|DR|Cr|Dr))?-?)"

LINE_RE = re.compile(
    rf"^\s*(?P<d1>{DATE_PAT})\s+(?:(?P<d2>{DATE_PAT})\s+)?(?P<desc>.+?)(?P<amts>(?:\s+{AMT_PAT})+)\s*$"
)
AMT_RE = re.compile(AMT_PAT)
FULL_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})\b")

SKIP_WORDS = [
    "BALANCE B/F", "BALANCE C/F", "BALANCE BROUGHT", "BALANCE CARRIED", "PREVIOUS BALANCE",
    "OPENING BALANCE", "CLOSING BALANCE", "NEW BALANCE", "SUB-TOTAL", "SUBTOTAL", "TOTAL",
    "MINIMUM PAYMENT", "CARRIED FORWARD", "BROUGHT FORWARD", "CREDIT LIMIT",
]
CREDIT_WORDS = [
    "PAYMENT - THANK", "PAYMENT-THANK", "PAYMENT RECEIVED", "THANK YOU", "REFUND", "REVERSAL",
    "CASHBACK", "CASH REBATE", "SALARY", "INTEREST EARNED", "INTEREST CREDIT", "INCOMING",
    "CREDIT ADVICE", "INWARD", "GIRO CREDIT",
]
# 转账描述里 "from 某人" 是转入，"to 某人" 是转出
TRANSFER_IN_RE = re.compile(r"\b(PAYNOW|TRANSFER|FAST|GIRO|FUND)\b.*\bFROM\b")
# 交易下面夹着的杂行：纯日期（OCBC 的实际刷卡日 29/07/26）、外币金额（USD .95）、单个字母、页脚编号（如 ABC12DEF\3）
NOISE_LINE_RE = re.compile(r"^(?:\d{1,2}/\d{1,2}/\d{2,4}|[A-Z]{3}\s+[\d,]*\.\d+|\S|\S+\\\d+)$")
OPENING_WORDS = ["BALANCE B/F", "BALANCE BROUGHT", "BROUGHT FORWARD", "OPENING BALANCE", "PREVIOUS BALANCE"]
HEADERISH = [
    "PAGE", "BALANCE", "TOTAL", "STATEMENT", "DESCRIPTION", "AMOUNT", "WITHDRAWAL", "DEPOSIT",
    "TRANSACTION DATE", "POSTING DATE", "CONTINUED", "PLEASE", "WWW.", "HTTP", "CO. REG",
]


class ParseResult:
    def __init__(self, df, method, text, message=""):
        self.df, self.method, self.text, self.message = df, method, text, message


# ---------------- 工具函数 ----------------
def _amount(tok):
    t = tok.strip()
    credit = bool(re.search(r"(CR|Cr)$", t)) or t.startswith("(") or t.startswith("-") or t.endswith("-")
    num = float(re.sub(r"[^\d.]", "", re.sub(r"(CR|DR|Cr|Dr)$", "", t)))
    return num, credit


def _parse_date(tok, ref: date):
    """把日期字符串转成 date；没有年份的用账单日期推断。"""
    t = tok.strip()
    y = m = d = None
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", t):
            y, m, d = map(int, t.split("-"))
        elif re.fullmatch(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?", t):
            parts = list(map(int, re.split(r"[/-]", t)))
            d, m = parts[0], parts[1]  # 新加坡习惯：日/月
            if len(parts) == 3:
                y = parts[2] + (2000 if parts[2] < 100 else 0)
        else:
            mm = re.fullmatch(r"(\d{1,2})\s?([A-Za-z]{3})(?:\s+(\d{4}))?", t)
            if mm:
                d, m = int(mm.group(1)), MONTHS.get(mm.group(2).upper())
                y = int(mm.group(3)) if mm.group(3) else None
            else:
                mm = re.fullmatch(r"([A-Za-z]{3})\s+(\d{1,2})(?:,?\s+(\d{4}))?", t)
                if not mm:
                    return None
                m, d = MONTHS.get(mm.group(1).upper()), int(mm.group(2))
                y = int(mm.group(3)) if mm.group(3) else None
        if not m:
            return None
        if y is None:
            y = ref.year
            dt = date(y, m, d)
            if dt > ref + timedelta(days=5):  # 1 月账单里的 12 月交易
                dt = date(y - 1, m, d)
            return dt
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def _statement_date(text):
    """在文本里找最晚的完整日期当作账单日期，用来补全没有年份的交易日期。"""
    found = []
    for d, mon, y in FULL_DATE_RE.findall(text[:5000]):
        mi = MONTHS.get(mon.upper())
        if mi:
            try:
                found.append(date(int(y), mi, int(d)))
            except ValueError:
                pass
    found = [f for f in found if f <= date.today() + timedelta(days=60)]
    return max(found) if found else date.today()


def _has(words, s):
    return any(w in s for w in words)


def _is_credit_desc(up):
    return _has(CREDIT_WORDS, up) or bool(TRANSFER_IN_RE.search(up))


# ---------------- 规则解析 ----------------
def parse_text(text, source=""):
    ref = _statement_date(text)
    rows, prev_balance, last_idx, cont = [], None, None, 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        dt = _parse_date(m.group("d1"), ref) if m else None
        if not m or dt is None:
            # 描述跨行：把下一两行接到上一笔交易后面（DBS 等银行常见）
            up = line.upper()
            # 没有日期的期初余额行（如 "BALANCE B/F 1,234.56"）也要记下余额，第一笔交易才能判断收支
            if not rows and _has(OPENING_WORDS, up):
                toks = AMT_RE.findall(line)
                if toks:
                    prev_balance = _amount(toks[-1])[0]
                continue
            # 跳过杂行但不结束当前交易，商户名通常在它们后面（OCBC 借记卡消费）
            if last_idx is not None and NOISE_LINE_RE.match(line):
                continue
            if (last_idx is not None and cont < 2 and len(line) < 70
                    and not _has(HEADERISH, up) and not AMT_RE.search(line)
                    and re.search(r"[A-Za-z]", line)):
                rows[last_idx]["description"] += " " + line
                cont += 1
            else:
                last_idx = None
            continue

        desc = m.group("desc").strip()
        up = desc.upper()
        toks = AMT_RE.findall(m.group("amts"))
        if _has(SKIP_WORDS, up):
            if "BALANCE" in up and toks:
                prev_balance = _amount(toks[-1])[0]
            last_idx = None
            continue

        if len(toks) >= 2:  # 银行账户格式：金额 + 余额
            amount, credit = _amount(toks[-2])
            balance = _amount(toks[-1])[0]
            if prev_balance is not None:
                credit = balance > prev_balance
            else:
                credit = credit or _is_credit_desc(up)
            prev_balance = balance
        else:  # 信用卡格式：单个金额
            amount, credit = _amount(toks[0])
            credit = credit or _is_credit_desc(up)

        if amount == 0:
            continue
        rows.append({
            "date": pd.Timestamp(dt),
            "description": desc,
            "amount": round(amount, 2),
            "type": "收入" if credit else "支出",
            "source": source,
        })
        last_idx, cont = len(rows) - 1, 0

    return pd.DataFrame(rows, columns=["date", "description", "amount", "type", "source"])


# ---------------- LLM 兜底解析 ----------------
LLM_EXTRACT_PROMPT = """下面是一份新加坡银行/信用卡月结单 PDF 中提取出来的文字（可能排版错乱）。
请找出其中所有的交易记录，只输出 JSON，格式如下：
{{"transactions": [{{"date": "YYYY-MM-DD", "description": "商户或交易描述", "amount": 12.50, "type": "支出"}}]}}
规则：
- amount 一律为正数；消费、取现、扣款的 type 为 "支出"；还款、退款、收入、带 CR 的为 "收入"
- 不要包含上期余额、本期余额、总计、最低还款额等汇总行
- 没有年份的日期，参考账单日期 {ref} 推断年份
- 找不到交易就输出 {{"transactions": []}}

文字内容：
{text}"""


def parse_with_llm(text, llm, source="", chunk_chars=5000):
    ref = _statement_date(text)
    rows = []
    chunks = [text[i:i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    for ch in chunks:
        data = llm.chat_json([{"role": "user", "content": LLM_EXTRACT_PROMPT.format(ref=ref, text=ch)}])
        items = (data or {}).get("transactions", []) if isinstance(data, dict) else (data or [])
        for it in items:
            try:
                dt = pd.Timestamp(datetime.strptime(str(it["date"])[:10], "%Y-%m-%d"))
                amt = abs(float(str(it["amount"]).replace(",", "")))
                if amt == 0:
                    continue
                rows.append({
                    "date": dt,
                    "description": str(it.get("description", "")).strip()[:120],
                    "amount": round(amt, 2),
                    "type": "收入" if str(it.get("type", "")).strip() == "收入" else "支出",
                    "source": source,
                })
            except Exception:
                continue
    return pd.DataFrame(rows, columns=["date", "description", "amount", "type", "source"])


# ---------------- 入口 ----------------
def extract_text(pdf_bytes, password=None):
    with pdfplumber.open(io.BytesIO(pdf_bytes), password=password or None) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def parse_pdf(pdf_bytes, source="", password=None, llm=None, min_rows=3):
    try:
        text = extract_text(pdf_bytes, password)
    except Exception as e:
        msg = str(e)
        if "password" in msg.lower() or "encrypt" in msg.lower() or not msg:
            msg = "PDF 有密码保护或密码错误，请在左侧栏填写 PDF 密码"
        return ParseResult(pd.DataFrame(), "失败", "", msg)

    if len(text.strip()) < 50:
        return ParseResult(pd.DataFrame(), "失败", text,
                           "PDF 里没有可提取的文字（可能是扫描件图片），需要先做 OCR")

    df = parse_text(text, source)
    if len(df) >= min_rows:
        return ParseResult(df, "规则解析", text)

    if llm is not None:
        try:
            df2 = parse_with_llm(text, llm, source)
            if len(df2) > len(df):
                return ParseResult(df2, "Qwen 提取", text,
                                   "规则解析没认出这个账单格式，已改用 Qwen 提取，请核对明细")
        except Exception as e:
            return ParseResult(df, "规则解析", text, f"Qwen 提取失败：{e}")
    return ParseResult(df, "规则解析", text,
                       "解析出的交易很少，可能是不认识这种账单格式，请展开「原始文本」检查")
