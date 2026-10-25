"""Ollama 客户端：普通对话、流式对话、JSON 输出。"""
import json
import re

import requests

import config

THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text: str) -> str:
    """Qwen3 等模型会输出 <think>...</think> 思考过程，这里去掉。"""
    text = THINK_RE.sub("", text)
    if "<think>" in text:  # 没闭合的思考块
        text = text.split("<think>")[0]
    return text.strip()


class OllamaClient:
    def __init__(self, url=config.OLLAMA_URL, model=config.DEFAULT_MODEL, num_ctx=config.NUM_CTX):
        self.url = url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx

    # ---------- 基础 ----------
    def list_models(self):
        r = requests.get(f"{self.url}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    def is_up(self):
        try:
            self.list_models()
            return True
        except Exception:
            return False

    def _payload(self, messages, stream, temperature, json_mode):
        p = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
        }
        if json_mode:
            p["format"] = "json"
        return p

    # ---------- 非流式 ----------
    def chat(self, messages, temperature=0.2, json_mode=False) -> str:
        r = requests.post(
            f"{self.url}/api/chat",
            json=self._payload(messages, False, temperature, json_mode),
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return strip_think(r.json()["message"]["content"])

    def chat_json(self, messages, temperature=0.0):
        """要求模型输出 JSON，并尽量容错地解析。解析失败返回 None。"""
        text = self.chat(messages, temperature=temperature, json_mode=True)
        return parse_json_loose(text)

    # ---------- 流式（给聊天框用） ----------
    def chat_stream(self, messages, temperature=0.3):
        r = requests.post(
            f"{self.url}/api/chat",
            json=self._payload(messages, True, temperature, False),
            stream=True,
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()

        def pieces():
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                yield data.get("message", {}).get("content", "")
                if data.get("done"):
                    break

        yield from filter_think_stream(pieces())


def filter_think_stream(pieces):
    """在流式输出中过滤掉 <think>...</think>。"""
    buf, in_think, started = "", False, False
    for piece in pieces:
        buf += piece
        out = ""
        while True:
            if in_think:
                i = buf.find("</think>")
                if i == -1:
                    buf = buf[-8:]
                    break
                buf, in_think = buf[i + 8:], False
                continue
            i = buf.find("<think>")
            if i != -1:
                out += buf[:i]
                buf, in_think = buf[i + 7:], True
                continue
            k = buf.rfind("<", max(0, len(buf) - 7))
            if k != -1 and "<think>".startswith(buf[k:]):
                out, buf = out + buf[:k], buf[k:]
            else:
                out, buf = out + buf, ""
            break
        if out:
            if not started:
                out = out.lstrip()
                started = bool(out)
            if out:
                yield out
    if buf and not in_think:
        yield buf


def parse_json_loose(text):
    text = strip_think(text or "")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"[\{\[].*[\}\]]", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None
