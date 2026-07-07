import argparse
import calendar
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


PROVIDERS = [
    ("OpenAI", ["gpt", "o1", "o3", "o4", "codex"]),
    ("Claude", ["claude", "sonnet", "opus", "haiku"]),
    ("DeepSeek", ["deepseek"]),
    ("Gemini", ["gemini"]),
    ("Qwen", ["qwen", "qwq", "通义"]),
    ("Doubao", ["doubao", "seed"]),
    ("Grok", ["grok", "xai"]),
    ("GLM", ["glm"]),
    ("Kimi", ["kimi", "moonshot"]),
]

SECOND_LEVEL_SUFFIXES = {
    "ac",
    "co",
    "com",
    "edu",
    "gov",
    "net",
    "org",
}

COUNTRY_SUFFIXES = {
    "au",
    "br",
    "cn",
    "hk",
    "in",
    "jp",
    "kr",
    "nz",
    "sg",
    "tw",
    "uk",
    "za",
}

NOTICE_RULES = [
    {
        "tag": "站点上线",
        "strong": ["本站正式上线", "平台正式上线", "站点正式上线", "网站正式上线"],
        "all": [["欢迎来到", "正式上线"], ["欢迎使用", "新站"], ["平台", "正式上线"]],
    },
    {
        "tag": "模型上架",
        "strong": ["模型上架", "模型新增", "新增模型", "新模型", "模型上线", "上线模型", "新接入模型", "模型开放"],
        "all": [
            ["上架", "模型"],
            ["新增", "模型"],
            ["开放", "模型"],
            ["新接入", "gpt"],
            ["新接入", "claude"],
            ["新接入", "deepseek"],
            ["新接入", "gemini"],
            ["新接入", "qwen"],
            ["新增", "gpt"],
            ["新增", "claude"],
            ["新增", "deepseek"],
            ["新增", "gemini"],
            ["新增", "qwen"],
            ["上架", "gpt"],
            ["上架", "claude"],
            ["上架", "deepseek"],
            ["上架", "gemini"],
            ["上架", "qwen"],
        ],
    },
    {
        "tag": "模型下架",
        "strong": ["模型下架", "下架模型", "模型停用", "停用模型", "暂停模型", "模型暂停", "移除模型", "关闭模型", "停止支持模型"],
        "all": [["下架", "模型"], ["停用", "模型"], ["移除", "模型"]],
    },
    {
        "tag": "价格调整",
        "strong": ["价格调整", "倍率", "计费", "费率", "扣费", "降价", "涨价", "调价", "下调", "上调", "免费额度", "恢复倍率"],
        "all": [["余额", "消耗"], ["费用", "调整"], ["价格", "调整"], ["价格", "恢复"]],
    },
    {
        "tag": "活动优惠",
        "strong": ["活动", "优惠", "限时", "福利", "折扣", "套餐", "兑换码", "注册送", "邀请好友", "充值返利", "返佣", "赠送"],
        "all": [["赠", "额度"], ["送", "额度"], ["获得", "额度"], ["额外", "获得"], ["充值", "送"], ["购买", "额外"], ["邀请", "奖励"]],
    },
    {
        "tag": "维护故障",
        "strong": ["维护", "故障", "修复", "波动", "服务异常", "接口异常", "模型异常", "节点异常", "系统异常", "超时", "拥堵", "排队", "线路切换"],
        "all": [["服务", "恢复"], ["接口", "恢复"], ["线路", "恢复"], ["节点", "异常"]],
    },
    {
        "tag": "风险",
        "exclude": ["绝不跑路", "不跑路", "不会跑路", "防失联", "防失联 & 报 bug"],
        "strong": ["跑路", "关站", "停止服务", "停止运营", "清退", "退款", "无法登录", "被墙", "风控", "域名更换", "余额处理", "停止受理"],
        "all": [["余额", "用完"], ["余额", "退款"], ["关闭", "站点"], ["关闭", "服务"], ["停止", "充值"]],
    },
    {
        "tag": "支付变更",
        "strong": ["微信支付", "支付宝", "支付方式", "充值通道", "支付通道", "扫码支付", "支付异常", "充值异常", "到账"],
        "all": [["支持", "支付"], ["新增", "支付"], ["充值", "扫码"], ["支付", "修复"]],
    },
    {
        "tag": "接入配置",
        "strong": ["base_url", "base url", "api 地址", "api地址", "接口地址", "令牌", "密钥", "客户端配置", "cherry studio", "lobechat", "claude code"],
        "all": [["令牌", "使用"], ["api", "文档"], ["base", "url"]],
    },
    {
        "tag": "域名迁移",
        "strong": ["域名更换", "更换域名", "入口更换", "新入口", "备用域名", "迁移", "主站迁移", "api 迁移", "api迁移"],
        "all": [["域名", "迁移"], ["入口", "迁移"], ["旧入口", "新入口"]],
    },
    {
        "tag": "更新",
        "strong": ["更新", "升级", "部署", "改版", "日志"],
        "all": [["系统", "升级"], ["平台", "更新"]],
    },
]

COMPLIANCE_KEYWORDS = [
    "清朗",
    "整治ai技术滥用",
    "严禁",
    "违法违规",
    "不实信息",
    "色情低俗",
    "未成年人",
    "内容标识",
    "生成内容标识",
    "使用规范",
    "合规",
]

GENERAL_NOTICE_KEYWORDS = [
    "欢迎使用",
    "欢迎来到",
    "正式上线",
    "新站开业",
    "新站上线",
    "开业公告",
    "平台公告",
    "系统公告",
    "服务承诺",
    "技术支持",
    "使用说明",
]

NOTICE_PREFIX_KEYWORDS = ["新站开业", "新站上线", "开业公告"]

"""
Avoid over-classifying notices.

The announcement feed should surface a few useful intelligence tags, not every
topic mentioned in a long marketing notice. General words like "API", "充值",
"分组", "支持", or "模型" are intentionally not standalone triggers.
"""

_DISABLED_NOTICE_RULES = [
    {
        "tag": "unused",
        "strong": [],
        "all": [
            ["支持", "gpt"],
            ["支持", "claude"],
            ["支持", "deepseek"],
            ["支持", "gemini"],
            ["支持", "qwen"],
            ["支持", "doubao"],
            ["支持", "grok"],
            ["支持", "glm"],
            ["支持", "kimi"],
        ],
    },
]


def stable_id(value):
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def load_json(path):
    file_path = Path(path)
    return json.loads(file_path.read_text(encoding="utf-8-sig"))


def iter_rows(path):
    file_path = Path(path)
    if file_path.suffix.lower() == ".jsonl":
        with file_path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    raw = load_json(file_path)
    rows = raw.get("rows", raw if isinstance(raw, list) else [])
    for row in rows:
        yield row


def parse_json_body(body):
    if not body:
        return None
    if isinstance(body, str):
        body = body.lstrip("\ufeff")
    try:
        return json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return None


def is_self_use_mode(endpoint):
    if endpoint.get("status") != 200:
        return False
    parsed = parse_json_body(endpoint.get("body") or "")
    data = parsed.get("data") if isinstance(parsed, dict) else {}
    if not isinstance(data, dict):
        return False
    return data.get("self_use_mode_enabled") is True


def registration_info(endpoint):
    if endpoint.get("status") != 200:
        return {
            "registration_status": "unknown",
            "register_enabled": None,
            "password_register_enabled": None,
        }
    parsed = parse_json_body(endpoint.get("body") or "")
    data = parsed.get("data") if isinstance(parsed, dict) else {}
    if not isinstance(data, dict):
        return {
            "registration_status": "unknown",
            "register_enabled": None,
            "password_register_enabled": None,
        }
    register_enabled = data.get("register_enabled")
    password_register_enabled = data.get("password_register_enabled")
    if data.get("self_use_mode_enabled") is True:
        status = "self_use"
    elif register_enabled is True:
        status = "open"
    elif register_enabled is False:
        status = "closed"
    elif password_register_enabled is True:
        status = "open"
    elif password_register_enabled is False:
        status = "closed"
    else:
        status = "unknown"
    return {
        "registration_status": status,
        "register_enabled": register_enabled if isinstance(register_enabled, bool) else None,
        "password_register_enabled": password_register_enabled if isinstance(password_register_enabled, bool) else None,
    }


def clean_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    value = re.sub(r"\s+", " ", value).strip()
    return value


ERROR_NOTICE_PATTERNS = (
    "HTTPSConnectionPool",
    "HTTPConnectionPool",
    "ConnectTimeout",
    "ConnectTimeoutError",
    "ReadTimeout",
    "TimeoutError",
    "NewConnectionError",
    "Max retries exceeded",
    "Failed to establish a new connection",
    "Cannot connect to host",
    "ClientConnectorError",
    "Connection refused",
    "connect timeout",
    "read timeout",
    "NameResolutionError",
)


def looks_like_error_text(value):
    text = value or ""
    return any(pattern.lower() in text.lower() for pattern in ERROR_NOTICE_PATTERNS)


def looks_like_error_json(parsed):
    if not isinstance(parsed, dict):
        return False
    if parsed.get("success") is False:
        return True
    error = parsed.get("error")
    if error not in (None, False, "", [], {}):
        return True
    for key in ("code", "status", "status_code"):
        if key not in parsed:
            continue
        value = parsed.get(key)
        if isinstance(value, bool) or value in (None, ""):
            continue
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            text = str(value).strip().lower()
            if text not in {"ok", "success"}:
                return True
            continue
        if numeric not in (0, 200):
            return True
    return False


def strip_html(value):
    if not value:
        return ""
    text = str(value)
    text = html.unescape(text)
    has_html = bool(re.search(r"(?is)<[a-z][a-z0-9:-]*(?:\s|/|>)", text))
    text = re.sub(r"(?is)<(script|style|noscript|svg|canvas)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    if has_html:
        text = re.sub(r"(?is)<h[1-6]\b[^>]*>(.*?)</h[1-6]>", lambda m: "\n\n### " + clean_text(re.sub(r"(?is)<[^>]+>", " ", m.group(1))) + "\n\n", text)
        text = re.sub(r"(?is)<li\b[^>]*>", "\n- ", text)
        text = re.sub(r"(?is)</li>", "\n", text)
        text = re.sub(r"(?is)<br\s*/?>", "\n", text)
        text = re.sub(r"(?is)</(p|div|tr|section|article|blockquote|ul|ol)>", "\n\n", text)
        text = re.sub(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", lambda m: f"[{clean_text(re.sub(r'(?is)<[^>]+>', ' ', m.group(2)))}]({m.group(1).strip()})", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
    # Some sites return full HTML where CSS survives outside a <style> block.
    text = re.sub(r"\b(?:body|html|h[1-6]|p|a|div|span|ul|ol|li|img|table|td|th)\s*\{[^{}]*\}", " ", text)
    # Many API notices store Markdown on one long line. Restore common block breaks.
    text = re.sub(r"\s+(#{1,6}\s+)", r"\n\n\1", text)
    text = re.sub(r"\s+([-*]\s+)", r"\n\1", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text


def site_name_from_origin(origin):
    host = urlparse(origin).netloc.split("@")[-1].split(":")[0]
    parts = host.split(".")
    if len(parts) >= 2:
        base = parts[-2]
    else:
        base = host
    return base.replace("-", " ").replace("_", " ").title() or host


def host_from_origin(origin):
    return urlparse(origin).netloc.split("@")[-1].split(":")[0].lower().strip(".")


def base_domain(host):
    host = host.lower().strip(".")
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return host
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL_SUFFIXES and parts[-1] in COUNTRY_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def representative_score(site):
    host = site.get("_host") or host_from_origin(site.get("origin", ""))
    root = site.get("root_domain") or base_domain(host)
    scheme = urlparse(site.get("origin", "")).scheme
    if host == root:
        host_rank = 0
    elif host == f"www.{root}":
        host_rank = 1
    elif host == f"api.{root}":
        host_rank = 2
    else:
        host_rank = 3
    scheme_rank = 0 if scheme == "https" else 1
    status_rank = {"online": 0, "partial": 1, "unknown": 2}.get(site.get("status"), 3)
    has_notice_rank = 0 if site.get("notice") else 1
    return (
        status_rank,
        host_rank,
        scheme_rank,
        -(site.get("model_count") or 0),
        has_notice_rank,
        len(host),
        site.get("origin", ""),
    )


def dedupe_sites(sites):
    grouped = defaultdict(list)
    for site in sites:
        grouped[site["root_domain"]].append(site)

    selected = []
    for root, items in grouped.items():
        selected.append(sorted(items, key=representative_score)[0])
    selected.sort(
        key=lambda item: (
            item["status"] != "online",
            item["lowest_ratio"] is None,
            item["lowest_ratio"] or 999999,
            item["root_domain"],
        )
    )
    for site in selected:
        site.pop("_host", None)
    return selected


def provider_for_model(model):
    lowered = model.lower()
    for provider, keys in PROVIDERS:
        if any(key in lowered for key in keys):
            return provider
    return "Other"


def provider_family(provider):
    lowered = (provider or "").lower()
    if any(key in lowered for key in ("anthropic", "claude")):
        return "anthropic"
    if any(key in lowered for key in ("openai", "gpt", "chatgpt", "codex")):
        return "openai"
    if "deepseek" in lowered:
        return "deepseek"
    if any(key in lowered for key in ("google", "gemini")):
        return "gemini"
    if any(key in lowered for key in ("qwen", "alibaba")):
        return "qwen"
    if any(key in lowered for key in ("doubao", "bytedance")):
        return "doubao"
    if any(key in lowered for key in ("kimi", "moonshot")):
        return "kimi"
    if any(key in lowered for key in ("glm", "zhipu")):
        return "glm"
    if any(key in lowered for key in ("grok", "xai")):
        return "grok"
    if "minimax" in lowered:
        return "minimax"
    return ""


def model_token_family(token):
    lowered = (token or "").lower().strip()
    lowered = lowered.strip("()[]{}")
    if not lowered:
        return ""
    if lowered.startswith("claude") or lowered in {"anthropic", "opus", "sonnet", "haiku"}:
        return "anthropic"
    if (
        lowered.startswith("gpt")
        or lowered.startswith("chatgpt")
        or lowered in {"openai", "codex"}
        or re.fullmatch(r"o[134](?:[.-].*)?", lowered)
    ):
        return "openai"
    if lowered.startswith("deepseek"):
        return "deepseek"
    if lowered.startswith("gemini") or lowered == "google":
        return "gemini"
    if lowered.startswith("qwen") or lowered.startswith("qwq") or lowered == "tongyi":
        return "qwen"
    if lowered.startswith("doubao") or lowered.startswith("seedream") or lowered.startswith("seedance"):
        return "doubao"
    if lowered.startswith("kimi") or lowered.startswith("moonshot"):
        return "kimi"
    if lowered.startswith("glm") or lowered.startswith("zhipu"):
        return "glm"
    if lowered.startswith("grok") or lowered == "xai":
        return "grok"
    if lowered.startswith("minimax") or lowered.startswith("mini-max"):
        return "minimax"
    return ""


def normalize_provider_version_separators(name, family):
    if family != "anthropic":
        return name
    normalized = re.sub(r"\bclaude(\d+(?:\.\d+)?)\b", r"claude-\1", name, flags=re.IGNORECASE)
    normalized = re.sub(
        r"\b(claude|opus|sonnet|haiku)(-\d+)\.(\d+)\b",
        r"\1\2-\3",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\b(claude-[a-z]+-\d+)\.(\d+)\b",
        r"\1-\2",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def normalize_model_name_for_provider(name, provider):
    family = provider_family(provider)
    if not family:
        return name

    parts = re.split(r"([-_/]+)", name)
    token_indexes = []
    for index, part in enumerate(parts):
        if not part or re.fullmatch(r"[-_/]+", part):
            continue
        token_indexes.append((index, model_token_family(part)))

    current_indexes = [index for index, token_family in token_indexes if token_family == family]
    if not current_indexes:
        return normalize_provider_version_separators(name, family)

    first_current = current_indexes[0]
    last_current = current_indexes[-1]

    if any(token_family and token_family != family for index, token_family in token_indexes if index < first_current):
        parts = parts[first_current:]
        while parts and re.fullmatch(r"[-_/]+", parts[0]):
            parts = parts[1:]
        token_indexes = []
        for index, part in enumerate(parts):
            if not part or re.fullmatch(r"[-_/]+", part):
                continue
            token_indexes.append((index, model_token_family(part)))
        current_indexes = [index for index, token_family in token_indexes if token_family == family]
        last_current = current_indexes[-1] if current_indexes else -1

    for index, token_family in token_indexes:
        if index <= last_current:
            continue
        if token_family and token_family != family:
            cut_at = index
            if cut_at > 0 and re.fullmatch(r"[-_/]+", parts[cut_at - 1]):
                cut_at -= 1
            parts = parts[:cut_at]
            break

    cleaned = "".join(parts).strip("-_/ ")
    return normalize_provider_version_separators(cleaned or name, family)


def classify_notice(text):
    normalized = clean_text(text).lower()
    if any(keyword.lower() in normalized for keyword in COMPLIANCE_KEYWORDS):
        return ["公告", "合规"]

    tags = []
    for rule in NOTICE_RULES:
        exclude = rule.get("exclude") or []
        if any(keyword.lower() in normalized for keyword in exclude):
            continue
        strong = rule.get("strong") or []
        groups = rule.get("all") or []
        if any(keyword.lower() in normalized for keyword in strong):
            tags.append(rule["tag"])
            continue
        if any(all(keyword.lower() in normalized for keyword in group) for group in groups):
            tags.append(rule["tag"])
    if not tags:
        tags.append("公告")
    elif any(keyword.lower() in normalized for keyword in NOTICE_PREFIX_KEYWORDS) and "站点上线" not in tags:
        tags.insert(0, "公告")
    elif any(tag in tags for tag in ("活动优惠", "价格调整", "支付变更", "接入配置")) and "公告" not in tags:
        tags.insert(0, "公告")
    return tags[:3]


def extract_notice(endpoint):
    if endpoint.get("status") != 200:
        return ""
    if endpoint.get("strict_tls_ok") is False:
        return ""
    body = endpoint.get("body") or ""
    if looks_like_error_text(body):
        return ""
    parsed = parse_json_body(body)
    if not isinstance(parsed, dict):
        return ""
    candidates = []
    if looks_like_error_json(parsed):
        return ""
    if isinstance(parsed, dict):
        for key in ("data", "notice", "content", "message", "msg"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value)
            elif isinstance(value, dict):
                for nested_key in ("notice", "content", "message", "text"):
                    nested = value.get(nested_key)
                    if isinstance(nested, str) and nested.strip():
                        candidates.append(nested)
            elif isinstance(value, list):
                candidates.extend(
                    item for item in value if isinstance(item, str) and item.strip()
                )
    elif isinstance(parsed, str):
        candidates.append(parsed)
    notice = strip_html(" ".join(candidates))
    if looks_like_error_text(notice):
        return ""
    return notice


def extract_status_announcements(endpoint):
    if endpoint.get("status") != 200:
        return []
    body = endpoint.get("body") or ""
    if looks_like_error_text(body):
        return []
    parsed = parse_json_body(body)
    if not isinstance(parsed, dict) or looks_like_error_json(parsed):
        return []
    data = parsed.get("data") if isinstance(parsed, dict) else {}
    if not isinstance(data, dict):
        return []

    raw_items = data.get("announcements") or []
    if isinstance(raw_items, str):
        raw_items = parse_json_body(raw_items) or []
    if not isinstance(raw_items, list):
        return []

    announcements = []
    for index, item in enumerate(raw_items):
        if isinstance(item, str):
            content = strip_html(item)
            extra = ""
            publish_at = ""
            source_id = str(index)
            notice_type = ""
        elif isinstance(item, dict):
            content = strip_html(item.get("content") or item.get("title") or "")
            extra = strip_html(item.get("extra") or "")
            publish_at = clean_text(
                item.get("publishDate")
                or item.get("publish_date")
                or item.get("created_at")
                or item.get("date")
            )
            source_id = clean_text(item.get("id") if item.get("id") is not None else index)
            notice_type = clean_text(item.get("type"))
        else:
            continue

        parts = [part for part in (content, extra) if part]
        content = "\n\n".join(parts).strip()
        if not content or looks_like_error_text(content):
            continue
        announcements.append(
            {
                "source_id": source_id,
                "content": content,
                "publish_at": publish_at,
                "type": notice_type,
            }
        )
    return announcements


def compact_status_notice(announcements):
    lines = []
    for item in announcements[:6]:
        content = clean_text(item.get("content") or "")
        if content:
            lines.append(f"- {content}")
    return "\n".join(lines)


def timestamp_sort_value(value):
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    offset_seconds = None
    if text.endswith("Z"):
        text = text[:-1]
        offset_seconds = 0
    else:
        offset_match = re.search(r"([+-])(\d{2}):?(\d{2})$", text)
        if offset_match:
            sign = 1 if offset_match.group(1) == "+" else -1
            offset_seconds = sign * (int(offset_match.group(2)) * 3600 + int(offset_match.group(3)) * 60)
            text = text[: offset_match.start()]
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            if offset_seconds is not None:
                return calendar.timegm(parsed.timetuple()) + parsed.microsecond / 1000000.0 - offset_seconds
            local_tz = datetime.now().astimezone().tzinfo
            return parsed.replace(tzinfo=local_tz).timestamp()
        except ValueError:
            continue
    return 0


def safe_announcement_time(publish_at, fallback_at, now_ts):
    publish_ts = timestamp_sort_value(publish_at)
    if publish_ts and publish_ts <= now_ts + 300:
        return publish_at
    return fallback_at


def load_previous_announcement_times(out_dir):
    path = Path(out_dir) / "announcements.json"
    if not path.exists():
        return {}
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(items, list):
        return {}
    previous = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        first_seen_at = item.get("first_seen_at")
        if item_id and first_seen_at:
            previous[item_id] = first_seen_at
    return previous


def numeric_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_perf_metrics(endpoint):
    # Parse /api/perf-metrics/summary -> {model_name: {latency, success, tps}}.
    # Not every site exposes this endpoint; returns {} when missing/empty.
    if endpoint.get("status") != 200:
        return {}
    parsed = parse_json_body(endpoint.get("body") or "")
    if not isinstance(parsed, dict):
        return {}
    data = parsed.get("data")
    models = data.get("models") if isinstance(data, dict) else None
    result = {}
    for item in models or []:
        if not isinstance(item, dict):
            continue
        name = clean_text(item.get("model_name"))
        if not name:
            continue
        result[name] = {
            "avg_latency_ms": numeric_or_none(item.get("avg_latency_ms")),
            "success_rate": numeric_or_none(item.get("success_rate")),
            "avg_tps": numeric_or_none(item.get("avg_tps")),
        }
    return result


def extract_group_perf_metrics(endpoint):
    # Parse the collected detail calls:
    # /api/perf-metrics?model=<model>&hours=24
    # -> {model_name: {group_name: {avg_latency_ms, avg_ttft_ms, success_rate, avg_tps}}}
    if endpoint.get("status") != 200 and not endpoint.get("ok"):
        return {}
    parsed = parse_json_body(endpoint.get("body") or "")
    if not isinstance(parsed, dict):
        return {}
    raw_models = parsed.get("models")
    if not isinstance(raw_models, dict):
        return {}

    result = {}
    for requested_model, payload in raw_models.items():
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            continue
        model_name = clean_text(data.get("model_name")) or clean_text(requested_model)
        groups = data.get("groups") or []
        group_result = {}
        for item in groups:
            if not isinstance(item, dict):
                continue
            group = clean_text(item.get("group"))
            if not group:
                continue
            group_result[group] = {
                "avg_ttft_ms": numeric_or_none(item.get("avg_ttft_ms")),
                "avg_latency_ms": numeric_or_none(item.get("avg_latency_ms")),
                "success_rate": numeric_or_none(item.get("success_rate")),
                "avg_tps": numeric_or_none(item.get("avg_tps")),
            }
        if group_result:
            result[model_name] = group_result
            if requested_model and requested_model != model_name:
                result[clean_text(requested_model)] = group_result
    return result


def selected_group_name(group_ratios):
    entries = []
    for group, value in (group_ratios or {}).items():
        numeric = numeric_or_none(value)
        entries.append((numeric is None, numeric if numeric is not None else 999999999, str(group)))
    return sorted(entries)[0][2] if entries else None


def has_multiple_group_prices(group_ratios):
    values = []
    for value in (group_ratios or {}).values():
        numeric = numeric_or_none(value)
        if numeric is not None:
            values.append(round(numeric, 12))
    return len(set(values)) > 1


def extract_price_config(endpoint):
    parsed = parse_json_body(endpoint.get("body") or "")
    data = parsed.get("data") if isinstance(parsed, dict) else {}
    if not isinstance(data, dict):
        data = {}

    display_unit_price = (
        numeric_or_none(data.get("price"))
        or numeric_or_none(data.get("usd_exchange_rate"))
        or numeric_or_none(data.get("stripe_unit_price"))
        or 7.0
    )
    quota_per_unit = numeric_or_none(data.get("quota_per_unit")) or 500000.0
    if quota_per_unit <= 0:
        quota_per_unit = 500000.0

    quota_display_type = clean_text(data.get("quota_display_type")).upper()
    custom_symbol = clean_text(data.get("custom_currency_symbol"))
    if quota_display_type == "CNY" or custom_symbol in {"¥", "￥"}:
        symbol = "¥"
        token_multiplier = display_unit_price * (1000000.0 / quota_per_unit)
        request_multiplier = display_unit_price
    elif quota_display_type == "USD" or custom_symbol == "$":
        symbol = "$"
        token_multiplier = 1000000.0 / quota_per_unit
        request_multiplier = 1.0
    else:
        symbol = custom_symbol or "¥"
        token_multiplier = display_unit_price * (1000000.0 / quota_per_unit)
        request_multiplier = display_unit_price

    return {
        "currency_symbol": symbol,
        "currency_unit_price": display_unit_price,
        "quota_per_unit": quota_per_unit,
        "token_price_multiplier": token_multiplier,
        "request_price_multiplier": request_multiplier,
        "display_in_currency": bool(data.get("display_in_currency", True)),
    }


def first_standard_billing_expr(expr):
    if not isinstance(expr, str) or not expr.strip():
        return ""
    match = re.search(r'tier\s*\(\s*["\']standard["\']\s*,\s*(.*?)\)', expr, re.IGNORECASE)
    return match.group(1) if match else ""


def billing_expr_coefficient(expr, symbol):
    if not expr:
        return None
    name = re.escape(symbol)
    number = r"(-?\d+(?:\.\d+)?)"
    patterns = [
        r"(?<![A-Za-z0-9_])" + name + r"(?![A-Za-z0-9_])\s*\*\s*" + number,
        number + r"\s*\*\s*(?<![A-Za-z0-9_])" + name + r"(?![A-Za-z0-9_])",
    ]
    for pattern in patterns:
        match = re.search(pattern, expr)
        if match:
            return numeric_or_none(match.group(1))
    if re.search(r"(?<![A-Za-z0-9_])" + name + r"(?![A-Za-z0-9_])", expr):
        return 1.0
    return None


def apply_billing_expression_prices(model):
    if model.get("quota_type") != 0:
        return
    expr = first_standard_billing_expr(model.get("billing_expr"))
    input_coeff = billing_expr_coefficient(expr, "p")
    if input_coeff is None or input_coeff <= 0:
        return

    token_multiplier = numeric_or_none(model.get("token_price_multiplier")) or 1.0
    if token_multiplier <= 0:
        token_multiplier = 1.0

    output_coeff = billing_expr_coefficient(expr, "c")
    cache_input_coeff = billing_expr_coefficient(expr, "cr")
    cache_write_coeff = billing_expr_coefficient(expr, "cc")

    model["model_ratio"] = input_coeff / token_multiplier
    if output_coeff is not None:
        model["completion_ratio"] = output_coeff / input_coeff
    if cache_input_coeff is not None:
        model["cache_ratio"] = cache_input_coeff / input_coeff
    if cache_write_coeff is not None:
        model["create_cache_ratio"] = cache_write_coeff / input_coeff
    model["billing_expr_applied"] = True


def extract_models(endpoint):
    parsed = parse_json_body(endpoint.get("body") or "")
    if not isinstance(parsed, dict):
        return []
    site_group_ratios = {}
    raw_group_ratios = parsed.get("group_ratio") or {}
    if isinstance(raw_group_ratios, dict):
        for group, ratio in raw_group_ratios.items():
            numeric_ratio = numeric_or_none(ratio)
            if numeric_ratio is not None:
                site_group_ratios[str(group)] = numeric_ratio
    vendors = {}
    for vendor in parsed.get("vendors") or []:
        if isinstance(vendor, dict) and "id" in vendor:
            vendors[str(vendor["id"])] = vendor.get("name") or ""

    models = []
    for item in parsed.get("data") or []:
        if not isinstance(item, dict):
            continue
        name = clean_text(item.get("model_name") or item.get("name"))
        if not name:
            continue
        ratio = item.get("model_ratio")
        completion_ratio = item.get("completion_ratio")
        cache_ratio = item.get("cache_ratio")
        create_cache_ratio = item.get("create_cache_ratio")
        if create_cache_ratio is None:
            create_cache_ratio = item.get("cache_creation_ratio")
        model_price = item.get("model_price")
        quota_type = item.get("quota_type")
        try:
            numeric_ratio = float(ratio)
        except (TypeError, ValueError):
            numeric_ratio = None
        try:
            numeric_completion_ratio = float(completion_ratio)
        except (TypeError, ValueError):
            numeric_completion_ratio = None
        try:
            numeric_cache_ratio = float(cache_ratio)
        except (TypeError, ValueError):
            numeric_cache_ratio = None
        try:
            numeric_create_cache_ratio = float(create_cache_ratio)
        except (TypeError, ValueError):
            numeric_create_cache_ratio = None
        try:
            numeric_price = float(model_price)
        except (TypeError, ValueError):
            numeric_price = None

        vendor_id = item.get("vendor_id")
        provider = vendors.get(str(vendor_id)) or provider_for_model(name)
        raw_name = name
        name = normalize_model_name_for_provider(raw_name, provider)
        model_groups = item.get("enable_groups") or []
        model_group_ratios = {
            group: site_group_ratios.get(group, 1.0)
            for group in model_groups
            if isinstance(group, str) and group.strip()
        }
        min_group_ratio = min(model_group_ratios.values()) if model_group_ratios else 1.0
        models.append(
            {
                "model": name,
                "raw_model": raw_name if raw_name != name else None,
                "provider": provider,
                "model_ratio": numeric_ratio,
                "completion_ratio": numeric_completion_ratio,
                "cache_ratio": numeric_cache_ratio,
                "create_cache_ratio": numeric_create_cache_ratio,
                "model_price": numeric_price,
                "quota_type": quota_type,
                "billing_mode": clean_text(item.get("billing_mode")),
                "billing_expr": clean_text(item.get("billing_expr")),
                "groups": model_groups,
                "group_ratios": model_group_ratios,
                "min_group_ratio": min_group_ratio,
            }
        )
    return models


def effective_model_ratio(model, group=None):
    ratio = model.get("model_ratio")
    if not isinstance(ratio, (int, float)):
        return None
    group_ratio = None
    if group:
        group_ratio = (model.get("group_ratios") or {}).get(group)
    if group_ratio is None:
        group_ratio = model.get("min_group_ratio")
    if not isinstance(group_ratio, (int, float)):
        group_ratio = 1.0
    return ratio * group_ratio


def display_group_ratio(model, group=None):
    if group:
        group_ratio = (model.get("group_ratios") or {}).get(group)
        if isinstance(group_ratio, (int, float)):
            return group_ratio
    min_group_ratio = model.get("min_group_ratio")
    if isinstance(min_group_ratio, (int, float)):
        return min_group_ratio
    return None


def model_sort_key(item):
    important = {
        "OpenAI": 0,
        "Claude": 1,
        "DeepSeek": 2,
        "Gemini": 3,
        "Qwen": 4,
        "Doubao": 5,
    }
    return (important.get(item.get("provider"), 99), item.get("model", ""))


def billing_type_for_model(model):
    quota_type = model.get("quota_type")
    if quota_type == 0:
        return "按量计费"
    if quota_type == 1:
        return "按次计费"
    return "未知类型"


def model_value_rank(model):
    ratio = display_group_ratio(model)
    price = model.get("model_price")
    if not isinstance(ratio, (int, float)):
        ratio = 999999
    if not isinstance(price, (int, float)):
        price = 999999
    return (ratio, price, model.get("model") or "")


def dedupe_models_for_site(models):
    selected = {}
    for model in models:
        key = ((model.get("provider") or "").lower(), (model.get("model") or "").lower())
        current = selected.get(key)
        if current is None or model_value_rank(model) < model_value_rank(current):
            selected[key] = model
    return list(selected.values())


def site_from_row(row, generated_ts=None):
    origin = row.get("origin") or ""
    if not origin:
        return None
    endpoints = row.get("endpoints") or {}
    status_ep = endpoints.get("status") or {}
    home_ep = endpoints.get("home_page_content") or {}
    notice_ep = endpoints.get("notice") or {}
    pricing_ep = endpoints.get("pricing") or {}
    ratio_ep = endpoints.get("ratio_config") or {}
    perf_ep = endpoints.get("perf_metrics") or {}
    perf_detail_ep = endpoints.get("perf_metrics_by_model") or {}

    if is_self_use_mode(status_ep):
        return None

    reg_info = registration_info(status_ep)
    price_config = extract_price_config(status_ep)
    models = extract_models(pricing_ep)
    for model in models:
        model.update(price_config)
        apply_billing_expression_prices(model)
    models = dedupe_models_for_site(models)
    perf_map = extract_perf_metrics(perf_ep)
    group_perf_map = extract_group_perf_metrics(perf_detail_ep)
    if perf_map:
        for model in models:
            perf = perf_map.get(model.get("raw_model") or model["model"]) or perf_map.get(model["model"])
            if perf:
                model["avg_latency_ms"] = perf["avg_latency_ms"]
                model["success_rate"] = perf["success_rate"]
                model["avg_tps"] = perf["avg_tps"]
    if group_perf_map:
        for model in models:
            group_perf = (
                group_perf_map.get(model.get("raw_model") or "")
                or group_perf_map.get(model.get("model") or "")
            )
            if not group_perf:
                continue
            model["group_perf"] = group_perf
            selected_group = selected_group_name(model.get("group_ratios"))
            perf = group_perf.get(selected_group) if selected_group else None
            model["perf_group"] = selected_group
            if perf:
                model["avg_ttft_ms"] = perf.get("avg_ttft_ms")
                model["avg_latency_ms"] = perf.get("avg_latency_ms")
                model["success_rate"] = perf.get("success_rate")
                model["avg_tps"] = perf.get("avg_tps")
            elif has_multiple_group_prices(model.get("group_ratios")):
                model["avg_ttft_ms"] = None
                model["avg_latency_ms"] = None
                model["success_rate"] = None
                model["avg_tps"] = None
                model["perf_ambiguous"] = True
    else:
        for model in models:
            if has_multiple_group_prices(model.get("group_ratios")):
                model["avg_ttft_ms"] = None
                model["avg_latency_ms"] = None
                model["success_rate"] = None
                model["avg_tps"] = None
                model["perf_ambiguous"] = True
    providers = sorted({model["provider"] for model in models if model.get("provider")})
    groups = sorted(
        {
            group
            for model in models
            for group in (model.get("groups") or [])
            if isinstance(group, str) and group.strip()
        }
    )
    billing_types = sorted({billing_type_for_model(model) for model in models})

    notice = extract_notice(notice_ep)
    status_announcements = extract_status_announcements(status_ep)
    notifications = compact_status_notice(status_announcements)
    status_body = clean_text(status_ep.get("body")) if status_ep.get("status") == 200 else ""
    home_ok = bool(home_ep.get("ok")) or (home_ep.get("status") == 200 and bool(home_ep.get("body_len")))
    online_count = sum(
        1
        for item in (notice_ep, pricing_ep, ratio_ep)
        if item.get("status") == 200 and clean_text(item.get("body"))
    )
    seen_count = sum(1 for item in (notice_ep, pricing_ep, ratio_ep) if item.get("status"))
    if status_body or home_ok or online_count:
        status = "online"
    elif seen_count:
        status = "partial"
    else:
        status = "unknown"

    group_ratio_values = [
        value
        for value in [display_group_ratio(model) for model in models]
        if isinstance(value, (int, float)) and value >= 0
    ]
    lowest_ratio = min(group_ratio_values) if group_ratio_values else None
    tags = []
    tags.extend(providers[:4])
    tags.extend(billing_types)
    if lowest_ratio == 0:
        tags.append("免费")
    elif lowest_ratio is not None:
        tags.append("付费")
    if notice or notifications:
        tags.append("有公告")

    site_id = stable_id(origin)
    compact_models = sorted(models, key=model_sort_key)[:120]
    host = host_from_origin(origin)
    root_domain = base_domain(host)
    return {
        "id": site_id,
        "name": site_name_from_origin(origin),
        "origin": origin,
        "domain": row.get("domain") or urlparse(origin).netloc,
        "root_domain": root_domain,
        "status": status,
        **reg_info,
        "tags": sorted(set(tags)),
        "providers": providers,
        "groups": groups,
        "billing_types": billing_types,
        "model_count": len(models),
        "models_preview": [model["model"] for model in compact_models[:10]],
        "lowest_ratio": lowest_ratio,
        "notice": notice,
        "notifications": notifications,
        "notice_tags": classify_notice("\n\n".join(part for part in (notice, notifications) if part)) if (notice or notifications) else [],
        "status_announcements": status_announcements,
        "status_status": status_ep.get("status"),
        "home_page_content_status": home_ep.get("status"),
        "pricing_status": pricing_ep.get("status"),
        "notice_status": notice_ep.get("status"),
        "ratio_status": ratio_ep.get("status"),
        "updated_at": row.get("scanned_at"),
        "models": compact_models,
        **price_config,
        "_host": host,
    }


def normalize(input_path, out_dir):
    generated_at = datetime.now(timezone.utc)
    generated_ts = generated_at.timestamp()
    previous_announcement_times = load_previous_announcement_times(out_dir)
    rows = iter_rows(input_path)
    sites = []
    announcements = []
    model_sites = defaultdict(list)
    provider_counter = Counter()

    processed_rows = 0
    for row in rows:
        processed_rows += 1
        if processed_rows % 500 == 0:
            print(
                f"normalize rows={processed_rows} sites={len(sites)} announcements={len(announcements)} model_keys={len(model_sites)}",
                flush=True,
            )
        site = site_from_row(row, generated_ts)
        if site:
            sites.append(site)

    sites = dedupe_sites(sites)

    for site in sites:
        site_id = site["id"]
        origin = site["origin"]
        status = site["status"]
        notice = site.get("notice") or ""
        status_announcements = site.get("status_announcements") or []
        for provider in site.get("providers", []):
            provider_counter[provider] += 1

        for model in site.get("models", []):
            key = model["model"].lower()
            model_sites[key].append(
                {
                    "site_id": site_id,
                    "site_name": site["name"],
                    "origin": origin,
                    "model": model["model"],
                    "raw_model": model.get("raw_model"),
                    "provider": model["provider"],
                    "model_ratio": model.get("model_ratio"),
                    "completion_ratio": model.get("completion_ratio"),
                    "cache_ratio": model.get("cache_ratio"),
                    "create_cache_ratio": model.get("create_cache_ratio"),
                    "model_price": model.get("model_price"),
                    "group_ratios": model.get("group_ratios"),
                    "min_group_ratio": model.get("min_group_ratio"),
                    "currency_symbol": model.get("currency_symbol"),
                    "token_price_multiplier": model.get("token_price_multiplier"),
                    "request_price_multiplier": model.get("request_price_multiplier"),
                    "avg_latency_ms": model.get("avg_latency_ms"),
                    "success_rate": model.get("success_rate"),
                    "avg_tps": model.get("avg_tps"),
                    "avg_ttft_ms": model.get("avg_ttft_ms"),
                    "group_perf": model.get("group_perf"),
                    "perf_group": model.get("perf_group"),
                    "perf_ambiguous": model.get("perf_ambiguous"),
                    "status": status,
                    "registration_status": site.get("registration_status"),
                }
            )

        if notice:
            content_hash = stable_id(notice)
            announcement_id = stable_id(origin + "notice:" + notice)
            announcements.append(
                {
                    "id": announcement_id,
                    "content_hash": content_hash,
                    "site_id": site_id,
                    "site_name": site["name"],
                    "origin": origin,
                    "content": notice,
                    "tags": classify_notice(notice),
                    "registration_status": site.get("registration_status"),
                    "first_seen_at": previous_announcement_times.get(announcement_id) or site.get("updated_at"),
                    "time_source": "observed",
                    "source": "notice",
                }
            )
        for index, item in enumerate(status_announcements):
            content = item.get("content") or ""
            if not content:
                continue
            content_hash = stable_id(content)
            announcement_id = stable_id(origin + "status:" + (item.get("source_id") or str(index)) + ":" + content)
            has_publish_at = bool(item.get("publish_at"))
            first_seen_at = safe_announcement_time(item.get("publish_at"), site.get("updated_at"), generated_ts)
            if not has_publish_at:
                first_seen_at = previous_announcement_times.get(announcement_id) or first_seen_at
            announcements.append(
                {
                    "id": announcement_id,
                    "content_hash": content_hash,
                    "site_id": site_id,
                    "site_name": site["name"],
                    "origin": origin,
                    "content": content,
                    "tags": classify_notice(content),
                    "registration_status": site.get("registration_status"),
                    "first_seen_at": first_seen_at,
                    "published_at": item.get("publish_at") or None,
                    "time_source": "published_at" if has_publish_at else "observed",
                    "source": "status_announcements",
                    "source_type": item.get("type"),
                }
            )

    for site in sites:
        site.pop("status_announcements", None)

    models_index = []
    for key, entries in model_sites.items():
        unique_entries = {}
        for entry in entries:
            current = unique_entries.get(entry["site_id"])
            if current is None or model_value_rank(entry) < model_value_rank(current):
                unique_entries[entry["site_id"]] = entry
        entries = list(unique_entries.values())
        ratios = [
            display_group_ratio(item)
            for item in entries
            if isinstance(display_group_ratio(item), (int, float)) and display_group_ratio(item) >= 0
        ]
        model_name = entries[0]["model"]
        success_vals = [e["success_rate"] for e in entries if isinstance(e.get("success_rate"), (int, float))]
        latency_vals = [e["avg_latency_ms"] for e in entries if isinstance(e.get("avg_latency_ms"), (int, float))]
        tps_vals = [e["avg_tps"] for e in entries if isinstance(e.get("avg_tps"), (int, float))]
        ok_success = [v for v in success_vals if 0 <= v <= 100]
        ok_latency = [v for v in latency_vals if v >= 500]
        ok_tps = [v for v in tps_vals if 0 < v <= 1000]
        models_index.append(
            {
                "model": model_name,
                "provider": entries[0]["provider"],
                "site_count": len(entries),
                "min_ratio": min(ratios) if ratios else None,
                "success_rate": max(ok_success) if ok_success else None,
                "avg_latency_ms": min(ok_latency) if ok_latency else None,
                "avg_tps": max(ok_tps) if ok_tps else None,
                "perf_site_count": len(success_vals or latency_vals or tps_vals),
                "sites": sorted(
                    entries,
                    key=lambda item: (
                        display_group_ratio(item) is None,
                        display_group_ratio(item) if display_group_ratio(item) is not None else 999999,
                    ),
                )[:80],
            }
        )

    announcements.sort(key=lambda item: timestamp_sort_value(item.get("first_seen_at")), reverse=True)
    sites.sort(key=lambda item: (item["status"] != "online", item["lowest_ratio"] is None, item["lowest_ratio"] or 999999))
    models_index.sort(key=lambda item: (-item["site_count"], item["min_ratio"] is None, item["min_ratio"] or 999999))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "sites.json").open("w", encoding="utf-8") as handle:
        json.dump(sites, handle, ensure_ascii=False)
    with (out / "models.json").open("w", encoding="utf-8") as handle:
        json.dump(models_index, handle, ensure_ascii=False)
    with (out / "announcements.json").open("w", encoding="utf-8") as handle:
        json.dump(announcements, handle, ensure_ascii=False)
    summary = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "sites": len(sites),
        "online_sites": sum(1 for item in sites if item["status"] == "online"),
        "models": len(models_index),
        "announcements": len(announcements),
        "providers": provider_counter.most_common(),
    }
    with (out / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="../api_config_results.json")
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()
    normalize(args.input, args.out_dir)


if __name__ == "__main__":
    main()
