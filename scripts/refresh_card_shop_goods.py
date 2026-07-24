import argparse
import csv
import gzip
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


FIELDNAMES = [
    "item_key",
    "item_url",
    "token",
    "token_fold",
    "shop_name",
    "shop_url",
    "goods_name",
    "category",
    "brand",
    "price",
    "stock",
    "stock_text",
    "status",
    "tags",
    "sold_24h",
    "goods_id",
    "updated_at",
    "captured_at",
    "source",
]

SHOP_API_BASE = "https://pay.ldxp.cn"
SOURCE_NAME = "ldxp_shop_api"
GOODS_TYPES = ("card", "article", "resource", "equity")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 RelayWatch/1.0"
)
ACW_BOX = [
    0xF, 0x23, 0x1D, 0x18, 0x21, 0x10, 0x1, 0x26, 0xA, 0x9,
    0x13, 0x1F, 0x28, 0x1B, 0x16, 0x17, 0x19, 0xD, 0x6, 0xB,
    0x27, 0x12, 0x14, 0x8, 0xE, 0x15, 0x20, 0x1A, 0x2, 0x1E,
    0x7, 0x4, 0x11, 0x5, 0x3, 0x1C, 0x22, 0x25, 0xC, 0x24,
]
ACW_KEY = "3000176000856006061501533003690027800375"
GLOBAL_COOKIE = ""
HTTP_BACKEND = os.environ.get("CARD_SHOP_HTTP_BACKEND", "auto").strip().lower() or "auto"
CURL_CFFI_IMPERSONATE = os.environ.get("CARD_SHOP_CURL_CFFI_IMPERSONATE", "chrome136")
SHOP_API_COOKIE = os.environ.get("CARD_SHOP_COOKIE", "").strip()
PROXY_INLINE = os.environ.get("CARD_SHOP_PROXIES", "").strip()
PROXY_FILE = os.environ.get("CARD_SHOP_PROXY_FILE", "").strip()
WAF_MARKERS = ("风控/HTML", "WAF 拦截", "http_bot_simple", "denied by", "403", "请求频率限制", "http_ratelimit")
RATE_LIMIT_MARKERS = ("http_ratelimit", "请求频率限制", "rate limit", "too many requests")
SHOP_API_TIMEOUT = float(os.environ.get("CARD_SHOP_API_TIMEOUT", "10"))
SHOP_API_RETRIES = max(int(os.environ.get("CARD_SHOP_API_RETRIES", "2") or 0), 0)
SHOP_API_RETRY_SLEEP = max(float(os.environ.get("CARD_SHOP_API_RETRY_SLEEP", "1") or 0), 0.0)
CHECK_FRONT_PAGE = os.environ.get("CARD_SHOP_CHECK_FRONT_PAGE", "1").strip().lower() not in {"0", "false", "no"}
SHOP_FRONT_UNAVAILABLE_MARKERS = ("店铺链接不存在", "商家已被关闭", "商家已被封禁", "该商家已被封禁")


class WafBlocked(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


class ShopClosedError(RuntimeError):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def unix_to_iso(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    return datetime.fromtimestamp(number, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_text(value):
    return server.fix_ldxp_text(value)


def clean_number(value):
    parsed = server.parse_ldxp_number(value)
    if parsed is None:
        return ""
    if float(parsed).is_integer():
        return str(int(parsed))
    return str(parsed)


def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv_atomic(path, fieldnames, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def load_proxy_pool(path="", inline=""):
    proxies = []
    raw_values = []
    if inline:
        raw_values.extend(re.split(r"[\n,]+", inline))
    if path and Path(path).exists():
        raw_values.extend(Path(path).read_text(encoding="utf-8", errors="ignore").splitlines())
    seen = set()
    for value in raw_values:
        item = str(value or "").strip()
        if not item or item.startswith("#"):
            continue
        if "://" not in item:
            item = "http://" + item
        if item in seen:
            continue
        seen.add(item)
        proxies.append(item)
    return proxies


def pick_proxy(proxy_pool, index):
    if not proxy_pool:
        return None
    return proxy_pool[(max(int(index or 1), 1) - 1) % len(proxy_pool)]


def response_text(response):
    data = response.read()
    if response.headers.get("Content-Encoding") == "gzip":
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
    return data.decode("utf-8", errors="replace")


def cookie_from_headers(headers):
    cookies = []
    for value in headers.get_all("Set-Cookie") or []:
        first = str(value).split(";", 1)[0].strip()
        if first:
            cookies.append(first)
    return "; ".join(cookies)


def merge_cookie(*values):
    merged = {}
    for value in values:
        for item in str(value or "").split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            key, val = item.split("=", 1)
            merged[key.strip()] = val.strip()
    return "; ".join(f"{key}={value}" for key, value in merged.items())


def acw_unsbox(arg):
    result = [""] * len(ACW_BOX)
    for index, char in enumerate(arg):
        for box_index, box_value in enumerate(ACW_BOX):
            if box_value == index + 1:
                result[box_index] = char
    return "".join(result)


def acw_hex_xor(left, right):
    result = ""
    for index in range(0, min(len(left), len(right)), 2):
        value = hex(int(left[index:index + 2], 16) ^ int(right[index:index + 2], 16))[2:]
        if len(value) == 1:
            value = "0" + value
        result += value
    return result


def solve_acw_cookie(html):
    match = re.search(r"var\s+arg1='([0-9A-Fa-f]+)'", html or "")
    if not match:
        return ""
    return "acw_sc__v2=" + acw_hex_xor(acw_unsbox(match.group(1)), ACW_KEY)


def is_rate_limited_text(value):
    lowered = str(value or "").lower()
    return any(marker.lower() in lowered for marker in RATE_LIMIT_MARKERS)


def effective_cookie():
    return merge_cookie(SHOP_API_COOKIE, GLOBAL_COOKIE)


def cookie_from_set_cookie_header(value):
    cookies = []
    for item in str(value or "").split(","):
        first = item.split(";", 1)[0].strip()
        if "=" in first:
            cookies.append(first)
    return "; ".join(cookies)


def apply_waf_cookie_from_body(body, extra_cookie="", headers=None):
    global GLOBAL_COOKIE
    acw_cookie = solve_acw_cookie(body)
    if extra_cookie or acw_cookie:
        GLOBAL_COOKIE = merge_cookie(GLOBAL_COOKIE, extra_cookie, acw_cookie)
        if headers is not None:
            cookie = effective_cookie()
            if cookie:
                headers["Cookie"] = cookie
        return bool(acw_cookie)
    return False


def refresh_waf_cookie(token):
    global GLOBAL_COOKIE
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": f"{SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}",
    }
    if GLOBAL_COOKIE:
        headers["Cookie"] = GLOBAL_COOKIE
    request = urllib.request.Request(f"{SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}", headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response_text(response)
        header_cookie = cookie_from_headers(response.headers)
    acw_cookie = solve_acw_cookie(html)
    if acw_cookie:
        GLOBAL_COOKIE = merge_cookie(GLOBAL_COOKIE, header_cookie, acw_cookie)
        return True
    if header_cookie:
        GLOBAL_COOKIE = merge_cookie(GLOBAL_COOKIE, header_cookie)
    return False


def get_shop_tokens(shops_path, limit=0, shuffle=False, only_tokens=None, offset=0):
    only = {item.strip().lower() for item in (only_tokens or []) if item.strip()}
    tokens = []
    seen = set()
    for row in read_csv(shops_path):
        token = str(row.get("token") or row.get("token_aliases") or "").strip()
        if not token:
            shop_url = str(row.get("shop_url") or "")
            token = shop_url.rstrip("/").split("/")[-1].strip()
        if not token:
            continue
        folded = token.lower()
        if only and folded not in only:
            continue
        if folded in seen:
            continue
        seen.add(folded)
        tokens.append(
            {
                "token": token,
                "token_fold": folded,
                "shop_name": clean_text(row.get("shop_name")),
                "shop_url": row.get("shop_url") or f"{SHOP_API_BASE}/shop/{urllib.parse.quote(token)}",
                "raw": row,
            }
        )
    if shuffle:
        random.shuffle(tokens)
    if offset and offset > 0 and tokens:
        tokens = tokens[offset:]
    if limit and limit > 0:
        tokens = tokens[:limit]
    return tokens


def read_state_offset(path):
    if not path:
        return 0
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return int(data.get("next_offset") or 0)
    except Exception:
        return 0


def write_state_offset(path, next_offset, total_tokens, report=None):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "next_offset": int(next_offset or 0),
        "total_tokens": int(total_tokens or 0),
        "updated_at": now_iso(),
        "last_report": report or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def removed_shop_tokens_path():
    path = os.environ.get("CARD_REMOVED_SHOPS_FILE", "").strip()
    if path:
        return Path(path)
    return Path("/root/relaywatch-deploy/state/card_removed_shop_tokens_current.txt")


def write_removed_shop_tokens(tokens):
    current = set()
    path = removed_shop_tokens_path()
    if path.exists():
        try:
            current.update(
                line.strip().lower()
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            )
        except Exception:
            pass
    current.update(str(token or "").strip().lower() for token in tokens if str(token or "").strip())
    token_list = sorted(token for token in current if token)
    if not token_list:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(token_list) + "\n", encoding="utf-8")


def is_shop_closed_text(value):
    text = clean_text(value if value is not None else "")
    lowered = text.lower()
    markers = (
        "店铺打烊",
        "店铺已打烊",
        "暂停营业",
        "歇业",
        "商家已被关闭",
        "商家已被封禁",
        "该商家已被封禁",
        "店铺不存在",
        "店铺链接不存在",
        "shop closed",
        "store closed",
        "temporarily closed",
        "shop unavailable",
        "not found",
    )
    return any(marker.lower() in lowered for marker in markers)


def post_shop_api(path, token, payload, timeout=SHOP_API_TIMEOUT, retries=SHOP_API_RETRIES, proxy=None):
    if HTTP_BACKEND in ("curl_cffi", "auto"):
        try:
            return post_shop_api_curl_cffi(path, token, payload, timeout=timeout, retries=retries, proxy=proxy)
        except Exception:
            if HTTP_BACKEND == "curl_cffi":
                raise

    if HTTP_BACKEND in ("tls_client", "auto"):
        try:
            return post_shop_api_tls_client(path, token, payload, timeout=timeout, retries=retries, proxy=proxy)
        except Exception:
            if HTTP_BACKEND == "tls_client":
                raise

    if HTTP_BACKEND in ("curl", "auto") and shutil.which("curl"):
        try:
            return post_shop_api_curl(path, token, payload, timeout=timeout, retries=retries, proxy=proxy)
        except Exception:
            if HTTP_BACKEND == "curl":
                raise
            # auto 模式下 curl 异常再走 urllib，方便本地没有 curl 或 curl 临时失败时兜底。

    return post_shop_api_urllib(path, token, payload, timeout=timeout, retries=retries)


def validate_shop_api_body(path, body):
    html = str(body or "").lstrip().lower()
    if (
        "var arg1=" in body
        or "X-Tengine-Error" in body
        or html.startswith("<!doctypehtml")
        or html.startswith("<!doctype html")
        or html.startswith("<html")
        or "<script" in html[:1000]
    ):
        raise WafBlocked("pay.ldxp.cn 返回了风控/HTML 页面，没有拿到 JSON 店铺数据")
    parsed = json.loads(body)
    if int(parsed.get("code") or 0) != 1:
        message = parsed.get("msg") or parsed.get("message") or ""
        if is_shop_closed_text(message) or is_shop_closed_text(parsed.get("data")):
            raise ShopClosedError(message or f"{path} shop closed")
        raise RuntimeError(f"{path} code={parsed.get('code')} msg={message}")
    return parsed.get("data")


def post_shop_api_curl_cffi(path, token, payload, timeout=20, retries=2, proxy=None):
    try:
        from curl_cffi import requests as curl_requests
    except Exception as exc:
        raise RuntimeError(f"curl_cffi 不可用：{exc}") from exc
    url = f"{SHOP_API_BASE}{path}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": SHOP_API_BASE,
        "Referer": f"{SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}",
    }
    cookie = effective_cookie()
    if cookie:
        headers["Cookie"] = cookie
    last_error = None
    waf_refreshed = False
    for attempt in range(retries + 1):
        try:
            request_kwargs = {}
            if proxy:
                request_kwargs["proxies"] = {"http": proxy, "https": proxy}
            response = curl_requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
                verify=False,
                impersonate=CURL_CFFI_IMPERSONATE,
                **request_kwargs,
            )
            tengine_error = response.headers.get("x-tengine-error") or ""
            if is_rate_limited_text(tengine_error) or is_rate_limited_text(response.text):
                raise RateLimited("pay.ldxp.cn 请求频率限制，当前出口访问过快，需要降并发或等待解除")
            try:
                return validate_shop_api_body(path, response.text)
            except WafBlocked:
                extra_cookie = cookie_from_set_cookie_header(response.headers.get("set-cookie"))
                if not waf_refreshed and apply_waf_cookie_from_body(response.text, extra_cookie, headers):
                    waf_refreshed = True
                    continue
                raise
        except Exception as exc:
            last_error = exc
            if isinstance(exc, RateLimited):
                break
            if attempt < retries and SHOP_API_RETRY_SLEEP > 0:
                time.sleep(SHOP_API_RETRY_SLEEP + attempt * SHOP_API_RETRY_SLEEP * 1.5)
    raise RuntimeError(f"{path} failed for {token}: {last_error}") from last_error


def post_shop_api_tls_client(path, token, payload, timeout=20, retries=2, proxy=None):
    try:
        import tls_client
    except Exception as exc:
        raise RuntimeError(f"tls_client 不可用：{exc}") from exc
    url = f"{SHOP_API_BASE}{path}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": SHOP_API_BASE,
        "Referer": f"{SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}",
    }
    cookie = effective_cookie()
    if cookie:
        headers["Cookie"] = cookie
    last_error = None
    waf_refreshed = False
    for attempt in range(retries + 1):
        try:
            session = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
            kwargs = {}
            if proxy:
                kwargs["proxy"] = proxy
            response = session.post(url, json=payload, headers=headers, timeout_seconds=timeout, **kwargs)
            tengine_error = response.headers.get("x-tengine-error") or ""
            if is_rate_limited_text(tengine_error) or is_rate_limited_text(response.text):
                raise RateLimited("pay.ldxp.cn 请求频率限制，当前出口访问过快，需要降并发或等待解除")
            try:
                return validate_shop_api_body(path, response.text)
            except WafBlocked:
                extra_cookie = cookie_from_set_cookie_header(response.headers.get("set-cookie"))
                if not waf_refreshed and apply_waf_cookie_from_body(response.text, extra_cookie, headers):
                    waf_refreshed = True
                    continue
                raise
        except Exception as exc:
            last_error = exc
            if isinstance(exc, RateLimited):
                break
            if attempt < retries and SHOP_API_RETRY_SLEEP > 0:
                time.sleep(SHOP_API_RETRY_SLEEP + attempt * SHOP_API_RETRY_SLEEP * 1.5)
    raise RuntimeError(f"{path} failed for {token}: {last_error}") from last_error


def post_shop_api_curl(path, token, payload, timeout=20, retries=2, proxy=None):
    url = f"{SHOP_API_BASE}{path}"
    last_error = None
    waf_refreshed = False
    for attempt in range(retries + 1):
        tmp_name = ""
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                tmp_name = handle.name
            cmd = [
                "curl",
                "-k",
                "-sS",
                "--compressed",
                "--max-time",
                str(max(int(timeout), 1)),
                "-A",
                USER_AGENT,
                "-H",
                "Accept: application/json, text/plain, */*",
                "-H",
                "Content-Type: application/json",
                "-H",
                f"Origin: {SHOP_API_BASE}",
                "-H",
                f"Referer: {SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}",
                "--data-binary",
                f"@{tmp_name}",
                url,
            ]
            cookie = effective_cookie()
            if cookie:
                cmd[-3:-3] = ["-H", f"Cookie: {cookie}"]
            if proxy:
                cmd[-3:-3] = ["-x", proxy]
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            body = output.decode("utf-8", errors="replace")
            if is_rate_limited_text(body):
                raise RateLimited("pay.ldxp.cn 请求频率限制，当前出口访问过快，需要降并发或等待解除")
            try:
                return validate_shop_api_body(path, body)
            except WafBlocked:
                if not waf_refreshed and apply_waf_cookie_from_body(body):
                    waf_refreshed = True
                    continue
                raise
        except (subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if isinstance(exc, RateLimited):
                break
            if attempt < retries and SHOP_API_RETRY_SLEEP > 0:
                time.sleep(SHOP_API_RETRY_SLEEP + attempt * SHOP_API_RETRY_SLEEP * 1.5)
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
    raise RuntimeError(f"{path} failed for {token}: {last_error}") from last_error


def post_shop_api_urllib(path, token, payload, timeout=20, retries=2):
    global GLOBAL_COOKIE
    url = f"{SHOP_API_BASE}{path}"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": SHOP_API_BASE,
        "Referer": f"{SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}",
    }
    if GLOBAL_COOKIE:
        headers["Cookie"] = effective_cookie()
    last_error = None
    waf_refreshed = False
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response_text(response)
                extra_cookie = cookie_from_headers(response.headers)
                if extra_cookie:
                    GLOBAL_COOKIE = merge_cookie(GLOBAL_COOKIE, extra_cookie)
                    headers["Cookie"] = effective_cookie()
                tengine_error = response.headers.get("X-Tengine-Error") or ""
                if is_rate_limited_text(tengine_error) or is_rate_limited_text(body):
                    raise RateLimited("pay.ldxp.cn 请求频率限制，当前出口访问过快，需要降并发或等待解除")
                if response.headers.get("X-Tengine-Error") or "var arg1=" in body:
                    if not waf_refreshed and apply_waf_cookie_from_body(body, extra_cookie, headers):
                        waf_refreshed = True
                        continue
                    if not waf_refreshed and refresh_waf_cookie(token):
                        headers["Cookie"] = effective_cookie()
                        waf_refreshed = True
                        continue
                    raise RuntimeError("pay.ldxp.cn WAF 拦截，当前服务器直连拿不到店铺自身接口数据")
                parsed = json.loads(body)
                if int(parsed.get("code") or 0) != 1:
                    raise RuntimeError(f"{path} code={parsed.get('code')} msg={parsed.get('msg')}")
                return parsed.get("data")
        except urllib.error.HTTPError as exc:
            try:
                body = response_text(exc)
            except Exception:
                body = ""
            if exc.code in (403, 412) or exc.headers.get("X-Tengine-Error") or "var arg1=" in body:
                if is_rate_limited_text(exc.headers.get("X-Tengine-Error")) or is_rate_limited_text(body):
                    last_error = RateLimited("pay.ldxp.cn 请求频率限制，当前出口访问过快，需要降并发或等待解除")
                    break
                last_error = RuntimeError("pay.ldxp.cn WAF 拦截，当前服务器直连拿不到店铺自身接口数据")
                if not waf_refreshed:
                    if apply_waf_cookie_from_body(body, cookie_from_headers(exc.headers), headers):
                        waf_refreshed = True
                        continue
                    try:
                        if refresh_waf_cookie(token):
                            headers["Cookie"] = effective_cookie()
                            waf_refreshed = True
                            continue
                    except Exception:
                        pass
                break
            last_error = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if isinstance(exc, RateLimited):
                break
            if attempt < retries and SHOP_API_RETRY_SLEEP > 0:
                time.sleep(SHOP_API_RETRY_SLEEP + attempt * SHOP_API_RETRY_SLEEP * 1.5)
    raise RuntimeError(f"{path} failed for {token}: {last_error}") from last_error


def shop_goods_types(info):
    data = info or {}
    ordered = [item for item in (data.get("goods_type_sort") or []) if item in GOODS_TYPES]
    for item in GOODS_TYPES:
        if item not in ordered:
            ordered.append(item)
    result = []
    for goods_type in ordered:
        count = server.parse_ldxp_number(data.get(f"{goods_type}_count"))
        if count is None:
            if goods_type == "card" and server.parse_ldxp_number(data.get("goods_count")):
                result.append(goods_type)
            continue
        if count > 0:
            result.append(goods_type)
    return result or ["card"]


def fetch_goods_page(token, goods_type, current, page_size, category_id="", proxy=None):
    return post_shop_api(
        "/shopApi/Shop/goodsList",
        token,
        {
            "token": token,
            "keywords": "",
            "category_id": category_id,
            "goods_type": goods_type,
            "current": current,
            "pageSize": page_size,
        },
        proxy=proxy,
    )


def fetch_categories(token, goods_type, proxy=None):
    try:
        data = post_shop_api(
            "/shopApi/Shop/categoryList",
            token,
            {"token": token, "goods_type": goods_type, "category_key": ""},
            retries=1,
            proxy=proxy,
        )
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_public_page(url, token="", proxy=None, timeout=6):
    try:
        from curl_cffi import requests as curl_requests
        kwargs = {}
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        response = curl_requests.get(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}" if token else SHOP_API_BASE,
            },
            timeout=timeout,
            verify=False,
            impersonate=CURL_CFFI_IMPERSONATE,
            **kwargs,
        )
        return response.text or ""
    except Exception:
        return ""


def assert_shop_front_available(token, proxy=None):
    if not CHECK_FRONT_PAGE:
        return
    body = fetch_public_page(f"{SHOP_API_BASE}/shop/{urllib.parse.quote(str(token))}", token=token, proxy=proxy)
    if not body:
        return
    if any(marker in body for marker in SHOP_FRONT_UNAVAILABLE_MARKERS) or is_shop_closed_text(body):
        raise ShopClosedError("店铺已打烊或已关闭")


def normalize_goods_row(item, token, fallback_shop, captured_at, goods_type=""):
    item = item or {}
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    category = item.get("category") if isinstance(item.get("category"), dict) else {}
    extend = item.get("extend") if isinstance(item.get("extend"), dict) else {}
    goods_key = str(item.get("goods_key") or "").strip()
    item_url = item.get("link") or (f"{SHOP_API_BASE}/item/{goods_key}" if goods_key else "")
    shop_name = clean_text(user.get("nickname") or fallback_shop.get("shop_name"))
    token_value = str(user.get("token") or token).strip()
    stock_value = extend.get("stock_count")
    stock = clean_number(stock_value)
    if stock != "":
        stock_number = float(stock)
        status = "online" if stock_number > 0 else "out_of_stock"
        if stock_number <= 0:
            stock_text = "缺货"
        elif stock_number <= 5:
            stock_text = "库存少量"
        elif stock_number <= 20:
            stock_text = "库存一般"
        else:
            stock_text = "库存充足"
    else:
        status = "online"
        stock_text = "库存充足"
    category_name = clean_text(category.get("name") or goods_type)
    tags = ",".join(item for item in [str(item.get("goods_type") or goods_type), category_name] if item)
    return {
        "item_key": goods_key,
        "item_url": item_url,
        "token": token_value,
        "token_fold": token_value.lower(),
        "shop_name": shop_name,
        "shop_url": user.get("link") or fallback_shop.get("shop_url") or f"{SHOP_API_BASE}/shop/{token_value}",
        "goods_name": clean_text(item.get("name")),
        "category": category_name,
        "brand": "",
        "price": clean_number(item.get("price")),
        "stock": stock,
        "stock_text": stock_text,
        "status": status,
        "tags": tags,
        "sold_24h": "",
        "goods_id": goods_key or str(item.get("id") or ""),
        "updated_at": unix_to_iso(item.get("update_time") or item.get("create_time")),
        "captured_at": captured_at,
        "source": SOURCE_NAME,
    }


def fetch_shop_goods(shop, page_size=100, max_pages=200, sleep_seconds=0.0, proxy=None):
    token = shop["token"]
    captured_at = now_iso()
    info = post_shop_api("/shopApi/Shop/info", token, {"token": token}, retries=SHOP_API_RETRIES, proxy=proxy)
    if isinstance(info, dict):
        shop["shop_name"] = clean_text(info.get("nickname") or shop.get("shop_name"))
        shop["shop_url"] = info.get("link") or shop.get("shop_url") or f"{SHOP_API_BASE}/shop/{token}"
    assert_shop_front_available(token, proxy=proxy)
    rows = []
    seen = set()
    for goods_type in shop_goods_types(info):
        page = 1
        total = None
        first_page_seen = False
        while page <= max_pages:
            data = fetch_goods_page(token, goods_type, page, page_size, proxy=proxy)
            if not isinstance(data, dict):
                break
            batch = data.get("list") or []
            if total is None:
                total = int(server.parse_ldxp_number(data.get("total")) or 0)
            if not batch:
                break
            first_page_seen = True
            for item in batch:
                row = normalize_goods_row(item, token, shop, captured_at, goods_type)
                unique = (row.get("token_fold"), row.get("item_key") or row.get("item_url") or row.get("goods_name"))
                if unique in seen:
                    continue
                seen.add(unique)
                rows.append(row)
            if total and page * page_size >= total:
                break
            if len(batch) < page_size:
                break
            page += 1
            if sleep_seconds:
                time.sleep(sleep_seconds)

        # 极少数店铺可能不支持空 category_id；首屏没数据时按分类兜底扫一遍。
        if not first_page_seen:
            for category in fetch_categories(token, goods_type, proxy=proxy):
                category_id = category.get("id")
                if not category_id:
                    continue
                page = 1
                while page <= max_pages:
                    data = fetch_goods_page(token, goods_type, page, page_size, str(category_id), proxy=proxy)
                    if not isinstance(data, dict):
                        break
                    batch = data.get("list") or []
                    if not batch:
                        break
                    for item in batch:
                        row = normalize_goods_row(item, token, shop, captured_at, goods_type)
                        if not row.get("category"):
                            row["category"] = clean_text(category.get("name"))
                        unique = (
                            row.get("token_fold"),
                            row.get("item_key") or row.get("item_url") or row.get("goods_name"),
                        )
                        if unique in seen:
                            continue
                        seen.add(unique)
                        rows.append(row)
                    total = int(server.parse_ldxp_number(data.get("total")) or 0)
                    if total and page * page_size >= total:
                        break
                    if len(batch) < page_size:
                        break
                    page += 1
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
    return rows, info or {}


def source_stats(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("source") or ""].append(row)
    stats = []
    for source, items in sorted(grouped.items()):
        unique_items = {row.get("item_key") or row.get("item_url") or row.get("goods_id") for row in items}
        unique_shops = {row.get("token_fold") or row.get("token") or row.get("shop_url") for row in items}
        stats.append(
            {
                "source": source,
                "raw_rows": len(items),
                "unique_items": len([item for item in unique_items if item]),
                "unique_shops": len([shop for shop in unique_shops if shop]),
            }
        )
    return stats


def preserve_failed_or_unvisited_rows(new_rows, old_rows, refreshed_tokens, failed_tokens=None, replace_only_shop_api=False):
    if not old_rows:
        return new_rows
    refreshed = {item.lower() for item in refreshed_tokens}
    failed = {item.lower() for item in (failed_tokens or [])}
    seen = {
        (
            row.get("source") or "",
            (row.get("token_fold") or row.get("token") or "").lower(),
            row.get("item_key") or row.get("item_url") or row.get("goods_id") or "",
        )
        for row in new_rows
    }
    preserved = []
    for row in old_rows:
        token = (row.get("token_fold") or row.get("token") or "").lower()
        source = row.get("source") or ""
        # ?????????????????????????????????????
        # ???????????????????????????/??????????
        if token in refreshed or token in failed:
            if replace_only_shop_api and source != SOURCE_NAME:
                pass
            else:
                continue
        key = (source, token, row.get("item_key") or row.get("item_url") or row.get("goods_id") or "")
        if key in seen:
            continue
        seen.add(key)
        preserved.append({field: row.get(field, "") for field in FIELDNAMES})
    if preserved:
        print(f"保留未刷新/失败店铺旧数据：{len(preserved)} 条")
    return new_rows + preserved


def refresh_shop_goods(args):
    only_tokens = [item.strip() for item in (args.tokens or "").split(",") if item.strip()]
    if getattr(args, "tokens_file", ""):
        try:
            with open(args.tokens_file, encoding="utf-8", errors="ignore") as f:
                only_tokens.extend(line.strip() for line in f if line.strip())
        except FileNotFoundError:
            pass
    all_tokens = get_shop_tokens(
        args.shops,
        limit=0,
        shuffle=args.shuffle,
        only_tokens=only_tokens,
        offset=0,
    )
    state_offset = read_state_offset(args.state_file) if args.state_file and not args.offset else 0
    effective_offset = int(args.offset or state_offset or 0)
    tokens = get_shop_tokens(
        args.shops,
        limit=args.limit_shops,
        shuffle=args.shuffle,
        only_tokens=only_tokens,
        offset=effective_offset,
    )
    if not tokens:
        raise RuntimeError("没有找到可刷新的店铺 token")

    rows = []
    refreshed_tokens = []
    successes = []
    failed = []
    closed = []
    closed_tokens = set()
    empty = []
    stopped_by_waf = False
    processed_count = 0
    consecutive_waf = 0
    proxy_pool = load_proxy_pool(args.proxy_file or PROXY_FILE, args.proxies or PROXY_INLINE)
    if proxy_pool:
        print(f"proxy_pool enabled: {len(proxy_pool)} proxies")

    def is_waf_error(error):
        text = str(error or "")
        return any(marker in text for marker in WAF_MARKERS)

    def proxy_candidates(index):
        """Return a small retry chain for one shop.

        Old logic bound one shop to one proxy; when that proxy was dead the
        shop was incorrectly marked failed.  We now rotate a few proxies and
        use direct access as the final fallback so a bad proxy does not make
        price/stock stale.
        """
        candidates = []
        if proxy_pool:
            start = (max(int(effective_offset + index or 1), 1) - 1) % len(proxy_pool)
            retry_count = min(len(proxy_pool), max(int(getattr(args, "proxy_retries", 4) or 0), 1))
            for offset in range(retry_count):
                candidates.append(proxy_pool[(start + offset) % len(proxy_pool)])
        if (not proxy_pool) or getattr(args, "allow_direct_fallback", False):
            candidates.append(None)
        deduped = []
        seen = set()
        for proxy in candidates:
            key = proxy or "__direct__"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(proxy)
        return deduped

    def run_one(index, shop):
        token = shop["token"]
        last_error = None
        tried = []
        for proxy in proxy_candidates(index):
            tried.append(proxy or "direct")
            try:
                shop_rows, info = fetch_shop_goods(
                    shop,
                    page_size=args.page_size,
                    max_pages=args.max_pages,
                    sleep_seconds=args.page_sleep,
                    proxy=proxy,
                )
                goods_count = len(shop_rows)
                shop_name = clean_text((info or {}).get("nickname") or shop.get("shop_name"))
                return {
                    "index": index,
                    "token": token,
                    "rows": shop_rows,
                    "info": info,
                    "shop_name": shop_name,
                    "goods_count": goods_count,
                    "error": "",
                    "closed": False,
                    "proxy": proxy or "",
                    "tried": tried,
                }
            except ShopClosedError as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                error_text = str(exc)
                if is_waf_error(error_text):
                    # WAF 命中时继续换代理/直连尝试，尽量拿到 JSON。
                    continue
                # 网络超时、代理连接失败、代理返回空内容也继续换。
                continue
        return {
            "index": index,
            "token": token,
            "rows": [],
            "info": {},
            "shop_name": shop.get("shop_name") or "",
            "goods_count": 0,
            "error": f"{last_error}; tried={','.join(tried)}",
            "closed": isinstance(last_error, ShopClosedError),
            "proxy": "",
            "tried": tried,
        }

    def handle_result(result):
        nonlocal stopped_by_waf, processed_count, consecutive_waf
        token = result["token"]
        processed_count = max(processed_count, int(result.get("index") or 0))
        if result.get("closed"):
            closed.append({"token": token, "error": result["error"]})
            closed_tokens.add(token.lower())
            print(f"[{result['index']}/{len(tokens)}] {token} 打烊：{result['error']}", file=sys.stderr)
            consecutive_waf = 0
            return
        if result.get("error"):
            failed.append({"token": token, "error": result["error"]})
            print(f"[{result['index']}/{len(tokens)}] {token} 失败：{result['error']}", file=sys.stderr)
            if args.stop_on_waf and is_waf_error(result["error"]):
                consecutive_waf += 1
                if not proxy_pool or consecutive_waf >= args.max_consecutive_waf:
                    stopped_by_waf = True
            else:
                consecutive_waf = 0
            return
        consecutive_waf = 0
        shop_rows = result.get("rows") or []
        rows.extend(shop_rows)
        refreshed_tokens.append(token)
        successes.append({"token": token, "goods_count": len(shop_rows)})
        if not shop_rows:
            empty.append(token)
        print(f"[{result['index']}/{len(tokens)}] {token} {result.get('shop_name') or ''} 商品 {len(shop_rows)}")

    workers = max(int(args.workers or 1), 1)
    if workers == 1:
        for index, shop in enumerate(tokens, start=1):
            handle_result(run_one(index, shop))
            if stopped_by_waf:
                print(f"检测到 pay.ldxp.cn 风控，本轮在第 {index}/{len(tokens)} 个店铺停止，避免继续触发。", file=sys.stderr)
                break
            if args.shop_sleep:
                low = max(float(args.shop_sleep) * 0.5, 0)
                high = max(float(args.shop_sleep) * 1.5, low)
                time.sleep(random.uniform(low, high))
    else:
        print(f"并发刷新店铺：workers={workers}, total={len(tokens)}")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(run_one, index, shop): index
                for index, shop in enumerate(tokens, start=1)
            }
            for future in as_completed(future_map):
                handle_result(future.result())

    old_rows = read_csv(args.goods_out)
    if args.preserve_failed:
        rows = preserve_failed_or_unvisited_rows(
            rows,
            old_rows,
            refreshed_tokens,
            failed_tokens=[item.get("token") for item in failed],
            replace_only_shop_api=args.replace_only_shop_api,
        )

    if len(rows) < args.min_rows:
        raise RuntimeError(f"刷新后只有 {len(rows)} 条，低于安全阈值 {args.min_rows}，不写入")
    fresh_rows = sum(1 for row in rows if row.get("source") == SOURCE_NAME)
    if fresh_rows < args.min_fresh_rows:
        raise RuntimeError(
            f"店铺自身接口新数据只有 {fresh_rows} 条，低于安全阈值 {args.min_fresh_rows}，不写入；"
            "大概率是服务器直连 pay.ldxp.cn 被 WAF 拦截，需要给脚本配置可访问该站的 HTTPS_PROXY/ALL_PROXY。"
        )

    stats = source_stats(rows)
    report = {
        "shops_total": len(tokens),
        "shops_refreshed": len(refreshed_tokens),
        "shops_failed": len(failed),
        "shops_closed": len(closed),
        "shops_empty": len(empty),
        "rows": len(rows),
        "fresh_rows": fresh_rows,
        "sources": stats,
        "failed_sample": failed[:20],
        "closed_sample": closed[:20],
        "empty_sample": empty[:20],
        "offset": effective_offset,
        "next_offset": ((effective_offset + (processed_count or len(tokens))) % len(all_tokens)) if all_tokens else 0,
        "stopped_by_waf": stopped_by_waf,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.dry_run:
        return report
    write_csv_atomic(args.goods_out, FIELDNAMES, rows)
    write_csv_atomic(args.stats_out, ["source", "raw_rows", "unique_items", "unique_shops"], stats)
    if args.failures_out:
        write_csv_atomic(args.failures_out, ["token", "error"], failed)
    if getattr(args, "closed_out", ""):
        write_csv_atomic(args.closed_out, ["token", "error"], closed)
    if args.successes_out:
        write_csv_atomic(args.successes_out, ["token", "goods_count"], successes)
    if args.state_file:
        write_state_offset(args.state_file, report["next_offset"], len(all_tokens), report)
    write_removed_shop_tokens(closed_tokens)
    return report


def main():
    parser = argparse.ArgumentParser(description="逐个刷新链动小铺现有店铺自己的商品列表、价格、库存和状态。")
    parser.add_argument("--shops", default=str(server.LDXP_SHOPS_PATH), help="现有店铺 CSV")
    parser.add_argument("--goods-out", default=str(server.LDXP_GOODS_PATH), help="商品 CSV 输出")
    parser.add_argument("--stats-out", default=str(server.LDXP_GOODS_SOURCE_STATS_PATH), help="来源统计 CSV 输出")
    parser.add_argument("--failures-out", default=str(server.LDXP_DATA_DIR / "ldxp_shop_api_failures.csv"))
    parser.add_argument("--successes-out", default="", help="成功刷新店铺 CSV，字段 token/goods_count")
    parser.add_argument("--tokens", default="", help="只刷新指定 token，逗号分隔")
    parser.add_argument("--tokens-file", default="", help="只刷新指定 token 文件，每行一个")
    parser.add_argument("--offset", type=int, default=0, help="从店铺列表指定 offset 开始刷新")
    parser.add_argument("--state-file", default="", help="记录下一批店铺 offset 的状态文件")
    parser.add_argument("--limit-shops", type=int, default=0, help="限制刷新店铺数量，0 表示全部")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--shop-sleep", type=float, default=0.05)
    parser.add_argument("--page-sleep", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=int(os.environ.get("CARD_SHOP_REFRESH_WORKERS", "1")))
    parser.add_argument("--proxy-file", default="", help="代理池文件，每行一个 http/socks5 代理")
    parser.add_argument("--proxies", default="", help="内联代理池，逗号或换行分隔")
    parser.add_argument(
        "--proxy-retries",
        type=int,
        default=int(os.environ.get("CARD_SHOP_PROXY_RETRIES", "1")),
        help="每个店铺最多轮换尝试几个代理，最后会直连兜底",
    )
    parser.add_argument("--stop-on-waf", dest="stop_on_waf", action="store_true", default=True)
    parser.add_argument("--no-stop-on-waf", dest="stop_on_waf", action="store_false")
    parser.add_argument(
        "--allow-direct-fallback",
        action="store_true",
        help="代理失败后允许直连兜底；采集 pay.ldxp.cn 时默认关闭，避免服务器 IP 被一起风控",
    )
    parser.add_argument("--max-consecutive-waf", type=int, default=int(os.environ.get("CARD_SHOP_MAX_CONSECUTIVE_WAF", "5")))
    parser.add_argument("--min-rows", type=int, default=1000)
    parser.add_argument("--min-fresh-rows", type=int, default=100)
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--no-preserve-failed", dest="preserve_failed", action="store_false")
    parser.add_argument(
        "--replace-only-shop-api",
        action="store_true",
        help="刷新成功的店铺只替换旧 ldxp_shop_api 行，保留第三方聚合源行；默认刷新成功后按店铺整体替换。",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(preserve_failed=True)
    args = parser.parse_args()
    refresh_shop_goods(args)


if __name__ == "__main__":
    main()
