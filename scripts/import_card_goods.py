import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def split_sources(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def parse_int(value):
    parsed = server.parse_ldxp_number(value)
    return int(parsed or 0)


def shop_token_key(row):
    return str(row.get("token_fold") or row.get("token") or "").strip().lower()


def read_goods_pool_shop_map():
    path = ROOT / "data" / "ldxp" / "ldxp_goods_pool_shops.csv"
    if not path.exists():
        return {}
    rows = server.read_ldxp_csv(path)
    return {shop_token_key(row): row for row in rows if shop_token_key(row)}


def merge_goods_pool_shop_source(row, goods_pool_shops):
    token_key = shop_token_key(row)
    pool_row = goods_pool_shops.get(token_key)
    if not pool_row:
        return row

    merged = dict(row)
    sources = split_sources(merged.get("sources"))
    if "ldxp_goods_pool" not in sources:
        sources.append("ldxp_goods_pool")
        merged["goods_count_sum_across_sources"] = str(
            parse_int(merged.get("goods_count_sum_across_sources")) + parse_int(pool_row.get("goods_count"))
        )

    merged["sources"] = ",".join(sorted(dict.fromkeys(sources)))
    merged["source_count"] = str(len(split_sources(merged.get("sources"))))
    merged["goods_count_max"] = str(max(parse_int(merged.get("goods_count_max")), parse_int(pool_row.get("goods_count"))))
    if not str(merged.get("shop_name") or "").strip():
        merged["shop_name"] = pool_row.get("shop_name") or ""
    if not str(merged.get("shop_url") or "").strip():
        merged["shop_url"] = pool_row.get("shop_url") or ""
    return merged


def item_time_value(item):
    for key in ("updated_at", "captured_at"):
        parsed = server.parse_ldxp_datetime(item.get(key))
        if parsed:
            return parsed.timestamp()
    return 0


def best_text(values):
    clean = [str(value or "").strip() for value in values if str(value or "").strip()]
    if not clean:
        return ""
    counts = Counter(clean)
    return sorted(counts, key=lambda value: (-counts[value], len(value), value))[0]


def source_priority(source):
    source = str(source or "").strip()
    # 店铺自身接口是库存/上下架状态的一手来源，优先级必须高于聚合站旧缓存。
    if source == "ldxp_shop_api":
        return 100
    if source == "cardnav_full_api":
        return 40
    if source == "findai8_goods":
        return 30
    if source == "priceai_offers_api":
        return 20
    return 10 if source else 0


def read_removed_shop_tokens():
    path = os.environ.get("CARD_REMOVED_SHOPS_FILE", "").strip()
    if not path:
        default_path = Path("/root/relaywatch-deploy/state/card_removed_shop_tokens_current.txt")
        if default_path.exists():
            path = str(default_path)
    if not path:
        return set()
    file_path = Path(path)
    if not file_path.exists():
        return set()
    tokens = set()
    for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        token = line.strip().lower()
        if token:
            tokens.add(token)
    return tokens


def read_manual_goods_overrides():
    path = os.environ.get("CARD_GOODS_OVERRIDES_FILE", "").strip()
    if not path:
        default_path = Path("/root/relaywatch-deploy/state/card_goods_manual_overrides.csv")
        if default_path.exists():
            path = str(default_path)
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}

    overrides = {}
    with file_path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
        for row in csv.DictReader(handle):
            normalized = {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}
            if not any(normalized.values()):
                continue
            for field in ("item_key", "goods_id", "item_url"):
                value = normalized.get(field, "")
                if value:
                    overrides[(field, value.lower())] = normalized
    return overrides


def find_manual_override(item, overrides):
    if not overrides:
        return None
    for field in ("item_key", "goods_id", "item_url"):
        value = str(item.get(field) or "").strip().lower()
        if value and (field, value) in overrides:
            return overrides[(field, value)]
    return None


def apply_manual_override(item, raw, payload, override):
    if not override:
        return False
    changed = False
    for field in ("status", "stock_text"):
        value = override.get(field, "")
        if value:
            item[field] = value
            raw[field] = value
            payload[field] = value
            changed = True
    if item.get("status") in {"unlisted", "removed"}:
        item["stock"] = None
        raw["stock"] = ""
        payload["stock"] = None
        changed = True
    if override.get("stock", "") != "":
        stock = server.parse_ldxp_number(override.get("stock"))
        item["stock"] = stock
        raw["stock"] = override.get("stock")
        payload["stock"] = stock
        changed = True
    if override.get("reason"):
        payload["manual_override_reason"] = override.get("reason")
        changed = True
    if override.get("is_active", ""):
        value = str(override.get("is_active") or "").strip().lower()
        is_active = value in {"1", "true", "yes", "y", "on", "online", "active"}
        item["_manual_is_active"] = is_active
        payload["manual_is_active"] = is_active
        changed = True
    return changed


def should_replace_primary(item, record):
    current_source = item.get("source") or ""
    current_priority = source_priority(current_source)
    record_item = record.get("item") or {}
    record_priority = source_priority(record_item.get("source") or "")
    if current_priority != record_priority:
        return current_priority > record_priority
    return item_time_value(item) >= record.get("best_time", 0)


def import_card_data(insert_snapshots=True):
    if not server.DB_ENABLED:
        raise RuntimeError("DATABASE_URL is empty; cannot import card data into database")

    _psycopg, _dict_row, Jsonb = server.import_psycopg()
    started_at = datetime.now(timezone.utc)
    raw_goods = server.read_ldxp_csv(server.LDXP_GOODS_PATH)
    prepared_goods = server.prepare_ldxp_goods(raw_goods)
    shops = server.read_ldxp_csv(server.LDXP_SHOPS_PATH)
    goods_pool_shops = read_goods_pool_shop_map()
    ldxp_refreshed_tokens = set()
    ldxp_current_item_urls = set()
    ldxp_current_goods_keys = set()
    ldxp_removed_shop_tokens = read_removed_shop_tokens()
    strict_ldxp_full_overwrite = os.environ.get("CARD_STRICT_LDXP_FULL_OVERWRITE", "1").strip().lower() not in {"0", "false", "no", "off"}
    manual_goods_overrides = read_manual_goods_overrides()
    manual_override_count = 0

    goods_by_key = {}
    for raw, item in zip(raw_goods, prepared_goods):
        key = server.card_goods_key(raw)
        payload = server.clean_ldxp_item(item)
        payload["id"] = key
        source = item.get("source") or ""
        if source == "ldxp_shop_api":
            ldxp_current_goods_keys.add(key)
            token_key = str(item.get("token_fold") or item.get("token") or "").strip().lower()
            if token_key:
                ldxp_refreshed_tokens.add(token_key)
            item_url = str(item.get("item_url") or "").strip()
            if item_url:
                ldxp_current_item_urls.add(item_url)
        if key not in goods_by_key:
            goods_by_key[key] = {
                "raw": raw,
                "item": item,
                "payload": payload,
                "sources": set(),
                "text_values": {
                    "token": [],
                    "token_fold": [],
                    "shop_name": [],
                    "shop_url": [],
                    "goods_name": [],
                    "category": [],
                    "brand": [],
                    "tags": [],
                },
                "raw_rows": 0,
                "best_time": item_time_value(item),
                "best_priority": source_priority(source),
            }
        record = goods_by_key[key]
        record["raw_rows"] += 1
        if source:
            record["sources"].add(source)
        for field in record["text_values"]:
            if item.get(field):
                record["text_values"][field].append(item.get(field))
        current_time = item_time_value(item)
        if should_replace_primary(item, record):
            record["raw"] = raw
            record["item"] = item
            record["payload"] = payload
            record["best_time"] = current_time
            record["best_priority"] = source_priority(source)

    with server.db_connect() as conn:
        conn.execute("SET statement_timeout = 0")
        with conn.cursor() as cur:
            server.ensure_card_schema(cur)
            cur.execute(
                """
                INSERT INTO card_monitor_runs (source, status, started_at, meta)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    "ldxp",
                    "running",
                    started_at,
                    Jsonb(
                        {
                            "goods_csv": str(server.LDXP_GOODS_PATH),
                            "shops_csv": str(server.LDXP_SHOPS_PATH),
                            "raw_goods_rows": len(raw_goods),
                            "unique_goods": len(goods_by_key),
                        }
                    ),
                ),
            )
            run_id = cur.fetchone()["id"]

            shop_count = 0
            for row in shops:
                row = merge_goods_pool_shop_source(row, goods_pool_shops)
                token = str(row.get("token") or "").strip()
                if not token:
                    continue
                payload = dict(row)
                payload["shop_name"] = server.fix_ldxp_text(row.get("shop_name"))
                payload["sample_goods_name"] = server.fix_ldxp_text(row.get("sample_goods_name"))
                token_fold = str(row.get("token_fold") or row.get("token") or row.get("token_aliases") or token).strip().lower()
                shop_status = "closed" if token_fold in ldxp_removed_shop_tokens else server.fix_ldxp_text(row.get("status"))
                payload["status"] = shop_status
                cur.execute(
                    """
                    INSERT INTO card_shops (
                      token, token_fold, shop_name, shop_url, sources, source_count,
                      goods_count_max, goods_count_sum_across_sources, sample_item_url,
                      sample_goods_name, status, last_seen_at, last_checked_at, payload, raw
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now(), %s, %s)
                    ON CONFLICT (token) DO UPDATE SET
                      token_fold = EXCLUDED.token_fold,
                      shop_name = EXCLUDED.shop_name,
                      shop_url = EXCLUDED.shop_url,
                      sources = EXCLUDED.sources,
                      source_count = EXCLUDED.source_count,
                      goods_count_max = EXCLUDED.goods_count_max,
                      goods_count_sum_across_sources = EXCLUDED.goods_count_sum_across_sources,
                      sample_item_url = EXCLUDED.sample_item_url,
                      sample_goods_name = EXCLUDED.sample_goods_name,
                      status = EXCLUDED.status,
                      last_seen_at = now(),
                      last_checked_at = now(),
                      payload = EXCLUDED.payload,
                      raw = EXCLUDED.raw
                    """,
                    (
                        token,
                        str(row.get("token_fold") or row.get("token") or row.get("token_aliases") or token).strip().lower(),
                        server.fix_ldxp_text(row.get("shop_name")),
                        row.get("shop_url") or "",
                        split_sources(row.get("sources")),
                        int(server.parse_ldxp_number(row.get("source_count")) or 0),
                        int(server.parse_ldxp_number(row.get("goods_count_max")) or 0),
                        int(server.parse_ldxp_number(row.get("goods_count_sum_across_sources")) or 0),
                        row.get("sample_item_url") or "",
                        server.fix_ldxp_text(row.get("sample_goods_name")),
                        shop_status,
                        Jsonb(payload),
                        Jsonb(dict(row)),
                    ),
                )
                shop_count += 1

            goods_count = 0
            snapshot_count = 0
            for key, record in goods_by_key.items():
                item = record["item"]
                raw = record["raw"]
                payload = record["payload"]
                sources = sorted(record.get("sources") or ([item.get("source")] if item.get("source") else []))
                primary_source = item.get("source") or (sources[0] if sources else "")
                payload["source"] = primary_source
                payload["sources"] = sources
                payload["source_count"] = len(sources)
                payload["raw_source_rows"] = record.get("raw_rows") or 1
                text_values = record.get("text_values") or {}
                for field in ("token", "token_fold", "shop_name", "shop_url", "goods_name", "category", "brand", "tags"):
                    chosen = best_text(text_values.get(field) or [])
                    if chosen:
                        item[field] = chosen
                        payload[field] = chosen
                updated_at = server.parse_ldxp_datetime(item.get("updated_at"))
                captured_at = server.parse_ldxp_datetime(item.get("captured_at"))
                token_key = str(item.get("token_fold") or item.get("token") or "").strip().lower()
                if apply_manual_override(item, raw, payload, find_manual_override(item, manual_goods_overrides)):
                    manual_override_count += 1
                item_is_active = primary_source == "ldxp_shop_api" and token_key not in ldxp_removed_shop_tokens
                if item.get("status") in {"unlisted", "removed"}:
                    item_is_active = False
                if "_manual_is_active" in item:
                    item_is_active = bool(item.get("_manual_is_active"))
                cur.execute(
                    """
                    INSERT INTO card_goods (
                      goods_key, source, sources, source_count, item_key, item_url, goods_id, token, token_fold,
                      shop_name, shop_url, goods_name, searchable_goods_name, category, brand,
                      ai_family, product_type, ai_category, tags, price, price_raw, stock,
                      stock_text, status, sold_24h, updated_at, captured_at, last_seen_at,
                      last_checked_at, is_active, payload, raw
                    )
                    VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, now(), now(), %s, %s, %s
                    )
                    ON CONFLICT (goods_key) DO UPDATE SET
                      source = EXCLUDED.source,
                      sources = EXCLUDED.sources,
                      source_count = EXCLUDED.source_count,
                      item_key = EXCLUDED.item_key,
                      item_url = EXCLUDED.item_url,
                      goods_id = EXCLUDED.goods_id,
                      token = EXCLUDED.token,
                      token_fold = EXCLUDED.token_fold,
                      shop_name = EXCLUDED.shop_name,
                      shop_url = EXCLUDED.shop_url,
                      goods_name = EXCLUDED.goods_name,
                      searchable_goods_name = EXCLUDED.searchable_goods_name,
                      category = EXCLUDED.category,
                      brand = EXCLUDED.brand,
                      ai_family = EXCLUDED.ai_family,
                      product_type = EXCLUDED.product_type,
                      ai_category = EXCLUDED.ai_category,
                      tags = EXCLUDED.tags,
                      price = EXCLUDED.price,
                      price_raw = EXCLUDED.price_raw,
                      stock = EXCLUDED.stock,
                      stock_text = EXCLUDED.stock_text,
                      status = EXCLUDED.status,
                      sold_24h = EXCLUDED.sold_24h,
                      updated_at = EXCLUDED.updated_at,
                      captured_at = EXCLUDED.captured_at,
                      last_seen_at = now(),
                      last_checked_at = now(),
                      is_active = EXCLUDED.is_active,
                      payload = EXCLUDED.payload,
                      raw = EXCLUDED.raw
                    """,
                    (
                        key,
                        primary_source,
                        sources,
                        len(sources),
                        item.get("item_key") or "",
                        item.get("item_url") or "",
                        item.get("goods_id") or "",
                        item.get("token") or "",
                        item.get("token_fold") or "",
                        item.get("shop_name") or "",
                        item.get("shop_url") or "",
                        item.get("goods_name") or "",
                        item.get("_search") or server.ldxp_searchable_goods_name(item.get("goods_name")),
                        item.get("category") or "",
                        item.get("brand") or "",
                        item.get("ai_family") or "其它",
                        item.get("product_type") or "其它",
                        item.get("ai_category") or item.get("product_type") or "其它",
                        item.get("tags") or "",
                        item.get("price"),
                        item.get("price_raw") or "",
                        item.get("stock"),
                        item.get("stock_text") or "",
                        item.get("status") or "",
                        item.get("sold_24h"),
                        updated_at,
                        captured_at,
                        item_is_active,
                        Jsonb(payload),
                        Jsonb(raw),
                    ),
                )
                goods_count += 1

                if insert_snapshots:
                    cur.execute(
                        """
                        INSERT INTO card_goods_snapshots (
                          run_id, goods_key, source, price, price_raw, stock,
                          stock_text, status, sold_24h, payload, raw
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            run_id,
                            key,
                            item.get("source") or "",
                            item.get("price"),
                            item.get("price_raw") or "",
                            item.get("stock"),
                            item.get("stock_text") or "",
                            item.get("status") or "",
                            item.get("sold_24h"),
                            Jsonb(payload),
                            Jsonb(raw),
                        ),
                    )
                    snapshot_count += 1

            if ldxp_refreshed_tokens:
                cur.execute(
                    """
                    UPDATE card_goods
                    SET is_active = false,
                        status = 'removed',
                        last_checked_at = now()
                    WHERE is_active = true
                      AND lower(COALESCE(NULLIF(token_fold, ''), token, '')) = ANY(%s)
                      AND NOT (COALESCE(item_url, '') = ANY(%s))
                    """,
                    (sorted(ldxp_refreshed_tokens), sorted(ldxp_current_item_urls)),
                )
                removed_ldxp_missing = cur.rowcount
            else:
                removed_ldxp_missing = 0

            if ldxp_removed_shop_tokens:
                cur.execute(
                    """
                    UPDATE card_goods
                    SET is_active = false,
                        status = 'removed',
                        last_checked_at = now()
                    WHERE is_active = true
                      AND lower(COALESCE(NULLIF(token_fold, ''), token, '')) = ANY(%s)
                    """,
                    (sorted(ldxp_removed_shop_tokens),),
                )
                removed_ldxp_shops = cur.rowcount
            else:
                removed_ldxp_shops = 0

            if strict_ldxp_full_overwrite and ldxp_current_goods_keys:
                cur.execute(
                    """
                    UPDATE card_goods
                    SET is_active = false,
                        status = 'removed',
                        last_checked_at = now()
                    WHERE is_active = true
                      AND (
                        source = 'ldxp_shop_api'
                        OR 'ldxp_shop_api' = ANY(COALESCE(sources, ARRAY[]::text[]))
                      )
                      AND NOT (goods_key = ANY(%s))
                    """,
                    (sorted(ldxp_current_goods_keys),),
                )
                removed_ldxp_strict_missing = cur.rowcount
            else:
                removed_ldxp_strict_missing = 0

            cur.execute(
                """
                UPDATE card_goods
                SET is_active = false
                WHERE last_checked_at < %s
                  AND NOT (
                    source = 'ldxp_shop_api'
                    OR 'ldxp_shop_api' = ANY(COALESCE(sources, ARRAY[]::text[]))
                  )
                """,
                (started_at,),
            )
            cur.execute(
                """
                UPDATE card_monitor_runs
                SET status = %s, finished_at = now(), shop_count = %s, goods_count = %s,
                    snapshot_count = %s, error_count = 0,
                    meta = meta || %s
                WHERE id = %s
                """,
                (
                    "success",
                    shop_count,
                    goods_count,
                    snapshot_count,
                    Jsonb(
                        {
                            "deduped_duplicate_rows": len(raw_goods) - len(goods_by_key),
                            "ldxp_refreshed_tokens": len(ldxp_refreshed_tokens),
                            "ldxp_current_goods_keys": len(ldxp_current_goods_keys),
                            "ldxp_missing_items_deactivated": removed_ldxp_missing,
                            "ldxp_strict_full_overwrite": strict_ldxp_full_overwrite,
                            "ldxp_strict_missing_goods_deactivated": removed_ldxp_strict_missing,
                            "ldxp_removed_shop_tokens": len(ldxp_removed_shop_tokens),
                            "ldxp_removed_shop_items_deactivated": removed_ldxp_shops,
                            "manual_goods_overrides": manual_override_count,
                        }
                    ),
                    run_id,
                ),
            )
            cur.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES ('card_data_version', %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (str(int(started_at.timestamp())),),
            )

    return {
        "run_id": run_id,
        "raw_goods_rows": len(raw_goods),
        "unique_goods": len(goods_by_key),
        "shops": shop_count,
        "snapshots": snapshot_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Import RelayWatch card shop goods into PostgreSQL tables.")
    parser.add_argument("--no-snapshots", action="store_true", help="Only update current goods, do not append snapshot rows.")
    args = parser.parse_args()
    result = import_card_data(insert_snapshots=not args.no_snapshots)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
