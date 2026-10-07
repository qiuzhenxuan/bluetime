"""HTTP 工具（纯标准库）。

统一处理：超时、gzip 解压、JSON 解析、重试。
不引入 requests，是为了让云函数包更小、冷启动更快。
"""

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

_UA = "bluetime/0.1 (+blue-hour forecast engine)"


def http_get_json(base_url, params=None, timeout=None, retries=2, headers=None):
    """发起 GET 请求并解析 JSON。失败时抛 RuntimeError（含状态码与响应片段）。"""
    timeout = timeout or config.NETWORK["timeout_seconds"]
    url = base_url
    if params:
        url = base_url + "?" + urllib.parse.urlencode(params, doseq=True)

    req_headers = {
        "User-Agent": _UA,
        "Accept-Encoding": "gzip",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            last_err = RuntimeError(f"HTTP {e.code} {e.reason} :: {body}")
            # 4xx 不重试（除 429 限流）
            if 400 <= e.code < 500 and e.code != 429:
                raise last_err
        except Exception as e:                      # 网络/解析异常
            last_err = RuntimeError(f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(0.4 * (attempt + 1))

    raise last_err if last_err else RuntimeError("请求失败")
