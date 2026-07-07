import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import aiohttp


ENDPOINTS = {
    "status": "/api/status",
    "home_page_content": "/api/home_page_content",
    "notice": "/api/notice",
    "pricing": "/api/pricing",
    "ratio_config": "/api/ratio_config",
    "perf_metrics": "/api/perf-metrics/summary?hours=24",
}

BODYLESS_ENDPOINTS = {"home_page_content"}


def json_safe(value):
    if isinstance(value, str):
        return value.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {json_safe(key): json_safe(item) for key, item in value.items()}
    return value


async def fetch_endpoint(session, origin, name, timeout, max_chars):
    path = ENDPOINTS[name]
    return await fetch_path(session, origin, name, path, timeout, max_chars)


async def fetch_path(session, origin, name, path, timeout, max_chars):
    url = origin.rstrip("/") + path
    started = time.time()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as response:
            text = await response.text(errors="replace")
            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]
            ok = bool(text.strip()) if name in {"status", "home_page_content"} else response.status == 200 and bool(text.strip())
            body = "" if name in BODYLESS_ENDPOINTS else text
            return {
                "name": name,
                "path": path,
                "url": url,
                "status": response.status,
                "content_type": response.headers.get("content-type", ""),
                "new_api_version": response.headers.get("X-New-Api-Version", ""),
                "elapsed_ms": int((time.time() - started) * 1000),
                "ok": ok,
                "truncated": truncated,
                "body_len": len(text),
                "body_sha1": hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest(),
                "body": body,
            }
    except Exception as exc:
        return {
            "name": name,
            "path": path,
            "url": url,
            "status": None,
            "content_type": "",
            "elapsed_ms": int((time.time() - started) * 1000),
            "ok": False,
            "error": exc.__class__.__name__,
            "body": str(exc)[:500],
        }


def parse_summary_model_names(summary_endpoint, pricing_endpoint, limit=12):
    if not summary_endpoint.get("ok"):
        return []
    try:
        parsed = json.loads(summary_endpoint.get("body") or "")
    except (TypeError, json.JSONDecodeError):
        return []
    data = parsed.get("data") if isinstance(parsed, dict) else {}
    raw_models = data.get("models") if isinstance(data, dict) else []
    names = []
    for item in raw_models or []:
        if isinstance(item, dict) and item.get("model_name"):
            names.append(str(item["model_name"]))
    if not names:
        return []

    multi_group_models = set()
    try:
        pricing = json.loads(pricing_endpoint.get("body") or "")
    except (TypeError, json.JSONDecodeError):
        pricing = {}
    for item in (pricing.get("data") if isinstance(pricing, dict) else []) or []:
        if not isinstance(item, dict):
            continue
        groups = [group for group in item.get("enable_groups") or [] if isinstance(group, str) and group.strip()]
        if len(groups) > 1 and item.get("model_name"):
            multi_group_models.add(str(item["model_name"]))

    result = []
    seen = set()
    for name in names:
        if name in seen:
            continue
        if multi_group_models and name not in multi_group_models:
            continue
        seen.add(name)
        result.append(name)
        if len(result) >= limit:
            break
    return result


async def fetch_perf_metric_details(session, origin, summary_endpoint, pricing_endpoint, timeout, max_chars):
    details = {}
    for model_name in parse_summary_model_names(summary_endpoint, pricing_endpoint):
        endpoint = await fetch_path(
            session,
            origin,
            "perf_metrics_detail",
            f"/api/perf-metrics?model={quote(model_name, safe='')}&hours=24",
            timeout,
            max_chars,
        )
        if not endpoint.get("ok"):
            continue
        try:
            parsed = json.loads(endpoint.get("body") or "")
        except (TypeError, json.JSONDecodeError):
            continue
        details[model_name] = parsed
    body = json.dumps({"models": details}, ensure_ascii=False)
    truncated = len(body) > max_chars
    return {
        "name": "perf_metrics_by_model",
        "path": "/api/perf-metrics?model={model}&hours=24",
        "url": origin.rstrip("/") + "/api/perf-metrics?model={model}&hours=24",
        "status": 200 if details else None,
        "content_type": "application/json",
        "new_api_version": "",
        "elapsed_ms": 0,
        "ok": bool(details),
        "truncated": truncated,
        "body": body[:max_chars] if truncated else body,
    }


async def verify_notice_with_strict_tls(origin, headers, timeout):
    connector = aiohttp.TCPConnector(ssl=True)
    try:
        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
            async with session.get(
                origin.rstrip("/") + ENDPOINTS["notice"],
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                text = await response.text(errors="replace")
                return response.status == 200 and bool(text.strip()), ""
    except Exception as exc:
        return False, exc.__class__.__name__


async def refresh_one(session, semaphore, origin, timeout, max_chars, endpoint_names):
    async with semaphore:
        endpoints = {}
        for name in endpoint_names:
            endpoints[name] = await fetch_endpoint(session, origin, name, timeout, max_chars)
        if "perf_metrics" in endpoints and "pricing" in endpoints:
            endpoints["perf_metrics_by_model"] = await fetch_perf_metric_details(
                session,
                origin,
                endpoints.get("perf_metrics") or {},
                endpoints.get("pricing") or {},
                timeout,
                max_chars,
            )
        notice_endpoint = endpoints.get("notice") or {}
        if notice_endpoint.get("ok") and origin.lower().startswith("https://"):
            strict_ok, strict_error = await verify_notice_with_strict_tls(
                origin,
                dict(session.headers),
                timeout,
            )
            notice_endpoint["strict_tls_ok"] = strict_ok
            if not strict_ok:
                notice_endpoint["strict_tls_error"] = strict_error
        return {
            "origin": origin,
            "domain": origin.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0],
            "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "any_ok": any(endpoint.get("ok") for endpoint in endpoints.values()),
            "endpoints": endpoints,
        }


def load_origins(sites_path, limit, online_only=False):
    sites = json.loads(Path(sites_path).read_text(encoding="utf-8"))
    origins = []
    seen = set()
    for site in sites:
        origin = site.get("origin")
        if not origin or origin in seen:
            continue
        if online_only and site.get("status") != "online":
            continue
        seen.add(origin)
        origins.append(origin)
    return origins[:limit] if limit else origins


def normalize_origin_token(value):
    raw = str(value or "").strip().strip("\"'<>[]()，,;")
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else "//" + raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path
    if not netloc:
        return None
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[-1]
    return f"{scheme}://{netloc}".rstrip("/")


def iter_origin_file_tokens(path):
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0]
        for token in line.replace(",", " ").replace(";", " ").split():
            token = token.strip()
            if token:
                yield token


def load_extra_origins(paths):
    origins = []
    seen = set()
    for path in paths or []:
        for token in iter_origin_file_tokens(path):
            origin = normalize_origin_token(token)
            if origin and origin not in seen:
                seen.add(origin)
                origins.append(origin)
    return origins


def prune_extra_origin_files(paths, origins_to_remove):
    remove_set = {origin.rstrip("/") for origin in origins_to_remove if origin}
    if not remove_set:
        return 0

    removed = 0
    for raw_path in paths or []:
        path = Path(raw_path)
        if not path.exists():
            continue
        new_lines = []
        changed = False
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue

            content, sep, comment = line.partition("#")
            tokens = content.replace(",", " ").replace(";", " ").split()
            kept_tokens = []
            for token in tokens:
                origin = normalize_origin_token(token)
                if origin and origin.rstrip("/") in remove_set:
                    removed += 1
                    changed = True
                    continue
                kept_tokens.append(token)

            rebuilt = " ".join(kept_tokens)
            if sep:
                rebuilt = (rebuilt + " " if rebuilt else "") + sep + comment
            if rebuilt.strip():
                new_lines.append(rebuilt)
            elif sep:
                new_lines.append(sep + comment)
        if changed:
            path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return removed


def append_unique_origins(origins, extra_origins):
    seen = set(origins)
    for origin in extra_origins:
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins


def row_has_new_api_header(row):
    for endpoint in (row.get("endpoints") or {}).values():
        if endpoint.get("new_api_version"):
            return True
    return False


def load_failed_origins(sites_path, limit):
    sites = json.loads(Path(sites_path).read_text(encoding="utf-8"))
    origins = []
    seen = set()
    for site in sites:
        origin = site.get("origin")
        if not origin or origin in seen:
            continue
        if site.get("status") == "unknown" or not site.get("model_count"):
            seen.add(origin)
            origins.append(origin)
    return origins[:limit] if limit else origins


def domain_from_origin(origin):
    return urlparse(origin or "").netloc.split("@")[-1].split(":", 1)[0].lower()


def root_domain(host):
    parts = [part for part in (host or "").split(".") if part]
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def related_variants(origin):
    variants = []

    def add(value):
        value = (value or "").rstrip("/")
        if value and value not in variants:
            variants.append(value)

    add(origin)
    host = domain_from_origin(origin)
    root = root_domain(host)
    if host:
        add(f"https://{host}")
        add(f"http://{host}")
    if root:
        add(f"https://api.{root}")
        add(f"https://{root}")
        add(f"http://api.{root}")
        add(f"http://{root}")
    return variants


def merge_rows(raw_path, refreshed_rows):
    raw_file = Path(raw_path)
    is_jsonl = raw_file.suffix.lower() == ".jsonl"
    if not raw_file.exists():
        rows = []
        raw = None if is_jsonl else {"rows": []}
    elif is_jsonl:
        rows = []
        raw = None
    else:
        raw = json.loads(raw_file.read_text(encoding="utf-8"))
        rows = raw.get("rows", raw if isinstance(raw, list) else [])

    updated = 0
    preserved = 0
    changed_origins = []
    refreshed_by_origin = {row.get("origin"): row for row in refreshed_rows if row.get("origin")}
    written_origins = set()

    def compact_endpoint_bodies(row):
        endpoints = row.get("endpoints") or {}
        for name in BODYLESS_ENDPOINTS:
            endpoint = endpoints.get(name)
            if isinstance(endpoint, dict) and endpoint.get("body"):
                body = endpoint.get("body") or ""
                endpoint.setdefault("body_len", len(body))
                endpoint.setdefault("body_sha1", hashlib.sha1(body.encode("utf-8", errors="replace")).hexdigest())
                endpoint["body"] = ""
        return row

    def row_fingerprint(value):
        value = value or {}
        endpoints = {}
        for name, endpoint in sorted((value.get("endpoints") or {}).items()):
            body = endpoint.get("body") or ""
            endpoints[name] = {
                "status": endpoint.get("status"),
                "ok": endpoint.get("ok"),
                "content_type": endpoint.get("content_type"),
                "new_api_version": endpoint.get("new_api_version"),
                "error": endpoint.get("error"),
                "strict_tls_ok": endpoint.get("strict_tls_ok"),
                "strict_tls_error": endpoint.get("strict_tls_error"),
                "truncated": endpoint.get("truncated"),
            }
            if name not in {"home_page_content", "perf_metrics", "perf_metrics_by_model"}:
                endpoints[name]["body_sha1"] = hashlib.sha1(body.encode("utf-8", errors="replace")).hexdigest()
        comparable = {
            "origin": value.get("origin"),
            "domain": value.get("domain"),
            "any_ok": value.get("any_ok"),
            "endpoints": endpoints,
        }
        return json.dumps(json_safe(comparable), ensure_ascii=False, sort_keys=True)

    def merge_one(row, previous=None):
        nonlocal updated
        row = compact_endpoint_bodies(dict(row))
        if previous:
            previous = compact_endpoint_bodies(dict(previous))
        before = row_fingerprint(previous) if previous else None
        if previous:
            merged_endpoints = dict(previous.get("endpoints") or {})
            for name, endpoint in (row.get("endpoints") or {}).items():
                previous_endpoint = merged_endpoints.get(name) or {}
                if name != "perf_metrics_by_model" and previous_endpoint.get("ok") and not endpoint.get("ok"):
                    continue
                merged_endpoints[name] = endpoint
            row["endpoints"] = merged_endpoints
            row["any_ok"] = any(endpoint.get("ok") for endpoint in merged_endpoints.values())
        after = row_fingerprint(row)
        if before != after:
            changed_origins.append(row["origin"])
        updated += 1
        return row

    tmp = raw_file.with_suffix(raw_file.suffix + ".tmp")
    if is_jsonl:
        with tmp.open("w", encoding="utf-8", errors="replace") as handle:
            if raw_file.exists():
                with raw_file.open("r", encoding="utf-8") as old_handle:
                    for line in old_handle:
                        line = line.strip()
                        if not line:
                            continue
                        previous = json.loads(line)
                        origin = previous.get("origin")
                        if origin in refreshed_by_origin:
                            row = merge_one(refreshed_by_origin[origin], previous)
                            written_origins.add(origin)
                        else:
                            row = compact_endpoint_bodies(previous)
                        handle.write(json.dumps(json_safe(row), ensure_ascii=False) + "\n")
            for origin, row in refreshed_by_origin.items():
                if origin in written_origins:
                    continue
                handle.write(json.dumps(json_safe(merge_one(row, None)), ensure_ascii=False) + "\n")
    else:
        by_origin = {row.get("origin"): row for row in rows if row.get("origin")}
        for row in refreshed_rows:
            if not row.get("origin"):
                continue
            by_origin[row["origin"]] = merge_one(row, by_origin.get(row["origin"]))
        merged_rows = list(by_origin.values())
        if isinstance(raw, list):
            output = merged_rows
        else:
            raw["rows"] = merged_rows
            output = raw
        tmp.write_text(json.dumps(json_safe(output), ensure_ascii=False), encoding="utf-8", errors="replace")
    tmp.replace(raw_file)
    return updated, preserved, changed_origins


async def refresh_group(session, semaphore, origins, timeout, max_chars, endpoint_names, retry_variants):
    requested_origin = origins[0] if origins else ""
    candidates = []
    for origin in origins:
        if retry_variants:
            candidates.extend(related_variants(origin))
        else:
            candidates.append(origin)

    seen = set()
    candidates = [origin for origin in candidates if not (origin in seen or seen.add(origin))]
    best_row = None
    best_score = (-1, -1, -1, -1)
    for origin in candidates:
        row = await refresh_one(session, semaphore, origin, timeout, max_chars, endpoint_names)
        endpoints = row.get("endpoints") or {}
        pricing_ok = 1 if (endpoints.get("pricing") or {}).get("ok") else 0
        status_ok = 1 if (endpoints.get("status") or {}).get("ok") else 0
        ok_count = sum(1 for endpoint in endpoints.values() if endpoint.get("ok"))
        status_count = sum(1 for endpoint in endpoints.values() if endpoint.get("status"))
        body_count = sum(1 for endpoint in endpoints.values() if endpoint.get("body"))
        score = (pricing_ok, status_ok, ok_count, status_count + body_count)
        if score > best_score:
            best_row = row
            best_score = score
        if pricing_ok:
            row["requested_origin"] = requested_origin
            return row
    if best_row:
        best_row["requested_origin"] = requested_origin
    return best_row


async def run(args):
    if args.origin:
        origins = []
        seen = set()
        for origin in args.origin:
            origin = origin.rstrip("/")
            if origin and origin not in seen:
                seen.add(origin)
                origins.append(origin)
        if args.limit:
            origins = origins[: args.limit]
    elif args.failed_only:
        origins = load_failed_origins(args.sites, args.limit)
    else:
        origins = load_origins(args.sites, args.limit, online_only=args.online_only)
    extra_origins = load_extra_origins(args.extra_origins_file)
    extra_origin_set = {origin.rstrip("/") for origin in extra_origins}
    origins = append_unique_origins(origins, extra_origins)
    endpoint_names = ["status", "home_page_content"] if args.status_only else list(ENDPOINTS)
    print(f"refresh origins={len(origins)} endpoints={','.join(endpoint_names)} concurrency={args.concurrency} timeout={args.timeout}", flush=True)

    connector = aiohttp.TCPConnector(limit=args.concurrency, limit_per_host=args.limit_per_host, ssl=False)
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 api-config-collector/1.1",
    }
    semaphore = asyncio.Semaphore(args.concurrency)
    started = time.time()
    rows = []
    ok_count = 0
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [
            asyncio.create_task(
                refresh_group(
                    session,
                    semaphore,
                    [origin],
                    args.timeout,
                    args.max_chars,
                    endpoint_names,
                    args.retry_variants,
                )
            )
            for origin in origins
        ]
        for index, task in enumerate(asyncio.as_completed(tasks), 1):
            row = await task
            rows.append(row)
            if row["any_ok"]:
                ok_count += 1
            if index % args.progress_every == 0 or index == len(origins):
                elapsed = max(1, int(time.time() - started))
                print(f"{index}/{len(origins)} ok={ok_count} elapsed={elapsed}s", flush=True)

    updated, preserved, changed_origins = merge_rows(args.raw, rows)
    if args.changed_origins_out:
        changed_path = Path(args.changed_origins_out)
        changed_path.parent.mkdir(parents=True, exist_ok=True)
        changed_path.write_text("\n".join(changed_origins) + ("\n" if changed_origins else ""), encoding="utf-8")
        print(f"changed_origins={len(changed_origins)} path={changed_path}", flush=True)
    if args.prune_extra_without_newapi_header and args.extra_origins_file:
        remove_origins = [
            row.get("requested_origin") or row.get("origin")
            for row in rows
            if (row.get("requested_origin") or row.get("origin") or "").rstrip("/") in extra_origin_set and not row_has_new_api_header(row)
        ]
        removed = prune_extra_origin_files(args.extra_origins_file, remove_origins)
        print(f"extra_origins_pruned_without_newapi_header={removed}", flush=True)
    print(f"merged={updated} preserved_old_success={preserved} refreshed={len(rows)} ok={ok_count} raw={args.raw}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", default="data/sites.json")
    parser.add_argument("--raw", default="../api_config_results.json")
    parser.add_argument("--concurrency", type=int, default=256)
    parser.add_argument("--limit-per-host", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--max-chars", type=int, default=200000)
    parser.add_argument("--progress-every", type=int, default=500)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--online-only", action="store_true")
    parser.add_argument("--origin", action="append", default=[])
    parser.add_argument("--extra-origins-file", action="append", default=[])
    parser.add_argument("--prune-extra-without-newapi-header", action="store_true", default=True)
    parser.add_argument("--no-prune-extra-without-newapi-header", dest="prune_extra_without_newapi_header", action="store_false")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--changed-origins-out", default="")
    parser.add_argument("--retry-variants", action="store_true", default=True)
    parser.add_argument("--no-retry-variants", dest="retry_variants", action="store_false")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
