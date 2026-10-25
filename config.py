"""全局配置：可以按需要修改这里。"""
import os
from pathlib import Path

# ---- Ollama ----
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("BILL_MODEL", "qwen2.5:7b")
# Ollama 默认上下文很短，太短会悄悄截断账单数据，这里调大
NUM_CTX = int(os.environ.get("BILL_NUM_CTX", "8192"))
REQUEST_TIMEOUT = 600  # 秒，本地模型第一次加载可能较慢

# ---- 数据存放位置 ----
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TX_FILE = DATA_DIR / "transactions.csv"
CATEGORY_CACHE = DATA_DIR / "merchant_categories.json"

CURRENCY = "S$"

# ---- 分类 ----
CATEGORIES = [
    "餐饮",
    "买菜日用",
    "交通",
    "购物",
    "娱乐",
    "订阅会员",
    "账单缴费",
    "医疗健康",
    "旅行",
    "教育",
    "转账",
    "信用卡还款",
    "收入/还款",
    "其他",
]

# 默认不计入"支出"统计的分类（避免银行账户里还信用卡的钱被重复计算）
DEFAULT_EXCLUDED = ["信用卡还款", "收入/还款"]

# 关键词规则：按顺序匹配（越具体的放越前面），匹配账单描述的大写形式。
# 规则匹配不上的商户才会交给 Qwen 判断。可以随意增删。
KEYWORD_RULES = [
    # 信用卡还款 / 转账
    ("CREDIT CARD", "信用卡还款"),
    ("CARD PAYMENT", "信用卡还款"),
    ("PAYNOW", "转账"),
    ("FUNDS TRANSFER", "转账"),
    ("FAST PAYMENT", "转账"),
    ("GIRO", "账单缴费"),
    # 餐饮（GRABFOOD 必须在 GRAB 之前）
    ("GRABFOOD", "餐饮"),
    ("GRAB FOOD", "餐饮"),
    ("FOODPANDA", "餐饮"),
    ("DELIVEROO", "餐饮"),
    ("MCDONALD", "餐饮"),
    ("KFC", "餐饮"),
    ("STARBUCKS", "餐饮"),
    ("TOAST BOX", "餐饮"),
    ("YA KUN", "餐饮"),
    ("KOPITIAM", "餐饮"),
    ("KOUFU", "餐饮"),
    ("FOOD REPUBLIC", "餐饮"),
    ("SUBWAY", "餐饮"),
    ("JOLLIBEE", "餐饮"),
    ("DIN TAI FUNG", "餐饮"),
    ("HAIDILAO", "餐饮"),
    ("COFFEE", "餐饮"),
    ("CAFE", "餐饮"),
    ("RESTAURANT", "餐饮"),
    ("SAIZERIYA", "餐饮"),
    ("SUSHI", "餐饮"),
    ("SONG FA", "餐饮"),
    ("ASTONS", "餐饮"),
    ("LUCKIN", "餐饮"),
    (" TEA ", "餐饮"),
    ("COOKIE", "餐饮"),
    ("BAKERY", "餐饮"),
    # 交通（GRAB SUBSCRIPTION 是会员费，要在 GRAB 之前）
    ("GRAB SUBSCRIPTION", "订阅会员"),
    ("SUBSCRIPTIONGRAB", "订阅会员"),
    ("GRAB", "交通"),
    ("CAUSEWAYLINK", "交通"),
    ("SENTOSA EXPRESS", "交通"),
    ("GOJEK", "交通"),
    ("TADA", "交通"),
    ("COMFORT", "交通"),
    ("CDG", "交通"),
    ("BUS/MRT", "交通"),
    ("TRANSIT", "交通"),
    ("SIMPLYGO", "交通"),
    ("EZ-LINK", "交通"),
    ("EZLINK", "交通"),
    ("SMRT", "交通"),
    ("SBS", "交通"),
    ("SHELL", "交通"),
    ("ESSO", "交通"),
    ("CALTEX", "交通"),
    ("SPC", "交通"),
    # 买菜日用
    ("FAIRPRICE", "买菜日用"),
    ("NTUC", "买菜日用"),
    ("COLD STORAGE", "买菜日用"),
    ("SHENG SIONG", "买菜日用"),
    ("GIANT", "买菜日用"),
    ("DON DON DONKI", "买菜日用"),
    ("DAISO", "买菜日用"),
    ("7-ELEVEN", "买菜日用"),
    ("7 ELEVEN", "买菜日用"),
    ("REDMART", "买菜日用"),
    # 订阅
    ("NETFLIX", "订阅会员"),
    ("SPOTIFY", "订阅会员"),
    ("APPLE.COM", "订阅会员"),
    ("YOUTUBE", "订阅会员"),
    ("DISNEY", "订阅会员"),
    ("OPENAI", "订阅会员"),
    ("CHATGPT", "订阅会员"),
    ("ANTHROPIC", "订阅会员"),
    ("CLAUDE.AI", "订阅会员"),
    ("GOOGLE", "订阅会员"),
    ("MICROSOFT", "订阅会员"),
    ("ICLOUD", "订阅会员"),
    # 账单缴费
    ("SINGTEL", "账单缴费"),
    ("STARHUB", "账单缴费"),
    ("M1 ", "账单缴费"),
    ("CIRCLES", "账单缴费"),
    ("SP SERVICES", "账单缴费"),
    ("SP GROUP", "账单缴费"),
    ("TOWN COUNCIL", "账单缴费"),
    ("INSURANCE", "账单缴费"),
    ("PRUDENTIAL", "账单缴费"),
    ("AIA", "账单缴费"),
    ("GREAT EASTERN", "账单缴费"),
    # 医疗
    ("GUARDIAN", "医疗健康"),
    ("WATSONS", "医疗健康"),
    ("UNITY", "医疗健康"),
    ("CLINIC", "医疗健康"),
    ("HOSPITAL", "医疗健康"),
    ("POLYCLINIC", "医疗健康"),
    ("DENTAL", "医疗健康"),
    ("PHARMACY", "医疗健康"),
    # 旅行
    ("AGODA", "旅行"),
    ("BOOKING.COM", "旅行"),
    ("AIRBNB", "旅行"),
    ("TRIP.COM", "旅行"),
    ("KLOOK", "旅行"),
    ("SCOOT", "旅行"),
    ("SINGAPORE AIRLINES", "旅行"),
    ("SIA ", "旅行"),
    ("AIRASIA", "旅行"),
    ("JETSTAR", "旅行"),
    ("HOTEL", "旅行"),
    # 购物
    ("SHOPEE", "购物"),
    ("LAZADA", "购物"),
    ("AMAZON", "购物"),
    ("AMZN", "购物"),
    ("TAOBAO", "购物"),
    ("CHALLENGER", "购物"),
    ("TEMU", "购物"),
    ("UNIQLO", "购物"),
    ("IKEA", "购物"),
    ("COURTS", "购物"),
    ("HARVEY NORMAN", "购物"),
    ("ZARA", "购物"),
    ("H&M", "购物"),
    # 娱乐
    ("GOLDEN VILLAGE", "娱乐"),
    ("SENTOSA DEVELOPME", "娱乐"),
    ("SHAW THEATRES", "娱乐"),
    ("CATHAY", "娱乐"),
    ("STEAM", "娱乐"),
    ("PLAYSTATION", "娱乐"),
    ("NINTENDO", "娱乐"),
    ("KTV", "娱乐"),
    # 教育
    ("UDEMY", "教育"),
    ("COURSERA", "教育"),
    ("POPULAR BOOK", "教育"),
    ("KINOKUNIYA", "教育"),
]
