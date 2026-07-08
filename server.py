from collections import Counter
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import html as html_lib
import ipaddress
import os
from collections import OrderedDict
from pathlib import Path
import socket
import threading
import urllib.error
import urllib.request
from urllib.parse import quote, unquote, urljoin, urlparse
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import json
import re
import time


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
CUSTOM_ORIGINS_PATH = Path(os.environ.get("CUSTOM_ORIGINS_FILE", ROOT.parent / "custom_origins.txt"))
SUBMIT_SITE_TIMEOUT = float(os.environ.get("RELAYWATCH_SUBMIT_SITE_TIMEOUT", "6"))
SUBMIT_SITE_MAX_CHARS = int(os.environ.get("RELAYWATCH_SUBMIT_SITE_MAX_CHARS", "200000"))
DETECTOR_BASE_URL = os.environ.get("RELAYWATCH_DETECTOR_BASE_URL", "").strip().rstrip("/")
DETECTOR_TIMEOUT = float(os.environ.get("RELAYWATCH_DETECTOR_TIMEOUT", "20"))
DETECTION_CONTEXT_TTL = float(os.environ.get("RELAYWATCH_DETECTION_CONTEXT_TTL", "3600"))
QUALITY_PROBE_TIMEOUT = float(os.environ.get("RELAYWATCH_QUALITY_PROBE_TIMEOUT", "18"))
ADMIN_TOKEN = os.environ.get("RELAYWATCH_ADMIN_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_ENABLED = bool(DATABASE_URL)
MODELS_ONLY = os.environ.get("RELAYWATCH_MODELS_ONLY") == "1"
DB_MODEL_CACHE = {"generation_id": None, "data_version": None, "models": None}
DB_MODEL_CACHE_LOCK = threading.RLock()
DB_MODEL_RESULT_CACHE = OrderedDict()
DB_MODEL_RESULT_CACHE_LOCK = threading.RLock()
DB_MODEL_RESULT_CACHE_MAX = int(os.environ.get("RELAYWATCH_MODEL_RESULT_CACHE_MAX", "256"))
DB_MODEL_RESULT_CACHE_TTL = float(os.environ.get("RELAYWATCH_MODEL_RESULT_CACHE_TTL", "600"))
DB_SITE_RESULT_CACHE = OrderedDict()
DB_SITE_RESULT_CACHE_LOCK = threading.RLock()
DB_SITE_RESULT_CACHE_MAX = int(os.environ.get("RELAYWATCH_SITE_RESULT_CACHE_MAX", "256"))
DB_SITE_RESULT_CACHE_TTL = float(os.environ.get("RELAYWATCH_SITE_RESULT_CACHE_TTL", "600"))
DB_STATEMENT_TIMEOUT_MS = int(os.environ.get("RELAYWATCH_DB_STATEMENT_TIMEOUT_MS", "8000"))
DB_META_CACHE = {}
DB_META_CACHE_LOCK = threading.RLock()
DB_META_CACHE_TTL = float(os.environ.get("RELAYWATCH_META_CACHE_TTL", "1800"))
REDIS_URL = os.environ.get("REDIS_URL", "").strip() or os.environ.get("RELAYWATCH_REDIS_URL", "").strip()
REDIS_PREFIX = os.environ.get("RELAYWATCH_REDIS_PREFIX", "relaywatch").strip() or "relaywatch"
REDIS_CLIENT = None
REDIS_AVAILABLE = None
REDIS_RETRY_AT = 0
REDIS_RETRY_INTERVAL = float(os.environ.get("RELAYWATCH_REDIS_RETRY_INTERVAL", "60"))
REDIS_LOCK = threading.RLock()
DETECTION_CONTEXTS = {}
DETECTION_QUALITY_RESULTS = {}
DETECTION_CONTEXTS_LOCK = threading.RLock()
SUBMITTED_ORIGINS_LOCK = threading.RLock()
JSON_DATA_FILES = ("sites.json", "models.json", "announcements.json", "summary.json")
JSON_DATA_SIGNATURE = None
JSON_RELOAD_LOCK = threading.RLock()
DEEPSEEK_STATUS_CACHE_PATH = Path(os.environ.get("RELAYWATCH_DEEPSEEK_STATUS_CACHE", DATA_DIR / "deepseek_status_cache.json"))
FEEDBACK_FALLBACK_PATH = Path(os.environ.get("RELAYWATCH_FEEDBACK_FALLBACK", DATA_DIR / "feedback.jsonl"))
OFFICIAL_STATUS_CACHE = {"expires_at": 0, "data": None}
OFFICIAL_STATUS_CACHE_LOCK = threading.RLock()
OFFICIAL_STATUS_TTL = float(os.environ.get("RELAYWATCH_OFFICIAL_STATUS_TTL", "300"))
OFFICIAL_STATUS_TIMEOUT = float(os.environ.get("RELAYWATCH_OFFICIAL_STATUS_TIMEOUT", "8"))
AI_NEWS_CACHE = {"expires_at": 0, "data": None}
AI_NEWS_CACHE_LOCK = threading.RLock()
AI_NEWS_TTL = float(os.environ.get("RELAYWATCH_AI_NEWS_TTL", "900"))
AI_NEWS_TIMEOUT = float(os.environ.get("RELAYWATCH_AI_NEWS_TIMEOUT", "5"))
AI_NEWS_CONTENT_MAX_CHARS = int(os.environ.get("RELAYWATCH_AI_NEWS_CONTENT_MAX_CHARS", "50000"))
AI_NEWS_PER_FEED = int(os.environ.get("RELAYWATCH_AI_NEWS_PER_FEED", "12"))
AI_NEWS_FETCH_WORKERS = int(os.environ.get("RELAYWATCH_AI_NEWS_FETCH_WORKERS", "6"))
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip() or os.environ.get("RELAYWATCH_GITHUB_TOKEN", "").strip()
GITHUB_PROJECTS_PER_QUERY = int(os.environ.get("RELAYWATCH_GITHUB_PROJECTS_PER_QUERY", "8"))
CHAT_PROXY_TIMEOUT = float(os.environ.get("RELAYWATCH_CHAT_PROXY_TIMEOUT", "120"))
CHAT_PROXY_MAX_MESSAGES = int(os.environ.get("RELAYWATCH_CHAT_PROXY_MAX_MESSAGES", "24"))
CHAT_PROXY_MAX_MESSAGE_CHARS = int(os.environ.get("RELAYWATCH_CHAT_PROXY_MAX_MESSAGE_CHARS", "12000"))
CHAT_DEFAULT_SYSTEM_PROMPT = (
    "你是一个普通在线聊天助手。"
    "你不能查看用户电脑、项目目录、终端、文件系统或运行命令。"
    "不要输出工具调用 JSON、命令执行计划或类似 {\"cmd\": ...} 的内容。"
    "如果用户要代码，直接给出可读代码和必要说明；如果缺少上下文，直接说明需要用户提供。"
)
AI_NEWS_TELEGRAM_KEYWORDS = tuple(
    token.strip().lower()
    for token in os.environ.get(
        "RELAYWATCH_AI_NEWS_TELEGRAM_KEYWORDS",
        "ai,openai,chatgpt,gpt,claude,claude code,codex,gemini,deepseek,qwen,kimi,通义,千问,智谱,glm,模型,大模型,api,newapi,中转,token,提示词,prompt,agent,智能体,mcp,cursor,copilot,cline,roo code,trae,vscode,llm,aigc,生成式",
    ).split(",")
    if token.strip()
)
LINUXDO_COOKIE = os.environ.get("RELAYWATCH_LINUXDO_COOKIE", "").strip()
LINUXDO_RSS_URLS = [
    item.strip()
    for item in os.environ.get(
        "RELAYWATCH_LINUXDO_RSS_URLS",
        "https://linux.do/latest.rss,https://linux.do/top.rss,https://linux.do/posts.rss",
    ).split(",")
    if item.strip()
]
NEWAPI_DOCS_BASE_URL = "https://docs.newapi.pro"
NEWAPI_APPS_DOCS_URL = f"{NEWAPI_DOCS_BASE_URL}/zh/docs/apps"


def read_json(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def json_data_signature():
    signature = []
    for name in JSON_DATA_FILES:
        path = DATA_DIR / name
        stat = path.stat()
        signature.append((name, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def import_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="psycopg is not installed") from exc
    return psycopg, dict_row, Jsonb


def db_connect():
    psycopg, dict_row, _Jsonb = import_psycopg()
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    conn.execute("SET jit = off")
    if DB_STATEMENT_TIMEOUT_MS > 0:
        conn.execute(f"SET statement_timeout = {int(DB_STATEMENT_TIMEOUT_MS)}")
    return conn


def db_active_state(cur):
    cur.execute(
        """
        SELECT
          (SELECT value::bigint FROM app_state WHERE key = 'active_generation_id') AS generation_id,
          COALESCE((SELECT value FROM app_state WHERE key = 'active_data_version'), '0') AS data_version
        """
    )
    row = cur.fetchone()
    if not row or not row.get("generation_id"):
        raise HTTPException(status_code=503, detail="No active database generation")
    return row["generation_id"], row["data_version"]


def db_active_generation(cur):
    generation_id, _data_version = db_active_state(cur)
    return generation_id


def db_paginate(total, items, page, page_size):
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


def paginate_prepared(items, page, page_size, prepare_item=None):
    total = len(items)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    page_items = items[start:end]
    if prepare_item:
        page_items = [prepare_item(item) for item in page_items]
    return {
        "items": page_items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


def fetch_json_url(url, timeout=OFFICIAL_STATUS_TIMEOUT):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "RelayWatchOfficialStatus/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def fetch_text_url(url, timeout=OFFICIAL_STATUS_TIMEOUT, headers=None):
    request_headers = {
        "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 RelayWatchOfficialStatus/1.0",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        url,
        headers=request_headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_first_text_url(urls, timeout=OFFICIAL_STATUS_TIMEOUT):
    last_error = None
    for url in urls:
        try:
            return fetch_text_url(url, timeout=timeout), url
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("no url provided")


STATUS_LABELS = {
    "none": "正常",
    "minor": "异常",
    "major": "严重",
    "critical": "中断",
    "maintenance": "维护",
    "unknown": "未知",
}

COMPONENT_STATUS_LABELS = {
    "operational": "正常",
    "degraded_performance": "性能下降",
    "partial_outage": "部分中断",
    "major_outage": "严重中断",
    "under_maintenance": "维护中",
}


STATUS_DESCRIPTION_LABELS = {
    "all systems operational": "所有系统正常",
    "partial system degradation": "部分系统性能下降",
    "degraded system performance": "系统性能下降",
    "minor service outage": "部分服务异常",
    "major service outage": "严重服务中断",
    "service under maintenance": "服务维护中",
}


INCIDENT_STATUS_LABELS = {
    "investigating": "排查中",
    "identified": "已定位",
    "monitoring": "监控恢复中",
    "resolved": "已恢复",
    "postmortem": "复盘",
    "completed": "已完成",
    "scheduled": "计划维护",
    "in_progress": "维护中",
    "verifying": "验证中",
    "available": "已恢复",
    "service_information": "服务信息",
    "service_disruption": "服务异常",
    "service_outage": "服务中断",
}


INCIDENT_KEYWORD_LABELS = [
    ("codex", "Codex"),
    ("chatgpt", "ChatGPT"),
    ("responses api", "Responses API"),
    ("api", "API"),
    ("gemini", "Gemini"),
    ("vertex ai", "Vertex AI"),
    ("generative ai", "生成式 AI"),
    ("ai studio", "AI Studio"),
    ("claude", "Claude"),
]


INCIDENT_KEYWORD_PROBLEMS = [
    ("elevated error", "错误率升高"),
    ("increased error", "错误率升高"),
    ("error rate", "错误率升高"),
    ("degraded", "性能下降"),
    ("latency", "延迟升高"),
    ("timeout", "请求超时"),
    ("outage", "服务中断"),
    ("unavailable", "不可用"),
    ("usage limit", "使用额度异常"),
    ("rate limit", "限速异常"),
    ("billing", "计费异常"),
    ("login", "登录异常"),
]


def normalize_status_indicator(indicator):
    value = (indicator or "").strip().lower()
    if value in {"none", "operational"}:
        return "none"
    if value in {"minor", "degraded_performance"}:
        return "minor"
    if value in {"major", "partial_outage"}:
        return "major"
    if value in {"critical", "major_outage"}:
        return "critical"
    if value in {"maintenance", "under_maintenance"}:
        return "maintenance"
    return "unknown"


def status_rank(indicator):
    return {"none": 0, "maintenance": 1, "minor": 2, "major": 3, "critical": 4, "unknown": 5}.get(indicator, 5)


def scoped_component_indicator(components, fallback="unknown"):
    indicators = [
        normalize_status_indicator(item.get("status"))
        for item in (components or [])
        if normalize_status_indicator(item.get("status")) != "unknown"
    ]
    if not indicators:
        return normalize_status_indicator(fallback)
    return max(indicators, key=status_rank)


def latest_incident_update(incident):
    updates = incident.get("incident_updates") or []
    if not updates:
        return {}
    return sorted(updates, key=lambda item: item.get("display_at") or item.get("created_at") or "", reverse=True)[0]


def compact_text(value, limit=360):
    if isinstance(value, dict):
        value = value.get("text") or value.get("body") or value.get("message") or json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", lambda match: match.group(0).split("](", 1)[0].lstrip("["), text)
    text = re.sub(r"[*_`#>]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


class ArticleTextParser(HTMLParser):
    BLOCK_TAGS = {"article", "section", "main", "div", "p", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "blockquote", "pre"}
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "form", "button", "nav", "footer", "header", "aside"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if lowered in self.BLOCK_TAGS and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if lowered in self.BLOCK_TAGS and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data or "").strip()
        if text:
            self.parts.append(text)

    def text(self):
        raw = " ".join(self.parts)
        raw = re.sub(r"\s*\n\s*", "\n", raw)
        raw = re.sub(r"[ \t]{2,}", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        lines = [line.strip() for line in raw.splitlines()]
        return "\n\n".join([line for line in lines if line])


class SafeArticleHtmlParser(HTMLParser):
    ALLOWED_TAGS = {
        "p", "br", "strong", "b", "em", "i", "code", "pre", "blockquote",
        "ul", "ol", "li", "h2", "h3", "h4", "a", "img", "figure", "figcaption",
    }
    VOID_TAGS = {"br", "img"}
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe", "form", "button", "nav", "footer", "header", "aside"}

    def __init__(self, base_url=""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts = []
        self.stack = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in self.ALLOWED_TAGS:
            return
        attr_map = {str(key).lower(): value for key, value in attrs if value}
        safe_attrs = []
        if tag == "a":
            href = attr_map.get("href", "").strip()
            if href and not href.lower().startswith(("javascript:", "data:")):
                safe_attrs.append(("href", urljoin(self.base_url, href)))
                safe_attrs.append(("target", "_blank"))
                safe_attrs.append(("rel", "noreferrer"))
        elif tag == "img":
            src = attr_map.get("src") or attr_map.get("data-src") or attr_map.get("data-original")
            alt = attr_map.get("alt", "")
            if src and not src.lower().startswith("javascript:"):
                safe_attrs.append(("src", urljoin(self.base_url, src.strip())))
                safe_attrs.append(("alt", alt.strip()))
                safe_attrs.append(("loading", "lazy"))
        attr_text = "".join(f' {name}="{html_lib.escape(value, quote=True)}"' for name, value in safe_attrs)
        self.parts.append(f"<{tag}{attr_text}>")
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth or tag not in self.ALLOWED_TAGS or tag in self.VOID_TAGS:
            return
        if tag in self.stack:
            while self.stack:
                opened = self.stack.pop()
                self.parts.append(f"</{opened}>")
                if opened == tag:
                    break

    def handle_data(self, data):
        if self.skip_depth:
            return
        if data:
            self.parts.append(html_lib.escape(data))

    def handle_entityref(self, name):
        if not self.skip_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip_depth:
            self.parts.append(f"&#{name};")

    def html(self):
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        html = "".join(self.parts)
        html = re.sub(r"<p>\s*</p>", "", html, flags=re.I)
        html = re.sub(r"(?:<br>\s*){3,}", "<br><br>", html, flags=re.I)
        return html.strip()


def html_to_article_text(markup, limit=AI_NEWS_CONTENT_MAX_CHARS):
    if not markup:
        return ""
    parser = ArticleTextParser()
    try:
        parser.feed(markup)
    except Exception:
        return compact_text(markup, limit)
    text = html_lib.unescape(parser.text())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip()
    return text


def sanitize_article_html(markup, base_url="", limit=AI_NEWS_CONTENT_MAX_CHARS):
    if not markup or not re.search(r"<\s*(p|br|img|h2|h3|ul|ol|li|blockquote|pre|figure|a)\b", markup, flags=re.I):
        return ""
    parser = SafeArticleHtmlParser(base_url)
    try:
        parser.feed(markup)
    except Exception:
        return ""
    cleaned = parser.html()
    text = compact_text(cleaned, 0)
    if len(text) < 120 and not re.search(r"<img\b", cleaned, flags=re.I):
        return ""
    if limit and len(cleaned) > limit * 3:
        cleaned = cleaned[: limit * 3].rsplit("<", 1)[0]
    return cleaned


def walk_json_values(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json_values(child)


def extract_json_ld_article_body(markup):
    bodies = []
    for match in re.finditer(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", markup or "", flags=re.I | re.S):
        payload = html_lib.unescape(match.group(1).strip())
        try:
            data = json.loads(payload)
        except Exception:
            continue
        for node in walk_json_values(data):
            node_type = node.get("@type")
            if isinstance(node_type, list):
                node_type = " ".join(str(item) for item in node_type)
            node_type = str(node_type or "").lower()
            body = node.get("articleBody") or node.get("text")
            if body and any(token in node_type for token in ("article", "newsarticle", "blogposting")):
                bodies.append(compact_text(body, AI_NEWS_CONTENT_MAX_CHARS))
    return max(bodies, key=len, default="")


def extract_article_candidate_blocks(markup):
    candidates = []
    patterns = [
        r"<article\b[^>]*>(.*?)</article>",
        r"<main\b[^>]*>(.*?)</main>",
        r"<div\b[^>]*(?:class|id)=[\"'][^\"']*(?:post-content|entry-content|article-content|article__content|rich-text|content-body|markdown-body|post_body|news-content|post-content__content)[^\"']*[\"'][^>]*>(.*?)</div>",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, markup or "", flags=re.I | re.S):
            block = next((group for group in match.groups() if group), "")
            text = html_to_article_text(block, AI_NEWS_CONTENT_MAX_CHARS)
            if len(text) >= 300:
                candidates.append(text)
    return candidates


def extract_article_full_text(markup):
    body = extract_json_ld_article_body(markup)
    if len(body) >= 400:
        return body
    candidates = extract_article_candidate_blocks(markup)
    if candidates:
        return max(candidates, key=len)
    text = html_to_article_text(markup, AI_NEWS_CONTENT_MAX_CHARS)
    navigation_tokens = ("cookie", "subscribe", "sign up", "advertisement", "privacy policy")
    if len(text) >= 800 and sum(token in text[:3000].lower() for token in navigation_tokens) < 3:
        return text
    return body or text


def extract_meta_content(markup, name="", prop=""):
    if name:
        pattern = rf"<meta[^>]+name=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']*)[\"']"
        match = re.search(pattern, markup or "", flags=re.I)
        if match:
            return html_lib.unescape(match.group(1).strip())
    if prop:
        pattern = rf"<meta[^>]+property=[\"']{re.escape(prop)}[\"'][^>]+content=[\"']([^\"']*)[\"']"
        match = re.search(pattern, markup or "", flags=re.I)
        if match:
            return html_lib.unescape(match.group(1).strip())
    return ""


def extract_html_title(markup):
    match = re.search(r"<title[^>]*>(.*?)</title>", markup or "", flags=re.I | re.S)
    return compact_text(html_lib.unescape(match.group(1)), 180) if match else ""


def clean_newapi_doc_text(text, title="", description=""):
    lines = [compact_text(line, 2000) for line in re.split(r"\n+", text or "") if compact_text(line, 2000)]
    cleaned = []
    previous = ""
    skip_tokens = {
        "复制 Markdown",
        "打开",
        "目录",
        "上一页",
        "下一页",
        "使用指南",
        "部署安装",
        "API 参考",
        "AI 应用",
        "Skills",
        "帮助支持",
        "商务合作",
        "合规与使用政策",
    }
    for line in lines:
        if title and (line == title or line == f"{title} | New API"):
            continue
        if description and line == compact_text(description, 2000):
            continue
        if line in skip_tokens:
            continue
        if line.startswith("⚠️") or line.startswith("合规提示："):
            continue
        if line == previous:
            continue
        cleaned.append(line)
        previous = line
    return "\n\n".join(cleaned).strip()


def clean_newapi_doc_html(article_markup, base_url):
    if not article_markup:
        return ""
    html = article_markup
    html = re.sub(r"<(script|style|svg|button|nav|aside|header|footer)\b[\s\S]*?</\1>", "", html, flags=re.I)
    html = re.sub(r"<div\b[^>]*class=[\"'][^\"']*(?:mb-6|border-b|toc|fd-toc|data-toc)[^\"']*[\"'][^>]*>[\s\S]*?</div>", "", html, flags=re.I)
    html = re.sub(r"<h1\b[\s\S]*?</h1>", "", html, count=1, flags=re.I)
    html = re.sub(r"<p\b[^>]*class=[\"'][^\"']*text-lg[^\"']*[\"'][^>]*>[\s\S]*?</p>", "", html, count=1, flags=re.I)

    def rewrite_img(match):
        tag = match.group(0)
        alt_match = re.search(r"\balt=[\"']([^\"']*)[\"']", tag, flags=re.I)
        alt = html_lib.escape(html_lib.unescape(alt_match.group(1) if alt_match else ""))
        src_match = re.search(r"\bsrc=[\"']([^\"']*)[\"']", tag, flags=re.I)
        src = html_lib.unescape(src_match.group(1)) if src_match else ""
        if src.startswith("/_next/image"):
            parsed = urlparse(src)
            query = parsed.query
            url_match = re.search(r"(?:^|&)url=([^&]+)", query)
            if url_match:
                src = unquote(url_match.group(1))
        src = urljoin(base_url, src)
        if not src or src.endswith("/assets/newapi.svg"):
            return ""
        return f'<img src="{html_lib.escape(src)}" alt="{alt}" loading="lazy" />'

    html = re.sub(r"<img\b[^>]*>", rewrite_img, html, flags=re.I)
    html = re.sub(r"\s(?:class|style|data-[\w-]+|aria-[\w-]+|width|height|decoding|loading|sizes|srcset)=[\"'][^\"']*[\"']", "", html, flags=re.I)
    html = re.sub(r"<a\b([^>]*)href=[\"']([^\"']*)[\"']([^>]*)>", lambda m: f'<a href="{html_lib.escape(urljoin(base_url, html_lib.unescape(m.group(2))))}" target="_blank" rel="noreferrer">', html, flags=re.I)
    allowed = "a|p|strong|em|code|pre|blockquote|ul|ol|li|h2|h3|h4|table|thead|tbody|tr|th|td|br|img"
    html = re.sub(rf"</?(?!{allowed}\b)[a-z][^>]*>", "", html, flags=re.I)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def extract_newapi_doc_article(markup):
    match = re.search(r"<article\b[^>]*>(.*?)</article>", markup or "", flags=re.I | re.S)
    article_markup = match.group(1) if match else markup
    text = html_to_article_text(article_markup, AI_NEWS_CONTENT_MAX_CHARS)
    title_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", article_markup or "", flags=re.I | re.S)
    title = compact_text(title_match.group(1) if title_match else extract_html_title(markup), 180)
    title = re.sub(r"\s*\|\s*New API\s*$", "", title).strip()
    description = extract_meta_content(markup, name="description") or extract_meta_content(markup, prop="og:description")
    content = clean_newapi_doc_text(text, title, description)
    content_html = clean_newapi_doc_html(article_markup, NEWAPI_DOCS_BASE_URL)
    return title, compact_text(description, 360), content, content_html


def chinese_status_description(indicator, description, active_count=0):
    desc = compact_text(description, 120)
    lowered = desc.lower()
    if lowered in STATUS_DESCRIPTION_LABELS:
        return STATUS_DESCRIPTION_LABELS[lowered]
    if indicator == "none":
        return "所有系统正常"
    if active_count:
        return f"发现 {active_count} 个官方活跃事件"
    return desc or STATUS_LABELS.get(indicator, "未知")


def chinese_incident_name(name, status=""):
    text = compact_text(name, 160)
    lowered = text.lower()
    product = next((label for token, label in INCIDENT_KEYWORD_LABELS if token in lowered), "")
    problem = next((label for token, label in INCIDENT_KEYWORD_PROBLEMS if token in lowered), "")
    if product and problem:
        return f"{product}：{problem}"
    if problem:
        return problem
    if product:
        status_label = INCIDENT_STATUS_LABELS.get(str(status or "").lower())
        return f"{product}：{status_label or '官方事件'}"
    return text or "官方事件"


def chinese_incident_body(body, status=""):
    text = compact_text(body, 360)
    status_label = INCIDENT_STATUS_LABELS.get(str(status or "").lower())
    if not text:
        return status_label or "官方暂未提供更多描述。"
    lowered = text.lower()
    status_part = f"当前阶段：{status_label}。" if status_label else ""
    if "resolved" in lowered or "mitigated" in lowered:
        state = "事件已恢复。"
    elif "investigating" in lowered:
        state = "官方正在排查。"
    elif "monitoring" in lowered:
        state = "官方正在监控恢复情况。"
    elif "identified" in lowered:
        state = "官方已定位问题。"
    else:
        state = "官方已发布事件更新。"
    impact_bits = []
    if any(token in lowered for token in ("error rate", "elevated errors", "increased errors", "5xx")):
        impact_bits.append("错误率升高")
    if any(token in lowered for token in ("latency", "latencies", "slow")):
        impact_bits.append("延迟升高")
    if any(token in lowered for token in ("outage", "unavailable", "disruption")):
        impact_bits.append("服务可用性受影响")
    if any(token in lowered for token in ("codex", "chatgpt", "api", "gemini", "vertex", "claude")):
        products = []
        for token, label in INCIDENT_KEYWORD_LABELS:
            if token in lowered and label not in products:
                products.append(label)
        if products:
            impact_bits.append("涉及 " + "、".join(products[:4]))
    impact = "；".join(impact_bits[:3])
    if impact:
        impact = f"影响摘要：{impact}。"
    return f"官方说明：{state}{status_part}{impact}".strip()


def parse_status_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        if hasattr(datetime, "fromisoformat"):
            parsed = datetime.fromisoformat(text)
        else:
            raise ValueError("datetime.fromisoformat unavailable")
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?([+-]\d{2}:?\d{2})?$", text)
            if not iso_match:
                return None
            base = f"{iso_match.group(1)} {iso_match.group(2)}"
            try:
                parsed = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
            offset = iso_match.group(3)
            if offset:
                clean = offset.replace(":", "")
                sign = 1 if clean.startswith("+") else -1
                hours = int(clean[1:3])
                minutes = int(clean[3:5])
                parsed = parsed.replace(tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes)))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_feed_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return parse_status_datetime(text)


def parse_feed_datetime_with_hint(value, timezone_hint=""):
    if not value:
        return None
    text = str(value).strip()
    if timezone_hint == "Asia/Shanghai":
        cleaned = re.sub(r"\s+(GMT|UTC)$", "", text, flags=re.I)
        for fmt in ("%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(cleaned, fmt)
                return parsed.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
            except ValueError:
                continue
    return parse_feed_datetime(value)


def format_china_datetime(value):
    parsed = parse_status_datetime(value)
    if not parsed:
        return str(value or "未知时间")
    china_time = parsed.astimezone(timezone(timedelta(hours=8)))
    return f"{china_time.year}年{china_time.month}月{china_time.day}日 {china_time.hour:02d}:{china_time.minute:02d}"


def status_datetime_sort_key(item):
    parsed = parse_status_datetime(item.get("created_at") or item.get("updated_at") or item.get("resolved_at"))
    if parsed:
        return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def parse_feed_items(xml_text, limit=12):
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall(".//item"):
        title = compact_text(item.findtext("title") or "", 220)
        body = compact_text(item.findtext("description") or item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or "", 520)
        published = item.findtext("pubDate") or item.findtext("published") or item.findtext("updated")
        parsed = parse_feed_datetime(published)
        detected_language = "zh" if re.search(r"[\u4e00-\u9fff]", f"{title} {text}") else (feed.get("language") or "")
        items.append({
            "id": item.findtext("guid") or item.findtext("link") or title,
            "title": title,
            "body": body,
            "published_at": parsed.isoformat().replace("+00:00", "Z") if parsed else published,
        })
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns):
        title = compact_text(entry.findtext("atom:title", default="", namespaces=ns), 220)
        summary = entry.findtext("atom:summary", default="", namespaces=ns) or entry.findtext("atom:content", default="", namespaces=ns)
        published = entry.findtext("atom:published", default="", namespaces=ns) or entry.findtext("atom:updated", default="", namespaces=ns)
        parsed = parse_feed_datetime(published)
        items.append({
            "id": entry.findtext("atom:id", default=title, namespaces=ns),
            "title": title,
            "body": compact_text(summary or "", 520),
            "published_at": parsed.isoformat().replace("+00:00", "Z") if parsed else published,
        })
    return sorted(items, key=lambda item: parse_status_datetime(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]


def deepseek_feed_item_status(item):
    text = f"{item.get('title') or ''} {item.get('body') or ''}"
    lowered = text.lower()
    match = re.search(r"\bstatus\s*[:：]\s*([a-z_ -]+)", lowered)
    if match:
        raw_status = match.group(1).split()[0].strip(".,;:()[]{}")
        if raw_status in {"resolved", "completed", "closed"}:
            return "resolved"
        if raw_status in {"scheduled", "in_progress", "verifying"}:
            return raw_status
        if raw_status in {"investigating", "identified", "monitoring"}:
            return raw_status
    if any(token in lowered for token in ("resolved", "restored", "service has been restored", "已解决", "已恢复", "服务已恢复")):
        return "resolved"
    if any(token in lowered for token in ("scheduled", "maintenance", "计划维护", "维护中")):
        return "scheduled"
    if any(token in lowered for token in ("investigating", "identified", "monitoring", "degraded", "outage", "unavailable", "故障", "异常", "中断", "不可用", "性能下降")):
        return "investigating"
    return "resolved"


def deepseek_feed_item_indicator(item):
    status = deepseek_feed_item_status(item)
    if status in {"resolved", "completed", "closed"}:
        return "none"
    if status in {"scheduled", "in_progress", "verifying"}:
        return "maintenance"
    return "minor"


def normalize_deepseek_history_items(items):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        next_item = dict(item)
        status = str(next_item.get("status") or "").lower()
        if status in {"resolved", "completed", "closed"}:
            next_item["status_label"] = "已恢复"
            next_item["impact"] = "none"
            next_item["resolved_at"] = next_item.get("resolved_at") or next_item.get("updated_at") or next_item.get("created_at")
        elif status:
            next_item["status_label"] = INCIDENT_STATUS_LABELS.get(status, next_item.get("status_label") or "已记录")
        normalized.append(next_item)
    return normalized


def read_deepseek_status_cache():
    try:
        if not DEEPSEEK_STATUS_CACHE_PATH.exists():
            return None
        payload = json.loads(DEEPSEEK_STATUS_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def write_deepseek_status_cache(payload):
    try:
        DEEPSEEK_STATUS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cache_payload = dict(payload)
        cache_payload["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cache_payload.pop("error", None)
        DEEPSEEK_STATUS_CACHE_PATH.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def day_range(days=90):
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    return [start + timedelta(days=index) for index in range(days)]


def incident_day_severity(indicator):
    indicator = normalize_status_indicator(indicator)
    return {"critical": 3, "major": 3, "minor": 2, "maintenance": 1, "unknown": 1, "none": 0}.get(indicator, 1)


def severity_indicator(severity):
    if severity >= 3:
        return "major"
    if severity == 2:
        return "minor"
    if severity == 1:
        return "maintenance"
    return "none"


def severity_uptime_weight(severity):
    if severity >= 3:
        return 0.0
    if severity == 2:
        return 0.55
    if severity == 1:
        return 0.85
    return 1.0


def severity_loss_factor(severity):
    if severity >= 3:
        return 1.0
    if severity == 2:
        return 0.45
    if severity == 1:
        return 0.15
    return 0.0


def component_matches_incident(component_name, incident):
    name = (component_name or "").lower()
    incident_components = [
        str(item).lower()
        for item in (incident.get("component_names") or [])
        if item
    ]
    if incident_components:
        normalized_name = re.sub(r"[^a-z0-9]+", " ", name).strip()
        return any(
            normalized_name == re.sub(r"[^a-z0-9]+", " ", item).strip()
            or normalized_name in re.sub(r"[^a-z0-9]+", " ", item).strip()
            or re.sub(r"[^a-z0-9]+", " ", item).strip() in normalized_name
            for item in incident_components
        )
    haystack = " ".join([
        str(incident.get("name") or ""),
        str(incident.get("body_raw") or incident.get("body") or ""),
        str(incident.get("name_zh") or ""),
    ]).lower()
    if not name:
        return True
    compact_name = re.sub(r"[^a-z0-9]+", " ", name).strip()
    tokens = [token for token in compact_name.split() if len(token) >= 3]
    if any(token in haystack for token in tokens):
        return True
    aliases = {
        "responses": ["responses", "api"],
        "api": ["api"],
        "codex": ["codex"],
        "chatgpt": ["chatgpt", "chat gpt"],
        "claude api": ["api.anthropic.com", "api", "claude"],
        "console": ["console", "platform"],
        "gemini": ["gemini", "vertex ai", "generative ai"],
        "vertex": ["vertex ai", "gemini"],
        "deepseek": ["deepseek", "api"],
        "api 服务": ["api", "api service"],
        "web chat service": ["web chat service", "网页对话"],
        "网页对话": ["chat", "web"],
    }
    for key, values in aliases.items():
        if key in name and any(value in haystack for value in values):
            return True
    return not tokens


def build_uptime_rows(components, incidents=None, provider_indicator="none", days=90):
    dates = day_range(days)
    incidents = incidents or []
    rows = []
    safe_components = components or [{"name": "官方服务", "status": provider_indicator, "status_label": STATUS_LABELS.get(provider_indicator, "未知")}]
    for component in safe_components:
        severities = {day.isoformat(): 0 for day in dates}
        losses = {day.isoformat(): 0.0 for day in dates}
        for incident in incidents:
            if not component_matches_incident(component.get("name"), incident):
                continue
            start_dt = parse_status_datetime(incident.get("created_at") or incident.get("updated_at"))
            end_dt = parse_status_datetime(incident.get("resolved_at") or incident.get("updated_at")) or datetime.now(timezone.utc)
            if not start_dt:
                continue
            if end_dt < start_dt:
                end_dt = start_dt + timedelta(minutes=30)
            start_day = max(start_dt.date(), dates[0])
            end_day = min(end_dt.date(), dates[-1])
            if end_day < dates[0] or start_day > dates[-1]:
                continue
            severity = incident_day_severity(incident.get("impact") or incident.get("status"))
            loss_factor = severity_loss_factor(severity)
            day = start_day
            while day <= end_day:
                key = day.isoformat()
                severities[key] = max(severities.get(key, 0), severity)
                day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                day_end = day_start + timedelta(days=1)
                overlap_start = max(start_dt, day_start)
                overlap_end = min(end_dt, day_end)
                overlap_seconds = max(0.0, (overlap_end - overlap_start).total_seconds())
                losses[key] = min(1.0, losses.get(key, 0.0) + (overlap_seconds / 86400.0) * loss_factor)
                day += timedelta(days=1)
        current_status = normalize_status_indicator(component.get("status") or provider_indicator)
        if current_status != "none":
            severities[dates[-1].isoformat()] = max(severities[dates[-1].isoformat()], incident_day_severity(current_status))
            losses[dates[-1].isoformat()] = max(losses[dates[-1].isoformat()], 0.05)
        daily = [
            {
                "date": day.isoformat(),
                "status": severity_indicator(severities[day.isoformat()]),
            }
            for day in dates
        ]
        uptime = sum(max(0.0, 1.0 - losses[day.isoformat()]) for day in dates) / max(1, len(dates)) * 100
        rows.append({
            "name": component.get("name") or "官方服务",
            "status": current_status,
            "status_label": component.get("status_label") or STATUS_LABELS.get(current_status, "未知"),
            "uptime_percent": round(uptime, 2),
            "daily": daily,
        })
    return {
        "window_days": days,
        "start_date": dates[0].isoformat(),
        "end_date": dates[-1].isoformat(),
        "rows": rows,
    }


def google_latest_update(item):
    updates = item.get("updates") or []
    if not isinstance(updates, list) or not updates:
        return {}
    return sorted(
        updates,
        key=lambda update: update.get("when") or update.get("modified") or update.get("created") or "",
        reverse=True,
    )[0]


def google_incident_status(item):
    latest = google_latest_update(item)
    return str(
        latest.get("status")
        or item.get("status")
        or item.get("state")
        or ""
    ).strip()


def google_incident_is_active(item):
    latest_status = google_incident_status(item).lower()
    if item.get("end"):
        return False
    if latest_status in {"available", "resolved", "closed", "completed"}:
        return False
    return True


def google_incident_updated_at(item):
    latest = google_latest_update(item)
    return latest.get("when") or item.get("modified") or item.get("updated") or item.get("begin")


def google_incident_body(item):
    latest = google_latest_update(item)
    return compact_text(
        latest.get("text")
        or item.get("most_recent_update")
        or item.get("description")
        or item.get("external_desc")
        or "",
        420,
    )


def statuspage_provider_status(provider):
    summary = fetch_json_url(provider["summary_url"])
    incidents_payload = fetch_json_url(provider["incidents_url"])
    components = summary.get("components") or []
    include = provider.get("include_components") or []
    if include:
        lowered = [item.lower() for item in include]
        selected_components = [
            item
            for item in components
            if any(token in (item.get("name") or "").lower() for token in lowered)
        ]
    else:
        selected_components = components[:8]
    active_incidents = [
        item for item in (summary.get("incidents") or [])
        if item.get("status") not in {"resolved", "completed"}
    ]
    history = sorted(
        incidents_payload.get("incidents") or [],
        key=lambda item: parse_status_datetime(item.get("created_at") or item.get("updated_at") or item.get("resolved_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[: provider.get("history_limit", 8)]
    component_items = [
        {
            "name": item.get("name"),
            "status": normalize_status_indicator(item.get("status")),
            "status_label": COMPONENT_STATUS_LABELS.get(item.get("status"), STATUS_LABELS.get(normalize_status_indicator(item.get("status")), "未知")),
            "updated_at": item.get("updated_at"),
        }
        for item in selected_components[: provider.get("component_limit", 8)]
    ]
    raw_indicator = normalize_status_indicator((summary.get("status") or {}).get("indicator"))
    indicator = scoped_component_indicator(component_items, raw_indicator) if include else raw_indicator
    description = (summary.get("status") or {}).get("description") or ""
    if include and indicator == "none" and raw_indicator != "none":
        description = "Selected API components operational"
    active_incident_items = sorted([
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "name_zh": chinese_incident_name(item.get("name"), item.get("status")),
            "status": item.get("status"),
            "status_label": INCIDENT_STATUS_LABELS.get(str(item.get("status") or "").lower(), item.get("status") or ""),
            "impact": normalize_status_indicator(item.get("impact")),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "resolved_at": item.get("resolved_at"),
            "component_names": [component.get("name") for component in (item.get("components") or []) if component.get("name")],
            "body": chinese_incident_body(latest_incident_update(item).get("body") or "", item.get("status")),
            "body_raw": compact_text(latest_incident_update(item).get("body") or "", 420),
        }
        for item in active_incidents[:4]
    ], key=status_datetime_sort_key, reverse=True)
    history_items = sorted([
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "name_zh": chinese_incident_name(item.get("name"), item.get("status")),
            "status": item.get("status"),
            "status_label": INCIDENT_STATUS_LABELS.get(str(item.get("status") or "").lower(), item.get("status") or ""),
            "impact": normalize_status_indicator(item.get("impact")),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "resolved_at": item.get("resolved_at"),
            "component_names": [component.get("name") for component in (item.get("components") or []) if component.get("name")],
            "body": chinese_incident_body(latest_incident_update(item).get("body") or "", item.get("status")),
            "body_raw": compact_text(latest_incident_update(item).get("body") or "", 420),
        }
        for item in history
    ], key=status_datetime_sort_key, reverse=True)
    return {
        "id": provider["id"],
        "name": provider["name"],
        "subtitle": provider.get("subtitle", ""),
        "status_url": provider["status_url"],
        "indicator": indicator,
        "status_label": STATUS_LABELS.get(indicator, "未知"),
        "description": chinese_status_description(indicator, description, len(active_incidents)),
        "description_raw": description,
        "updated_at": (summary.get("page") or {}).get("updated_at"),
        "components": component_items,
        "active_incidents": active_incident_items,
        "history": history_items,
        "uptime": build_uptime_rows(component_items, history_items + active_incident_items, indicator),
        "error": "",
    }


def google_status_provider_status(provider):
    # Google Cloud exposes a global incidents feed. It does not have the same
    # simple Statuspage summary shape, so keep this adapter deliberately broad
    # and filter for Gemini / Vertex AI / Generative AI related incidents.
    payload = fetch_json_url(provider["incidents_url"])
    incident_items = payload.get("incidents") if isinstance(payload, dict) else payload
    if not isinstance(incident_items, list):
        incident_items = []
    tokens = [token.lower() for token in provider.get("match_tokens", [])]
    matched = []
    for item in incident_items:
        haystack = json.dumps(item, ensure_ascii=False).lower()
        if any(token in haystack for token in tokens):
            matched.append(item)
    active = [item for item in matched if google_incident_is_active(item)]
    indicator = "none" if not active else "minor"
    component_items = [
        {
            "id": "gemini-vertex",
            "name": "Gemini / Vertex AI",
            "status": indicator,
            "status_label": STATUS_LABELS.get(indicator, "未知"),
            "updated_at": payload.get("updated") or payload.get("updated_at") if isinstance(payload, dict) else None,
        }
    ]
    active_incident_items = [
        {
            "id": str(item.get("id") or item.get("number") or item.get("name") or ""),
            "name": item.get("external_desc") or item.get("name") or item.get("title") or "Google Cloud incident",
            "name_zh": chinese_incident_name(item.get("external_desc") or item.get("name") or item.get("title") or "Google Cloud incident", google_incident_status(item)),
            "status": google_incident_status(item),
            "status_label": INCIDENT_STATUS_LABELS.get(google_incident_status(item).lower(), google_incident_status(item)),
            "impact": "minor",
            "created_at": item.get("begin") or item.get("created"),
            "updated_at": google_incident_updated_at(item),
            "resolved_at": item.get("end"),
            "body": chinese_incident_body(google_incident_body(item), google_incident_status(item)),
            "body_raw": google_incident_body(item),
        }
        for item in active[:4]
    ]
    history_items = [
        {
            "id": str(item.get("id") or item.get("number") or item.get("name") or ""),
            "name": item.get("external_desc") or item.get("name") or item.get("title") or "Google Cloud incident",
            "name_zh": chinese_incident_name(item.get("external_desc") or item.get("name") or item.get("title") or "Google Cloud incident", google_incident_status(item)),
            "status": google_incident_status(item),
            "status_label": INCIDENT_STATUS_LABELS.get(google_incident_status(item).lower(), google_incident_status(item)),
            "impact": "minor",
            "created_at": item.get("begin") or item.get("created"),
            "updated_at": google_incident_updated_at(item),
            "resolved_at": item.get("end"),
            "body": chinese_incident_body(google_incident_body(item), google_incident_status(item)),
            "body_raw": google_incident_body(item),
        }
        for item in matched[:8]
    ]
    return {
        "id": provider["id"],
        "name": provider["name"],
        "subtitle": provider.get("subtitle", ""),
        "status_url": provider["status_url"],
        "indicator": indicator,
        "status_label": STATUS_LABELS.get(indicator, "未知"),
        "description": "未发现 Gemini / Vertex AI 相关活跃事件" if not active else f"发现 {len(active)} 个相关活跃事件",
        "updated_at": payload.get("updated") or payload.get("updated_at") if isinstance(payload, dict) else None,
        "components": component_items,
        "active_incidents": active_incident_items,
        "history": history_items,
        "uptime": build_uptime_rows(component_items, history_items + active_incident_items, indicator),
        "error": "",
    }


def deepseek_status_provider_status(provider):
    feed_items = []
    feed_error = None
    try:
        feed_text, feed_url = fetch_first_text_url(provider.get("feed_urls") or [])
        feed_items = parse_feed_items(feed_text, limit=12)
    except Exception as exc:
        feed_error = exc
    html = ""
    html_error = None
    try:
        html = fetch_text_url(provider["status_url"])
    except Exception as exc:
        html_error = exc
    text = compact_text(html, 30000)
    lowered = text.lower()
    feed_haystack = " ".join([f"{item.get('title')} {item.get('body')}" for item in feed_items]).lower()
    feed_active_items = [item for item in feed_items if deepseek_feed_item_status(item) not in {"resolved", "completed", "closed"}]
    if "everything is running smoothly" in lowered or "all systems operational" in lowered or "一切运行正常" in lowered:
        indicator = "none"
        description = "所有系统正常"
    elif feed_items and not feed_active_items:
        indicator = "none"
        description = "所有系统正常"
    elif feed_active_items:
        indicator = max((deepseek_feed_item_indicator(item) for item in feed_active_items), key=status_rank)
        description = f"发现 {len(feed_active_items)} 个未恢复事件"
    elif any(token in lowered for token in ("degraded", "partial outage", "incident", "maintenance")):
        indicator = "minor"
        description = "官方状态页显示存在事件或维护"
    elif feed_items and not any(token in feed_haystack for token in ("degraded", "outage", "incident", "maintenance", "中断", "故障", "维护")):
        indicator = "none"
        description = "所有系统正常"
    else:
        indicator = "unknown"
        description = "已读取官方订阅源，但未识别到明确状态" if feed_items else "官方状态源连接受限"

    component_aliases = [
        ("api", "API 服务", ["api service", "api 服务", "api"]),
        ("chat", "网页对话服务", ["web chat service", "chat service", "网页对话", "网页对话服务"]),
    ]
    components = []
    for component_id, name, tokens in component_aliases:
        component_status = "none" if any(token in lowered for token in tokens) and indicator == "none" else indicator
        if not html and indicator == "none":
            component_status = "none"
        components.append({
            "id": component_id,
            "name": name,
            "status": component_status,
            "status_label": STATUS_LABELS.get(component_status, "未知"),
            "updated_at": None,
        })
    history_items = normalize_deepseek_history_items([
        {
            "id": item.get("id"),
            "name": item.get("title") or "DeepSeek 官方更新",
            "name_zh": chinese_incident_name(item.get("title") or "DeepSeek 官方更新", deepseek_feed_item_status(item)),
            "status": deepseek_feed_item_status(item),
            "status_label": "已恢复" if deepseek_feed_item_status(item) == "resolved" else INCIDENT_STATUS_LABELS.get(deepseek_feed_item_status(item), "已记录"),
            "impact": deepseek_feed_item_indicator(item),
            "created_at": item.get("published_at"),
            "updated_at": item.get("published_at"),
            "resolved_at": item.get("published_at") if deepseek_feed_item_status(item) == "resolved" else None,
            "body": chinese_incident_body(item.get("body") or item.get("title") or "", deepseek_feed_item_status(item)),
            "body_raw": item.get("body") or "",
        }
        for item in feed_items[:8]
    ])
    uptime = build_uptime_rows(components, history_items, indicator)
    error = ""
    if not html and not feed_items:
        cached = read_deepseek_status_cache()
        if cached:
            cached_components = cached.get("components") or components
            cached_history = normalize_deepseek_history_items(cached.get("history") or [])
            cached_indicator = normalize_status_indicator(cached.get("indicator") or "none")
            return {
                "id": provider["id"],
                "name": provider["name"],
                "subtitle": provider.get("subtitle", ""),
                "status_url": provider["status_url"],
                "indicator": cached_indicator,
                "status_label": STATUS_LABELS.get(cached_indicator, "未知"),
                "description": cached.get("description") or chinese_status_description(cached_indicator, "", 0),
                "description_raw": cached.get("description_raw") or "cached DeepSeek official status",
                "updated_at": cached.get("updated_at"),
                "components": cached_components,
                "active_incidents": cached.get("active_incidents") or [],
                "history": cached_history,
                "uptime": cached.get("uptime") or build_uptime_rows(cached_components, cached_history, cached_indicator),
                "error": "" if cached_indicator == "none" else f"服务器直连 DeepSeek 官方源受限，当前使用本地缓存：{format_china_datetime(cached.get('generated_at'))}",
            }
        error = f"服务器访问 DeepSeek 官方状态页/RSS 被重置：{str(html_error or feed_error or '')[:180]}"

    result = {
        "id": provider["id"],
        "name": provider["name"],
        "subtitle": provider.get("subtitle", ""),
        "status_url": provider["status_url"],
        "indicator": indicator,
        "status_label": STATUS_LABELS.get(indicator, "未知"),
        "description": description,
        "description_raw": "Everything is running smoothly" if indicator == "none" else compact_text(text or feed_haystack, 240),
        "updated_at": None,
        "components": components,
        "active_incidents": [],
        "history": history_items,
        "uptime": uptime,
        "error": error,
    }
    if html or feed_items:
        write_deepseek_status_cache(result)
    return result


OFFICIAL_STATUS_PROVIDERS = [
    {
        "id": "openai",
        "name": "OpenAI",
        "subtitle": "OpenAI API",
        "status_url": "https://status.openai.com/",
        "summary_url": "https://status.openai.com/api/v2/summary.json",
        "incidents_url": "https://status.openai.com/api/v2/incidents.json",
        "include_components": ["api", "responses", "chatgpt", "codex", "batch", "fine-tuning", "embeddings", "files"],
        "adapter": "statuspage",
    },
    {
        "id": "claude",
        "name": "Claude",
        "subtitle": "Anthropic API",
        "status_url": "https://status.claude.com/",
        "summary_url": "https://status.claude.com/api/v2/summary.json",
        "incidents_url": "https://status.claude.com/api/v2/incidents.json",
        "include_components": ["api.anthropic.com", "console", "claude.ai", "claude code", "cowork"],
        "adapter": "statuspage",
    },
    {
        "id": "gemini",
        "name": "Gemini",
        "subtitle": "Google AI / Vertex AI",
        "status_url": "https://status.cloud.google.com/products/Z0FZJAMvEB4j3NbCJs6B",
        "incidents_url": "https://status.cloud.google.com/incidents.json",
        "match_tokens": ["gemini", "vertex ai", "generative ai", "ai studio"],
        "adapter": "google",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "subtitle": "DeepSeek API",
        "status_url": "https://status.deepseek.com/",
        "feed_urls": ["https://status.deepseek.com/feed.rss", "https://status.deepseek.com/feed.atom"],
        "adapter": "deepseek",
    },
]


AI_NEWS_FEEDS = [
    {
        "id": "openai-news",
        "provider": "OpenAI",
        "title": "OpenAI News",
        "url": "https://openai.com/news/rss.xml",
        "homepage": "https://openai.com/news/",
        "language": "en",
        "priority": 75,
    },
    {
        "id": "qbitai",
        "provider": "量子位",
        "title": "量子位",
        "url": "https://www.qbitai.com/feed",
        "homepage": "https://www.qbitai.com/",
        "language": "zh",
        "priority": 100,
    },
    {
        "id": "mit-tr-ai",
        "provider": "MIT Technology Review",
        "title": "MIT Technology Review AI",
        "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
        "homepage": "https://www.technologyreview.com/topic/artificial-intelligence/",
        "language": "en",
        "priority": 72,
    },
    {
        "id": "marktechpost",
        "provider": "MarkTechPost",
        "title": "MarkTechPost",
        "url": "https://www.marktechpost.com/feed/",
        "homepage": "https://www.marktechpost.com/",
        "language": "en",
        "priority": 70,
    },
    {
        "id": "v2ex-tech",
        "provider": "V2EX",
        "title": "V2EX 技术",
        "url": "https://www.v2ex.com/feed/tab/tech.xml",
        "homepage": "https://www.v2ex.com/?tab=tech",
        "language": "zh",
        "priority": 58,
        "category": "社区讨论",
        "rss_only": True,
        "limit": 80,
    },
    {
        "id": "v2ex-programmer",
        "provider": "V2EX",
        "title": "V2EX 程序员",
        "url": "https://www.v2ex.com/feed/programmer.xml",
        "homepage": "https://www.v2ex.com/go/programmer",
        "language": "zh",
        "priority": 57,
        "category": "社区讨论",
        "rss_only": True,
        "limit": 80,
    },
    {
        "id": "linuxdo-latest",
        "provider": "LinuxDo",
        "title": "LinuxDo 最新",
        "url": "https://linux.do/latest.rss",
        "urls": LINUXDO_RSS_URLS,
        "homepage": "https://linux.do/latest",
        "language": "zh",
        "priority": 56,
        "category": "社区讨论",
        "rss_only": True,
        "headers": "linuxdo",
        "telegram_fallback": "https://t.me/s/LinuxDoNew",
    },
    {
        "id": "tg-ai-copilot",
        "provider": "Telegram",
        "title": "AI Copilot",
        "url": "https://t.me/s/AI_Copilot_Channel",
        "homepage": "https://t.me/AI_Copilot_Channel",
        "language": "zh",
        "priority": 64,
        "category": "AI 资讯",
        "telegram_url": "https://t.me/s/AI_Copilot_Channel",
        "telegram_channel": "AI_Copilot_Channel",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-ai-news-cn",
        "provider": "Telegram",
        "title": "AI 新闻聚合",
        "url": "https://t.me/s/AI_News_CN",
        "homepage": "https://t.me/AI_News_CN",
        "language": "zh",
        "priority": 63,
        "category": "AI 资讯",
        "telegram_url": "https://t.me/s/AI_News_CN",
        "telegram_channel": "AI_News_CN",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-linuxdoit",
        "provider": "Telegram",
        "title": "LinuxDo 社区动态",
        "url": "https://t.me/s/linuxdoit",
        "homepage": "https://t.me/linuxdoit",
        "language": "zh",
        "priority": 60,
        "category": "社区讨论",
        "telegram_url": "https://t.me/s/linuxdoit",
        "telegram_channel": "linuxdoit",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-newlearner",
        "provider": "Telegram",
        "title": "Newlearner",
        "url": "https://t.me/s/newlearner_channel",
        "homepage": "https://t.me/newlearner_channel",
        "language": "zh",
        "priority": 59,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/newlearner_channel",
        "telegram_channel": "newlearner_channel",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-zhetengsha",
        "provider": "Telegram",
        "title": "折腾啥",
        "url": "https://t.me/s/zhetengsha",
        "homepage": "https://t.me/zhetengsha",
        "language": "zh",
        "priority": 58,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/zhetengsha",
        "telegram_channel": "zhetengsha",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-testflightcn",
        "provider": "Telegram",
        "title": "科技圈在花频道",
        "url": "https://t.me/s/TestFlightCN",
        "homepage": "https://t.me/TestFlightCN",
        "language": "zh",
        "priority": 57,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/TestFlightCN",
        "telegram_channel": "TestFlightCN",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-geekshare",
        "provider": "Telegram",
        "title": "极客分享",
        "url": "https://t.me/s/geekshare",
        "homepage": "https://t.me/geekshare",
        "language": "zh",
        "priority": 56,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/geekshare",
        "telegram_channel": "geekshare",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-awesome-chatgpt",
        "provider": "Telegram",
        "title": "ChatGPT 精选",
        "url": "https://t.me/s/AwesomeChatGPT",
        "homepage": "https://t.me/AwesomeChatGPT",
        "language": "zh",
        "priority": 56,
        "category": "AI 资讯",
        "telegram_url": "https://t.me/s/AwesomeChatGPT",
        "telegram_channel": "AwesomeChatGPT",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-digital-nomad",
        "provider": "Telegram",
        "title": "数字牧民",
        "url": "https://t.me/s/digitalnomadlc",
        "homepage": "https://t.me/digitalnomadlc",
        "language": "zh",
        "priority": 55,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/digitalnomadlc",
        "telegram_channel": "digitalnomadlc",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-aigc-guide",
        "provider": "Telegram",
        "title": "AI探索指南",
        "url": "https://t.me/s/aigc1024",
        "homepage": "https://t.me/aigc1024",
        "language": "zh",
        "priority": 63,
        "category": "AI 资讯",
        "telegram_url": "https://t.me/s/aigc1024",
        "telegram_channel": "aigc1024",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-chatgpt-openai",
        "provider": "Telegram",
        "title": "Chat GPT",
        "url": "https://t.me/s/ChatGPT_OpenAi",
        "homepage": "https://t.me/ChatGPT_OpenAi",
        "language": "zh",
        "priority": 61,
        "category": "AI 资讯",
        "telegram_url": "https://t.me/s/ChatGPT_OpenAi",
        "telegram_channel": "ChatGPT_OpenAi",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-github-open-source",
        "provider": "Telegram",
        "title": "GitHub 开源观察",
        "url": "https://t.me/s/GitHubTrendingHub",
        "homepage": "https://t.me/GitHubTrendingHub",
        "language": "zh",
        "priority": 58,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/GitHubTrendingHub",
        "telegram_channel": "GitHubTrendingHub",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-github-trends",
        "provider": "Telegram",
        "title": "GitHub Trends",
        "url": "https://t.me/s/githubtrending",
        "homepage": "https://t.me/githubtrending",
        "language": "zh",
        "priority": 57,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/githubtrending",
        "telegram_channel": "githubtrending",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-readhub",
        "provider": "Telegram",
        "title": "Readhub",
        "url": "https://t.me/s/Readhub_cn",
        "homepage": "https://t.me/Readhub_cn",
        "language": "zh",
        "priority": 55,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/Readhub_cn",
        "telegram_channel": "Readhub_cn",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-appinn",
        "provider": "Telegram",
        "title": "小众软件",
        "url": "https://t.me/s/appinnfeed",
        "homepage": "https://t.me/appinnfeed",
        "language": "zh",
        "priority": 54,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/appinnfeed",
        "telegram_channel": "appinnfeed",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-producthunt",
        "provider": "Telegram",
        "title": "ProductHunt",
        "url": "https://t.me/s/ProductHuntDaily",
        "homepage": "https://t.me/ProductHuntDaily",
        "language": "zh",
        "priority": 53,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/ProductHuntDaily",
        "telegram_channel": "ProductHuntDaily",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "tg-pingwest",
        "provider": "Telegram",
        "title": "PingWest",
        "url": "https://t.me/s/pingwest",
        "homepage": "https://t.me/pingwest",
        "language": "zh",
        "priority": 52,
        "category": "技术社区",
        "telegram_url": "https://t.me/s/pingwest",
        "telegram_channel": "pingwest",
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "segmentfault",
        "provider": "SegmentFault",
        "title": "SegmentFault 技术问答",
        "url": "https://segmentfault.com/feeds",
        "homepage": "https://segmentfault.com/",
        "language": "zh",
        "priority": 58,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "oschina-news",
        "provider": "开源中国",
        "title": "开源中国",
        "url": "https://www.oschina.net/news/rss",
        "homepage": "https://www.oschina.net/news",
        "language": "zh",
        "priority": 57,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "infoq-cn",
        "provider": "InfoQ 中文",
        "title": "InfoQ 中文",
        "url": "https://www.infoq.cn/feed",
        "homepage": "https://www.infoq.cn/",
        "language": "zh",
        "priority": 57,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
        "timezone_hint": "Asia/Shanghai",
    },
    {
        "id": "cnblogs-sitehome",
        "provider": "博客园",
        "title": "博客园首页",
        "url": "https://feed.cnblogs.com/blog/sitehome/rss",
        "homepage": "https://www.cnblogs.com/",
        "language": "zh",
        "priority": 55,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "cnode",
        "provider": "CNode",
        "title": "CNode",
        "url": "https://cnodejs.org/rss",
        "homepage": "https://cnodejs.org/",
        "language": "zh",
        "priority": 54,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "ruby-china",
        "provider": "Ruby China",
        "title": "Ruby China",
        "url": "https://ruby-china.org/topics/feed",
        "homepage": "https://ruby-china.org/",
        "language": "zh",
        "priority": 54,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "juejin",
        "provider": "掘金",
        "title": "掘金",
        "url": "https://juejin.cn/rss",
        "homepage": "https://juejin.cn/",
        "language": "zh",
        "priority": 54,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "sspai",
        "provider": "少数派",
        "title": "少数派",
        "url": "https://sspai.com/feed",
        "homepage": "https://sspai.com/",
        "language": "zh",
        "priority": 53,
        "category": "技术社区",
        "rss_only": True,
        "keywords": AI_NEWS_TELEGRAM_KEYWORDS,
    },
    {
        "id": "hn-ai",
        "provider": "Hacker News",
        "title": "Hacker News AI",
        "url": "https://hnrss.org/newest?q=AI",
        "homepage": "https://news.ycombinator.com/",
        "language": "en",
        "priority": 55,
        "category": "社区讨论",
        "rss_only": True,
    },
    {
        "id": "lobsters-ai",
        "provider": "Lobsters",
        "title": "Lobsters AI",
        "url": "https://lobste.rs/t/ai.rss",
        "homepage": "https://lobste.rs/t/ai",
        "language": "en",
        "priority": 54,
        "category": "技术社区",
        "rss_only": True,
    },
    {
        "id": "lobsters-programming",
        "provider": "Lobsters",
        "title": "Lobsters Programming",
        "url": "https://lobste.rs/t/programming.rss",
        "homepage": "https://lobste.rs/t/programming",
        "language": "en",
        "priority": 53,
        "category": "技术社区",
        "rss_only": True,
    },
    {
        "id": "solidot",
        "provider": "Solidot",
        "title": "Solidot",
        "url": "https://www.solidot.org/index.rss",
        "homepage": "https://www.solidot.org/",
        "language": "zh",
        "priority": 52,
        "category": "技术社区",
        "rss_only": True,
    },
]

AI_NEWS_OFFICIAL_ITEMS = [
    {
        "id": "official-openai-news",
        "source": "OpenAI News",
        "provider": "OpenAI",
        "category": "官方入口",
        "title": "OpenAI 官方新闻与产品动态",
        "title_zh": "OpenAI 官方新闻与产品动态",
        "summary": "用于跟踪 OpenAI 模型、产品、API、研究与安全相关更新。打开来源可查看官方原文。",
        "published_at": None,
        "url": "https://openai.com/news/",
        "kind": "official",
        "priority": 100,
    },
    {
        "id": "official-anthropic-news",
        "source": "Anthropic News",
        "provider": "Anthropic",
        "category": "官方入口",
        "title": "Anthropic 官方新闻与 Claude 动态",
        "title_zh": "Anthropic 官方新闻与 Claude 动态",
        "summary": "用于跟踪 Claude、Claude Code、API 能力、安全研究和企业产品更新。",
        "published_at": None,
        "url": "https://www.anthropic.com/news",
        "kind": "official",
        "priority": 96,
    },
    {
        "id": "official-anthropic-release-notes",
        "source": "Anthropic Release Notes",
        "provider": "Anthropic",
        "category": "发布记录",
        "title": "Anthropic API 发布记录",
        "title_zh": "Anthropic API 发布记录",
        "summary": "更适合开发者查看接口、模型、SDK、工具调用和兼容性变化。",
        "published_at": None,
        "url": "https://docs.anthropic.com/en/release-notes/overview",
        "kind": "official",
        "priority": 94,
    },
    {
        "id": "official-google-gemini-models",
        "source": "Google AI for Developers",
        "provider": "Gemini",
        "category": "模型文档",
        "title": "Gemini API 模型文档",
        "title_zh": "Gemini API 模型文档",
        "summary": "查看 Gemini 模型版本、上下文、输入输出能力和开发接口说明。",
        "published_at": None,
        "url": "https://ai.google.dev/gemini-api/docs/models",
        "kind": "official",
        "priority": 92,
    },
    {
        "id": "official-deepseek-status",
        "source": "DeepSeek Status",
        "provider": "DeepSeek",
        "category": "官方状态",
        "title": "DeepSeek 官方状态页",
        "title_zh": "DeepSeek 官方状态页",
        "summary": "查看 DeepSeek API 与网页服务是否有维护、性能下降或已恢复事件。",
        "published_at": None,
        "url": "https://status.deepseek.com/",
        "kind": "official",
        "priority": 88,
    },
    {
        "id": "official-qwen-blog",
        "source": "Qwen Blog",
        "provider": "Qwen",
        "category": "开源模型",
        "title": "Qwen 官方博客",
        "title_zh": "Qwen 官方博客",
        "summary": "用于跟踪通义千问模型、开源权重、多模态能力和推理模型更新。",
        "published_at": None,
        "url": "https://qwenlm.github.io/blog/",
        "kind": "official",
        "priority": 86,
    },
]

AI_NEWS_ORIGINAL_ARTICLES = [
    {
        "id": "relaywatch-fake-model-checklist",
        "source": "RelayWatch",
        "provider": "本站原创",
        "category": "教程",
        "title": "怎么判断一个站点里的 GPT-5.5、Claude 4.8 是不是真的",
        "title_zh": "怎么判断一个站点里的 GPT-5.5、Claude 4.8 是不是真的",
        "summary": "只看模型名没有意义。判断一个模型是否真实可用，要同时看接口、行为、流式格式、工具能力、长上下文和错误返回。",
        "published_at": "2026-07-06T00:00:00+08:00",
        "url": "",
        "kind": "original",
        "language": "zh",
        "priority": 120,
        "content": """很多中转站会在模型列表里放非常夸张的模型名，比如 GPT-5.5、Claude Opus 4.8、某些不存在的 preview 型号。模型名本身不能证明任何事情，因为 New API 一类后台允许站长自定义模型名称，也允许把一个上游模型包装成另一个名字。

第一步，看模型列表接口。真正有意义的不是“页面上写了什么”，而是 `/v1/models` 或兼容接口返回了什么。返回里如果只有一个自定义模型名，没有 owner、created、permission 或结构明显不像 OpenAI/Anthropic 官方格式，只能说明站点自己登记了这个名字，不能说明上游真实存在。

第二步，做最小对话请求。不要一开始就跑复杂题，先用极短提示确认模型能不能完成基本响应，例如“只回答 ok”。如果连最小请求都 404、模型不存在、分组无权限、余额不足或超时，这个模型对普通用户就不可用。

第三步，看错误格式。很多假模型或反代线路会暴露真实上游的错误体。比如你请求 GPT 名称，却返回 Claude 风格错误；请求 Claude 名称，却返回 OpenAI 风格 `model_not_found`；或者错误里出现上游分组、渠道名、池子名，这些都说明模型名和真实线路可能不一致。

第四步，看流式格式。假兼容最容易在 stream 上露馅。OpenAI Chat Completions、Responses API、Anthropic Messages 的流式事件格式都不同。如果站点声称支持某个官方接口，却只返回拼接文本、缺少 delta/event/type，或者结束事件异常，就说明兼容层不完整。

第五步，测工具和图片。高阶模型通常伴随工具调用、图片输入、长上下文等能力。如果一个“满血模型”只能完成普通文本聊天，工具调用一测就失败，图片输入直接报不支持，那就要把它当成普通文本模型，而不是页面宣传的模型。

第六步，看长上下文。很多低价线路会把上下文砍得很小。你不需要一次烧很多 token，可以用中等长度文本测试是否提前截断、是否开始胡乱总结、是否报 context length exceeded。真正可用的模型至少应该稳定处理它宣传的上下文范围。

最后，把测试结果和价格放在一起看。便宜不是问题，问题是“便宜但宣传成满血”。如果一个站点价格极低、公告频繁维护、模型名夸张、错误格式混乱、工具能力缺失，那它更像是包装线路，而不是真正的官方同等能力模型。RelayWatch 的模型检测页适合做第一轮筛查，最终是否购买仍要以你自己的 Key 实测为准。""",
    },
    {
        "id": "relaywatch-price-ratio-guide",
        "source": "RelayWatch",
        "provider": "本站原创",
        "category": "教程",
        "title": "中转站倍率、美元额度和人民币价格到底怎么看",
        "title_zh": "中转站倍率、美元额度和人民币价格到底怎么看",
        "summary": "便宜不等于真实便宜。要同时看充值汇率、模型倍率、分组倍率、缓存价格、按次计费和成功率。",
        "published_at": "2026-07-06T00:05:00+08:00",
        "url": "",
        "kind": "original",
        "language": "zh",
        "priority": 118,
        "content": """中转站价格最容易让人看晕，因为页面里经常同时出现美元额度、人民币充值、模型倍率、分组倍率、按次价格和缓存倍率。只看某一个数字，很容易被误导。

先看充值比例。有些站写的是“1 元兑换 1 美元额度”，有些站是“0.5 元兑换 1 美元额度”，还有些站充值后显示的是自定义 quota。你看到的“美元”不一定等于官方美元，它可能只是站内计价单位。

再看模型倍率。模型倍率通常表示在官方输入输出价格基础上乘多少倍。比如某模型官方输入价格是 1，站点倍率 0.5，看起来就是半价。但如果站内 1 美元额度本身是按人民币充值换算的，还要把充值比例一起算进去。

第三，看分组倍率。很多 New API 站会给不同渠道设置不同分组，例如 default、vip、aws、官代、逆向、共享池。模型倍率只是第一层，分组倍率会再次影响最终消耗。一个模型在默认分组便宜，不代表在你能用的分组也便宜。

第四，看缓存价格。Claude、OpenAI、Gemini 等模型可能有 cache read、cache write、cached input 等不同价格。如果你做长上下文、多轮复用、代码库问答，缓存价格会显著影响真实成本。只看 input/output 价格是不够的。

第五，区分按量计费和按次计费。按量计费通常按 token 消耗；按次计费则可能每请求扣固定额度。对于短问题，按次可能很贵；对于超长问题，按次可能反而便宜。两种模式不能直接横向比较。

第六，看成功率和延迟。一个站点价格低但成功率很差，实际成本会变高，因为你要重试、换站、浪费时间。价格比较页应该和模型检测结果一起看：低价、低延迟、高成功率才是真正值得关注。

最后，不要被 0.0000 几的数字迷惑。站长可以把倍率写得非常小，但实际是否可用取决于分组权限、余额、渠道状态、模型是否真的存在。RelayWatch 展示的是公开接口采集到的价格，适合筛选候选站点；真正下单前，最好先小额测试。""",
    },
    {
        "id": "relaywatch-official-vs-relay-outage",
        "source": "RelayWatch",
        "provider": "本站原创",
        "category": "教程",
        "title": "模型调用失败时，怎么区分官方故障和中转站故障",
        "title_zh": "模型调用失败时，怎么区分官方故障和中转站故障",
        "summary": "先查官方状态，再看站点公告，最后用模型检测确认自己的 Key、分组和模型是否可用。",
        "published_at": "2026-07-06T00:10:00+08:00",
        "url": "",
        "kind": "original",
        "language": "zh",
        "priority": 116,
        "content": """当模型调用失败时，第一反应不要直接判断“站跑路了”或者“官方炸了”。正确做法是把问题拆成三层：官方上游、中转站线路、你自己的账号和参数。

第一层，看官方状态页。OpenAI、Anthropic、Google、DeepSeek 等厂商都会有官方状态或公告。如果官方 API 组件正在维护、错误率升高、延迟升高，那么多个中转站同时失败就很正常。这种情况下换站不一定有用，因为上游本身有波动。

第二层，看中转站公告。很多站点会在公告里写“低价线路维护”“Claude 池子封号”“某分组暂不可用”“域名迁移”“价格调整”。如果官方状态正常，但某个站公告频繁出现维护故障，那问题更可能在站点线路或 Key 池。

第三层，用模型检测。相同 base_url、相同 Key、相同模型名，跑一次最小请求。如果返回模型不存在，说明模型名或分组不对；如果返回余额不足，说明账号额度问题；如果返回上游错误，说明线路可能还能连到上游但当前失败；如果一直超时，可能是网络或站点后端压力。

第四层，对比多个模型。如果只有某个 Claude 模型失败，而 GPT 正常，问题可能是单个上游渠道或模型池；如果所有模型都失败，可能是站点认证、余额、域名、反代服务整体异常。

第五层，对比多个站点。如果多个不同站点的同一官方模型都失败，再结合官方状态页异常，就更像上游故障。如果只有一个站失败，其他站正常，那基本就是站点自身问题。

最后，记录错误体很重要。错误码、错误 message、响应头、流式中断位置，都能帮助判断问题。RelayWatch 的官方状态页、公告流和模型检测页，本质上就是为了把这三层信息放在一起看，减少盲目切站和误判。""",
    },
    {
        "id": "relaywatch-responses-api-checklist",
        "source": "RelayWatch",
        "provider": "本站原创",
        "category": "教程",
        "title": "中转站支持 Responses API 吗？迁移前要测这些点",
        "title_zh": "中转站支持 Responses API 吗？迁移前要测这些点",
        "summary": "不要只看路径能不能访问。要测试输入格式、流式事件、工具调用、图片输入和错误兼容。",
        "published_at": "2026-07-06T00:15:00+08:00",
        "url": "",
        "kind": "original",
        "language": "zh",
        "priority": 114,
        "content": """Responses API 把文本、工具、图片、多模态输入统一到一个接口里，对开发者很方便。但中转站是否真正支持，不能只看 `/v1/responses` 这个路径是否存在。

第一，测试最小请求。用简单 input 发起请求，确认返回结构里是否有 id、status、output、usage 等关键字段。如果只是把 Chat Completions 的结果包了一层，后续复杂能力很可能不稳定。

第二，测试流式返回。Responses API 的 stream 事件和传统 Chat Completions 不一样。你要看事件类型是否完整、增量文本是否正常、结束事件是否明确、usage 是否能返回。如果流式格式错了，前端或 SDK 很容易解析失败。

第三，测试工具调用。很多中转站普通对话能跑，但工具调用会失败。你可以定义一个简单工具，比如 get_time，让模型触发工具调用。真正兼容的接口应该能返回结构化工具调用，而不是把工具参数当普通文本吐出来。

第四，测试图片输入。如果站点声称支持多模态，就要用一张小图测试。常见问题包括 base64 不支持、URL 图片不支持、content type 写法不兼容、模型名映射错误。

第五，测试错误格式。迁移到 Responses API 后，业务代码经常依赖错误体判断重试、降级或提示用户。如果中转站错误格式混乱，线上排障会很难。至少要确认模型不存在、余额不足、参数错误、上游超时这几类错误能被区分。

第六，测试模型映射。某些站会把 Responses API 请求转到旧的 Chat Completions 模型，短文本看不出来，一到工具、多模态、结构化输出就露馅。迁移前要用目标业务真实场景跑一遍。

结论是：Responses API 迁移不是换一个 URL。对中转站来说，它考验的是完整兼容层。最稳妥的做法是先保留旧接口兜底，逐站检测 Responses API 能力，确认稳定后再切正式流量。""",
    },
]

AI_NEWS_SOURCES = [
    {
        "provider": "OpenAI",
        "name": "OpenAI News",
        "url": "https://openai.com/news/",
        "kind": "官方新闻",
        "description": "官方模型、产品、API 与研究动态。",
    },
    {
        "provider": "Anthropic",
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "kind": "官方新闻",
        "description": "Claude、Claude Code、API 与安全研究动态。",
    },
    {
        "provider": "Anthropic",
        "name": "Anthropic Release Notes",
        "url": "https://docs.anthropic.com/en/release-notes/overview",
        "kind": "发布记录",
        "description": "API、模型与产品变更说明。",
    },
    {
        "provider": "Google",
        "name": "Google AI Developers",
        "url": "https://ai.google.dev/gemini-api/docs/models",
        "kind": "开发者文档",
        "description": "Gemini API 模型、能力与开发文档。",
    },
    {
        "provider": "DeepSeek",
        "name": "DeepSeek Status",
        "url": "https://status.deepseek.com/",
        "kind": "官方状态",
        "description": "DeepSeek API 与网页服务状态。",
    },
    {
        "provider": "Qwen",
        "name": "Qwen Blog",
        "url": "https://qwenlm.github.io/blog/",
        "kind": "官方博客",
        "description": "通义千问模型、开源权重与能力更新。",
    },
]


GITHUB_PROJECT_SEARCHES = [
    {
        "id": "ai-aggregation-cn",
        "title": "GitHub AI 聚合项目",
        "query": "ai聚合 OR AI聚合 in:name,description,readme pushed:>2024-01-01",
        "priority": 96,
    },
    {
        "id": "ai-news-aggregator",
        "title": "GitHub AI 资讯聚合",
        "query": "AI news aggregator RSS daily in:name,description,readme pushed:>2024-01-01",
        "priority": 94,
    },
    {
        "id": "llm-gateway",
        "title": "GitHub LLM Gateway",
        "query": "llm gateway openai compatible proxy in:name,description,readme pushed:>2024-01-01",
        "priority": 90,
    },
    {
        "id": "newapi-oneapi",
        "title": "GitHub NewAPI / OneAPI",
        "query": "newapi OR one-api openai compatible in:name,description,readme pushed:>2024-01-01",
        "priority": 88,
    },
]


GITHUB_CURATED_PROJECTS = [
    "SuYxh/ai-news-aggregator",
    "comeonzhj/AutoContents",
    "justlovemaki/CloudFlare-AI-Insight-Daily",
    "sansan0/TrendRadar",
    "openziti/llm-gateway",
    "BerriAI/litellm",
    "mozilla-ai/otari",
    "ZianTT/chatnio",
    "didala083/aggregatedai",
    "qifan777/uni-ai",
    "xx025/carrot",
    "zzmlb/ai-news-bot",
]

AI_TUTORIALS = [
    {
        "id": "newapi-api-key-smoke-test",
        "title": "判断一个中转站模型是否真的可用",
        "category": "教程",
        "level": "实用",
        "summary": "先看公开模型列表，再用自己的 Key 做最小请求、流式请求和错误格式检查，避免只看站内模型名就误判。",
        "bullets": ["确认 base_url 与 /v1/models 可访问", "用低成本模型做 chat/completions 或 responses 探测", "记录状态码、错误体、延迟和是否降智"],
    },
    {
        "id": "price-ratio-reading",
        "title": "看懂倍率、美元额度和人民币结算",
        "category": "教程",
        "level": "入门",
        "summary": "不同站点的 1 美元额度不一定等于官方 1 美元，低倍率还要结合分组、缓存价格和请求成功率一起看。",
        "bullets": ["优先比较有效倍率和分组倍率", "注意按次计费和按量计费不是一回事", "低价站点要结合成功率与公告维护频率"],
    },
    {
        "id": "official-status-triage",
        "title": "模型调用失败时先区分上游故障和中转故障",
        "category": "排障",
        "level": "实用",
        "summary": "先看官方状态页，再看目标站公告与模型检测结果；如果官方正常但单站失败，多半是站点线路、Key 池或分组问题。",
        "bullets": ["官方状态用于判断上游波动", "公告流用于判断站点维护和价格调整", "模型检测用于确认你的 Key 和目标模型是否可用"],
    },
    {
        "id": "responses-api-migration",
        "title": "从 Chat Completions 迁移到 Responses API 的检查点",
        "category": "API",
        "level": "进阶",
        "summary": "新接口能力更统一，但中转站兼容程度不完全一致；迁移前要检查模型名、工具调用、流式格式和错误返回。",
        "bullets": ["确认站点是否代理 /v1/responses", "比较 stream 事件格式", "工具调用和图片输入要单独测"],
    },
]

def ai_news_category(text):
    lowered = (text or "").lower()
    if any(token in lowered for token in ("release", "launch", "introduc", "model", "gpt", "claude", "gemini", "deepseek", "qwen")):
        return "AI 资讯"
    if any(token in lowered for token in ("api", "developer", "responses", "tool", "function calling", "sdk")):
        return "AI 资讯"
    if any(token in lowered for token in ("guide", "tutorial", "cookbook", "how to", "best practices")):
        return "教程"
    if any(token in lowered for token in ("safety", "research", "eval", "benchmark")):
        return "AI 资讯"
    return "AI 资讯"


def ai_news_title_zh(title):
    text = compact_text(title, 180)
    lowered = text.lower()
    replacements = [
        ("introducing", "发布"),
        ("announcing", "宣布"),
        ("launching", "上线"),
        ("new", "新"),
        ("model", "模型"),
        ("models", "模型"),
        ("api", "API"),
        ("developer", "开发者"),
        ("developers", "开发者"),
        ("chatgpt", "ChatGPT"),
        ("openai", "OpenAI"),
        ("codex", "Codex"),
        ("sora", "Sora"),
        ("safety", "安全"),
        ("research", "研究"),
    ]
    if any(ord(ch) > 127 for ch in text):
        return text
    for source, target in replacements:
        lowered = re.sub(rf"\b{re.escape(source)}\b", target, lowered, flags=re.I)
    cleaned = re.sub(r"\s+", " ", lowered).strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else text


def ai_news_display_title(title, language=""):
    text = compact_text(title, 180)
    if language == "zh" or any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return text
    return ai_news_title_zh(text)


def ai_news_summary(body, fallback=""):
    text = compact_text(body or fallback, 180)
    if not text:
        return "官方发布了新的 AI 相关动态，建议打开来源查看完整说明。"
    return text


def ai_news_display_summary(body, title="", language=""):
    text = compact_text(body, 260)
    if text:
        return text
    return "该来源没有在 RSS 中提供摘要，可打开原文查看完整内容。"


def ai_news_excerpt(body, language=""):
    text = compact_text(body, 760)
    if not text:
        return "该来源没有在 RSS 中提供更多正文。你可以打开原文继续阅读。"
    if language != "zh" and not any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return f"原文摘录：{text}"
    return text


def feed_item_text(item, names):
    for name in names:
        value = item.findtext(name)
        if value:
            return value
        if not name.startswith("{"):
            for namespace in ("http://www.w3.org/2005/Atom", "http://purl.org/rss/1.0/modules/content/"):
                value = item.findtext(f"{{{namespace}}}{name}")
                if value:
                    return value
    for child in list(item):
        lowered = str(child.tag).lower()
        if any(lowered.endswith(name.lower()) for name in names):
            return child.text or ""
    return ""


def feed_item_link(item, fallback):
    direct = item.findtext("link")
    if direct:
        return direct
    for child in list(item):
        if str(child.tag).lower().endswith("link"):
            href = child.attrib.get("href")
            if href:
                return href
            if child.text:
                return child.text
    return fallback


def feed_fetch_headers(feed):
    if feed.get("headers") != "linuxdo":
        return None
    headers = {
        "Accept": "application/rss+xml,application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Referer": "https://linux.do/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if LINUXDO_COOKIE:
        headers["Cookie"] = LINUXDO_COOKIE
    return headers


def fetch_feed_text(feed):
    urls = feed.get("urls") or [feed["url"]]
    last_error = None
    for url in urls:
        try:
            return fetch_text_url(url, timeout=AI_NEWS_TIMEOUT, headers=feed_fetch_headers(feed)), url
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("no feed url")


def parse_key_value_lines(text):
    keys = ["标题", "作者", "板块", "编号", "帖子", "时间", "摘要", "主 题", "发 布 者", "标签分类", "内容预览", "直达链接"]
    key_pattern = "|".join(re.escape(key) for key in keys)
    result = {}
    for match in re.finditer(rf"({key_pattern})\s*[:：]\s*([\s\S]*?)(?=\s*(?:{key_pattern})\s*[:：]|$)", text or ""):
        key = match.group(1).strip()
        value = re.sub(r"\s*\|\s*$", "", match.group(2)).strip()
        if value:
            result[key] = value
    return result


def parse_linuxdo_telegram_items(feed, limit=AI_NEWS_PER_FEED):
    url = feed.get("telegram_fallback")
    if not url:
        return []
    page = fetch_text_url(url, timeout=AI_NEWS_TIMEOUT, headers={
        "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 RelayWatch/1.0",
    })
    blocks = re.findall(r'<div class="tgme_widget_message_wrap[\s\S]*?(?=<div class="tgme_widget_message_wrap|</main>)', page)
    items = []
    for block in blocks[:limit]:
        text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>', block)
        if not text_match:
            continue
        raw = re.sub(r"<br\s*/?>", "\n", text_match.group(1), flags=re.I)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = html_lib.unescape(re.sub(r"[ \t\r\f\v]+", " ", raw)).strip()
        fields = parse_key_value_lines(raw)
        title = compact_text(fields.get("标题") or fields.get("主 题") or raw, 180)
        link = fields.get("帖子") or fields.get("直达链接") or ""
        if not link:
            hrefs = re.findall(r'href="(https://linux\.do/t/topic/[^"]+)"', block)
            link = hrefs[0] if hrefs else feed.get("homepage") or feed["url"]
        date_match = re.search(r'<time datetime="([^"]+)"', block)
        published = date_match.group(1) if date_match else None
        summary = fields.get("摘要") or fields.get("内容预览") or ""
        author = fields.get("作者") or fields.get("发 布 者") or ""
        board = fields.get("板块") or fields.get("标签分类") or ""
        content = "\n\n".join([part for part in [
            f"作者：{author}" if author else "",
            f"板块：{board}" if board else "",
            summary,
        ] if part])
        msg_match = re.search(r'data-post="([^"]+)"', block)
        msg_id = msg_match.group(1) if msg_match else link
        items.append({
            "id": f"{feed['id']}::telegram::{msg_id}",
            "source": feed["title"],
            "provider": feed["provider"],
            "category": feed.get("category") or "社区讨论",
            "title": title,
            "title_zh": title,
            "summary": compact_text(summary or content or title, 260),
            "excerpt": compact_text(content or summary or title, 760),
            "content": content or summary or title,
            "content_status": "telegram",
            "content_length": len(content or summary or title),
            "published_at": published,
            "url": link,
            "kind": "community",
            "language": "zh",
            "priority": feed.get("priority", 50),
        })
    return sorted(items, key=lambda item: parse_status_datetime(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def telegram_message_blocks(page):
    return re.findall(r'<div class="tgme_widget_message_wrap[\s\S]*?(?=<div class="tgme_widget_message_wrap|</main>)', page or "")


def telegram_block_text(block):
    text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>', block or "")
    if not text_match:
        return ""
    raw = re.sub(r"<br\s*/?>", "\n", text_match.group(1), flags=re.I)
    raw = re.sub(r"</p\s*>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html_lib.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def telegram_block_links(block):
    links = []
    for href in re.findall(r'href="([^"]+)"', block or ""):
        href = html_lib.unescape(href)
        if href.startswith("tg://") or "t.me/iv?" in href:
            continue
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http") and href not in links:
            links.append(href)
    return links


def non_telegram_links(links):
    result = []
    for link in links or []:
        host = (urlparse(link).netloc or "").lower()
        if host in {"t.me", "telegram.me"} or host.endswith(".t.me") or host.endswith(".telegram.me"):
            continue
        result.append(link)
    return result


def preferred_discussion_link(links):
    patterns = [
        r"^https?://(?:www\.)?linux\.do/t/topic/\d+",
        r"^https?://(?:www\.)?v2ex\.com/t/\d+",
        r"^https?://ruby-china\.org/topics/\d+",
        r"^https?://cnodejs\.org/topic/",
        r"^https?://(?:www\.)?segmentfault\.com/[qa]/",
    ]
    for pattern in patterns:
        for link in links or []:
            if re.search(pattern, link, flags=re.I):
                return link
    return ""


def ai_news_source_from_url(url, fallback_source="", fallback_provider=""):
    host = (urlparse(url or "").netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return fallback_source or fallback_provider or "", fallback_provider or ""
    source_map = [
        ("linux.do", ("LinuxDo", "LinuxDo")),
        ("v2ex.com", ("V2EX", "V2EX")),
        ("producthunt.com", ("ProductHunt", "ProductHunt")),
        ("readhub.cn", ("Readhub", "Readhub")),
        ("appinn.com", ("小众软件", "小众软件")),
        ("github.com", ("GitHub", "GitHub")),
        ("qbitai.com", ("量子位", "量子位")),
        ("36kr.com", ("36氪", "36氪")),
        ("pingwest.com", ("PingWest", "PingWest")),
        ("openai.com", ("OpenAI", "OpenAI")),
        ("anthropic.com", ("Anthropic", "Anthropic")),
        ("deepseek.com", ("DeepSeek", "DeepSeek")),
        ("googleblog.com", ("Google", "Google")),
    ]
    for domain, labels in source_map:
        if host == domain or host.endswith("." + domain):
            return labels
    label = host.split(":")[0]
    return label, label


def telegram_message_url(block, feed):
    post_match = re.search(r'data-post="([^"]+)"', block or "")
    if post_match:
        return f"https://t.me/{post_match.group(1)}"
    channel = feed.get("telegram_channel") or urlparse(feed.get("telegram_url") or "").path.rstrip("/").split("/")[-1]
    return f"https://t.me/{channel}" if channel else feed.get("homepage") or feed.get("url") or ""


def telegram_item_matches(feed, title, content):
    keywords = feed.get("keywords")
    if not keywords:
        return True
    haystack = f"{title} {content}".lower()
    for token in keywords:
        if not token:
            continue
        token_text = str(token).lower()
        if re.search(r"[\u4e00-\u9fff]", token_text):
            if token_text in haystack:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(token_text)}(?![a-z0-9])", haystack):
            return True
    return False


def telegram_title_from_text(text):
    lines = [compact_text(line, 180) for line in (text or "").splitlines() if compact_text(line, 180)]
    if not lines:
        return ""
    for line in lines:
        if line.startswith("@") or re.fullmatch(r"https?://\S+", line):
            continue
        if line in {"阅读全文", "Read more", "Instant View"}:
            continue
        if len(line) >= 8 and not re.fullmatch(r"[#\s\W]+", line):
            return line
    return lines[0]


def parse_telegram_feed_items(feed, limit=AI_NEWS_PER_FEED):
    url = feed.get("telegram_url")
    if not url:
        return []
    page = fetch_text_url(url, timeout=AI_NEWS_TIMEOUT, headers={
        "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 RelayWatch/1.0",
    })
    items = []
    for block in telegram_message_blocks(page):
        text = telegram_block_text(block)
        if not text:
            continue
        title = telegram_title_from_text(text)
        if not title or not telegram_item_matches(feed, title, text):
            continue
        msg_url = telegram_message_url(block, feed)
        links = [link for link in telegram_block_links(block) if link != msg_url]
        external_links = non_telegram_links(links)
        canonical_url = preferred_discussion_link(external_links)
        date_match = re.search(r'<time datetime="([^"]+)"', block)
        published = date_match.group(1) if date_match else None
        msg_match = re.search(r'data-post="([^"]+)"', block)
        msg_id = msg_match.group(1) if msg_match else msg_url or title
        content_parts = [text]
        if external_links:
            content_parts.append("相关链接：\n" + "\n".join(external_links[:6]))
        content = "\n\n".join(part for part in content_parts if part)
        detected_language = "zh" if re.search(r"[\u4e00-\u9fff]", f"{title} {text}") else ""
        external_url = canonical_url
        source_name, provider_name = ai_news_source_from_url(
            external_url,
            feed["title"],
            feed.get("provider") if external_url else feed["title"],
        )
        items.append({
            "id": f"{feed['id']}::telegram::{msg_id}",
            "source": source_name,
            "provider": provider_name,
            "category": feed.get("category") or ai_news_category(f"{title} {text}"),
            "title": title,
            "title_zh": title,
            "summary": compact_text(text, 260),
            "excerpt": compact_text(content, 760),
            "content": content,
            "content_status": "telegram",
            "content_length": len(content),
            "published_at": published,
            "url": external_url,
            "source_url": msg_url,
            "kind": "community",
            "language": detected_language,
            "priority": feed.get("priority", 50),
        })
        if len(items) >= limit:
            break
    return sorted(items, key=lambda item: parse_status_datetime(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def fetch_ai_article_content(url, fallback_markup=""):
    fallback_text = html_to_article_text(fallback_markup, AI_NEWS_CONTENT_MAX_CHARS)
    if fallback_text and len(fallback_text) >= 1200:
        return fallback_text, "feed"
    if not url:
        return fallback_text, "feed" if fallback_text else "missing"
    try:
        page = fetch_text_url(url, timeout=AI_NEWS_TIMEOUT)
        content = extract_article_full_text(page)
        if len(content) >= max(400, len(fallback_text)):
            return content, "page"
    except Exception:
        pass
    return fallback_text, "feed" if fallback_text else "limited"


def article_line_is_noise(line, title="", provider="", source=""):
    text = compact_text(line, 220)
    if not text:
        return True
    lowered = text.lower()
    if re.fullmatch(r"<\s*/?\s*[a-z][^>]*>", text, flags=re.I):
        return True
    if re.search(r"<\s*(img|figure|iframe|script|style)\b", text, flags=re.I):
        return True
    if lowered.startswith(("本文转载", "转载请联系", "点击上方", "关注我们", "设为星标")):
        return True
    clean_title = compact_text(title, 160)
    if clean_title and len(text) <= len(clean_title) + 12 and (text in clean_title or clean_title in text):
        return True
    if provider and provider in text and len(text) <= 80:
        return True
    if source and source in text and len(text) <= 80:
        return True
    if re.search(r"(来源|发自|整理|作者|编辑|出品|公众号|qbitai|量子位)", text, flags=re.I) and len(text) <= 120:
        return True
    if re.search(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", text) and len(text) <= 140:
        return True
    return False


def clean_ai_article_content(content, title="", provider="", source=""):
    text = html_lib.unescape(content or "")
    text = re.sub(r"&lt;[^&]+?&gt;", " ", text)
    lines = [line.strip() for line in re.split(r"\n+", text) if line.strip()]
    if not lines:
        return ""

    # Many media sites place title, byline, logo HTML and account metadata
    # before the first real paragraph. Drop the whole leading metadata block.
    metadata_end = -1
    for index, line in enumerate(lines[:24]):
        if article_line_is_noise(line, title, provider, source):
            metadata_end = index
            continue
        if metadata_end >= 0 and len(compact_text(line, 220)) < 28:
            metadata_end = index
            continue
        break
    if metadata_end >= 0:
        lines = lines[metadata_end + 1:]

    cleaned = []
    previous = ""
    for line in lines:
        text_line = compact_text(line, 2000)
        if article_line_is_noise(text_line, title, provider, source):
            continue
        if text_line == previous:
            continue
        cleaned.append(text_line)
        previous = text_line
    return "\n\n".join(cleaned).strip()


def parse_rss_article_item(feed, item):
    title = compact_text(feed_item_text(item, ["title"]) or "", 180)
    link = feed_item_link(item, feed.get("homepage") or feed["url"])
    raw_body = feed_item_text(item, ["{http://purl.org/rss/1.0/modules/content/}encoded", "encoded", "content", "description", "summary"])
    content_html = sanitize_article_html(raw_body, link or feed.get("homepage") or feed["url"])
    if feed.get("rss_only"):
        content = html_to_article_text(raw_body, AI_NEWS_CONTENT_MAX_CHARS)
        content_status = "feed"
    else:
        content, content_status = fetch_ai_article_content(link, raw_body)
        content = clean_ai_article_content(content, title, feed.get("provider", ""), feed.get("title", ""))
    body = compact_text(content or raw_body, 360)
    published = feed_item_text(item, ["pubDate", "published", "updated"])
    parsed = parse_feed_datetime_with_hint(published, feed.get("timezone_hint", ""))
    language = feed.get("language") or ""
    category = feed.get("category") or (ai_news_category(f"{title} {body} {content[:500]}") if language != "zh" else "AI 资讯")
    return {
        "id": f"{feed['id']}::{item.findtext('guid') or link or title}",
        "source": feed["title"],
        "provider": feed["provider"],
        "category": category,
        "title": title,
        "title_zh": ai_news_display_title(title, language),
        "summary": ai_news_display_summary(body, title, language),
        "excerpt": ai_news_excerpt(body, language),
        "content": content,
        "content_html": content_html,
        "content_status": content_status,
        "content_length": len(content or ""),
        "image_count": len(re.findall(r"<img\b", content_html or "", flags=re.I)),
        "published_at": parsed.isoformat().replace("+00:00", "Z") if parsed else published,
        "url": link,
        "kind": "article",
        "language": language,
        "priority": feed.get("priority", 50),
    }


def parse_rss_feed_articles(feed, limit=8):
    text, fetched_url = fetch_feed_text(feed)
    root = ET.fromstring(text)
    raw_items = root.findall(".//item")[:limit]
    if not raw_items:
        raw_items = root.findall(".//{http://www.w3.org/2005/Atom}entry")[:limit]
    items = []
    with ThreadPoolExecutor(max_workers=max(1, AI_NEWS_FETCH_WORKERS)) as executor:
        futures = [executor.submit(parse_rss_article_item, feed, item) for item in raw_items]
        for future in as_completed(futures):
            item = future.result()
            if feed.get("keywords") and not telegram_item_matches(feed, item.get("title") or "", " ".join([
                item.get("summary") or "",
                item.get("excerpt") or "",
                item.get("content") or "",
            ])):
                continue
            items.append(item)
    feed["_last_fetched_url"] = fetched_url
    return sorted(items, key=lambda item: (item.get("priority") or 0, parse_status_datetime(item.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)


def github_api_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "RelayWatchGitHubProjects/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=AI_NEWS_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def github_project_item(repo, search):
    full_name = compact_text(repo.get("full_name") or "", 180)
    if not full_name:
        return None
    name = compact_text(repo.get("name") or full_name.split("/")[-1], 120)
    description = compact_text(repo.get("description") or "GitHub 开源项目，仓库未提供简介。", 280)
    url = repo.get("html_url") or f"https://github.com/{full_name}"
    topics = repo.get("topics") or []
    license_info = repo.get("license") or {}
    license_name = compact_text(license_info.get("spdx_id") or license_info.get("name") or "未标注", 80)
    stars = int(repo.get("stargazers_count") or 0)
    forks = int(repo.get("forks_count") or 0)
    language = compact_text(repo.get("language") or "未知语言", 80)
    updated_at = repo.get("pushed_at") or repo.get("updated_at")
    homepage = compact_text(repo.get("homepage") or "", 260)
    topic_text = "、".join(compact_text(topic, 32) for topic in topics[:8] if topic)
    summary = f"GitHub 开源项目：{description}"
    content_lines = [
        f"项目：{full_name}",
        f"简介：{description}",
        f"Stars：{stars}",
        f"Forks：{forks}",
        f"主要语言：{language}",
        f"许可证：{license_name}",
        f"最近更新：{updated_at or '未知'}",
    ]
    if topic_text:
        content_lines.append(f"Topics：{topic_text}")
    if homepage:
        content_lines.append(f"项目主页：{homepage}")
    content_lines.append(f"GitHub 原地址：{url}")
    return {
        "id": f"github-project::{full_name.lower()}",
        "source": "GitHub",
        "provider": "GitHub",
        "category": "开源项目",
        "title": full_name,
        "title_zh": f"{name}：{description}" if description else full_name,
        "summary": compact_text(summary, 260),
        "excerpt": compact_text("\n\n".join(content_lines), 760),
        "content": "\n\n".join(content_lines),
        "content_status": "github-api",
        "content_length": len("\n\n".join(content_lines)),
        "published_at": updated_at,
        "url": url,
        "kind": "project",
        "language": "zh",
        "priority": int(search.get("priority") or 80) + min(12, stars // 1000),
        "level": f"{stars} stars",
        "bullets": [
            f"{stars} stars / {forks} forks",
            f"主要语言：{language}",
            f"许可证：{license_name}",
        ],
        "github": {
            "full_name": full_name,
            "stars": stars,
            "forks": forks,
            "language": language,
            "license": license_name,
            "topics": topics[:12],
            "homepage": homepage,
        },
    }


def github_project_relevance(repo):
    text = " ".join([
        str(repo.get("full_name") or ""),
        str(repo.get("name") or ""),
        str(repo.get("description") or ""),
        " ".join(repo.get("topics") or []),
    ]).lower()
    if any(term in text for term in ("resume", "portfolio", "game server", "minecraft", "invoice", "banking", "stock", "interview", "java", "transformers", "training from scratch")):
        return 0
    strong_markers = [
        "ai聚合", "聚合", "aggregator", "aggregation", "news", "daily", "rss", "radar", "trend",
        "gateway", "openai compatible", "openai-compatible", "one-api", "newapi", "new-api",
        "proxy", "router",
    ]
    if not any(marker in text for marker in strong_markers):
        return 0
    score = 0
    weighted_terms = [
        ("ai聚合", 5), ("聚合", 4), ("one-api", 5), ("newapi", 5), ("new-api", 5),
        ("openai compatible", 5), ("openai-compatible", 5), ("ai gateway", 5), ("llm gateway", 5),
        ("gateway", 3), ("relay", 3), ("proxy", 2), ("router", 2),
        ("aggregator", 4), ("aggregation", 4), ("news", 3), ("daily", 2), ("rss", 3),
        ("radar", 3), ("trend", 2),
        ("llm", 3), ("mcp", 3), ("openai", 2), ("chatgpt", 2), ("claude", 2),
        ("agent", 1), ("ai", 1),
    ]
    for term, weight in weighted_terms:
        if term in text:
            score += weight
    if "mcp" in text and ("llm" in text or "ai" in text or "agent" in text):
        score += 3
    if "agent" in text and not any(term in text for term in ("llm", "mcp", "openai", "claude", "chatgpt", "ai")):
        score -= 2
    stars = int(repo.get("stargazers_count") or 0)
    if stars >= 1000:
        score += 2
    elif stars >= 100:
        score += 1
    if not repo.get("description") and stars < 30:
        score -= 2
    return score


def github_project_items():
    items = []
    seen = set()
    per_page = max(3, min(GITHUB_PROJECTS_PER_QUERY, 20))
    curated_search = {"id": "curated", "title": "GitHub 精选开源项目", "priority": 104}
    for full_name in GITHUB_CURATED_PROJECTS:
        try:
            repo = github_api_json(f"https://api.github.com/repos/{quote(full_name, safe='/')}")
            item = github_project_item(repo, curated_search)
            if not item or item["id"] in seen:
                continue
            seen.add(item["id"])
            items.append(item)
        except Exception as exc:
            print(f"github_curated_project_failed repo={full_name} error={str(exc)[:160]}", flush=True)
            continue
    for search in GITHUB_PROJECT_SEARCHES:
        try:
            url = (
                "https://api.github.com/search/repositories"
                f"?q={quote(search['query'], safe='')}"
                f"&sort=stars&order=desc&per_page={per_page}"
            )
            data = github_api_json(url)
            for repo in data.get("items") or []:
                if github_project_relevance(repo) < 4:
                    continue
                item = github_project_item(repo, search)
                if not item or item["id"] in seen:
                    continue
                seen.add(item["id"])
                items.append(item)
        except Exception as exc:
            print(f"github_project_search_failed id={search.get('id')} error={str(exc)[:160]}", flush=True)
            continue
    return sort_ai_news_items(items)


def discover_newapi_app_doc_urls():
    text = fetch_text_url(NEWAPI_APPS_DOCS_URL, timeout=AI_NEWS_TIMEOUT)
    urls = set()
    for match in re.finditer(r"href=[\"'](/zh/docs/apps[^\"']*)[\"']", text, flags=re.I):
        path = match.group(1).rstrip("\\")
        if path.rstrip("/") != "/zh/docs/apps" and path.startswith("/zh/docs/apps"):
            urls.add(f"{NEWAPI_DOCS_BASE_URL}{path}")
    return sorted(urls)


def parse_newapi_doc_article(url):
    markup = fetch_text_url(url, timeout=AI_NEWS_TIMEOUT)
    title, summary, content, content_html = extract_newapi_doc_article(markup)
    slug = urlparse(url).path.rstrip("/").split("/")[-1] or "apps"
    if not title:
        title = "NewAPI AI 应用教程" if slug == "apps" else f"NewAPI 教程：{slug}"
    return {
        "id": f"newapi-docs::{slug}",
        "source": "NewAPI 官方文档",
        "provider": "NewAPI",
        "category": "教程",
        "title": title,
        "title_zh": title,
        "summary": summary or "NewAPI 官方应用集成教程，已获得转载授权并保留原文地址。",
        "excerpt": compact_text(content, 760),
        "content": content,
        "content_html": content_html,
        "content_status": "newapi-docs",
        "content_length": len(content or ""),
        "image_count": len(re.findall(r"<img\b", content_html or "", flags=re.I)),
        "published_at": None,
        "url": url,
        "kind": "tutorial",
        "language": "zh",
        "priority": 110 if slug != "apps" else 112,
        "level": "官方文档",
        "bullets": [],
    }


def newapi_tutorial_items():
    urls = discover_newapi_app_doc_urls()
    items = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, AI_NEWS_FETCH_WORKERS))) as executor:
        futures = [executor.submit(parse_newapi_doc_article, url) for url in urls]
        for future in as_completed(futures):
            try:
                item = future.result()
                if item.get("content"):
                    items.append(item)
            except Exception:
                continue
    return sorted(items, key=lambda item: (item["priority"], item["title"]), reverse=True)


def dedupe_ai_news_items(items):
    deduped = []
    seen = set()
    for item in sort_ai_news_items(items):
        url = re.sub(r"#.*$", "", (item.get("url") or "").strip()).rstrip("/")
        title = compact_text(item.get("title") or item.get("title_zh") or "", 180).lower()
        key = url or f"{item.get('provider')}::{title}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def ai_news_sort_key(item):
    published_at = parse_status_datetime(item.get("published_at"))
    has_time = 1 if published_at else 0
    timestamp = published_at.timestamp() if published_at else 0
    return (has_time, timestamp, item.get("priority") or 0, item.get("title") or "")


def sort_ai_news_items(items):
    return sorted(items, key=ai_news_sort_key, reverse=True)


ARTICLES_SCHEMA_READY = False
ARTICLES_SCHEMA_LOCK = threading.RLock()
FEEDBACK_SCHEMA_READY = False
FEEDBACK_SCHEMA_LOCK = threading.RLock()


def ensure_articles_schema(cur):
    global ARTICLES_SCHEMA_READY
    if ARTICLES_SCHEMA_READY:
        return
    with ARTICLES_SCHEMA_LOCK:
        if ARTICLES_SCHEMA_READY:
            return
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
              id TEXT PRIMARY KEY,
              source TEXT NOT NULL DEFAULT '',
              provider TEXT NOT NULL DEFAULT '',
              category TEXT NOT NULL DEFAULT '动态',
              title TEXT NOT NULL,
              title_zh TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL DEFAULT '',
              excerpt TEXT NOT NULL DEFAULT '',
              content TEXT NOT NULL DEFAULT '',
              content_html TEXT NOT NULL DEFAULT '',
              url TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'article',
              language TEXT NOT NULL DEFAULT '',
              priority INTEGER NOT NULL DEFAULT 0,
              image_count INTEGER NOT NULL DEFAULT 0,
              content_length INTEGER NOT NULL DEFAULT 0,
              published_at TIMESTAMPTZ,
              first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              is_active BOOLEAN NOT NULL DEFAULT true,
              payload JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC NULLS LAST)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_active_time ON articles(is_active, published_at DESC NULLS LAST)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_provider ON articles(provider)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_kind ON articles(kind)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_title_trgm ON articles USING gin (lower(title) gin_trgm_ops)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_articles_summary_trgm ON articles USING gin (lower(summary) gin_trgm_ops)")
        ARTICLES_SCHEMA_READY = True


def article_db_id(item):
    raw_id = compact_text(item.get("id") or "", 260)
    if raw_id:
        return raw_id
    url = re.sub(r"#.*$", "", (item.get("url") or "").strip()).rstrip("/")
    title = compact_text(item.get("title") or item.get("title_zh") or "", 180).lower()
    return f"{item.get('provider') or item.get('source') or 'article'}::{url or title}"


def article_published_at(item):
    parsed = parse_status_datetime(item.get("published_at"))
    return parsed


def article_payload_for_db(item):
    payload = dict(item)
    payload["id"] = article_db_id(item)
    return payload


def upsert_articles_to_db(items):
    if not DB_ENABLED or not items:
        return 0
    _psycopg, _dict_row, Jsonb = import_psycopg()
    with db_connect() as conn:
        with conn.cursor() as cur:
            ensure_articles_schema(cur)
            for item in items:
                payload = article_payload_for_db(item)
                cur.execute(
                    """
                    INSERT INTO articles (
                      id, source, provider, category, title, title_zh, summary, excerpt,
                      content, content_html, url, kind, language, priority, image_count,
                      content_length, published_at, last_seen_at, is_active, payload
                    )
                    VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), true, %s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      source = EXCLUDED.source,
                      provider = EXCLUDED.provider,
                      category = EXCLUDED.category,
                      title = EXCLUDED.title,
                      title_zh = EXCLUDED.title_zh,
                      summary = EXCLUDED.summary,
                      excerpt = EXCLUDED.excerpt,
                      content = EXCLUDED.content,
                      content_html = EXCLUDED.content_html,
                      url = EXCLUDED.url,
                      kind = EXCLUDED.kind,
                      language = EXCLUDED.language,
                      priority = EXCLUDED.priority,
                      image_count = EXCLUDED.image_count,
                      content_length = EXCLUDED.content_length,
                      published_at = COALESCE(EXCLUDED.published_at, articles.published_at),
                      last_seen_at = now(),
                      is_active = true,
                      payload = EXCLUDED.payload
                    """,
                    (
                        payload["id"],
                        payload.get("source") or "",
                        payload.get("provider") or "",
                        payload.get("category") or "动态",
                        payload.get("title") or payload.get("title_zh") or "",
                        payload.get("title_zh") or payload.get("title") or "",
                        payload.get("summary") or "",
                        payload.get("excerpt") or "",
                        payload.get("content") or "",
                        payload.get("content_html") or "",
                        payload.get("url") or "",
                        payload.get("kind") or "article",
                        payload.get("language") or "",
                        int(payload.get("priority") or 0),
                        int(payload.get("image_count") or 0),
                        int(payload.get("content_length") or len(payload.get("content") or "")),
                        article_published_at(payload),
                        Jsonb(payload),
                    ),
                )
    return len(items)


def db_article_items(limit=None):
    if not DB_ENABLED:
        return []
    with db_connect() as conn:
        with conn.cursor() as cur:
            ensure_articles_schema(cur)
            query = """
                SELECT payload
                FROM articles
                WHERE is_active = true
                ORDER BY
                  CASE WHEN published_at IS NULL THEN 0 ELSE 1 END DESC,
                  published_at DESC NULLS LAST,
                  priority DESC,
                  title ASC
                """
            params = ()
            if limit is not None:
                query += " LIMIT %s"
                params = (limit,)
            cur.execute(query, params)
            return [db_json(row["payload"]) for row in cur.fetchall()]


def db_article_item(article_id):
    if not DB_ENABLED or not article_id:
        return None
    with db_connect() as conn:
        with conn.cursor() as cur:
            ensure_articles_schema(cur)
            cur.execute(
                """
                SELECT payload
                FROM articles
                WHERE is_active = true AND id = %s
                LIMIT 1
                """,
                (article_id,),
            )
            row = cur.fetchone()
            return db_json(row["payload"]) if row else None


def db_tutorial_article_items(limit=80):
    if not DB_ENABLED:
        return []
    with db_connect() as conn:
        with conn.cursor() as cur:
            ensure_articles_schema(cur)
            cur.execute(
                """
                SELECT payload
                FROM articles
                WHERE is_active = true
                  AND (kind = 'tutorial' OR provider = 'NewAPI')
                ORDER BY priority DESC, title ASC, last_seen_at DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
            return [db_json(row["payload"]) for row in cur.fetchall()]


def db_article_count():
    if not DB_ENABLED:
        return 0
    with db_connect() as conn:
        with conn.cursor() as cur:
            ensure_articles_schema(cur)
            cur.execute("SELECT count(*) AS count FROM articles WHERE is_active = true")
            row = cur.fetchone()
            return int(row["count"] or 0)


def ensure_feedback_schema(cur):
    global FEEDBACK_SCHEMA_READY
    if FEEDBACK_SCHEMA_READY:
        return
    with FEEDBACK_SCHEMA_LOCK:
        if FEEDBACK_SCHEMA_READY:
            return
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
              id BIGSERIAL PRIMARY KEY,
              type TEXT NOT NULL DEFAULT 'feedback',
              content TEXT NOT NULL,
              contact TEXT NOT NULL DEFAULT '',
              page_url TEXT NOT NULL DEFAULT '',
              user_agent TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'new',
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              payload JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status)")
        FEEDBACK_SCHEMA_READY = True


def store_feedback(payload, user_agent=""):
    content = compact_text(payload.get("content") or payload.get("message") or "", 4000)
    if len(content) < 3:
        raise HTTPException(status_code=400, detail="反馈内容太短")
    contact = compact_text(payload.get("contact") or "", 200)
    feedback_type = compact_text(payload.get("type") or "feedback", 40) or "feedback"
    if feedback_type not in {"feedback", "takedown", "bug", "feature"}:
        feedback_type = "feedback"
    page_url = compact_text(payload.get("page_url") or payload.get("url") or "", 500)
    created_at = datetime.now(timezone.utc)
    record = {
        "type": feedback_type,
        "content": content,
        "contact": contact,
        "page_url": page_url,
        "user_agent": compact_text(user_agent, 500),
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
    }
    if DB_ENABLED:
        _psycopg, _dict_row, Jsonb = import_psycopg()
        with db_connect() as conn:
            with conn.cursor() as cur:
                ensure_feedback_schema(cur)
                cur.execute(
                    """
                    INSERT INTO feedback (type, content, contact, page_url, user_agent, payload)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at
                    """,
                    (feedback_type, content, contact, page_url, record["user_agent"], Jsonb(record)),
                )
                row = cur.fetchone()
                return {"id": row["id"], "created_at": row["created_at"].isoformat(), "storage": "postgres"}
    FEEDBACK_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_FALLBACK_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False))
        handle.write("\n")
    return {"id": None, "created_at": record["created_at"], "storage": "jsonl"}


def list_feedback(limit=20):
    limit = min(max(int(limit or 20), 1), 100)
    if DB_ENABLED:
        with db_connect() as conn:
            with conn.cursor() as cur:
                ensure_feedback_schema(cur)
                cur.execute(
                    """
                    SELECT id, type, content, status, page_url, created_at
                    FROM feedback
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "content": row["content"],
                        "status": row["status"],
                        "page_url": row["page_url"],
                        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                    }
                    for row in cur.fetchall()
                ]
    if not FEEDBACK_FALLBACK_PATH.exists():
        return []
    items = []
    for line in FEEDBACK_FALLBACK_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except Exception:
            continue
        items.append({
            "id": None,
            "type": item.get("type") or "feedback",
            "content": item.get("content") or "",
            "status": "new",
            "page_url": item.get("page_url") or "",
            "created_at": item.get("created_at"),
        })
    return sorted(items, key=lambda item: item.get("created_at") or "", reverse=True)[:limit]


def require_admin_token(request):
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="管理员删除功能未配置")
    token = (
        request.headers.get("x-admin-token")
        or request.query_params.get("admin_token")
        or ""
    ).strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="管理员口令不正确")


def delete_feedback_item(feedback_id):
    if feedback_id < 1:
        raise HTTPException(status_code=400, detail="反馈 ID 不正确")
    if not DB_ENABLED:
        raise HTTPException(status_code=503, detail="当前反馈未使用数据库，暂不支持页面删除")
    with db_connect() as conn:
        with conn.cursor() as cur:
            ensure_feedback_schema(cur)
            cur.execute("DELETE FROM feedback WHERE id = %s RETURNING id", (feedback_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="反馈不存在或已删除")
    return {"deleted": True, "id": feedback_id}


def db_article_source_states():
    if not DB_ENABLED:
        return []
    with db_connect() as conn:
        with conn.cursor() as cur:
            ensure_articles_schema(cur)
            cur.execute(
                """
                SELECT provider, COALESCE(NULLIF(source, ''), provider) AS name, count(*) AS count, max(last_seen_at) AS last_seen_at
                FROM articles
                WHERE is_active = true
                GROUP BY provider, COALESCE(NULLIF(source, ''), provider)
                ORDER BY count(*) DESC, provider
                """
            )
            return [
                {
                    "name": row["name"] or row["provider"] or "文章来源",
                    "provider": row["provider"] or "",
                    "status": "ok",
                    "count": int(row["count"] or 0),
                    "url": "",
                    "last_seen_at": row["last_seen_at"].isoformat() if row.get("last_seen_at") else None,
                    "storage": "postgres",
                }
                for row in cur.fetchall()
            ]


def has_chinese_text(*values):
    return any(re.search(r"[\u4e00-\u9fff]", str(value or "")) for value in values)


def should_show_ai_news_item(item):
    if item.get("content_status") == "telegram" and not (item.get("url") or "").strip():
        return False
    if item.get("provider") == "NewAPI" or item.get("kind") in {"tutorial", "original", "project"}:
        return True
    if item.get("language") == "zh":
        return True
    return has_chinese_text(item.get("title"), item.get("content"))


def unknown_provider_status(provider, error):
    component_items = [{
        "name": provider.get("subtitle") or provider["name"],
        "status": "unknown",
        "status_label": "未知",
        "updated_at": None,
    }]
    return {
        "id": provider["id"],
        "name": provider["name"],
        "subtitle": provider.get("subtitle", ""),
        "status_url": provider["status_url"],
        "indicator": "unknown",
        "status_label": "未知",
        "description": "官方状态页连接受限" if provider.get("id") == "deepseek" else "官方状态获取失败",
        "updated_at": None,
        "components": [],
        "active_incidents": [],
        "history": [],
        "uptime": build_uptime_rows(component_items, [], "unknown"),
        "error": str(error)[:240],
    }


def load_official_status(force=False):
    now = time.time()
    with OFFICIAL_STATUS_CACHE_LOCK:
        if not force and OFFICIAL_STATUS_CACHE["data"] is not None and OFFICIAL_STATUS_CACHE["expires_at"] > now:
            return OFFICIAL_STATUS_CACHE["data"]
    providers = []
    for provider in OFFICIAL_STATUS_PROVIDERS:
        try:
            if provider.get("adapter") == "google":
                item = google_status_provider_status(provider)
            elif provider.get("adapter") == "deepseek":
                item = deepseek_status_provider_status(provider)
            else:
                item = statuspage_provider_status(provider)
        except Exception as exc:
            item = unknown_provider_status(provider, exc)
        providers.append(item)
    overall_candidates = [item["indicator"] for item in providers if item["indicator"] != "unknown"]
    overall = max(overall_candidates or [item["indicator"] for item in providers], key=status_rank, default="unknown")
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cache_ttl": OFFICIAL_STATUS_TTL,
        "overall": overall,
        "overall_label": STATUS_LABELS.get(overall, "未知"),
        "providers": providers,
    }
    with OFFICIAL_STATUS_CACHE_LOCK:
        OFFICIAL_STATUS_CACHE["data"] = result
        OFFICIAL_STATUS_CACHE["expires_at"] = now + OFFICIAL_STATUS_TTL
    return result


def compact_official_status(status):
    return {
        "generated_at": status.get("generated_at"),
        "cache_ttl": status.get("cache_ttl"),
        "overall": status.get("overall"),
        "overall_label": status.get("overall_label"),
        "providers": [
            {
                "id": provider.get("id"),
                "name": provider.get("name"),
                "subtitle": provider.get("subtitle", ""),
                "status_url": provider.get("status_url"),
                "indicator": provider.get("indicator"),
                "status_label": provider.get("status_label"),
                "description": provider.get("description"),
                "updated_at": provider.get("updated_at"),
                "error": provider.get("error", ""),
            }
            for provider in (status.get("providers") or [])
        ],
    }


def db_json(value):
    return value or {}


def db_all_models(cur, generation_id, data_version="0"):
    if (
        DB_MODEL_CACHE.get("generation_id") == generation_id
        and DB_MODEL_CACHE.get("data_version") == data_version
        and DB_MODEL_CACHE.get("models") is not None
    ):
        return DB_MODEL_CACHE["models"]
    with DB_MODEL_CACHE_LOCK:
        if (
            DB_MODEL_CACHE.get("generation_id") == generation_id
            and DB_MODEL_CACHE.get("data_version") == data_version
            and DB_MODEL_CACHE.get("models") is not None
        ):
            return DB_MODEL_CACHE["models"]
        cur.execute(
            """
            SELECT id, payload
            FROM canonical_models
            WHERE generation_id = %s
            ORDER BY sort_index NULLS LAST, id
            """,
            (generation_id,),
        )
        models = []
        for row in cur.fetchall():
            model = hydrate_model_runtime_fields(db_json(row["payload"]))
            model["_db_id"] = row["id"]
            models.append(model)
        DB_MODEL_CACHE["generation_id"] = generation_id
        DB_MODEL_CACHE["data_version"] = data_version
        DB_MODEL_CACHE["models"] = models
        with DB_MODEL_RESULT_CACHE_LOCK:
            DB_MODEL_RESULT_CACHE.clear()
        with DB_SITE_RESULT_CACHE_LOCK:
            DB_SITE_RESULT_CACHE.clear()
        return models


def db_model_site_candidate_ids(cur, generation_id, q_lower):
    like = f"%{q_lower}%"
    cur.execute(
        """
        WITH matched_sites AS (
          SELECT id
          FROM sites
          WHERE generation_id = %s
            AND lower(name) LIKE %s
          UNION
          SELECT id
          FROM sites
          WHERE generation_id = %s
            AND lower(origin) LIKE %s
          UNION
          SELECT id
          FROM sites
          WHERE generation_id = %s
            AND lower(coalesce(domain, '')) LIKE %s
        )
        SELECT DISTINCT cms.canonical_model_id
        FROM canonical_model_sites cms
        JOIN matched_sites s ON s.id = cms.site_id
        WHERE cms.generation_id = %s
        UNION
        SELECT DISTINCT canonical_model_id
        FROM canonical_model_sites
        WHERE generation_id = %s
          AND lower(model) LIKE %s
        """,
        (generation_id, like, generation_id, like, generation_id, like, generation_id, generation_id, like),
    )
    return {row["canonical_model_id"] for row in cur.fetchall()}


def redis_cache_key(namespace, key):
    try:
        serialized = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        serialized = repr(key)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{REDIS_PREFIX}:{namespace}:{digest}"


def redis_client():
    global REDIS_CLIENT, REDIS_AVAILABLE, REDIS_RETRY_AT
    if not REDIS_URL:
        REDIS_AVAILABLE = False
        return None
    now = time.time()
    if REDIS_AVAILABLE is False and now < REDIS_RETRY_AT:
        return None
    with REDIS_LOCK:
        now = time.time()
        if REDIS_AVAILABLE is False and now < REDIS_RETRY_AT:
            return None
        if REDIS_CLIENT is not None and REDIS_AVAILABLE:
            return REDIS_CLIENT
        try:
            import redis
            client = redis.Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
                retry_on_timeout=False,
            )
            client.ping()
            REDIS_CLIENT = client
            REDIS_AVAILABLE = True
            REDIS_RETRY_AT = 0
            print("redis_cache_enabled", flush=True)
            return client
        except Exception as exc:
            REDIS_CLIENT = None
            REDIS_AVAILABLE = False
            REDIS_RETRY_AT = time.time() + REDIS_RETRY_INTERVAL
            print(f"redis_cache_disabled error={str(exc)[:160]}", flush=True)
            return None


def redis_cache_get(namespace, key):
    client = redis_client()
    if client is None:
        return None
    try:
        raw = client.get(redis_cache_key(namespace, key))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as exc:
        print(f"redis_cache_get_failed namespace={namespace} error={str(exc)[:160]}", flush=True)
        return None


def redis_cache_set(namespace, key, value, ttl):
    client = redis_client()
    if client is None:
        return
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        client.setex(redis_cache_key(namespace, key), max(1, int(ttl)), raw)
    except Exception as exc:
        print(f"redis_cache_set_failed namespace={namespace} error={str(exc)[:160]}", flush=True)


def model_result_cache_get(key):
    now = time.time()
    with DB_MODEL_RESULT_CACHE_LOCK:
        cached = DB_MODEL_RESULT_CACHE.get(key)
        if cached:
            timestamp, value = cached
            if now - timestamp <= DB_MODEL_RESULT_CACHE_TTL:
                DB_MODEL_RESULT_CACHE.move_to_end(key)
                return value
            DB_MODEL_RESULT_CACHE.pop(key, None)
    cached = redis_cache_get("model-result", key)
    if cached is not None:
        with DB_MODEL_RESULT_CACHE_LOCK:
            DB_MODEL_RESULT_CACHE[key] = (time.time(), cached)
            DB_MODEL_RESULT_CACHE.move_to_end(key)
        return cached
    return None


def model_result_cache_set(key, value):
    with DB_MODEL_RESULT_CACHE_LOCK:
        DB_MODEL_RESULT_CACHE[key] = (time.time(), value)
        DB_MODEL_RESULT_CACHE.move_to_end(key)
        while len(DB_MODEL_RESULT_CACHE) > DB_MODEL_RESULT_CACHE_MAX:
            DB_MODEL_RESULT_CACHE.popitem(last=False)
    redis_cache_set("model-result", key, value, DB_MODEL_RESULT_CACHE_TTL)


def site_result_cache_get(key):
    now = time.time()
    with DB_SITE_RESULT_CACHE_LOCK:
        cached = DB_SITE_RESULT_CACHE.get(key)
        if cached:
            timestamp, value = cached
            if now - timestamp <= DB_SITE_RESULT_CACHE_TTL:
                DB_SITE_RESULT_CACHE.move_to_end(key)
                return value
            DB_SITE_RESULT_CACHE.pop(key, None)
    cached = redis_cache_get("site-result", key)
    if cached is not None:
        with DB_SITE_RESULT_CACHE_LOCK:
            DB_SITE_RESULT_CACHE[key] = (time.time(), cached)
            DB_SITE_RESULT_CACHE.move_to_end(key)
        return cached
    return None


def site_result_cache_set(key, value):
    with DB_SITE_RESULT_CACHE_LOCK:
        DB_SITE_RESULT_CACHE[key] = (time.time(), value)
        DB_SITE_RESULT_CACHE.move_to_end(key)
        while len(DB_SITE_RESULT_CACHE) > DB_SITE_RESULT_CACHE_MAX:
            DB_SITE_RESULT_CACHE.popitem(last=False)
    redis_cache_set("site-result", key, value, DB_SITE_RESULT_CACHE_TTL)


def db_meta_cache_get(key):
    with DB_META_CACHE_LOCK:
        cached = DB_META_CACHE.get(key)
        if cached is not None:
            return cached
    return redis_cache_get("meta", key)


def db_meta_cache_set(key, value):
    with DB_META_CACHE_LOCK:
        DB_META_CACHE[key] = value
        if len(DB_META_CACHE) > 64:
            for old_key in list(DB_META_CACHE.keys())[: len(DB_META_CACHE) - 64]:
                DB_META_CACHE.pop(old_key, None)
    redis_cache_set("meta", key, value, DB_META_CACHE_TTL)


def normalize_submitted_origin(value):
    text = (value or "").strip()
    if not text or len(text) > 300:
        raise HTTPException(status_code=400, detail="请输入有效站点地址")
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="只支持 http 或 https 地址")
    host = (parsed.netloc or "").split("@")[-1].strip().lower()
    if not host or any(char.isspace() for char in host):
        raise HTTPException(status_code=400, detail="站点域名格式不正确")
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
        raise HTTPException(status_code=400, detail="请提交公网可访问的站点")
    host_without_port = host.rsplit(":", 1)[0] if host.count(":") <= 1 else host.strip("[]")
    try:
        ip = ipaddress.ip_address(host_without_port)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(status_code=400, detail="请提交公网可访问的站点")
    except ValueError:
        pass
    return f"{parsed.scheme.lower()}://{host}".rstrip("/")


def append_custom_origin(origin):
    path = CUSTOM_ORIGINS_PATH
    with SUBMITTED_ORIGINS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if path.exists():
            existing = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        normalized_existing = {item.rstrip("/") for item in existing}
        if origin in normalized_existing:
            return False
        with path.open("a", encoding="utf-8") as handle:
            if existing:
                handle.write("\n")
            handle.write(origin)
            handle.write("\n")
    return True


def submit_site_fetch(origin, path):
    import httpx

    timeout = httpx.Timeout(
        SUBMIT_SITE_TIMEOUT,
        connect=SUBMIT_SITE_TIMEOUT,
        read=SUBMIT_SITE_TIMEOUT,
        write=SUBMIT_SITE_TIMEOUT,
        pool=SUBMIT_SITE_TIMEOUT,
    )
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 RelayWatch submit-check/1.0",
        "New-Api-User": "-1",
        "Cache-Control": "no-store",
    }
    url = origin.rstrip("/") + path
    started = time.time()
    try:
        with httpx.Client(headers=headers, timeout=timeout, verify=False, follow_redirects=True, trust_env=True) as client:
            response = client.get(url)
        text = response.text or ""
        truncated = len(text) > SUBMIT_SITE_MAX_CHARS
        if truncated:
            text = text[:SUBMIT_SITE_MAX_CHARS]
        parsed = None
        json_ok = False
        try:
            parsed = json.loads(text) if text.strip() else None
            json_ok = isinstance(parsed, dict)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        return {
            "url": url,
            "status": response.status_code,
            "headers": dict(response.headers),
            "content_type": response.headers.get("content-type", ""),
            "elapsed_ms": int((time.time() - started) * 1000),
            "ok": response.status_code == 200 and json_ok,
            "json_ok": json_ok,
            "json": parsed,
            "truncated": truncated,
            "body": text,
        }
    except Exception as exc:
        return {
            "url": url,
            "status": None,
            "headers": {},
            "content_type": "",
            "elapsed_ms": int((time.time() - started) * 1000),
            "ok": False,
            "json_ok": False,
            "json": None,
            "truncated": False,
            "error": exc.__class__.__name__,
            "body": str(exc)[:500],
        }


async def fetch_submit_endpoint(origin, path):
    return await asyncio.to_thread(submit_site_fetch, origin, path)


def new_api_version_header(endpoint):
    for key, value in (endpoint.get("headers") or {}).items():
        if key.lower() == "x-new-api-version" and str(value).strip():
            return str(value).strip()
    return ""


def submit_status_data(endpoint):
    parsed = endpoint.get("json")
    data = parsed.get("data") if isinstance(parsed, dict) else {}
    return data if isinstance(data, dict) else {}


def submitted_site_is_self_use(status_endpoint):
    return submit_status_data(status_endpoint).get("self_use_mode_enabled") is True


def normalize_detection_protocol(value):
    protocol = str(value or "openai").strip().lower()
    aliases = {
        "openai": "openai",
        "gemini": "gemini",
        "claude": "anthropic",
        "anthropic": "anthropic",
    }
    if protocol not in aliases:
        raise HTTPException(status_code=400, detail="检测协议只支持 openai、anthropic 或 gemini")
    return aliases[protocol]


def normalize_detection_mode(value):
    mode = str(value or "quick").strip().lower()
    if mode not in {"quick", "standard", "full"}:
        raise HTTPException(status_code=400, detail="检测强度只支持 quick、standard 或 full")
    return mode


def normalize_detection_model(value):
    model = str(value or "").strip()
    if not model or len(model) > 200:
        raise HTTPException(status_code=400, detail="模型名称不能为空且不能超过 200 个字符")
    return model


def normalize_detection_api_key(value):
    api_key = str(value or "").strip()
    if len(api_key) < 8 or len(api_key) > 4096:
        raise HTTPException(status_code=400, detail="API Key 格式不正确")
    return api_key


def normalize_public_http_url(value):
    text = (value or "").strip()
    if not text or len(text) > 500:
        raise HTTPException(status_code=400, detail="请输入有效 Base URL")
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="只支持 http 或 https 地址")
    host = (parsed.netloc or "").split("@")[-1].strip().lower()
    if not host or any(char.isspace() for char in host):
        raise HTTPException(status_code=400, detail="Base URL 域名格式不正确")
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.endswith(".local"):
        raise HTTPException(status_code=400, detail="请使用公网可访问的 Base URL")
    host_without_port = host.rsplit(":", 1)[0] if host.count(":") <= 1 else host.strip("[]")
    try:
        ip = ipaddress.ip_address(host_without_port)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(status_code=400, detail="请使用公网可访问的 Base URL")
    except ValueError:
        pass
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{host}{path}".rstrip("/")


def normalize_detection_base_url(value, origin, protocol):
    raw = str(value or "").strip()
    if not raw:
        raw = origin.rstrip("/") + "/v1" if protocol in {"openai", "gemini"} else origin
    normalized = normalize_public_http_url(raw)
    if protocol in {"openai", "gemini"} and not normalized.rstrip("/").endswith("/v1"):
        normalized = normalized.rstrip("/") + "/v1"
    return normalized.rstrip("/")


def detection_endpoint_for_protocol(protocol):
    if protocol == "anthropic":
        return "/api/detect/claude"
    return f"/api/detect/{protocol}"


def detector_connection_error_message(exc):
    name = exc.__class__.__name__
    if name in {"ConnectError", "ConnectTimeout"}:
        return "检测服务暂时不可用：后台检测进程没有响应，请稍后再试"
    if name in {"ReadTimeout", "TimeoutException", "PoolTimeout"}:
        return "检测服务响应超时：本次检测没有及时返回，请稍后重试"
    return "检测服务连接异常：请稍后重试"


QUALITY_PROBE_LABELS = {
    "smoke": "真实调用",
    "cn_number": "中文数字判断",
    "reasoning": "基础推理",
    "json_follow": "严格 JSON",
    "code_only": "代码指令跟随",
    "self_report": "模型自述",
    "ai_summary": "AI 总结",
}


CORE_QUALITY_PROBE_NAMES = ("smoke", "cn_number", "reasoning", "json_follow", "code_only")


QUALITY_PROBES = [
    {
        "name": "smoke",
        "prompt": "Reply with OK only.",
        "max_tokens": 8,
        "weight": 2,
    },
    {
        "name": "cn_number",
        "prompt": "请严格只用中文回答：9.11 和 9.9 哪个数字更大？先给结论，再用不超过20字解释。",
        "max_tokens": 48,
        "weight": 2,
    },
    {
        "name": "reasoning",
        "prompt": "A bat and a ball cost $1.10 total. The bat costs $1.00 more than the ball. How much does the ball cost? Reply with final answer and one sentence reasoning.",
        "max_tokens": 96,
        "weight": 2,
    },
    {
        "name": "json_follow",
        "prompt": 'Return exactly this JSON object and nothing else: {"ok":true,"n":17}',
        "max_tokens": 48,
        "weight": 2,
    },
    {
        "name": "code_only",
        "prompt": "请给出一个 Python 函数 is_valid_parentheses(s)，判断 ()[]{} 是否正确嵌套；严格只输出代码，不要解释，不要 Markdown。",
        "max_tokens": 120,
        "weight": 1,
    },
    {
        "name": "self_report",
        "prompt": '只返回 JSON，不要解释：{"self_reported_model":"","capabilities":[],"risk_note":""}',
        "max_tokens": 64,
        "weight": 0,
    },
]


def quality_probe_status(name, text, response_model=None, requested_model=None, usage=None, error=None):
    if error:
        error_text = short_detection_text(error, 180)
        if "超时" in error_text:
            return "warn", error_text
        return "fail", error_text
    value = (text or "").strip()
    lowered = value.lower()
    if not value:
        return "fail", "返回内容为空"
    if name == "smoke":
        return ("pass", "最小请求成功") if re.fullmatch(r"(?is)\s*ok[.!。！]?\s*", value) else ("warn", f"返回了内容，但不是严格 OK：{short_detection_text(value, 80)}")
    if name == "cn_number":
        if "9.9" in value and ("更大" in value or "大于" in value or ">" in value):
            return "pass", "能正确比较 9.9 与 9.11"
        if "9.11" in value and ("更大" in value or "大于" in value or ">" in value):
            return "fail", "简单数字比较答错"
        return "warn", "没有给出清晰中文结论"
    if name == "reasoning":
        if any(token in lowered for token in ("$0.05", "0.05", "5 cents", "five cents", "5美分")):
            return "pass", "经典推理题结果正确"
        if any(token in lowered for token in ("0.10", "$0.10", "10 cents", "10美分")):
            return "fail", "经典推理题答成常见错误"
        return "warn", "推理题没有给出可识别答案"
    if name == "json_follow":
        candidate = value.strip()
        if candidate.startswith("```"):
            return "warn", "JSON 正确性受 Markdown 包裹影响"
        try:
            parsed = json.loads(candidate)
        except Exception:
            return "fail", "未按要求返回可解析 JSON"
        return ("pass", "严格 JSON 输出正确") if parsed == {"ok": True, "n": 17} else ("fail", "JSON 内容不符合指定对象")
    if name == "code_only":
        bad_markdown = "```" in value
        has_explain = any(token in value for token in ("解释", "说明", "这个函数", "Here's", "Here is"))
        has_function = "def is_valid_parentheses" in value and ("stack" in lowered or "append" in lowered)
        if has_function and not bad_markdown and not has_explain:
            return "pass", "能按要求只输出代码"
        if has_function:
            return "warn", "代码存在，但没有严格遵守只输出代码"
        return "fail", "没有输出可识别的目标函数"
    if name == "self_report":
        try:
            parsed = json.loads(value)
            reported = short_detection_text(parsed.get("self_reported_model") or "", 80) if isinstance(parsed, dict) else ""
        except Exception:
            return "warn", "模型自述不是严格 JSON"
        if reported and requested_model and reported.lower() not in requested_model.lower() and requested_model.lower() not in reported.lower():
            return "warn", f"模型自述为 {reported}，只能作为弱证据"
        return "pass", "模型自述格式可解析，仅作弱证据"
    return "pass", "探针完成"


def quality_probe_exception_result(exc):
    name = exc.__class__.__name__
    if name in {"ReadTimeout", "TimeoutException", "PoolTimeout"}:
        message = "该探针请求超时"
    elif name in {"ConnectError", "ConnectTimeout"}:
        message = "该探针连接目标站点失败"
    else:
        message = f"该探针执行异常：{name}"
    return {
        "ok": False,
        "error": message,
        "http_status": None,
        "latency_ms": None,
        "response_model": None,
        "usage": {},
        "text": "",
    }


def build_quality_row(probe, result, model):
    status, summary = quality_probe_status(
        probe["name"],
        result.get("text"),
        response_model=result.get("response_model"),
        requested_model=model,
        usage=result.get("usage"),
        error=result.get("error"),
    )
    return {
        "name": probe["name"],
        "display_name": QUALITY_PROBE_LABELS.get(probe["name"]),
        "status": status,
        "weight": probe["weight"],
        "summary": summary,
        "http_status": result.get("http_status"),
        "latency_ms": result.get("latency_ms"),
        "response_model": result.get("response_model"),
        "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
        "sample": short_detection_text(result.get("text") or "", 240),
    }


def quality_score(rows):
    total = 0
    earned = 0
    for row in rows:
        weight = int(row.get("weight") or 0)
        total += weight
        if row.get("status") == "pass":
            earned += weight
        elif row.get("status") == "warn":
            earned += weight * 0.45
    return round((earned / total) * 100) if total else None


def quality_risk_tags(rows, response_model, requested_model):
    tags = []
    by_name = {row.get("name"): row for row in rows}
    if by_name.get("smoke", {}).get("status") == "fail":
        tags.append("真实调用失败")
    if by_name.get("cn_number", {}).get("status") == "fail":
        tags.append("中文数字判断错误")
    if by_name.get("reasoning", {}).get("status") == "fail":
        tags.append("基础推理错误")
    if by_name.get("json_follow", {}).get("status") in {"warn", "fail"}:
        tags.append("指令跟随不稳")
    if by_name.get("code_only", {}).get("status") in {"warn", "fail"}:
        tags.append("代码输出不够干净")
    if response_model and requested_model:
        left = str(response_model).lower()
        right = str(requested_model).lower()
        if left != right and left not in right and right not in left:
            tags.append("响应模型名不一致")
    for row in rows:
        if "超时" in str(row.get("summary") or ""):
            tags.append("响应超时或偏慢")
            break
    for row in rows:
        usage = row.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        if isinstance(prompt_tokens, (int, float)) and prompt_tokens > 800:
            tags.append("疑似隐藏提示过长")
            break
    return list(dict.fromkeys(tags))[:8]


def quality_passed_checks_text(rows):
    labels = []
    by_name = {row.get("name"): row for row in rows}
    display_names = {
        "smoke": "真实调用",
        "cn_number": "数字判断",
        "reasoning": "基础推理",
        "json_follow": "严格JSON",
        "code_only": "代码指令跟随",
    }
    for name in CORE_QUALITY_PROBE_NAMES:
        if by_name.get(name, {}).get("status") == "pass":
            labels.append(display_names[name])
    if not labels:
        return ""
    if len(labels) == 1:
        return f"{labels[0]}通过"
    return "、".join(labels[:-1]) + f"和{labels[-1]}均通过"


def clean_quality_summary_text(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    blocked_patterns = [
        r"建议继续进行小额测试[，,、 ]*观察稳定性与成本表现[。.!！]*",
        r"建议继续小额测试[，,、 ]*观察稳定性与成本表现[。.!！]*",
        r"建议.*?小额测试.*?(?:成本表现|扣费情况)[。.!！]*",
    ]
    for pattern in blocked_patterns:
        text = re.sub(pattern, "", text, flags=re.I)
    return text.strip(" ，,。.!！")


def deterministic_ai_summary(score, tags, rows):
    failed = [QUALITY_PROBE_LABELS.get(row.get("name"), row.get("name")) for row in rows if row.get("status") == "fail"]
    warned = [QUALITY_PROBE_LABELS.get(row.get("name"), row.get("name")) for row in rows if row.get("status") == "warn"]
    passed_text = quality_passed_checks_text(rows)
    if score is None:
        return "协议检测已经完成，目标站点可以完成基础连接和响应结构验证。质量实测暂时没有拿到足够样本，不能直接判断模型水平；需要结合协议报告和后续多次实测再看。"
    if any("超时" in str(row.get("summary") or "") for row in rows):
        return "协议检测已经通过，说明接口和模型基础链路可用。质量实测里出现请求超时，主要风险在响应速度和稳定性；不建议直接承担重要任务。"
    if score >= 85:
        detail = f"{passed_text}，" if passed_text else ""
        risk = "未发现明显功能风险" if not tags else "主要风险为" + "、".join(tags[:2])
        return f"本次实测得分 {score}，{detail}说明接口能完成最小真实请求、基础判断、推理、结构化输出和代码指令跟随；{risk}，整体可用性较好。"
    if score >= 70:
        focus = "，主要风险集中在" + "、".join(tags[:2]) if tags else ""
        detail = f"{passed_text}，" if passed_text else ""
        return f"本次质量实测整体可用，{detail}核心调用能力没有明显问题{focus}。它更适合作为备选或低风险任务线路。"
    if failed:
        return "本次质量实测风险偏高，问题主要出现在" + "、".join(failed[:3]) + "。协议可用只说明接口能连通，不代表模型质量稳定；建议不要直接用于重要任务。"
    detail = f"{passed_text}，" if passed_text else ""
    return f"本次质量实测结果一般，{detail}基础调用可以完成，但稳定性、指令跟随和输出可信度还需要更多样本确认。"


def quality_level(score):
    if score is None:
        return "unknown"
    if score >= 85:
        return "recommended"
    if score >= 70:
        return "usable"
    if score >= 50:
        return "risky"
    return "poor"


def quality_row_is_timeout(row):
    return "超时" in str(row.get("summary") or "")


def finalize_quality_result(rows, response_model, model):
    if rows and all(quality_row_is_timeout(row) for row in rows):
        score = None
        tags = ["响应超时或偏慢"]
        ai_summary = "协议检测已通过，说明接口入口和协议结构基本可用。质量实测阶段连续超时，没有拿到足够的模型行为样本；当前最大风险是响应速度和稳定性。"
    else:
        score = quality_score(rows)
        tags = quality_risk_tags(rows, response_model, model)
        ai_summary = deterministic_ai_summary(score, tags, rows)
    return score, tags, ai_summary


def detection_context_cleanup():
    now = time.time()
    with DETECTION_CONTEXTS_LOCK:
        for key, value in list(DETECTION_CONTEXTS.items()):
            if now - value.get("created_at", now) > DETECTION_CONTEXT_TTL:
                DETECTION_CONTEXTS.pop(key, None)
        for key, value in list(DETECTION_QUALITY_RESULTS.items()):
            if now - value.get("created_at", now) > DETECTION_CONTEXT_TTL:
                DETECTION_QUALITY_RESULTS.pop(key, None)


def store_detection_context(job_id, context):
    detection_context_cleanup()
    with DETECTION_CONTEXTS_LOCK:
        DETECTION_CONTEXTS[job_id] = {"created_at": time.time(), **context}


def pop_detection_context(job_id):
    with DETECTION_CONTEXTS_LOCK:
        return DETECTION_CONTEXTS.pop(job_id, None)


def peek_detection_context(job_id):
    detection_context_cleanup()
    with DETECTION_CONTEXTS_LOCK:
        return DETECTION_CONTEXTS.get(job_id)


def store_detection_quality(job_id, quality):
    detection_context_cleanup()
    with DETECTION_CONTEXTS_LOCK:
        DETECTION_QUALITY_RESULTS[job_id] = {"created_at": time.time(), "quality": quality}


def get_detection_quality(job_id):
    detection_context_cleanup()
    with DETECTION_CONTEXTS_LOCK:
        cached = DETECTION_QUALITY_RESULTS.get(job_id)
        return cached.get("quality") if isinstance(cached, dict) else None


def openai_quality_target(base_url):
    return chat_proxy_target(base_url)


async def call_openai_quality_probe(client, base_url, api_key, model, prompt, max_tokens):
    target_url = openai_quality_target(base_url)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}
    started = time.time()
    response = await client.post(target_url, headers=headers, json=body)
    latency_ms = round((time.time() - started) * 1000)
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": chat_error_message(response.text),
            "http_status": response.status_code,
            "latency_ms": latency_ms,
            "response_model": payload.get("model") if isinstance(payload, dict) else None,
            "usage": payload.get("usage") if isinstance(payload, dict) else {},
            "text": "",
        }
    return {
        "ok": True,
        "error": "",
        "http_status": response.status_code,
        "latency_ms": latency_ms,
        "response_model": payload.get("model") if isinstance(payload, dict) else None,
        "usage": payload.get("usage") if isinstance(payload, dict) else {},
        "text": chat_completion_text(payload),
    }


async def call_anthropic_quality_probe(client, base_url, api_key, model, prompt, max_tokens):
    normalized = base_url.rstrip("/")
    target_url = f"{normalized}/messages" if normalized.endswith("/v1") else f"{normalized}/v1/messages"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "accept": "application/json",
    }
    started = time.time()
    response = await client.post(target_url, headers=headers, json=body)
    latency_ms = round((time.time() - started) * 1000)
    try:
        payload = response.json()
    except Exception:
        payload = {}
    usage = payload.get("usage") if isinstance(payload, dict) else {}
    normalized_usage = {}
    if isinstance(usage, dict):
        normalized_usage = {
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0),
        }
    if response.status_code >= 400:
        return {
            "ok": False,
            "error": chat_error_message(response.text),
            "http_status": response.status_code,
            "latency_ms": latency_ms,
            "response_model": payload.get("model") if isinstance(payload, dict) else None,
            "usage": normalized_usage,
            "text": "",
        }
    text_parts = []
    content_parts = payload.get("content") if isinstance(payload, dict) else []
    for part in content_parts or []:
        if isinstance(part, dict):
            text_parts.append(part.get("text") or "")
    return {
        "ok": True,
        "error": "",
        "http_status": response.status_code,
        "latency_ms": latency_ms,
        "response_model": payload.get("model") if isinstance(payload, dict) else None,
        "usage": normalized_usage,
        "text": "".join(text_parts),
    }


async def run_quality_probes(context):
    import httpx

    protocol = context.get("protocol") or "openai"
    base_url = context.get("base_url") or ""
    api_key = context.get("api_key") or ""
    model = context.get("model") or ""
    mode = context.get("mode") or "standard"
    native_probe = call_anthropic_quality_probe if protocol == "anthropic" else call_openai_quality_probe

    probes = QUALITY_PROBES if mode in {"standard", "full"} else [QUALITY_PROBES[0], QUALITY_PROBES[1], QUALITY_PROBES[3]]
    rows = []
    response_model = ""
    timeout = httpx.Timeout(QUALITY_PROBE_TIMEOUT, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True, trust_env=True) as client:
        for probe in probes:
            try:
                result = await native_probe(
                    client,
                    base_url,
                    api_key,
                    model,
                    probe["prompt"],
                    probe["max_tokens"],
                )
            except Exception as exc:
                result = quality_probe_exception_result(exc)
            if result.get("response_model") and not response_model:
                response_model = result.get("response_model")
            row = build_quality_row(probe, result, model)
            rows.append(row)
            if probe["name"] == "smoke" and quality_row_is_timeout(row):
                score, tags, ai_summary = finalize_quality_result(rows, response_model, model)
                return {
                    "status": "done",
                    "score": score,
                    "level": quality_level(score),
                    "response_model": response_model,
                    "requested_model": model,
                    "risk_tags": tags,
                    "ai_summary": ai_summary,
                    "rows": rows,
                }
            if probe["name"] == "smoke" and row["status"] == "fail":
                break

        score, tags, fallback_summary = finalize_quality_result(rows, response_model, model)
        passed_checks = quality_passed_checks_text(rows)
        summary_prompt = (
            "你是模型检测报告的总结员。请基于下面事实，用中文给用户总结本次模型实测结果。"
            "要求：2到3句话，90到170个汉字；先说明得分，再说明实际检测了什么，最后说明主要风险。"
            "通过项请优先使用这些名称：真实调用、数字判断、基础推理、严格JSON、代码指令跟随。"
            "不要自称，不要营销，不要写“建议继续进行小额测试，观察稳定性与成本表现”或类似小额测试、扣费建议：\n"
            + json.dumps(
                {
                    "score": score,
                    "passed_checks": passed_checks,
                    "risk_tags": tags,
                    "checks": [{k: row.get(k) for k in ("display_name", "status", "summary")} for row in rows],
                },
                ensure_ascii=False,
            )
        )
        ai_summary = fallback_summary
        try:
            summary_result = await native_probe(client, base_url, api_key, model, summary_prompt, 180)
            summary_text = clean_quality_summary_text(compact_text(summary_result.get("text") or "", 260))
            if summary_result.get("ok") and len(summary_text) >= 45:
                ai_summary = summary_text.strip(" \n`")
        except Exception:
            pass

    return {
        "status": "done",
        "score": score,
        "level": quality_level(score),
        "response_model": response_model,
        "requested_model": model,
        "risk_tags": tags,
        "ai_summary": ai_summary,
        "rows": rows,
    }


DETECTION_SUB_CHECK_LABELS = {
    "usage_present": "用量字段",
    "usage_arithmetic": "用量加法关系",
    "additive_usage": "输入/输出令牌加法",
    "length_delta": "长短文本令牌增量",
    "stream_usage": "流式用量统计",
    "stream_consistency": "流式/非流式统计一致性",
    "normal_usage": "普通用量对照",
    "token_range": "令牌范围",
    "output_tokens": "输出令牌",
    "input_tokens": "输入令牌",
    "stream_output_tokens": "流式输出令牌",
    "tool_call": "工具调用",
    "json_schema": "结构化输出规则",
}

DETECTION_VALUE_LABELS = {
    "critical": "致命",
    "major": "严重",
    "minor": "轻微",
    "low": "偏低",
    "high": "偏高",
    "ok": "正常",
    "pass": "通过",
    "passed": "通过",
    "fail": "未通过",
    "failed": "未通过",
    "skip": "跳过",
    "skipped": "跳过",
    "error": "错误",
    "missing-usage": "没有返回用量字段",
    "insufficient-token-usage": "返回的令牌用量信息不足，无法可靠判断",
    "usage_source_non_openai": "用量字段暴露了非 OpenAI 上游来源",
    "usage_mixed_token_fields": "Chat Completions 的用量字段混入了其它协议的 token 字段",
    "usage_input_tokens_invalid": "输入 token 字段不是有效的非负整数",
    "usage_output_tokens_invalid": "输出 token 字段不是有效的非负整数",
    "usage_total_tokens_invalid": "总 token 字段不是有效的非负整数",
    "usage_total_mismatch": "总 token 与输入/输出 token 加和不一致",
    "chat_completion_object_invalid": "响应对象类型不符合 Chat Completions 规范",
    "chat_completion_id_invalid": "响应 ID 不符合 Chat Completions 规范",
    "choices_missing": "响应缺少 choices 列表",
    "choice_not_object": "choices 中存在非对象项目",
    "message_missing": "响应缺少 message 字段",
    "message_role_invalid": "message.role 不符合协议",
    "finish_reason_invalid": "finish_reason 不在官方枚举范围内",
    "stream_event_invalid": "流式事件格式不符合协议",
    "stream_done_missing": "流式响应缺少结束标记",
}

DETECTION_TEXT_TRANSLATIONS = (
    ("OpenAI usage declares upstream source is", "OpenAI 用量字段声明上游来源为"),
    ("relay is impersonating OpenAI", "该中转站可能在伪装 OpenAI 协议"),
    ("OpenAI usage without explicit non-OpenAI source marker", "不应带有非 OpenAI 来源标记"),
    ("Chat Completions usage mixes OpenAI fields with input/output token fields", "Chat Completions 的用量字段混入了 input/output token 字段"),
    ("prompt_tokens/completion_tokens/total_tokens", "prompt_tokens / completion_tokens / total_tokens"),
    ("Each Responses API output item must be an object", "Responses API 的每个输出项都必须是对象"),
    ("Unknown Responses API output item type", "Responses API 输出项类型不在已知范围内"),
    ("Token usage is abnormal: stream usage is missing.", "令牌用量异常：流式响应没有返回用量统计。"),
    ("Token usage is abnormal", "令牌用量异常"),
    ("stream usage is missing", "流式响应没有返回用量统计"),
    ("stream usage", "流式用量统计"),
    ("missing usage", "没有返回用量统计"),
    ("missing-usage", "没有返回用量字段"),
    ("insufficient-token-usage", "返回的令牌用量信息不足，无法可靠判断"),
    ("bad id prefix", "响应 ID 前缀不符合协议"),
    ("id prefix", "ID 前缀"),
    ("prefix", "前缀"),
    ("usage field", "用量字段"),
    ("usage", "用量统计"),
    ("stream", "流式响应"),
    ("non-stream", "非流式响应"),
    ("tokens", "令牌"),
    ("token", "令牌"),
    ("expected", "期望"),
    ("actual", "实际"),
    ("field", "字段"),
    ("critical", "致命"),
    ("major", "严重"),
    ("minor", "轻微"),
    ("missing", "缺失"),
    ("failed", "未通过"),
    ("error", "错误"),
)


def translate_detection_text(text):
    value = str(text or "")
    if not value:
        return ""
    label = DETECTION_VALUE_LABELS.get(value.lower())
    if label:
        return label
    for source, target in DETECTION_TEXT_TRANSLATIONS:
        value = re.sub(re.escape(source), target, value, flags=re.I)
    return value


def short_detection_text(value, max_length=180):
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in ("message", "summary", "description", "reason", "error", "code"):
            text = short_detection_text(value.get(key), max_length)
            if text:
                return text
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    elif isinstance(value, (list, tuple)):
        parts = [short_detection_text(item, 60) for item in value[:4]]
        text = "、".join(part for part in parts if part)
    else:
        text = str(value)
    text = text.strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = translate_detection_text(text)
    return text[: max_length - 1] + "…" if len(text) > max_length else text


def detection_issue_text(issue):
    if isinstance(issue, str):
        return short_detection_text(issue)
    if not isinstance(issue, dict):
        return ""
    code = short_detection_text(issue.get("code"), 80)
    for key in ("message", "summary", "description", "reason", "error", "code"):
        text = short_detection_text(issue.get(key))
        if text:
            path = short_detection_text(issue.get("path") or issue.get("field"), 80)
            severity = short_detection_text(issue.get("severity"), 40)
            path = f"字段 {path}" if path else ""
            if code and key != "code" and code != text:
                text = f"{code}：{text}"
            expected = short_detection_text(issue.get("expected"), 80)
            actual = short_detection_text(issue.get("actual"), 80)
            if expected or actual:
                text += "（"
                text += "；".join(part for part in (f"期望 {expected}" if expected else "", f"实际 {actual}" if actual else "") if part)
                text += "）"
            prefix = " / ".join(part for part in (severity, path) if part)
            return f"{prefix}：{text}" if prefix else text
    parts = []
    key_labels = {"severity": "级别", "path": "字段", "field": "字段", "expected": "期望", "actual": "实际"}
    for key in ("severity", "path", "field", "expected", "actual"):
        text = short_detection_text(issue.get(key), 60)
        if text:
            parts.append(f"{key_labels.get(key, key)}={text}")
    return "，".join(parts[:4])


def detection_failed_sub_checks(details):
    sub_checks = details.get("sub_checks")
    if not isinstance(sub_checks, dict):
        return []
    failed = []
    for key, value in sub_checks.items():
        label = DETECTION_SUB_CHECK_LABELS.get(str(key), str(key).replace("_", " "))
        if isinstance(value, dict):
            passed = value.get("pass")
            if passed is True:
                continue
            reason = ""
            for reason_key in ("reason", "error", "message", "summary", "direction"):
                reason = short_detection_text(value.get(reason_key), 90)
                if reason:
                    break
            failed.append(f"{label}（{reason}）" if reason else label)
        elif value is False or value is None:
            failed.append(label)
    return failed


def detection_detail_summary(item):
    for key in ("summary", "message", "description"):
        text = short_detection_text(item.get(key))
        if text:
            return text

    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    for key in ("evaluation_zh", "evaluation", "summary", "reason", "skip_reason"):
        text = short_detection_text(details.get(key))
        if text:
            return text

    text = short_detection_text(item.get("error") or details.get("error"))
    if text:
        return text

    issues = details.get("issues")
    if isinstance(issues, list) and issues:
        issue_lines = [detection_issue_text(issue) for issue in issues[:2]]
        issue_lines = [line for line in issue_lines if line]
        if issue_lines:
            more = f"；另有 {len(issues) - 2} 项问题" if len(issues) > 2 else ""
            return "；".join(issue_lines) + more

    violations = details.get("violations")
    if isinstance(violations, list) and violations:
        return "发现规范差异：" + "、".join(short_detection_text(item, 60) for item in violations[:4] if item)

    failed_sub_checks = detection_failed_sub_checks(details)
    if failed_sub_checks:
        return "子检查未通过：" + "、".join(failed_sub_checks[:4])

    status = str(item.get("status") or "").lower()
    score = item.get("score")
    if status in {"fail", "failed", "error"}:
        score_text = short_detection_text(score, 40)
        return f"该检测项分数未达通过阈值（{score_text} 分）" if score_text else "该检测项未达到通过条件"
    return ""


def detection_number_from(source, keys, allow_zero=False):
    if not isinstance(source, dict):
        return None
    for key in keys:
        current = source
        for part in str(key).split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        try:
            number = float(current)
        except (TypeError, ValueError):
            continue
        if number > 0 or (allow_zero and number >= 0):
            return int(number) if number.is_integer() else number
    return None


def compact_detection_performance(performance):
    if not isinstance(performance, dict):
        return {}
    usage = performance.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    total_latency_ms = detection_number_from(
        performance,
        ("total_latency_ms", "total_ms", "duration_ms", "latency_ms", "elapsed_ms"),
        allow_zero=True,
    )
    ttft_ms = detection_number_from(
        performance,
        ("ttft_ms", "first_token_ms", "firstTokenMs", "time_to_first_token_ms"),
        allow_zero=True,
    )
    input_tokens = detection_number_from(
        {"usage": usage, **performance},
        ("usage.input_tokens", "usage.prompt_tokens", "input_tokens", "prompt_tokens"),
        allow_zero=True,
    )
    output_tokens = detection_number_from(
        {"usage": usage, **performance},
        ("usage.output_tokens", "usage.completion_tokens", "output_tokens", "completion_tokens"),
        allow_zero=True,
    )
    tokens_per_second = detection_number_from(
        performance,
        ("tokens_per_second", "tps", "throughput", "throughput_tps"),
        allow_zero=True,
    )
    if (tokens_per_second is None or tokens_per_second == 0) and output_tokens and total_latency_ms:
        tokens_per_second = output_tokens * 1000 / total_latency_ms

    normalized_usage = dict(usage)
    if input_tokens is not None:
        normalized_usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        normalized_usage["output_tokens"] = output_tokens

    normalized = dict(performance)
    normalized["usage"] = normalized_usage
    if total_latency_ms is not None:
        normalized["total_latency_ms"] = total_latency_ms
    if ttft_ms is not None:
        normalized["ttft_ms"] = ttft_ms
    if tokens_per_second is not None:
        normalized["tokens_per_second"] = tokens_per_second
    return normalized


def compact_detection_result(report):
    if not isinstance(report, dict):
        return None
    results = []
    for item in report.get("results") or []:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "name": item.get("name"),
                "display_name": item.get("display_name") or item.get("displayName"),
                "status": item.get("status"),
                "score": item.get("score"),
                "weight": item.get("weight"),
                "summary": detection_detail_summary(item),
                "severity": item.get("severity"),
                "duration_ms": item.get("duration_ms") or item.get("durationMs"),
                "details": item.get("details") if isinstance(item.get("details"), dict) else {},
                "error": item.get("error"),
            }
        )
    return {
        "protocol": report.get("protocol"),
        "tier": report.get("tier"),
        "tier_title": report.get("tier_title"),
        "base_url": report.get("base_url"),
        "target_model": report.get("target_model"),
        "mode": report.get("mode"),
        "timestamp": report.get("timestamp"),
        "total_score": report.get("total_score"),
        "verdict": report.get("verdict"),
        "summary": report.get("summary"),
        "run_error": report.get("run_error"),
        "performance": compact_detection_performance(report.get("performance")),
        "results": results,
    }


def pricing_models_from_json(parsed):
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if isinstance(data, dict):
        for key in ("models", "items", "data"):
            if isinstance(data.get(key), list):
                data = data.get(key)
                break
    if not isinstance(data, list):
        return []
    names = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("model_name") or item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


async def validate_submitted_site(origin):
    status_endpoint = await fetch_submit_endpoint(origin, "/api/status")
    if not status_endpoint.get("json_ok"):
        raise HTTPException(status_code=400, detail="暂不支持该类站点")
    version = new_api_version_header(status_endpoint)
    if not version:
        raise HTTPException(status_code=400, detail="暂不支持该类站点")
    if submitted_site_is_self_use(status_endpoint):
        raise HTTPException(status_code=400, detail="该站点为自用模式，暂不收录")

    pricing_endpoint = await fetch_submit_endpoint(origin, "/api/pricing")
    if not pricing_endpoint.get("ok"):
        raise HTTPException(status_code=400, detail="模型价格接口获取失败，暂未加入")
    model_names = pricing_models_from_json(pricing_endpoint.get("json") or {})
    if not model_names:
        raise HTTPException(status_code=400, detail="模型价格接口未解析到可用模型，暂未加入")

    warnings = []
    notice_endpoint = await fetch_submit_endpoint(origin, "/api/notice")
    notice_ok = bool(notice_endpoint.get("ok"))

    perf_ok = False
    perf_endpoint = await fetch_submit_endpoint(origin, "/api/perf-metrics/summary?hours=24")
    if perf_endpoint.get("ok"):
        perf_ok = True
    else:
        for model_name in model_names[:3]:
            perf_endpoint = await fetch_submit_endpoint(
                origin,
                f"/api/perf-metrics?model={quote(model_name, safe='')}&hours=24",
            )
            if perf_endpoint.get("ok"):
                perf_ok = True
                break
    if not perf_ok:
        warnings.append("性能接口获取失败")

    status_data = submit_status_data(status_endpoint)
    system_name = status_data.get("system_name") or status_data.get("passkey_display_name")
    return {
        "new_api_version": version,
        "system_name": system_name,
        "model_count": len(model_names),
        "notice_ok": notice_ok,
        "perf_ok": perf_ok,
        "warnings": warnings,
    }


def db_list_values(cur, sql, params=()):
    cur.execute(sql, params)
    return [{"value": row["value"], "count": row["count"]} for row in cur.fetchall() if row.get("value")]


def chat_proxy_target(base_url):
    raw = (base_url or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="请填写 API 地址")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="API 地址必须是 http/https 域名")
    host = parsed.hostname.strip().lower()
    if host in {"localhost"} or host.endswith(".localhost"):
        raise HTTPException(status_code=400, detail="不允许访问本机地址")
    try:
        ipaddress.ip_address(host)
        hosts = [host]
    except ValueError:
        try:
            hosts = [item[-1][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)]
        except socket.gaierror as exc:
            raise HTTPException(status_code=400, detail="API 域名解析失败") from exc
    for address in set(hosts):
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(status_code=400, detail="不允许访问内网或本机地址")
    normalized = raw.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def clean_chat_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="请先输入消息")
    cleaned = []
    for item in messages[-CHAT_PROXY_MAX_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"system", "user", "assistant"}:
            continue
        content = compact_text(item.get("content") or "", CHAT_PROXY_MAX_MESSAGE_CHARS)
        if content:
            cleaned.append({"role": role, "content": content})
    if not cleaned or cleaned[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="最后一条消息必须是用户输入")
    if not any(item["role"] == "system" for item in cleaned):
        cleaned.insert(0, {"role": "system", "content": CHAT_DEFAULT_SYSTEM_PROMPT})
    return cleaned


def chat_completion_text(payload):
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(part.get("text") or part.get("content") or "")
            return "".join(parts)
    output = payload.get("output") or []
    parts = []
    for item in output if isinstance(output, list) else []:
        for part in item.get("content") or []:
            if isinstance(part, dict):
                parts.append(part.get("text") or "")
    return "".join(parts)


def chat_stream_delta(payload):
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices") or []
    if choices:
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(part.get("text") or "" for part in content if isinstance(part, dict))
    if payload.get("type") in {"response.output_text.delta", "output_text.delta"}:
        return payload.get("delta") or ""
    return ""


def chat_error_message(text):
    raw = compact_text(text or "", 1200)
    try:
        payload = json.loads(raw)
    except Exception:
        return raw or "上游接口请求失败"
    if isinstance(payload, dict):
        for key in ("error", "detail", "message", "msg"):
            value = payload.get(key)
            if isinstance(value, dict):
                nested = value.get("message") or value.get("msg") or value.get("detail") or value.get("code")
                if nested:
                    return compact_text(str(nested), 600)
            if isinstance(value, str) and value:
                return compact_text(value, 600)
    return raw or "上游接口请求失败"


async def stream_chat_completion(target_url, headers, body):
    import httpx
    timeout = httpx.Timeout(CHAT_PROXY_TIMEOUT, connect=12.0)
    async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True, trust_env=True) as client:
        if body.get("stream"):
            async with client.stream("POST", target_url, headers=headers, json=body) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise HTTPException(status_code=response.status_code, detail=chat_error_message(detail.decode("utf-8", errors="ignore")))
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = line[5:].strip() if line.startswith("data:") else line.strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = chat_stream_delta(json.loads(data))
                    except Exception:
                        chunk = ""
                    if chunk:
                        yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
            return
        response = await client.post(target_url, headers=headers, json=body)
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=chat_error_message(response.text))
        yield f"data: {json.dumps({'text': chat_completion_text(response.json()) or response.text}, ensure_ascii=False)}\n\n"


async def fetch_chat_models(base_url, api_key):
    import httpx
    target_url = chat_proxy_target(base_url).rsplit("/chat/completions", 1)[0] + "/models"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0), verify=False, follow_redirects=True, trust_env=True) as client:
        response = await client.get(target_url, headers=headers)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=chat_error_message(response.text))
    data = response.json()
    models = []
    for item in data.get("data") or []:
        model_id = item.get("id") if isinstance(item, dict) else ""
        if model_id:
            models.append(str(model_id))
    return sorted(set(models), key=lambda value: value.lower())[:2000]


app = FastAPI(title="RelayWatch", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.on_event("startup")
def warm_db_model_cache():
    if not DB_ENABLED:
        return
    try:
        started = time.time()
        with db_connect() as conn:
            with conn.cursor() as cur:
                generation_id, data_version = db_active_state(cur)
                db_all_models(cur, generation_id, data_version)
        db_summary()
        db_filters()
        elapsed_ms = int((time.time() - started) * 1000)
        print(f"db_model_cache_warmed elapsed_ms={elapsed_ms}", flush=True)
    except Exception as exc:
        print(f"db_model_cache_warm_failed error={exc}", flush=True)
        return

    def warm_results():
        model_warmups = [
            {"q": "", "provider": "all", "sort": "usd", "min_success": None, "max_latency": None, "min_tps": None, "page": 1, "page_size": 24},
            {"q": "gpt", "provider": "all", "sort": "usd", "min_success": None, "max_latency": None, "min_tps": None, "page": 1, "page_size": 24},
            {"q": "claude", "provider": "all", "sort": "usd", "min_success": None, "max_latency": None, "min_tps": None, "page": 1, "page_size": 24},
            {"q": "deepseek", "provider": "all", "sort": "usd", "min_success": None, "max_latency": None, "min_tps": None, "page": 1, "page_size": 24},
            {"q": "gpt", "provider": "all", "sort": "usd", "min_success": 99, "max_latency": None, "min_tps": None, "page": 1, "page_size": 24},
            {"q": "claude", "provider": "all", "sort": "usd", "min_success": 99, "max_latency": None, "min_tps": None, "page": 1, "page_size": 24},
        ]
        site_warmups = [
            {"q": "", "status": "all", "provider": "all", "group": "all", "billing": "all", "tag": "all", "model": "", "sort": "random", "page": 1, "page_size": 24},
            {"q": "", "status": "all", "provider": "all", "group": "all", "billing": "all", "tag": "all", "model": "", "sort": "online", "page": 1, "page_size": 24},
            {"q": "", "status": "all", "provider": "all", "group": "all", "billing": "all", "tag": "all", "model": "", "sort": "price", "page": 1, "page_size": 24},
            {"q": "", "status": "all", "provider": "all", "group": "all", "billing": "all", "tag": "all", "model": "", "sort": "models", "page": 1, "page_size": 24},
        ]
        started = time.time()
        warmed_models = 0
        warmed_sites = 0
        for kwargs in model_warmups:
            try:
                db_models(**kwargs)
                warmed_models += 1
            except Exception as exc:
                print(f"db_model_result_warm_failed query={kwargs} error={exc}", flush=True)
        for kwargs in site_warmups:
            try:
                db_sites(**kwargs)
                warmed_sites += 1
            except Exception as exc:
                print(f"db_site_result_warm_failed query={kwargs} error={exc}", flush=True)
        try:
            load_official_status(force=True)
        except Exception as exc:
            print(f"official_status_warm_failed error={exc}", flush=True)
        elapsed_ms = int((time.time() - started) * 1000)
        print(f"db_result_cache_warmed models={warmed_models} sites={warmed_sites} elapsed_ms={elapsed_ms}", flush=True)

    threading.Thread(target=warm_results, daemon=True).start()


@app.middleware("http")
async def no_cache_api(request, call_next):
    # Never let the browser cache API responses (filters/models/sites). Stale
    # cached /api/filters otherwise shows dropdown options that no longer exist
    # in the live data (e.g. a "Hunyuan" entry that returns 0 results).
    started = time.time()
    response = await call_next(request)
    path = request.url.path
    elapsed_ms = int((time.time() - started) * 1000)
    response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
    if path.startswith("/api/") and elapsed_ms >= 1000:
        print(f"slow_api path={path} elapsed_ms={elapsed_ms}", flush=True)
    if path.startswith("/api/") or path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

if DB_ENABLED:
    RAW_MODELS = []
    ANNOUNCEMENTS = []
    SUMMARY = {}
else:
    RAW_MODELS = read_json("models.json")
    ANNOUNCEMENTS = read_json("announcements.json")
    SUMMARY = read_json("summary.json")

TOKEN_PRICE_MULTIPLIER = 14
REQUEST_PRICE_MULTIPLIER = 7
MODEL_SITE_PREVIEW_LIMIT = 10
MODEL_FAMILY_ORDER = {
    "gpt": 0,
    "claude": 1,
    "deepseek": 2,
    "gemini": 3,
    "qwen": 4,
    "doubao": 5,
    "grok": 6,
    "glm": 7,
    "kimi": 8,
}
MODEL_VARIANT_ORDER = {
    "opus": 0,
    "sonnet": 1,
    "pro": 2,
    "plus": 3,
    "flash": 4,
    "haiku": 5,
    "mini": 6,
    "lite": 7,
}

MODEL_FAMILY_MARKERS = {
    "gpt": {"gpt", "chatgpt"},
    "claude": {"claude", "opus", "sonnet", "haiku", "fable"},
    "deepseek": {"deepseek"},
    "gemini": {"gemini"},
    "qwen": {"qwen", "qwq"},
    "doubao": {"doubao", "seedream", "seedance"},
    "grok": {"grok", "xai"},
    "glm": {"glm"},
    "kimi": {"kimi", "moonshot"},
}

# A model's provider is the vendor COMPANY, keyed off model-name markers
# (Kimi -> Moonshot, GLM -> 智谱, Grok -> xAI, ...).
MODEL_PROVIDER_MARKERS = [
    ("Anthropic", {"claude", "opus", "sonnet", "haiku", "fable"}),
    ("DeepSeek", {"deepseek"}),
    ("Google", {"gemini", "gemma", "imagen", "veo", "banana", "aqa"}),
    ("阿里巴巴", {"qwen", "qwq", "tongyi", "wan", "wanx", "qvq", "gte"}),
    ("字节跳动", {"doubao", "seed", "seedance", "seedream", "jimeng"}),
    ("xAI", {"grok", "xai"}),
    ("智谱", {"glm", "chatglm", "cogview", "cogvideox", "cogvideo"}),
    ("Moonshot", {"kimi", "moonshot"}),
    ("MiniMax", {"minimax", "abab", "hailuo"}),
    ("Meta", {"llama", "codellama"}),
    ("百度", {"ernie", "wenxin"}),
    ("腾讯", {"hunyuan"}),
    ("Mistral", {"mistral", "mixtral", "codestral", "magistral", "ministral", "devstral", "pixtral"}),
    ("Cohere", {"cohere"}),
    ("Nvidia", {"nemotron"}),
    ("小米", {"mimo"}),
    ("百川智能", {"baichuan"}),
    ("阶跃星辰", {"stepfun", "step"}),
    ("美团", {"longcat"}),
    ("商汤", {"sensenova"}),
    ("BAAI", {"bge"}),
    ("Microsoft", {"phi"}),
    ("Midjourney", {"mj", "midjourney", "niji"}),
    ("Agnes", {"agnes"}),
    ("OpenAI", {"gpt", "chatgpt", "o1", "o3", "o4", "codex", "openai",
                "sora", "whisper", "dalle", "dall", "davinci", "babbage", "curie"}),
]

# Normalization for the raw site-supplied vendor label (used only when the model
# name itself isn't recognizable): collapse the many aliases a company appears
# under into one canonical name. Keys are matched case-insensitively.
PROVIDER_LABEL_ALIASES = {
    # Google
    "gemini": "Google", "谷歌": "Google", "google gemini": "Google",
    "google / gemini": "Google", "google veo": "Google", "veo": "Google",
    "google ai studio": "Google", "gemma": "Google",
    # Alibaba
    "qwen": "阿里巴巴", "通义千问": "阿里巴巴", "通义": "阿里巴巴", "tongyi": "阿里巴巴",
    "alibaba": "阿里巴巴", "阿里云": "阿里巴巴", "阿里云百炼": "阿里巴巴",
    "aliyun-bailian": "阿里巴巴", "aliyun（wan）": "阿里巴巴", "dashscope": "阿里巴巴",
    "wan": "阿里巴巴", "bailian": "阿里巴巴", "阿里": "阿里巴巴",
    # ByteDance
    "bytedance": "字节跳动", "doubao": "字节跳动", "豆包": "字节跳动", "即梦": "字节跳动",
    "volcano": "字节跳动", "volcengine": "字节跳动", "火山": "字节跳动", "火山方舟": "字节跳动",
    "seedance": "字节跳动", "seedream": "字节跳动",
    # Zhipu
    "zhipu": "智谱", "zhipu ai": "智谱", "智谱ai": "智谱", "chatglm": "智谱",
    "glm": "智谱", "z.ai": "智谱", "智谱(zhipu)": "智谱",
    # Moonshot
    "kimi": "Moonshot", "moonshot ai": "Moonshot", "moonshotai": "Moonshot",
    "月之暗面": "Moonshot", "moonshot kimi": "Moonshot",
    # xAI
    "grok": "xAI", "x ai": "xAI", "xai": "xAI", "grok (xai)": "xAI",
    # MiniMax
    "minimax": "MiniMax", "minimaxai": "MiniMax", "海螺": "MiniMax", "hailuo": "MiniMax",
    "minimax ai": "MiniMax",
    # Baidu
    "baidu": "百度", "文心": "百度", "文心一言": "百度", "ernie": "百度", "wenxin": "百度",
    # Tencent
    "tencent": "腾讯", "混元": "腾讯", "hunyuan": "腾讯", "腾讯混元": "腾讯",
    # Meta
    "llama": "Meta", "meta llama": "Meta",
    # Kuaishou
    "kuaishou": "快手", "可灵": "快手", "kling": "快手", "快手可灵": "快手",
    "kling/可灵": "快手", "kling(可灵)": "快手",
    # Xiaomi
    "xiaomi": "小米", "xiaomi mimo": "小米", "xiaomimimo": "小米", "mimo": "小米",
    "xiaomi mimo ": "小米",
    # Misc company-name variants
    "微软": "Microsoft", "microsoft": "Microsoft", "nvidia": "Nvidia",
    "百川": "百川智能", "nanobanana": "Google", "nano banana": "Google",
    "sensenova": "商汤", "商汤科技": "商汤", "longcat": "美团", "美团龙猫": "美团",
    "z-image": "阿里巴巴", "z image": "阿里巴巴", "agnesai": "Agnes", "agnes ai": "Agnes",
}

MODEL_PROVIDER_NOISE = {
    "ai",
    "api",
    "official",
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "models",
    "model",
    "bytedance",
    "alibaba",
    "qwen",
}

MODEL_ROUTE_NOISE = {
    "default",
    "free",
    "stable",
    "fast",
    "high",
    "low",
    "medium",
    "xhigh",
    "max",
    "plus",
    "std",
    "standard",
    "special",
    "vip",
    "svip",
    "premium",
    "none",
    "pool",
}

GPT_VARIANTS = {
    "audio",
    "chat",
    "codex",
    "compact",
    "image",
    "mini",
    "nano",
    "omni",
    "preview",
    "pro",
    "realtime",
    "search",
    "spark",
    "turbo",
    "vision",
}

CLAUDE_VARIANTS = {"opus", "sonnet", "haiku", "fable"}
CLAUDE_CAPABILITY_SUFFIXES = {"thinking"}
# Keep lists per family: only the family prefix, version numbers, product tiers
# and distinct modalities (image / video / audio / vision / tts) survive in the
# canonical key. Pure mode words (thinking / search / reasoning / fast / ...),
# routing prefixes, single-letter codes, dates and region tags are dropped, so
# they collapse into the base model.
DEEPSEEK_VARIANTS = {"chat", "flash", "ocr", "pro", "r", "reasoner", "v", "vl", "vision", "image"}
GEMINI_VARIANTS = {"flash", "image", "imagen", "lite", "preview", "pro", "tts", "video", "vision", "audio", "edit"}
QWEN_VARIANTS = {"coder", "flash", "image", "long", "max", "plus", "turbo", "vl", "video", "vision", "audio", "edit", "tts", "omni"}
KIMI_VARIANTS = {"code", "coder", "instruct", "chat", "turbo", "flash", "vision", "vl"}
GLM_VARIANTS = {"flash", "flashx", "air", "airx", "plus", "pro", "turbo", "long", "vision", "image", "video", "edge", "v", "z", "nano", "mini", "lite"}
GROK_VARIANTS = {"imagine", "image", "video", "vision", "mini", "code", "fast", "heavy"}
DOUBAO_VARIANTS = {"seed", "seedream", "seedance", "pro", "lite", "vision", "character", "flash", "image", "video", "code", "ui", "translation"}
SIZE_UNITS = {"b", "k", "m"}

SECOND_LEVEL_SUFFIXES = {"ac", "co", "com", "edu", "gov", "net", "org"}
COUNTRY_SUFFIXES = {"au", "br", "cn", "hk", "in", "jp", "kr", "nz", "sg", "tw", "uk", "za"}

# Trailing country/region codes some relays append to the same model
# (gpt-5.5-pro-US / -FR / -DE / -HK ...). Dropped from the canonical key so
# region variants collapse into one row.
GPT_REGION_SUFFIXES = {
    "us", "uk", "gb", "eu", "fr", "de", "da", "it", "es", "nl", "se", "ch",
    "ru", "tr", "sa", "ae", "za", "hk", "tw", "cn", "jp", "kr", "sg", "au",
    "nz", "ca", "br", "mx", "in",
}

# Family -> canonical provider display name, used as a fallback when the model
# name carries no explicit provider marker (e.g. the "g5.5" GPT abbreviation).
# Map each model family to its vendor COMPANY (not the model/brand name), using
# the dominant labels already present in the data, so a model's provider lines
# up with how the company is listed (Kimi -> Moonshot, GLM -> 智谱, ...).
FAMILY_PROVIDER_NAME = {
    "gpt": "OpenAI",
    "claude": "Anthropic",
    "deepseek": "DeepSeek",
    "gemini": "Google",
    "qwen": "阿里巴巴",
    "doubao": "字节跳动",
    "grok": "xAI",
    "glm": "智谱",
    "kimi": "Moonshot",
}


def host_from_origin(origin):
    return urlparse(origin or "").netloc.split("@")[-1].split(":")[0].lower().strip(".")


def canonical_domain(host):
    host = (host or "").lower().strip(".")
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return host
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL_SUFFIXES and parts[-1] in COUNTRY_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def representative_score(site):
    host = host_from_origin(site.get("origin", ""))
    root = canonical_domain(host)
    scheme = urlparse(site.get("origin", "")).scheme
    if host == root:
        host_rank = 0
    elif host == f"www.{root}":
        host_rank = 1
    elif host == f"api.{root}":
        host_rank = 2
    else:
        host_rank = 3
    return (
        {"online": 0, "partial": 1, "unknown": 2}.get(site.get("status"), 3),
        host_rank,
        0 if scheme == "https" else 1,
        -(site.get("model_count") or 0),
        0 if site.get("notice") else 1,
        len(host),
        site.get("origin", ""),
    )


def dedupe_sites(sites):
    grouped = {}
    for site in sites:
        host = host_from_origin(site.get("origin", ""))
        root = canonical_domain(host)
        site = dict(site)
        site["root_domain"] = root
        grouped.setdefault(root, []).append(site)
    selected = [sorted(items, key=representative_score)[0] for items in grouped.values()]
    selected.sort(
        key=lambda item: (
            item.get("status") != "online",
            item.get("lowest_ratio") is None,
            item.get("lowest_ratio") or 999999,
            item.get("root_domain", ""),
        )
    )
    return selected


SITES = [] if DB_ENABLED or MODELS_ONLY else dedupe_sites(read_json("sites.json"))
SITE_BY_ID = {site["id"]: site for site in SITES}


def current_site_random_seed(extra=""):
    if DB_ENABLED:
        return str(extra or "")
    generated_at = (SUMMARY or {}).get("generated_at") if isinstance(SUMMARY, dict) else ""
    return f"{generated_at or JSON_DATA_SIGNATURE or 'static'}:{extra or ''}"


def stable_site_random_key(site, seed=""):
    identity = site.get("id") or site.get("origin") or site.get("domain") or site.get("name") or ""
    return hashlib.sha1(f"{seed}|{identity}".encode("utf-8", errors="ignore")).hexdigest()


def paginate(items, page, page_size):
    total = len(items)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    return {
        "items": items[start:end],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


def billing_type_for_model(model):
    quota_type = model.get("quota_type")
    if quota_type == 0:
        return "按量计费"
    if quota_type == 1:
        return "按次计费"
    return "未知类型"


def filtered_site_models(site, provider="all", group="all", billing="all", model_query=""):
    model_query = (model_query or "").lower().strip()
    filtered = []
    for model in site.get("models", []) or []:
        if provider != "all" and model.get("provider") != provider:
            continue
        if group != "all" and group not in (model.get("groups") or []):
            continue
        if billing != "all" and billing_type_for_model(model) != billing:
            continue
        if model_query and model_query not in (model.get("model") or "").lower():
            continue
        filtered.append(model)
    return filtered


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


def ordered_group_ratios(group_ratios):
    if not isinstance(group_ratios, dict):
        return group_ratios
    entries = []
    for group, value in group_ratios.items():
        numeric = numeric_value(value)
        entries.append((numeric is None, numeric if numeric is not None else 999999999, str(group), value))
    entries.sort(key=lambda item: (item[0], item[1], item[2]))
    return {group: value for _missing, _numeric, group, value in entries}


def numeric_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def multiplied_price(base, multiplier):
    base = numeric_value(base)
    multiplier = numeric_value(multiplier)
    if base is None or multiplier is None:
        return None
    return base * multiplier


def input_price_value(model):
    multiplier = numeric_value(model.get("token_price_multiplier"))
    if multiplier is None:
        multiplier = TOKEN_PRICE_MULTIPLIER
    return multiplied_price(effective_model_ratio(model), multiplier)


def request_price_value(model):
    price = numeric_value(model.get("model_price"))
    if price is None or price <= 0:
        return None
    multiplier = numeric_value(model.get("request_price_multiplier"))
    if multiplier is None:
        multiplier = REQUEST_PRICE_MULTIPLIER
    return multiplied_price(multiplied_price(price, display_group_ratio(model) or 1), multiplier)


def site_billing_bucket(site):
    # Each site belongs to exactly one billing bucket; the sort dropdown
    # (usd / cny / request) is really a per-bucket view.
    if request_price_value(site) is not None:
        return "request"          # 按次计费
    if site.get("currency_symbol") == "$":
        return "usd"              # 美元 / 1M tokens
    return "cny"                  # 人民币 (含自定义符号) / 1M tokens


def site_sort_price(site, sort):
    if site_billing_bucket(site) != sort:
        return None
    if sort == "request":
        return request_price_value(site)
    return input_price_value(site)


def model_sort_price(model, sort):
    prices = [
        site_sort_price(site, sort)
        for site in model.get("sites", []) or []
    ]
    prices = [price for price in prices if isinstance(price, (int, float)) and price >= 0]
    return min(prices) if prices else None


def model_release_sort_key(model_name):
    raw_name = (model_name or "").lower()
    canonical = canonical_model_key(model_name)
    name = (canonical or raw_name).lower()
    family_matches = [
        (name.find(marker), rank, marker)
        for marker, rank in MODEL_FAMILY_ORDER.items()
        if marker in name
    ]
    if family_matches:
        family_index, family_rank, _marker = min(family_matches, key=lambda item: (item[0], item[1]))
    else:
        family_index, family_rank, _marker = 0, 99, ""

    comparable_name = name[family_index:]
    variant_rank = min(
        (rank for marker, rank in MODEL_VARIANT_ORDER.items() if marker in comparable_name),
        default=99,
    )
    if variant_rank == 99 and _marker:
        # A clean "family + version" flagship (no tier word AND no other letters
        # left, e.g. gpt-5.5) ranks with the top tier, while names carrying an
        # unknown word (claude-fable-5, gpt-oss-120b) keep the lowest rank.
        remainder = comparable_name[len(_marker):]
        if not re.search(r"[a-z]", remainder):
            variant_rank = 0

    version_numbers = []
    date_numbers = []
    date_mode = False
    for part in re.findall(r"\d+", comparable_name):
        number = int(part)
        if date_mode or (len(part) >= 4 and number >= 1900):
            date_numbers.append(number)
            date_mode = True
        else:
            version_numbers.append(number)

    version_numbers = version_numbers[:8]
    date_numbers = date_numbers[:2]
    version_rank = tuple([-part for part in version_numbers] + [0] * (8 - len(version_numbers)))
    date_rank = tuple([-part for part in date_numbers] + [0] * (2 - len(date_numbers)))
    return (family_rank, variant_rank, version_rank, date_rank, name, raw_name)


def model_tokens(model_name):
    name = (model_name or "").lower().strip()
    family_positions = [
        name.find(marker)
        for markers in MODEL_FAMILY_MARKERS.values()
        for marker in markers
        if name.find(marker) >= 0
    ]
    if family_positions:
        name = name[min(family_positions):]
    name = re.sub(r"[\[\(（【][^\]\)）】]*[\]\)）】]", " ", name)
    name = re.sub(r"[/_:]+", "-", name)
    return re.findall(r"[a-z]+|\d+", name)


def looks_like_gpt_abbreviation(tokens):
    # Some relays abbreviate GPT models as "g5.5" / "g-5.4" / "G3.1": a leading
    # standalone "g" immediately followed by a version number. Only fires when
    # no other family marker matched (the caller checks markers first), so names
    # like "cursor-g-5.5", "XAIO-O-G5-4" or "nano-banana_G" are not affected.
    return len(tokens) >= 2 and tokens[0] == "g" and tokens[1].isdigit()


def detect_model_family(tokens):
    for family, markers in MODEL_FAMILY_MARKERS.items():
        if any(token in markers for token in tokens):
            return family
    if looks_like_gpt_abbreviation(tokens):
        return "gpt"
    return ""


def effective_provider_for_model_name(model_name):
    tokens = model_tokens(model_name)
    token_set = set(tokens)
    for provider, markers in MODEL_PROVIDER_MARKERS:
        if token_set.intersection(markers):
            return provider
    family = detect_model_family(tokens)
    if family in FAMILY_PROVIDER_NAME:
        return FAMILY_PROVIDER_NAME[family]
    return "Other"


def resolved_provider_name(provider_name, model_name=""):
    # Prefer the canonical provider inferred from the model name itself so the
    # same model is not split across noisy relay/aggregator labels (e.g. a
    # claude model tagged "Venice AI" / "OpenRouter" / "Claude" all collapse to
    # "Anthropic"). Fall back to the site-supplied label only when the model
    # name is not recognizable.
    inferred = effective_provider_for_model_name(model_name)
    if inferred != "Other":
        return inferred
    provider = (provider_name or "").strip()
    if provider:
        return PROVIDER_LABEL_ALIASES.get(provider.lower(), provider)
    return "Other"


def is_date_token(token):
    if not token.isdigit():
        return False
    if len(token) == 8 and token.startswith("20"):
        return True
    if len(token) == 6 and token.startswith(("24", "25", "26", "27")):
        return True
    if len(token) == 6 and token.startswith("20") and "01" <= token[4:6] <= "12":
        # YYYYMM release stamp, e.g. 202605 / 202606.
        return True
    if len(token) == 4 and token.startswith("20"):
        return True
    return False


def date_skip_count(tokens, index):
    token = tokens[index] if index < len(tokens) else ""
    next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
    third_token = tokens[index + 2] if index + 2 < len(tokens) else ""
    if token.isdigit() and len(token) == 4 and token.startswith("20"):
        if next_token.isdigit() and third_token.isdigit() and len(next_token) <= 2 and len(third_token) <= 2:
            return 3
        return 1
    if token.isdigit() and len(token) <= 2 and next_token.isdigit() and len(next_token) == 4 and next_token.startswith("20"):
        return 2
    if is_date_token(token):
        return 1
    return 0


def append_size_token(parts, token, next_token=None):
    if token.isdigit() and next_token in SIZE_UNITS:
        parts.append(f"{token}{next_token}")
        return True
    return False


def append_compound_size_token(parts, tokens, index):
    token = tokens[index] if index < len(tokens) else ""
    next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
    third_token = tokens[index + 2] if index + 2 < len(tokens) else ""
    if len(token) == 1 and token.isalpha() and next_token.isdigit() and third_token in SIZE_UNITS:
        parts.append(f"{token}{next_token}{third_token}")
        return 3
    return 0


def compact_parts(parts):
    cleaned = []
    for part in parts:
        if not part or part in MODEL_PROVIDER_NOISE:
            continue
        if cleaned and cleaned[-1] == part:
            continue
        cleaned.append(part)
    return cleaned


def canonical_claude_key(tokens):
    variant = next((token for token in tokens if token in CLAUDE_VARIANTS), "")
    version = []
    seen_date = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        skip = date_skip_count(tokens, index)
        if skip:
            seen_date = True
            index += skip
            continue
        if token in {"claude"} or token == variant or token in MODEL_PROVIDER_NOISE:
            index += 1
            continue
        if token.isdigit():
            if seen_date:
                index += 1
                continue
            if next_token in SIZE_UNITS:
                index += 2
                continue
            if not version and len(token) == 2 and token.startswith(("3", "4")):
                version.extend([token[0], token[1]])
            elif len(version) < 2:
                version.append(token)
            index += 1
            continue
        index += 1
    if variant and version:
        parts = ["claude", variant, ".".join(version[:2])]
        return "-".join(parts)
    return ""


def canonical_gpt_key(tokens):
    parts = ["gpt"]
    suffix = []
    numbers = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if token in {"gpt", "chatgpt"} or token in MODEL_PROVIDER_NOISE:
            index += 1
            continue
        if index == 0 and token == "g" and next_token and next_token.isdigit():
            # Leading "g5.5" abbreviation -> treat the "g" as the GPT prefix.
            index += 1
            continue
        if token in GPT_REGION_SUFFIXES:
            # Drop trailing country/region codes (gpt-5.5-pro-US -> gpt-5.5-pro).
            index += 1
            continue
        if token == "latest":
            index += 1
            continue
        skip = append_compound_size_token(suffix, tokens, index)
        if skip:
            index += skip
            continue
        if append_size_token(suffix, token, next_token):
            index += 2
            continue
        if token.isdigit() and next_token == "x" and numbers:
            index += 2
            continue
        if token.isdigit() and len(token) >= 4 and is_date_token(token):
            index += date_skip_count(tokens, index) or 1
            continue
        if token.isdigit() and len(numbers) < 2:
            if next_token == "o" and not numbers:
                numbers.append(f"{token}o")
                index += 2
                continue
            numbers.append(token)
            index += 1
            continue
        skip = date_skip_count(tokens, index)
        if skip:
            index += skip
            continue
        # Keep only recognized GPT variants (pro / mini / codex / turbo / image
        # / compact / ...). Every other trailing token is a relay-specific
        # routing/vanity label (gpt-5.5-a, -cyber, -newapi, -fd, ...) and is
        # dropped so those collapse into the base model, mirroring how the
        # Claude key is built from known components only.
        if token in GPT_VARIANTS:
            suffix.append(token)
        index += 1
    if numbers:
        parts.append(".".join(numbers))
    parts.extend(compact_parts(suffix))
    return "-".join(parts) if len(parts) > 1 else ""


def canonical_deepseek_key(tokens):
    parts = ["deepseek"]
    suffix = []
    numbers = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"deepseek", "ai"} or token in MODEL_PROVIDER_NOISE:
            index += 1
            continue
        if token.isdigit() and suffix[:1] == ["r"] and numbers and len(token) in {4, 6}:
            suffix.append(token[-4:])
            index += 1
            continue
        skip = date_skip_count(tokens, index)
        if skip:
            index += skip
            continue
        if token.isdigit() and suffix and not (suffix[:1] in (["v"], ["r"]) and len(numbers) < 2):
            index += 1
            continue
        if token.isdigit() and len(numbers) < 2:
            numbers.append(token)
        elif token in DEEPSEEK_VARIANTS:
            suffix.append(token)
        index += 1
    if suffix and suffix[0] in {"v", "r"} and numbers:
        parts.append(f"{suffix.pop(0)}{'.'.join(numbers)}")
    elif numbers:
        parts.append(".".join(numbers))
    parts.extend(compact_parts(suffix))
    return "-".join(parts) if len(parts) > 1 else ""


def canonical_gemini_key(tokens):
    parts = ["gemini"]
    suffix = []
    numbers = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "gemini" or token in MODEL_PROVIDER_NOISE:
            index += 1
            continue
        if token == "latest":
            index += 1
            continue
        if token.isdigit() and len(token) >= 4 and is_date_token(token):
            index += date_skip_count(tokens, index) or 1
            continue
        if token.isdigit() and len(numbers) < 2 and not (suffix and numbers):
            numbers.append(token)
            index += 1
            continue
        skip = date_skip_count(tokens, index)
        if skip:
            index += skip
            continue
        if token in GEMINI_VARIANTS:
            suffix.append(token)
        index += 1
    if numbers:
        parts.append(".".join(numbers))
    parts.extend(compact_parts(suffix))
    return "-".join(parts) if len(parts) > 1 else ""


def canonical_qwen_key(tokens):
    parts = ["qwen"]
    suffix = []
    numbers = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if token in {"qwen", "qwq"} or token in MODEL_PROVIDER_NOISE:
            index += 1
            continue
        skip = append_compound_size_token(suffix, tokens, index)
        if skip:
            index += skip
            continue
        if append_size_token(suffix, token, next_token):
            index += 2
            continue
        if token.isdigit() and len(token) >= 4 and is_date_token(token):
            index += date_skip_count(tokens, index) or 1
            continue
        if token.isdigit() and len(numbers) < 2 and not (suffix and numbers):
            numbers.append(token)
            index += 1
            continue
        skip = date_skip_count(tokens, index)
        if skip:
            index += skip
            continue
        if token in QWEN_VARIANTS:
            suffix.append(token)
        index += 1
    if numbers:
        parts.append(".".join(numbers))
    parts.extend(compact_parts(suffix))
    return "-".join(parts) if len(parts) > 1 else ""


def _generic_family_key(family, tokens, variants):
    # Shared builder for families without bespoke parsing (kimi / glm / grok /
    # doubao). Keeps the family prefix, up to three version numbers and any
    # recognized variant; every other token (mode words, single-letter codes,
    # dates, routing leftovers) is dropped so variants collapse into the base.
    parts = [family]
    numbers = []
    suffix = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        # Drop the family word itself and generic provider noise. Variant
        # markers (e.g. doubao's seedream/seedance) are intentionally NOT
        # skipped here so they survive into the key.
        if token == family or token in MODEL_PROVIDER_NOISE:
            index += 1
            continue
        if token in GPT_REGION_SUFFIXES:
            index += 1
            continue
        if append_size_token(suffix, token, next_token):
            index += 2
            continue
        if token.isdigit() and len(token) >= 4 and is_date_token(token):
            index += date_skip_count(tokens, index) or 1
            continue
        skip = date_skip_count(tokens, index)
        if skip:
            index += skip
            continue
        if token.isdigit() and len(numbers) < 3 and not (suffix and numbers):
            numbers.append(token)
            index += 1
            continue
        if token in variants:
            suffix.append(token)
        index += 1
    if numbers:
        parts.append(".".join(numbers))
    parts.extend(compact_parts(suffix))
    return "-".join(parts) if len(parts) > 1 else ""


def canonical_kimi_key(tokens):
    return _generic_family_key("kimi", tokens, KIMI_VARIANTS)


def canonical_glm_key(tokens):
    return _generic_family_key("glm", tokens, GLM_VARIANTS)


def canonical_grok_key(tokens):
    return _generic_family_key("grok", tokens, GROK_VARIANTS)


def canonical_doubao_key(tokens):
    return _generic_family_key("doubao", tokens, DOUBAO_VARIANTS)


def canonical_model_key(model_name):
    tokens = model_tokens(model_name)
    family = detect_model_family(tokens)
    if family == "claude":
        key = canonical_claude_key(tokens)
    elif family == "gpt":
        key = canonical_gpt_key(tokens)
    elif family == "deepseek":
        key = canonical_deepseek_key(tokens)
    elif family == "gemini":
        key = canonical_gemini_key(tokens)
    elif family == "qwen":
        key = canonical_qwen_key(tokens)
    elif family == "kimi":
        key = canonical_kimi_key(tokens)
    elif family == "glm":
        key = canonical_glm_key(tokens)
    elif family == "grok":
        key = canonical_grok_key(tokens)
    elif family == "doubao":
        key = canonical_doubao_key(tokens)
    else:
        key = ""
    if key:
        return key

    name = (model_name or "").lower().strip()
    name = re.sub(r"[\[\(（【][^\]\)）】]*[\]\)）】]", " ", name)
    name = re.sub(r"\s+", "", name)
    name = re.sub(r"(?<=\d)[._-](?=\d{1,2}(?:\D|$))", ".", name)
    name = re.sub(r"[_/:]+", "-", name)
    return name


def model_display_name_score(model_name):
    name = model_name or ""
    lowered = name.lower()
    tokens = model_tokens(name)
    family = detect_model_family(tokens)
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    canonical = canonical_model_key(name)
    family_missing = family == "claude" and "claude" not in tokens
    family_prefix_missing = family and not normalized.startswith(family)
    noisy_annotation = bool(re.search(r"[\[\(（【]", name))
    non_ascii = bool(re.search(r"[^\x00-\x7f]", name))
    return (
        noisy_annotation,
        family_missing,
        family_prefix_missing,
        non_ascii,
        "/" in name,
        0 if canonical and normalized.replace("-", ".").startswith(canonical.replace("-", ".")) else 1,
        0 if not re.search(r"\s", name) else 1,
        0 if re.search(r"\d+\.\d+", name) else 1,
        len(name),
        lowered,
    )


def site_entry_score(site):
    prices = [
        site_sort_price(site, sort)
        for sort in ("usd", "cny", "request")
    ]
    prices = [price for price in prices if price is not None]
    return (
        not prices,
        min(prices) if prices else 999999999,
        site.get("site_name", ""),
    )


def has_group_ambiguous_perf(site):
    ratios = []
    for value in (site.get("group_ratios") or {}).values():
        numeric = numeric_value(value)
        if numeric is not None:
            ratios.append(round(numeric, 12))
    # /api/perf-metrics/summary reports model-level metrics. When the same
    # model has several differently-priced groups, those metrics cannot be
    # truthfully attached to the cheapest group shown in the price row.
    return len(set(ratios)) > 1


def selected_pricing_group(site):
    entries = []
    for group, value in (site.get("group_ratios") or {}).items():
        if not group:
            continue
        numeric = numeric_value(value)
        entries.append((numeric is None, numeric if numeric is not None else 999999999, str(group)))
    if not entries:
        return None
    return sorted(entries)[0][2]


def selected_group_perf(site):
    group = selected_pricing_group(site)
    if not group:
        return None
    perf = (site.get("group_perf") or {}).get(group)
    return perf if isinstance(perf, dict) else None


def site_perf_value(site, key):
    if site.get("perf_group"):
        return site.get(key)
    perf = selected_group_perf(site)
    if perf is not None:
        return perf.get(key)
    if has_group_ambiguous_perf(site):
        return None
    return site.get(key)


def apply_selected_group_perf(site):
    if site.get("perf_group"):
        item = dict(site)
        item["group_ratios"] = ordered_group_ratios(item.get("group_ratios"))
        return item
    perf = selected_group_perf(site)
    if perf is not None:
        item = dict(site)
        item["group_ratios"] = ordered_group_ratios(item.get("group_ratios"))
        item["success_rate"] = perf.get("success_rate")
        item["avg_latency_ms"] = perf.get("avg_latency_ms")
        item["avg_tps"] = perf.get("avg_tps")
        item["avg_ttft_ms"] = perf.get("avg_ttft_ms")
        item["perf_group"] = selected_pricing_group(site)
        return item
    if not has_group_ambiguous_perf(site):
        item = dict(site)
        item["group_ratios"] = ordered_group_ratios(item.get("group_ratios"))
        return item
    item = dict(site)
    item["group_ratios"] = ordered_group_ratios(item.get("group_ratios"))
    item["success_rate"] = None
    item["avg_latency_ms"] = None
    item["avg_tps"] = None
    item["avg_ttft_ms"] = None
    item["perf_ambiguous"] = True
    return item


# Bounds for discarding garbage perf readings when picking a model's best site.
PERF_MIN_LATENCY_MS = 500    # a real full-response latency is never < 0.5s
PERF_MAX_TPS = 1000          # > 1000 tok/s is not a real text-generation rate


def merge_equivalent_models(models):
    grouped = {}
    for model in models:
        model_name = model.get("model", "")
        canonical = canonical_model_key(model_name) or (model_name or "").lower().strip()
        for site in model.get("sites", []) or []:
            provider = resolved_provider_name(site.get("provider") or model.get("provider"), model_name)
            key = (provider.lower(), canonical)
            if key not in grouped:
                grouped[key] = {
                    "model": model_name,
                    "provider": provider,
                    "aliases": set(),
                    "sites_by_key": {},
                }
            current = grouped[key]
            if model_name:
                current["aliases"].add(model_name)
                if model_display_name_score(model_name) < model_display_name_score(current["model"]):
                    current["model"] = model_name

            site_copy = dict(site)
            site_copy["provider"] = provider
            site_key = site_copy.get("site_id") or site_copy.get("origin") or f"{site_copy.get('site_name')}:{site_copy.get('model')}"
            previous = current["sites_by_key"].get(site_key)
            if previous is None or site_entry_score(site_copy) < site_entry_score(previous):
                current["sites_by_key"][site_key] = site_copy

    merged = []
    for item in grouped.values():
        sites = list(item["sites_by_key"].values())
        if not sites:
            continue
        ratios = [
            display_group_ratio(site)
            for site in sites
            if isinstance(display_group_ratio(site), (int, float)) and display_group_ratio(site) >= 0
        ]
        success_vals = [site_perf_value(s, "success_rate") for s in sites if isinstance(site_perf_value(s, "success_rate"), (int, float))]
        latency_vals = [site_perf_value(s, "avg_latency_ms") for s in sites if isinstance(site_perf_value(s, "avg_latency_ms"), (int, float))]
        tps_vals = [site_perf_value(s, "avg_tps") for s in sites if isinstance(site_perf_value(s, "avg_tps"), (int, float))]
        # Best (optimal) site for the model, after dropping physically-impossible
        # readings some sites report (sub-500ms full-response latency, >1000 tps).
        ok_success = [v for v in success_vals if 0 <= v <= 100]
        ok_latency = [v for v in latency_vals if v >= PERF_MIN_LATENCY_MS]
        ok_tps = [v for v in tps_vals if 0 < v <= PERF_MAX_TPS]
        merged.append(
            {
                "model": item["model"],
                "provider": item["provider"],
                "aliases": sorted(item["aliases"], key=model_display_name_score),
                "site_count": len(sites),
                "min_ratio": min(ratios) if ratios else None,
                "success_rate": max(ok_success) if ok_success else None,
                "avg_latency_ms": min(ok_latency) if ok_latency else None,
                "avg_tps": max(ok_tps) if ok_tps else None,
                "perf_site_count": len(success_vals or latency_vals or tps_vals),
                "sites": sorted(sites, key=site_entry_score),
            }
        )
    return sorted(merged, key=lambda item: model_release_sort_key(item.get("model", "")))


MODELS = merge_equivalent_models(RAW_MODELS)


def model_price_sort_key(model, sort):
    price = (model.get("_sort_prices") or {}).get(sort)
    if price is None:
        price = model_sort_price(model, sort)
    return (
        price is None,
        model.get("_release_sort_key") or model_release_sort_key(model.get("model", "")),
        price if price is not None else 999999999,
    )


def sort_model_sites(model, sort, site_limit=None):
    if sort not in {"usd", "cny", "request"}:
        return strip_internal_model_fields(model)
    item = {key: value for key, value in model.items() if not key.startswith("_")}
    # Keep only the sites of the selected billing bucket, sorted cheapest-first.
    matching = (model.get("_sites_by_billing") or {}).get(sort)
    if matching is None:
        matching = [site for site in (model.get("sites") or []) if site_billing_bucket(site) == sort]
        matching.sort(
            key=lambda site: (
                site_sort_price(site, sort) is None,
                site_sort_price(site, sort) if site_sort_price(site, sort) is not None else 999999999,
                site.get("site_name", ""),
            ),
    )
    item["site_count"] = len(matching)
    selected = matching if site_limit is None else matching[:site_limit]
    item["sites"] = [apply_selected_group_perf(site) for site in selected]
    return item


def model_has_billing_sites(model, sort):
    if sort not in {"usd", "cny", "request"}:
        return True
    buckets = model.get("_sites_by_billing")
    if buckets is not None:
        return bool((buckets or {}).get(sort))
    return any(site_billing_bucket(site) == sort for site in (model.get("sites") or []))


def model_site_search_text(site):
    return " ".join(
        [
            site.get("site_name", ""),
            site.get("origin", ""),
            host_from_origin(site.get("origin", "")),
            site.get("model", ""),
            site.get("raw_model", "") or "",
            " ".join((site.get("group_ratios") or {}).keys()),
        ]
    ).lower()


def model_query_matches_sites(model, q_lower):
    if not q_lower:
        return []
    matched = []
    entries = model.get("_site_search_entries")
    if entries is None:
        entries = [(model_site_search_text(site), site) for site in (model.get("sites") or [])]
    for haystack, site in entries:
        if q_lower in haystack:
            matched.append(site)
    return matched


def strip_internal_model_fields(model):
    return {key: value for key, value in model.items() if not key.startswith("_")}


def prepare_model_indexes(models):
    sort_indexes = {}
    for model in models:
        hydrate_model_runtime_fields(model)

    for sort in ("usd", "cny", "request"):
        sorted_models = [model for model in models if model.get("_sites_by_billing", {}).get(sort)]
        sorted_models.sort(
            key=lambda item: (
                model_bareness_rank(item),
                model_popularity_rank(item),
                item.get("_release_sort_key") or model_release_sort_key(item.get("model", "")),
                -(item.get("site_count") or 0),
                model_price_sort_key(item, sort),
            )
        )
        sort_indexes[sort] = [sort_model_sites(model, sort, MODEL_SITE_PREVIEW_LIMIT) for model in sorted_models]
    return sort_indexes


def hydrate_model_runtime_fields(model):
    model["_total_site_count"] = model.get("_total_site_count") or model.get("site_count") or len(model.get("sites") or [])
    sites_by_billing = {}
    sort_prices = {}
    for sort in ("usd", "cny", "request"):
        matching = [site for site in (model.get("sites") or []) if site_billing_bucket(site) == sort]
        matching.sort(
            key=lambda site: (
                site_sort_price(site, sort) is None,
                site_sort_price(site, sort) if site_sort_price(site, sort) is not None else 999999999,
                site.get("site_name", ""),
            ),
        )
        sites_by_billing[sort] = matching
        prices = [site_sort_price(site, sort) for site in matching]
        prices = [price for price in prices if isinstance(price, (int, float)) and price >= 0]
        sort_prices[sort] = min(prices) if prices else None

    model["_sites_by_billing"] = sites_by_billing
    model["_sort_prices"] = sort_prices
    model["_release_sort_key"] = model_release_sort_key(model.get("model", ""))
    model["_canonical_model"] = canonical_model_key(model.get("model", ""))
    aliases = model.get("aliases", []) or []
    model["_search_text"] = " ".join(
        [
            model.get("model", ""),
            " ".join(aliases),
            model["_canonical_model"],
        ]
    ).lower()
    model["_alias_lowers"] = [(alias or "").lower() for alias in aliases[:80]]
    model["_alias_canonicals"] = [canonical_model_key(alias) for alias in aliases[:80]]
    alias_tokens = set()
    for alias in aliases[:80]:
        alias_tokens.update(model_tokens(alias))
    model["_alias_tokens"] = alias_tokens
    model["_display_tokens"] = set(model_tokens(model.get("model", "")))
    return model


def model_view_sort_key(item, sort, q_lower="", q_canonical=""):
    if sort in {"usd", "cny", "request"}:
        if q_lower:
            return (
                model_query_family_score(item, q_lower),
                model_bareness_rank(item),
                model_query_match_score(item, q_lower, q_canonical),
                model_popularity_rank(item),
                item.get("_release_sort_key") or model_release_sort_key(item.get("model", "")),
                -(item.get("site_count") or 0),
                model_price_sort_key(item, sort),
            )
        return (
            model_bareness_rank(item),
            model_popularity_rank(item),
            item.get("_release_sort_key") or model_release_sort_key(item.get("model", "")),
            -(item.get("site_count") or 0),
            model_price_sort_key(item, sort),
        )
    if sort == "name":
        return (
            model_query_family_score(item, q_lower),
            model_bareness_rank(item),
            model_query_match_score(item, q_lower, q_canonical),
            -(item.get("site_count") or 0) if q_lower else 0,
            item.get("_release_sort_key") or model_release_sort_key(item.get("model", "")),
        )
    return (
        model_bareness_rank(item),
        model_popularity_rank(item),
        item.get("_release_sort_key") or model_release_sort_key(item.get("model", "")),
        -(item.get("site_count") or 0),
    )


def sort_models_for_view(models, sort, q_lower="", q_canonical=""):
    models.sort(key=lambda item: model_view_sort_key(item, sort, q_lower, q_canonical))
    return models


def model_query_match_score(model, q_lower, q_canonical):
    if not q_lower:
        return 0
    query_is_specific = bool(re.search(r"\d", q_canonical or q_lower))
    display = (model.get("model") or "").lower()
    aliases = model.get("_alias_lowers") or [(alias or "").lower() for alias in (model.get("aliases", []) or [])[:80]]
    display_tokens = model.get("_display_tokens") or set(model_tokens(display))
    alias_tokens = model.get("_alias_tokens") or set()
    canonical = model.get("_canonical_model") or canonical_model_key(model.get("model", ""))
    alias_canonicals = model.get("_alias_canonicals") or [canonical_model_key(alias) for alias in aliases[:80]]
    if query_is_specific and q_canonical and (canonical == q_canonical or q_canonical in alias_canonicals):
        return 0
    if display == q_lower or q_lower in aliases:
        return 1
    if q_lower in display_tokens or q_lower in alias_tokens:
        return 2
    if display.startswith(q_lower) or any(alias.startswith(q_lower) for alias in aliases):
        return 3
    if q_canonical and (canonical.startswith(q_canonical) or any(item.startswith(q_canonical) for item in alias_canonicals)):
        return 4
    return 5


def model_query_family_score(model, q_lower):
    if not q_lower:
        return 0
    query_family = detect_model_family(model_tokens(q_lower))
    if not query_family:
        return 0
    model_family = detect_model_family(model_tokens(canonical_model_key(model.get("model", ""))))
    return 0 if model_family == query_family else 1


def model_version_specificity(model):
    canonical = model.get("_canonical_model") or canonical_model_key(model.get("model", ""))
    return len(re.findall(r"\d+", canonical))


def model_bareness_rank(model):
    # Models carrying a concrete version number (claude-opus-4.8, gpt-4o, ...)
    # rank ahead of bare family-only names ("claude", "gpt", "gemini"), which
    # are pushed to the back of the list.
    return 0 if model_version_specificity(model) > 0 else 1


POPULAR_SITE_THRESHOLD = 50


def model_popularity_rank(model):
    # Two tiers: "established" models (offered by enough sites) rank ahead of
    # niche / vanity names. Within each tier the list is then ordered by newest
    # version, so a brand-new model carried by only a handful of sites does not
    # leapfrog a widely-available flagship.
    total_site_count = model.get("_total_site_count") or model.get("site_count") or 0
    return 0 if total_site_count >= POPULAR_SITE_THRESHOLD else 1


MODEL_SORT_INDEX = prepare_model_indexes(MODELS)

if not DB_ENABLED:
    try:
        JSON_DATA_SIGNATURE = json_data_signature()
    except OSError:
        JSON_DATA_SIGNATURE = None


def reload_json_data_if_needed():
    if DB_ENABLED:
        return
    global RAW_MODELS, ANNOUNCEMENTS, SUMMARY, SITES, SITE_BY_ID, MODELS, MODEL_SORT_INDEX, JSON_DATA_SIGNATURE
    try:
        signature = json_data_signature()
    except OSError as exc:
        print(f"json_reload_signature_failed error={exc}", flush=True)
        return
    if signature == JSON_DATA_SIGNATURE:
        return
    with JSON_RELOAD_LOCK:
        try:
            signature = json_data_signature()
        except OSError as exc:
            print(f"json_reload_signature_failed error={exc}", flush=True)
            return
        if signature == JSON_DATA_SIGNATURE:
            return
        try:
            next_sites = dedupe_sites(read_json("sites.json"))
            next_raw_models = read_json("models.json")
            next_announcements = read_json("announcements.json")
            next_summary = read_json("summary.json")
            next_models = merge_equivalent_models(next_raw_models)
            next_sort_index = prepare_model_indexes(next_models)
        except Exception as exc:
            print(f"json_reload_failed error={exc}", flush=True)
            return
        SITES = next_sites
        SITE_BY_ID = {site["id"]: site for site in SITES}
        RAW_MODELS = next_raw_models
        ANNOUNCEMENTS = next_announcements
        SUMMARY = next_summary
        MODELS = next_models
        MODEL_SORT_INDEX = next_sort_index
        JSON_DATA_SIGNATURE = signature
        print(f"json_reload_ok sites={len(SITES)} models={len(MODELS)} announcements={len(ANNOUNCEMENTS)}", flush=True)


def preview_models(models, provider_order=None, limit=16):
    if not models:
        return []
    provider_order = provider_order or []
    providers = [provider for provider in provider_order if provider]
    by_provider = {}
    for model in models:
        provider = model.get("provider") or "Other"
        if provider not in by_provider:
            by_provider[provider] = []
            if provider not in providers:
                providers.append(provider)
        by_provider[provider].append(model)

    selected = []
    seen = set()
    for provider in providers:
        bucket = by_provider.get(provider) or []
        for model in bucket:
            name = model.get("model")
            if not name or name in seen:
                continue
            selected.append(name)
            seen.add(name)
            if len(selected) >= limit:
                return selected
    return selected


def clean_site(site, include_models=False, display_models=None, active_group=None):
    item = dict(site)
    if not include_models:
        models = display_models if display_models is not None else (item.get("models") or [])
        if models:
            item["models_preview"] = preview_models(models, item.get("providers") or [])
            item["model_count"] = len(models)
            ratios = [
                display_group_ratio(model, active_group)
                for model in models
                if isinstance(display_group_ratio(model, active_group), (int, float)) and display_group_ratio(model, active_group) >= 0
            ]
            item["lowest_ratio"] = min(ratios) if ratios else None
        else:
            item["models_preview"] = []
            item["model_count"] = 0
            item["lowest_ratio"] = None
    if not include_models:
        item.pop("models", None)
        if len(item.get("notice") or "") > 220:
            item["notice"] = item["notice"][:220] + "..."
    return item


def count_values(field):
    counter = Counter()
    for site in SITES:
        for value in site.get(field, []) or []:
            if value:
                counter[value] += 1
    return [{"value": value, "count": count} for value, count in counter.most_common()]


def count_scalar_values(field):
    counter = Counter()
    for site in SITES:
        value = site.get(field)
        if value:
            counter[value] += 1
    return [{"value": value, "count": count} for value, count in counter.most_common()]


def count_announcement_tags():
    counter = Counter()
    for item in ANNOUNCEMENTS:
        for value in item.get("tags", []) or []:
            if value:
                counter[value] += 1
    return [{"value": value, "count": count} for value, count in counter.most_common()]


def count_model_providers():
    # Model-view provider options must reflect the canonical providers actually
    # carried by MODELS (e.g. Kimi / GLM / Qwen after normalization), not the
    # raw site labels (Moonshot / 智谱 / 阿里巴巴) used by the sites view, or the
    # filter would point at provider names no model row uses.
    counter = Counter()
    for model in MODELS:
        provider = model.get("provider")
        if provider:
            counter[provider] += 1
    return [{"value": value, "count": count} for value, count in counter.most_common()]


def db_summary():
    with db_connect() as conn:
        with conn.cursor() as cur:
            generation_id, data_version = db_active_state(cur)
            cache_key = ("summary", generation_id, data_version)
            cached = db_meta_cache_get(cache_key)
            if cached is not None:
                return cached
            cur.execute(
                """
                SELECT
                  (SELECT count(*) FROM sites WHERE generation_id = %s) AS sites,
                  (SELECT count(*) FROM sites WHERE generation_id = %s AND status = 'online') AS online_sites,
                  (SELECT count(*) FROM canonical_models WHERE generation_id = %s) AS models,
                  (SELECT count(*) FROM announcements) AS announcements
                """,
                (generation_id, generation_id, generation_id),
            )
            row = cur.fetchone()
            cur.execute("SELECT meta FROM data_generations WHERE id = %s", (generation_id,))
            generation = cur.fetchone() or {}
    result = {
        "generated_at": (db_json(generation.get("meta")).get("generated_at") if generation else None),
        "sites": row["sites"],
        "online_sites": row["online_sites"],
        "models": row["models"],
        "announcements": row["announcements"],
    }
    db_meta_cache_set(cache_key, result)
    return result


def db_filters():
    with db_connect() as conn:
        with conn.cursor() as cur:
            generation_id, data_version = db_active_state(cur)
            cache_key = ("filters", generation_id, data_version)
            cached = db_meta_cache_get(cache_key)
            if cached is not None:
                return cached
            def count_site_array(column, limit=None):
                cur.execute(
                    f"""
                    SELECT value, count(*) AS count
                    FROM sites s
                    CROSS JOIN LATERAL unnest(s.{column}) AS value
                    WHERE s.generation_id = %s
                      AND value <> ''
                    GROUP BY value
                    ORDER BY count DESC, value
                    {f'LIMIT {int(limit)}' if limit else ''}
                    """,
                    (generation_id,),
                )
                return [{"value": row["value"], "count": row["count"]} for row in cur.fetchall()]

            cur.execute(
                """
                SELECT provider AS value, count(*) AS count
                FROM canonical_models
                WHERE generation_id = %s
                  AND provider <> ''
                GROUP BY provider
                ORDER BY count DESC, provider
                """,
                (generation_id,),
            )
            model_providers = [{"value": row["value"], "count": row["count"]} for row in cur.fetchall()]

            cur.execute(
                """
                SELECT value, count(*) AS count
                FROM announcements a
                CROSS JOIN LATERAL unnest(a.tags) AS value
                WHERE a.is_active = true
                  AND value <> ''
                GROUP BY value
                ORDER BY count DESC, value
                """
            )
            announcement_tags = [{"value": row["value"], "count": row["count"]} for row in cur.fetchall()]

            result = {
                "providers": count_site_array("providers", limit=200),
                "model_providers": model_providers,
                "groups": count_site_array("groups", limit=120),
                "billing_types": count_site_array("billing_types"),
                "tags": [],
                "announcement_tags": announcement_tags,
            }
            db_meta_cache_set(cache_key, result)
            return result


def db_site_where(generation_id, q, status, provider, group, billing, tag, model):
    clauses = ["s.generation_id = %s"]
    params = [generation_id]
    if status != "all":
        clauses.append("s.status = %s")
        params.append(status)
    if tag != "all":
        clauses.append("s.tags @> ARRAY[%s]::text[]")
        params.append(tag)
    model_lower = (model or "").strip().lower()
    if provider != "all" or group != "all" or billing != "all" or model_lower:
        model_clauses = ["sm.generation_id = s.generation_id", "sm.site_id = s.id"]
        model_params = []
        if provider != "all":
            model_clauses.append("sm.provider = %s")
            model_params.append(provider)
        if group != "all":
            model_clauses.append(
                """
                EXISTS (
                  SELECT 1 FROM site_model_groups smg
                  WHERE smg.site_model_id = sm.id
                    AND smg.group_name = %s
                )
                """
            )
            model_params.append(group)
        if billing != "all":
            if billing == "按量计费":
                model_clauses.append("sm.quota_type = 0")
            elif billing == "按次计费":
                model_clauses.append("sm.quota_type = 1")
            else:
                model_clauses.append("(sm.quota_type IS NULL OR sm.quota_type NOT IN (0, 1))")
        if model_lower:
            model_clauses.append("lower(sm.model) LIKE %s")
            like = f"%{model_lower}%"
            model_params.append(like)
        clauses.append(
            f"""
            EXISTS (
              SELECT 1 FROM site_models sm
              WHERE {' AND '.join(model_clauses)}
            )
            """
        )
        params.extend(model_params)
    q_lower = (q or "").strip().lower()
    if q_lower:
        clauses.append(
            """
            (
              lower(s.name) LIKE %s OR lower(s.origin) LIKE %s OR lower(coalesce(s.domain, '')) LIKE %s
              OR lower(coalesce(s.notice, '')) LIKE %s
              OR EXISTS (SELECT 1 FROM unnest(s.tags) tag_value WHERE lower(tag_value) LIKE %s)
              OR EXISTS (
                SELECT 1 FROM site_models sm
                WHERE sm.generation_id = s.generation_id
                  AND sm.site_id = s.id
                  AND lower(sm.model) LIKE %s
              )
            )
            """
        )
        like = f"%{q_lower}%"
        params.extend([like, like, like, like, like, like])
    return " AND ".join(clauses), params


def db_sites(q, status, provider, group, billing, tag, model, sort, page, page_size):
    offset = (page - 1) * page_size
    with db_connect() as conn:
        with conn.cursor() as cur:
            generation_id, data_version = db_active_state(cur)
            q_lower = (q or "").strip().lower()
            cache_key = (
                "sites",
                generation_id,
                data_version,
                q_lower,
                status,
                provider,
                group,
                billing,
                tag,
                (model or "").strip().lower(),
                sort,
                page,
                page_size,
            )
            cached = site_result_cache_get(cache_key)
            if cached is not None:
                return cached
            where_sql, params = db_site_where(generation_id, q, status, provider, group, billing, tag, model)
            cur.execute(f"SELECT count(*) AS count FROM sites s WHERE {where_sql}", params)
            total = cur.fetchone()["count"]
            lowest_ratio_sort = "s.lowest_ratio IS NULL, CASE WHEN s.lowest_ratio = 0 THEN 999999 ELSE s.lowest_ratio END"
            order_params = []
            if sort == "random":
                order_sql = (
                    "CASE "
                    "WHEN s.status = 'online' AND COALESCE(s.model_count, 0) > 0 THEN 0 "
                    "WHEN s.status = 'online' THEN 1 "
                    "WHEN s.status = 'partial' AND COALESCE(s.model_count, 0) > 0 THEN 2 "
                    "WHEN s.status = 'partial' THEN 3 "
                    "WHEN s.status = 'unknown' THEN 4 "
                    "ELSE 5 END, "
                    "md5(coalesce(s.id::text, '') || %s) ASC, s.origin ASC"
                )
                order_params.append(f"{generation_id}:{data_version}")
            else:
                order_sql = {
                    "price": f"{lowest_ratio_sort}, s.sort_index NULLS LAST, s.origin ASC",
                    "models": "s.model_count DESC, s.sort_index NULLS LAST, s.origin ASC",
                    "name": "s.name ASC, s.sort_index NULLS LAST, s.origin ASC",
                }.get(sort, "s.sort_index NULLS LAST, s.origin ASC")
            cur.execute(
                f"""
                SELECT s.payload
                FROM sites s
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT %s OFFSET %s
                """,
                params + order_params + [page_size, offset],
            )
            items = []
            for row in cur.fetchall():
                site = db_json(row["payload"])
                display_models = filtered_site_models(
                    site,
                    provider=provider,
                    group=group,
                    billing=billing,
                    model_query=(model or "").strip().lower(),
                )
                items.append(
                    clean_site(
                        site,
                        display_models=display_models,
                        active_group=group if group != "all" else None,
                    )
                )
    result = db_paginate(total, items, page, page_size)
    site_result_cache_set(cache_key, result)
    return result


def db_site_detail(site_id):
    with db_connect() as conn:
        with conn.cursor() as cur:
            generation_id = db_active_generation(cur)
            cur.execute(
                "SELECT payload FROM sites WHERE generation_id = %s AND id = %s",
                (generation_id, site_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Site not found")
            return db_json(row["payload"])


def db_announcements(q, tag, page, page_size):
    offset = (page - 1) * page_size
    clauses = ["is_active = true"]
    params = []
    if tag != "all":
        clauses.append("tags @> ARRAY[%s]::text[]")
        params.append(tag)
    q_lower = (q or "").strip().lower()
    if q_lower:
        clauses.append("(lower(site_name) LIKE %s OR lower(origin) LIKE %s OR lower(content) LIKE %s)")
        like = f"%{q_lower}%"
        params.extend([like, like, like])
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS count FROM announcements {where_sql}", params)
            total = cur.fetchone()["count"]
            cur.execute(
                f"""
                SELECT
                  payload || jsonb_build_object(
                    'is_active', is_active,
                    'last_seen_at', last_seen_at,
                    'first_seen_at', first_seen_at
                  ) AS payload
                FROM announcements
                {where_sql}
                ORDER BY first_seen_at DESC NULLS LAST, id DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            items = [db_json(row["payload"]) for row in cur.fetchall()]
    return db_paginate(total, items, page, page_size)


def ai_news_feed_items():
    items = []
    source_states = []
    for feed in AI_NEWS_FEEDS:
        try:
            feed_limit = int(feed.get("limit") or AI_NEWS_PER_FEED)
            if feed.get("telegram_url"):
                feed_items = parse_telegram_feed_items(feed, limit=feed_limit)
                source_url = ""
            else:
                feed_items = parse_rss_feed_articles(feed, limit=feed_limit)
                source_url = feed.get("_last_fetched_url") or feed.get("homepage") or feed["url"]
            items.extend(feed_items)
            source_states.append({"name": feed["title"], "provider": feed["provider"], "status": "ok", "count": len(feed_items), "url": source_url, "fallback": "telegram" if feed.get("telegram_url") else ""})
        except Exception as exc:
            if feed.get("telegram_fallback"):
                try:
                    fallback_items = parse_linuxdo_telegram_items(feed, limit=int(feed.get("limit") or AI_NEWS_PER_FEED))
                    if fallback_items:
                        items.extend(fallback_items)
                        source_states.append({"name": feed["title"], "provider": feed["provider"], "status": "ok", "count": len(fallback_items), "url": feed["telegram_fallback"], "fallback": "telegram"})
                        continue
                except Exception:
                    pass
            error_text = str(exc)[:160]
            if feed.get("provider") == "LinuxDo" and ("403" in error_text or "Forbidden" in error_text):
                error_text = "Cloudflare challenge blocked RSS; set RELAYWATCH_LINUXDO_COOKIE or RELAYWATCH_LINUXDO_RSS_URLS"
            source_states.append({"name": feed["title"], "provider": feed["provider"], "status": "limited", "count": 0, "url": "" if feed.get("telegram_url") else feed.get("homepage") or feed["url"], "error": error_text})
    return items, source_states


def compact_ai_news_item(item):
    compacted = dict(item)
    compacted.pop("content", None)
    compacted.pop("content_html", None)
    compacted.pop("excerpt", None)
    if compacted.get("summary"):
        compacted["summary"] = compact_text(compacted.get("summary") or "", 220)
    return compacted


def compact_ai_news_payload(result):
    if not isinstance(result, dict):
        return result
    compacted = dict(result)
    compacted["items"] = [compact_ai_news_item(item) for item in result.get("items") or []]
    compacted["featured"] = [compact_ai_news_item(item) for item in result.get("featured") or []]
    compacted["tutorials"] = [compact_ai_news_item(item) for item in result.get("tutorials") or []]
    compacted["feed_items"] = [compact_ai_news_item(item) for item in result.get("feed_items") or []]
    return compacted


def build_ai_news_payload(items, tutorial_items, source_states, feed_items):
    items = sort_ai_news_items([item for item in dedupe_ai_news_items(items) if should_show_ai_news_item(item)])
    categories = Counter(item.get("category") or "动态" for item in items)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cache_ttl": AI_NEWS_TTL,
        "items": items,
        "featured": items[:5],
        "tutorials": tutorial_items,
        "sources": AI_NEWS_SOURCES + AI_NEWS_OFFICIAL_ITEMS,
        "source_states": source_states,
        "feed_items": [item for item in feed_items if should_show_ai_news_item(item)][:10],
        "categories": [{"value": key, "count": count} for key, count in categories.most_common()],
        "data_version": "article-db-v1",
        "storage": "postgres" if DB_ENABLED else "memory",
    }


def collect_ai_news_items():
    feed_items, source_states = ai_news_feed_items()
    tutorial_items = newapi_tutorial_items()
    project_items = github_project_items()
    if tutorial_items:
        source_states.append({"name": "NewAPI AI 应用文档", "provider": "NewAPI", "status": "ok", "count": len(tutorial_items), "url": NEWAPI_APPS_DOCS_URL})
    else:
        source_states.append({"name": "NewAPI AI 应用文档", "provider": "NewAPI", "status": "limited", "count": 0, "url": NEWAPI_APPS_DOCS_URL})
    if project_items:
        source_states.append({"name": "GitHub 开源项目", "provider": "GitHub", "status": "ok", "count": len(project_items), "url": "https://github.com/search?q=ai%E8%81%9A%E5%90%88&type=repositories"})
    else:
        source_states.append({"name": "GitHub 开源项目", "provider": "GitHub", "status": "limited", "count": 0, "url": "https://github.com/search?q=ai%E8%81%9A%E5%90%88&type=repositories"})
    source_states.append({"name": "RelayWatch 原创文章", "provider": "本站原创", "status": "ok", "count": len(AI_NEWS_ORIGINAL_ARTICLES), "url": ""})
    collected_items = feed_items + tutorial_items + project_items + AI_NEWS_ORIGINAL_ARTICLES
    return collected_items, tutorial_items, source_states, feed_items


def build_ai_news_from_db(source_states=None, feed_items=None):
    items = db_article_items()
    if not items:
        return None
    tutorial_items = db_tutorial_article_items()
    item_ids = {item.get("id") for item in items if item.get("id")}
    items = items + [item for item in tutorial_items if item.get("id") not in item_ids]
    return build_ai_news_payload(items, tutorial_items, source_states if source_states is not None else db_article_source_states(), feed_items or [])


def build_ai_news(force=False):
    now = time.time()
    with AI_NEWS_CACHE_LOCK:
        if not force and AI_NEWS_CACHE["data"] is not None and AI_NEWS_CACHE["expires_at"] > now:
            return AI_NEWS_CACHE["data"]
    result = None
    if DB_ENABLED and not force:
        try:
            result = build_ai_news_from_db()
        except Exception:
            result = None
    if result is None:
        collected_items, tutorial_items, source_states, feed_items = collect_ai_news_items()
        visible_items = [item for item in dedupe_ai_news_items(collected_items) if should_show_ai_news_item(item)]
        if DB_ENABLED:
            try:
                upsert_articles_to_db(visible_items)
                result = build_ai_news_from_db(source_states=source_states, feed_items=feed_items)
            except Exception:
                result = None
        if result is None:
            result = build_ai_news_payload(visible_items, tutorial_items, source_states, feed_items)
    with AI_NEWS_CACHE_LOCK:
        AI_NEWS_CACHE["data"] = result
        AI_NEWS_CACHE["expires_at"] = time.time() + AI_NEWS_TTL
    return result


def get_ai_news_article(article_id):
    article_id = (article_id or "").strip()
    if not article_id:
        raise HTTPException(status_code=404, detail="Article not found")
    if DB_ENABLED:
        try:
            item = db_article_item(article_id)
            if item:
                return item
        except Exception:
            pass
    payload = build_ai_news(force=False)
    for item in (payload.get("items") or []) + (payload.get("tutorials") or []):
        if item.get("id") == article_id:
            return item
    raise HTTPException(status_code=404, detail="Article not found")


def db_model_perf_clause(min_success, max_latency, min_tps):
    clauses = []
    params = []
    if min_success is not None:
        clauses.append("cms.success_rate IS NOT NULL AND cms.success_rate >= %s")
        params.append(min_success)
    if max_latency is not None:
        clauses.append("cms.avg_latency_ms IS NOT NULL AND cms.avg_latency_ms <= %s")
        params.append(max_latency)
    if min_tps is not None:
        clauses.append("cms.avg_tps IS NOT NULL AND cms.avg_tps >= %s")
        params.append(min_tps)
    return clauses, params


def db_models(q, provider, sort, min_success, max_latency, min_tps, page, page_size):
    if sort == "price":
        sort = "usd"
    if sort not in {"usd", "cny", "request", "name"}:
        sort = "usd"
    with db_connect() as conn:
        with conn.cursor() as cur:
            generation_id, data_version = db_active_state(cur)
            cache_key = (
                "models",
                generation_id,
                data_version,
                (q or "").strip().lower(),
                provider or "all",
                sort,
                min_success,
                max_latency,
                min_tps,
                page,
                page_size,
            )
            cached = model_result_cache_get(cache_key)
            if cached is not None:
                return cached
            q_lower = (q or "").strip().lower()
            all_models = db_all_models(cur, generation_id, data_version)
            q_canonical = canonical_model_key(q_lower) if q_lower else ""
            perf_filter = min_success is not None or max_latency is not None or min_tps is not None
            site_candidate_ids = None
            allow_site_search = bool(q_lower and (len(q_lower) >= 4 or "." in q_lower or "/" in q_lower))
            if allow_site_search:
                site_candidate_ids = db_model_site_candidate_ids(cur, generation_id, q_lower)
            def site_qualifies(site):
                if min_success is not None:
                    value = site_perf_value(site, "success_rate")
                    if value is None or value < min_success:
                        return False
                if max_latency is not None:
                    value = site_perf_value(site, "avg_latency_ms")
                    if value is None or value > max_latency:
                        return False
                if min_tps is not None:
                    value = site_perf_value(site, "avg_tps")
                    if value is None or value < min_tps:
                        return False
                return True

            filtered = []
            for source_model in all_models:
                model_item = source_model
                if provider != "all" and model_item.get("provider") != provider:
                    continue
                if perf_filter:
                    qualifying = [site for site in (model_item.get("sites") or []) if site_qualifies(site)]
                    if not qualifying:
                        continue
                    model_item = hydrate_model_runtime_fields(
                        {
                            **model_item,
                            "sites": qualifying,
                            "site_count": len(qualifying),
                        }
                    )
                if q_lower:
                    haystack = model_item.get("_search_text") or " ".join(
                        [
                            model_item.get("model", ""),
                            " ".join(model_item.get("aliases", []) or []),
                            canonical_model_key(model_item.get("model", "")),
                        ]
                    ).lower()
                    model_matched = q_lower in haystack or (q_canonical and q_canonical in haystack)
                    if not model_matched:
                        if not allow_site_search:
                            continue
                        if site_candidate_ids is not None and model_item.get("_db_id") not in site_candidate_ids:
                            continue
                        matched_sites = model_query_matches_sites(model_item, q_lower)
                        if not matched_sites:
                            continue
                        model_item = hydrate_model_runtime_fields(
                            {
                                **model_item,
                                "sites": matched_sites,
                                "site_count": len(matched_sites),
                            }
                        )
                filtered.append(model_item)

            sort_models_for_view(filtered, sort, q_lower, q_canonical)
            if sort in {"usd", "cny", "request"}:
                filtered = [item for item in filtered if model_has_billing_sites(item, sort)]
            result = paginate_prepared(
                filtered,
                page,
                page_size,
                lambda item: sort_model_sites(item, sort, MODEL_SITE_PREVIEW_LIMIT),
            )
    model_result_cache_set(cache_key, result)
    return result


def db_model_sites(provider, model, sort, min_success, max_latency, min_tps, page, page_size):
    if sort == "price":
        sort = "usd"
    if sort not in {"usd", "cny", "request"}:
        sort = "usd"
    offset = (page - 1) * page_size
    with db_connect() as conn:
        with conn.cursor() as cur:
            generation_id, data_version = db_active_state(cur)
            cache_key = (
                "model-sites",
                generation_id,
                data_version,
                (provider or "").lower(),
                model,
                sort,
                min_success,
                max_latency,
                min_tps,
                page,
                page_size,
            )
            cached = model_result_cache_get(cache_key)
            if cached is not None:
                return cached
            canonical = canonical_model_key(model)
            provider_lower = (provider or "").lower()
            target = None
            for item in db_all_models(cur, generation_id, data_version):
                if (item.get("provider") or "").lower() != provider_lower:
                    continue
                if item.get("model") == model:
                    target = item
                    break
                if canonical and (item.get("_canonical_model") or canonical_model_key(item.get("model", ""))) == canonical:
                    target = item
                    break
            if not target:
                raise HTTPException(status_code=404, detail="Model not found")
            model_item = target
            sites = (model_item.get("_sites_by_billing") or {}).get(sort) or []
            if min_success is not None or max_latency is not None or min_tps is not None:
                def site_qualifies(site):
                    if min_success is not None:
                        value = site_perf_value(site, "success_rate")
                        if value is None or value < min_success:
                            return False
                    if max_latency is not None:
                        value = site_perf_value(site, "avg_latency_ms")
                        if value is None or value > max_latency:
                            return False
                    if min_tps is not None:
                        value = site_perf_value(site, "avg_tps")
                        if value is None or value < min_tps:
                            return False
                    return True

                sites = [site for site in sites if site_qualifies(site)]
    result = paginate([apply_selected_group_perf(site) for site in sites], page, page_size)
    model_result_cache_set(cache_key, result)
    return result


@app.get("/api/summary")
def summary():
    if DB_ENABLED:
        return db_summary()
    reload_json_data_if_needed()
    data = dict(SUMMARY)
    data["models"] = len(MODELS)
    return data


@app.get("/api/official-status")
def official_status(force: bool = False):
    return load_official_status(force=force)


@app.get("/api/official-status/summary")
def official_status_summary():
    return compact_official_status(load_official_status(force=False))


@app.post("/api/chat/proxy")
async def chat_proxy(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求内容不是有效 JSON") from exc
    target_url = chat_proxy_target(payload.get("base_url"))
    api_key = (payload.get("api_key") or "").strip()
    model = compact_text(payload.get("model") or "", 180)
    if not api_key:
        raise HTTPException(status_code=400, detail="请填写 API Key")
    if not model:
        raise HTTPException(status_code=400, detail="请填写模型名称")
    body = {
        "model": model,
        "messages": clean_chat_messages(payload.get("messages")),
        "stream": bool(payload.get("stream", True)),
        "temperature": max(0, min(2, float(payload.get("temperature") if payload.get("temperature") not in {None, ""} else 0.7))),
        "max_tokens": max(1, min(8192, int(payload.get("max_tokens") if payload.get("max_tokens") not in {None, ""} else 2048))),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if body["stream"] else "application/json",
    }

    async def body_stream():
        try:
            async for chunk in stream_chat_completion(target_url, headers, body):
                yield chunk
        except HTTPException as exc:
            yield f"data: {json.dumps({'error': compact_text(str(exc.detail), 300)}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': compact_text(str(exc), 300)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(body_stream(), media_type="text/event-stream")


@app.post("/api/chat/models")
async def chat_models(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求内容不是有效 JSON") from exc
    api_key = (payload.get("api_key") or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="请填写 API Key")
    models = await fetch_chat_models(payload.get("base_url"), api_key)
    return {"items": models, "total": len(models)}


@app.get("/api/filters")
def filters():
    if DB_ENABLED:
        return db_filters()
    reload_json_data_if_needed()
    return {
        "providers": count_values("providers")[:200],
        "model_providers": count_model_providers(),
        "groups": count_values("groups")[:120],
        "billing_types": count_values("billing_types"),
        "tags": [],
        "announcement_tags": count_announcement_tags(),
    }


@app.get("/api/sites")
def sites(
    q: str = "",
    status: str = "all",
    provider: str = "all",
    group: str = "all",
    billing: str = "all",
    tag: str = "all",
    model: str = "",
    sort: str = "random",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    if DB_ENABLED:
        return db_sites(q, status, provider, group, billing, tag, model, sort, page, page_size)
    reload_json_data_if_needed()
    q_lower = q.lower().strip()
    model_lower = model.lower().strip()
    filtered = []
    filtered_models_by_site = {}
    for site in SITES:
        if status != "all" and site.get("status") != status:
            continue
        if tag != "all" and tag not in site.get("tags", []):
            continue
        display_models = filtered_site_models(site, provider=provider, group=group, billing=billing, model_query=model_lower)
        if (provider != "all" or group != "all" or billing != "all" or model_lower) and not display_models:
            continue
        if q_lower:
            haystack = " ".join(
                [
                    site.get("name", ""),
                    site.get("origin", ""),
                    site.get("domain", ""),
                    site.get("notice", ""),
                    " ".join(site.get("models_preview", [])),
                    " ".join(site.get("tags", [])),
                ]
            ).lower()
            if q_lower not in haystack:
                continue
        filtered.append(site)
        filtered_models_by_site[site["id"]] = display_models

    if sort == "random":
        seed = current_site_random_seed(f"{q_lower}:{status}:{provider}:{group}:{billing}:{tag}:{model_lower}")
        def random_site_bucket(item):
            state = item.get("status")
            has_models = (item.get("model_count") or 0) > 0
            if state == "online" and has_models:
                return 0
            if state == "online":
                return 1
            if state == "partial" and has_models:
                return 2
            if state == "partial":
                return 3
            if state == "unknown":
                return 4
            return 5
        filtered.sort(
            key=lambda item: (
                random_site_bucket(item),
                stable_site_random_key(item, seed),
            )
        )
    elif sort == "price":
        filtered.sort(key=lambda item: (item.get("lowest_ratio") is None, item.get("lowest_ratio") or 999999))
    elif sort == "models":
        filtered.sort(key=lambda item: item.get("model_count", 0), reverse=True)
    elif sort == "name":
        filtered.sort(key=lambda item: item.get("name", ""))
    else:
        filtered.sort(key=lambda item: (item.get("status") != "online", item.get("lowest_ratio") is None, item.get("lowest_ratio") or 999999))

    page_data = paginate(
        [
            clean_site(
                site,
                display_models=filtered_models_by_site.get(site["id"]),
                active_group=group if group != "all" else None,
            )
            for site in filtered
        ],
        page,
        page_size,
    )
    return page_data


@app.get("/api/sites/{site_id}")
def site_detail(site_id: str):
    if DB_ENABLED:
        return db_site_detail(site_id)
    reload_json_data_if_needed()
    site = SITE_BY_ID.get(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return clean_site(site, include_models=True)


@app.get("/api/models")
def models(
    q: str = "",
    provider: str = "all",
    sort: str = "usd",
    min_success: float = Query(None),
    max_latency: float = Query(None),
    min_tps: float = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    if DB_ENABLED:
        return db_models(q, provider, sort, min_success, max_latency, min_tps, page, page_size)
    reload_json_data_if_needed()
    if sort == "price":
        sort = "usd"
    if sort not in {"usd", "cny", "request", "name"}:
        sort = "usd"
    q_lower = q.lower().strip()
    q_canonical = canonical_model_key(q_lower) if q_lower else ""
    perf_filter = min_success is not None or max_latency is not None or min_tps is not None
    if sort in {"usd", "cny", "request"} and provider == "all" and not q_lower and not perf_filter:
        return paginate(MODEL_SORT_INDEX.get(sort, []), page, page_size)

    def site_qualifies(site):
        # A site passes only if, for every active threshold, it actually reports
        # that metric and meets it. (max_latency arrives in ms from the client.)
        if min_success is not None:
            v = site_perf_value(site, "success_rate")
            if v is None or v < min_success:
                return False
        if max_latency is not None:
            v = site_perf_value(site, "avg_latency_ms")
            if v is None or v > max_latency:
                return False
        if min_tps is not None:
            v = site_perf_value(site, "avg_tps")
            if v is None or v < min_tps:
                return False
        return True

    filtered = []
    for model in MODELS:
        if provider != "all" and model.get("provider") != provider:
            continue
        if perf_filter:
            # Keep only the qualifying sites, and drop the model if none qualify,
            # so the site list shown matches the filter (no sub-threshold sites).
            qualifying = [site for site in (model.get("sites") or []) if site_qualifies(site)]
            if not qualifying:
                continue
            model = {
                **model,
                "sites": qualifying,
                "site_count": len(qualifying),
                "_sites_by_billing": None,
                "_sort_prices": None,
            }
        if q_lower:
            haystack = model.get("_search_text") or " ".join(
                [
                    model.get("model", ""),
                    " ".join(model.get("aliases", []) or []),
                    canonical_model_key(model.get("model", "")),
                ]
            ).lower()
            model_matched = q_lower in haystack or (q_canonical and q_canonical in haystack)
            if not model_matched:
                matched_sites = model_query_matches_sites(model, q_lower)
                if not matched_sites:
                    continue
                model = hydrate_model_runtime_fields(
                    {
                        **model,
                        "sites": matched_sites,
                        "site_count": len(matched_sites),
                    }
                )
        filtered.append(model)

    sort_models_for_view(filtered, sort, q_lower, q_canonical)

    if sort in {"usd", "cny", "request"}:
        # Drop models that have no site in the selected billing bucket so each
        # view (美元 / 人民币 / 按次) only shows models priced that way.
        filtered = [item for item in filtered if model_has_billing_sites(item, sort)]
    return paginate_prepared(
        filtered,
        page,
        page_size,
        lambda item: sort_model_sites(item, sort, MODEL_SITE_PREVIEW_LIMIT),
    )


@app.get("/api/model-sites")
def model_sites(
    provider: str,
    model: str,
    sort: str = "usd",
    min_success: float = Query(None),
    max_latency: float = Query(None),
    min_tps: float = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=2000),
):
    if DB_ENABLED:
        return db_model_sites(provider, model, sort, min_success, max_latency, min_tps, page, page_size)
    reload_json_data_if_needed()
    if sort == "price":
        sort = "usd"
    if sort not in {"usd", "cny", "request"}:
        sort = "usd"
    canonical = canonical_model_key(model)
    provider_lower = (provider or "").lower()
    target = None
    for item in MODELS:
        if (item.get("provider") or "").lower() != provider_lower:
            continue
        if item.get("model") == model:
            target = item
            break
        if canonical and (item.get("_canonical_model") or canonical_model_key(item.get("model", ""))) == canonical:
            target = item
            break
    if not target:
        raise HTTPException(status_code=404, detail="Model not found")
    sites = (target.get("_sites_by_billing") or {}).get(sort) or []
    if min_success is not None or max_latency is not None or min_tps is not None:
        def site_qualifies(site):
            if min_success is not None:
                value = site_perf_value(site, "success_rate")
                if value is None or value < min_success:
                    return False
            if max_latency is not None:
                value = site_perf_value(site, "avg_latency_ms")
                if value is None or value > max_latency:
                    return False
            if min_tps is not None:
                value = site_perf_value(site, "avg_tps")
                if value is None or value < min_tps:
                    return False
            return True

        sites = [site for site in sites if site_qualifies(site)]
    return paginate([apply_selected_group_perf(site) for site in sites], page, page_size)


@app.get("/api/announcements")
def announcements(
    q: str = "",
    tag: str = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    if DB_ENABLED:
        return db_announcements(q, tag, page, page_size)
    reload_json_data_if_needed()
    q_lower = q.lower().strip()
    filtered = []
    for item in ANNOUNCEMENTS:
        if tag != "all" and tag not in item.get("tags", []):
            continue
        if q_lower:
            haystack = " ".join(
                [item.get("site_name", ""), item.get("origin", ""), item.get("content", ""), " ".join(item.get("tags", []))]
            ).lower()
            if q_lower not in haystack:
                continue
        filtered.append(item)
    return paginate(filtered, page, page_size)


@app.get("/api/ai-news")
def ai_news(force: bool = False):
    return compact_ai_news_payload(build_ai_news(force=force))


@app.get("/api/ai-news/articles/{article_id:path}")
def ai_news_article(article_id: str):
    return get_ai_news_article(article_id)


@app.post("/api/submit-site")
async def submit_site(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="提交内容不是有效 JSON") from exc
    origin = normalize_submitted_origin(payload.get("origin") or payload.get("url"))
    validation = await validate_submitted_site(origin)
    added = append_custom_origin(origin)
    warnings = validation.get("warnings") or []
    if added:
        message = "获取成功，已加入本站收录列表"
    else:
        message = "该站点已在收录列表中"
    if warnings:
        message = "已识别站点并加入，但" + "、".join(warnings)
    return {
        "ok": True,
        "origin": origin,
        "added": added,
        "status": "accepted_with_warning" if warnings else "accepted",
        "message": message,
        "warnings": warnings,
        "site": {
            "name": validation.get("system_name"),
            "model_count": validation.get("model_count"),
            "new_api_version": validation.get("new_api_version"),
            "notice_ok": validation.get("notice_ok"),
            "perf_ok": validation.get("perf_ok"),
        },
    }


@app.post("/api/feedback")
async def feedback(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="反馈内容不是有效 JSON") from exc
    result = store_feedback(payload, request.headers.get("user-agent", ""))
    return {"ok": True, "message": "反馈已收到，感谢提醒。", **result}


@app.get("/api/feedback")
def feedback_list(limit: int = Query(20, ge=1, le=100)):
    items = list_feedback(limit)
    return {"items": items, "total": len(items)}


@app.delete("/api/feedback/{feedback_id}")
def feedback_delete(feedback_id: int, request: Request):
    require_admin_token(request)
    return {"ok": True, **delete_feedback_item(feedback_id)}


@app.post("/api/detections")
async def create_detection(request: Request):
    if not DETECTOR_BASE_URL:
        raise HTTPException(status_code=503, detail="检测服务未启用")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="提交内容不是有效 JSON") from exc

    protocol = normalize_detection_protocol(payload.get("protocol"))
    mode = normalize_detection_mode(payload.get("mode"))
    model = normalize_detection_model(payload.get("model"))
    origin = normalize_submitted_origin(payload.get("origin") or payload.get("site_origin") or "")
    base_url = normalize_detection_base_url(payload.get("base_url"), origin, protocol)
    api_key = normalize_detection_api_key(payload.get("api_key"))
    include_long_context = bool(payload.get("include_long_context"))
    include_long_context_extreme = bool(payload.get("include_long_context_extreme"))

    import httpx

    form = {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "mode": mode,
    }
    if protocol != "gemini":
        form["include_long_context"] = "true" if include_long_context else "false"
        form["include_long_context_extreme"] = "true" if include_long_context_extreme else "false"
    endpoint = DETECTOR_BASE_URL + detection_endpoint_for_protocol(protocol)
    try:
        async with httpx.AsyncClient(timeout=DETECTOR_TIMEOUT, trust_env=True) as client:
            response = await client.post(endpoint, data=form)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=detector_connection_error_message(exc)) from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="检测服务返回了非 JSON 内容") from exc
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise HTTPException(status_code=response.status_code, detail=detail or "检测服务拒绝了请求")
    if not isinstance(body, dict) or not body.get("job_id"):
        raise HTTPException(status_code=502, detail="检测服务返回格式异常")
    store_detection_context(
        body.get("job_id"),
        {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "protocol": protocol,
            "mode": mode,
        },
    )
    return {
        "ok": True,
        "job_id": body.get("job_id"),
        "protocol": protocol,
        "mode": mode,
        "origin": origin,
        "base_url": base_url,
        "model": model,
        "status_url": f"/api/detections/{body.get('job_id')}",
    }


@app.get("/api/detections/{job_id}")
async def detection_status(job_id: str):
    if not DETECTOR_BASE_URL:
        raise HTTPException(status_code=503, detail="检测服务未启用")
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", job_id or ""):
        raise HTTPException(status_code=400, detail="检测任务 ID 格式不正确")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=DETECTOR_TIMEOUT, trust_env=True) as client:
            status_response = await client.get(f"{DETECTOR_BASE_URL}/api/status/{quote(job_id)}")
            status_body = status_response.json()
            result_body = None
            if status_response.status_code == 200 and status_body.get("status") == "done":
                result_response = await client.get(f"{DETECTOR_BASE_URL}/api/result/{quote(job_id)}.json")
                if result_response.status_code == 200:
                    result_body = result_response.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=detector_connection_error_message(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="检测服务返回了非 JSON 内容") from exc

    if status_response.status_code == 404:
        raise HTTPException(status_code=404, detail="检测任务不存在")
    if status_response.status_code >= 400:
        detail = status_body.get("detail") if isinstance(status_body, dict) else None
        raise HTTPException(status_code=status_response.status_code, detail=detail or "检测服务状态异常")
    result = compact_detection_result(result_body) if result_body else None
    if result:
        quality = get_detection_quality(job_id)
        if quality:
            result["quality"] = quality
            result["ai_summary"] = quality.get("ai_summary")
        else:
            result["quality"] = {
                "status": "pending",
                "score": None,
                "level": "unknown",
                "risk_tags": [],
                "ai_summary": "协议检测已完成，正在生成质量实测。",
                "rows": [],
            }
    return {
        "ok": True,
        "job": status_body,
        "result": result,
    }


@app.post("/api/detections/{job_id}/quality")
async def detection_quality(job_id: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", job_id or ""):
        raise HTTPException(status_code=400, detail="检测任务 ID 格式不正确")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="提交内容不是有效 JSON") from exc

    cached = get_detection_quality(job_id)
    if cached and cached.get("status") == "done":
        return {"ok": True, "quality": cached}

    protocol = normalize_detection_protocol(payload.get("protocol"))
    mode = normalize_detection_mode(payload.get("mode"))
    model = normalize_detection_model(payload.get("model"))
    origin = normalize_submitted_origin(payload.get("origin") or payload.get("site_origin") or "")
    base_url = normalize_detection_base_url(payload.get("base_url"), origin, protocol)
    api_key = normalize_detection_api_key(payload.get("api_key"))
    try:
        quality = await run_quality_probes({
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "protocol": protocol,
            "mode": mode,
        })
    except Exception as exc:
        quality = {
            "status": "done",
            "score": None,
            "level": "unknown",
            "risk_tags": ["质量实测未形成完整结论"],
            "ai_summary": f"协议检测已完成，质量实测未形成完整结论：{exc.__class__.__name__}",
            "rows": [],
        }
    pop_detection_context(job_id)
    store_detection_quality(job_id, quality)
    return {"ok": True, "quality": quality}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
