import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import load_json_to_db as dbload
import normalize_data as norm


def import_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise SystemExit("Missing dependency: psycopg. Install with `pip install psycopg[binary]`.") from exc
    return psycopg, Jsonb, dict_row


def import_server_helpers(conninfo):
    if conninfo and not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = conninfo
    import server

    return server


def clean_db_value(value):
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [clean_db_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(clean_db_value(item) for item in value)
    if isinstance(value, set):
        return [clean_db_value(item) for item in sorted(value, key=str)]
    if isinstance(value, dict):
        return {clean_db_value(key): clean_db_value(item) for key, item in value.items()}
    return value


def read_origin_set(path):
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return set()
    values = set()
    for line in file_path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip().rstrip("/")
        if value:
            values.add(value)
    return values


def iter_selected_rows(raw_path, origins):
    if origins is not None and not origins:
        return
    for row in norm.iter_rows(raw_path):
        origin = (row.get("origin") or "").rstrip("/")
        requested = (row.get("requested_origin") or "").rstrip("/")
        if origins is None or origin in origins or requested in origins:
            yield row


def normalize_rows(rows):
    generated_at = datetime.now(timezone.utc)
    generated_ts = generated_at.timestamp()
    sites = []
    announcements_by_site = defaultdict(list)
    for row in rows:
        site = norm.site_from_row(row, generated_ts)
        if not site:
            continue
        site_id = site["id"]
        notice = site.get("notice") or ""
        status_announcements = site.get("status_announcements") or []
        if notice:
            announcement_id = norm.stable_id(site["origin"] + "notice:" + notice)
            announcements_by_site[site_id].append(
                clean_db_value(
                    {
                    "id": announcement_id,
                    "content_hash": norm.stable_id(notice),
                    "site_id": site_id,
                    "site_name": site["name"],
                    "origin": site["origin"],
                    "content": notice,
                    "tags": norm.classify_notice(notice),
                    "registration_status": site.get("registration_status"),
                    "first_seen_at": site.get("updated_at"),
                    "time_source": "observed",
                    "source": "notice",
                }
                )
            )
        for index, item in enumerate(status_announcements):
            content = item.get("content") or ""
            if not content:
                continue
            has_publish_at = bool(item.get("publish_at"))
            first_seen_at = norm.safe_announcement_time(item.get("publish_at"), site.get("updated_at"), generated_ts)
            announcements_by_site[site_id].append(
                clean_db_value(
                    {
                    "id": norm.stable_id(site["origin"] + "status:" + (item.get("source_id") or str(index)) + ":" + content),
                    "content_hash": norm.stable_id(content),
                    "site_id": site_id,
                    "site_name": site["name"],
                    "origin": site["origin"],
                    "content": content,
                    "tags": norm.classify_notice(content),
                    "registration_status": site.get("registration_status"),
                    "first_seen_at": first_seen_at,
                    "published_at": item.get("publish_at") or None,
                    "time_source": "published_at" if has_publish_at else "observed",
                    "source": "status_announcements",
                    "source_type": item.get("type"),
                    "source_id": item.get("source_id"),
                }
                )
            )
        site.pop("status_announcements", None)
        sites.append(clean_db_value(site))
    return norm.dedupe_sites(sites), announcements_by_site, generated_at


def canonical_key_for(server, provider, model):
    model_name = model or ""
    canonical = server.canonical_model_key(model_name) or model_name.lower().strip()
    resolved_provider = server.resolved_provider_name(provider or "Other", model_name)
    return (resolved_provider.lower(), canonical)


def site_model_keys(server, site):
    return {
        canonical_key_for(server, model.get("provider"), model.get("model"))
        for model in site.get("models") or []
        if model.get("model")
    }


def load_old_canonical_keys(cur, server, generation_id, site_ids):
    if not site_ids:
        return set()
    cur.execute(
        """
        SELECT provider, model
        FROM site_models
        WHERE generation_id = %s AND site_id = ANY(%s)
        """,
        (generation_id, list(site_ids)),
    )
    return {canonical_key_for(server, row["provider"], row["model"]) for row in cur.fetchall()}


def load_existing_site_payloads(cur, generation_id, site_ids):
    if not site_ids:
        return {}
    cur.execute(
        """
        SELECT id, payload
        FROM sites
        WHERE generation_id = %s AND id = ANY(%s)
        """,
        (generation_id, list(site_ids)),
    )
    return {row["id"]: dict(row["payload"] or {}) for row in cur.fetchall()}


MODEL_PRESERVE_FIELDS = [
    "providers",
    "groups",
    "billing_types",
    "model_count",
    "models_preview",
    "lowest_ratio",
    "models",
    "currency_symbol",
    "currency_unit_price",
    "quota_per_unit",
    "token_price_multiplier",
    "request_price_multiplier",
    "display_in_currency",
]
MODEL_PRICE_TAGS = {"\u514d\u8d39", "\u4ed8\u8d39"}


def preserve_existing_model_payload(site, old_payload):
    if not old_payload:
        return site
    merged = dict(site)
    for key in MODEL_PRESERVE_FIELDS:
        if key in old_payload:
            merged[key] = old_payload.get(key)
    old_tags = set(old_payload.get("tags") or [])
    old_model_tags = {
        tag
        for tag in old_tags
        if tag in set(old_payload.get("providers") or [])
        or tag in set(old_payload.get("billing_types") or [])
        or tag in MODEL_PRICE_TAGS
    }
    next_tags = set(site.get("tags") or [])
    next_tags.difference_update(MODEL_PRICE_TAGS)
    next_tags.difference_update(set(site.get("providers") or []))
    next_tags.difference_update(set(site.get("billing_types") or []))
    merged["tags"] = sorted(next_tags | old_model_tags)
    return merged


def classify_site_model_updates(cur, server, generation_id, sites):
    site_ids = {site["id"] for site in sites}
    existing_payloads = load_existing_site_payloads(cur, generation_id, site_ids)
    replace_sites = []
    preserved_sites = []
    for site in sites:
        old_payload = existing_payloads.get(site["id"]) or {}
        old_model_count = int(old_payload.get("model_count") or 0)
        new_model_count = int(site.get("model_count") or 0)
        if old_model_count > 0 and new_model_count == 0:
            preserved_sites.append(preserve_existing_model_payload(site, old_payload))
        else:
            replace_sites.append(site)
    merged_sites = replace_sites + preserved_sites
    replace_site_ids = {site["id"] for site in replace_sites}
    old_keys = load_old_canonical_keys(cur, server, generation_id, replace_site_ids)
    new_keys = set()
    for site in replace_sites:
        new_keys.update(site_model_keys(server, site))
    return merged_sites, replace_sites, preserved_sites, site_ids, replace_site_ids, old_keys | new_keys


def delete_site_models(cur, generation_id, site_ids):
    if not site_ids:
        return
    cur.execute(
        "DELETE FROM site_models WHERE generation_id = %s AND site_id = ANY(%s)",
        (generation_id, list(site_ids)),
    )


def upsert_sites(cur, Jsonb, generation_id, sites):
    if not sites:
        return
    cur.execute("SELECT COALESCE(max(sort_index), 0) AS max_sort FROM sites WHERE generation_id = %s", (generation_id,))
    base_sort = int((cur.fetchone() or {}).get("max_sort") or 0)
    rows = []
    for index, site in enumerate(sites, 1):
        origin = site.get("origin") or ""
        rows.append(
            (
                generation_id,
                site.get("id"),
                origin,
                site.get("domain") or dbload.host_from_origin(origin),
                site.get("root_domain"),
                site.get("name") or dbload.host_from_origin(origin),
                base_sort + index,
                site.get("status") or "unknown",
                site.get("registration_status") or "unknown",
                site.get("register_enabled"),
                site.get("password_register_enabled"),
                int(site.get("model_count") or 0),
                dbload.numeric(site.get("lowest_ratio")),
                site.get("notice"),
                site.get("notifications"),
                site.get("notice_tags") or [],
                site.get("tags") or [],
                site.get("providers") or [],
                site.get("groups") or [],
                site.get("billing_types") or [],
                site.get("status_status"),
                site.get("home_page_content_status"),
                site.get("pricing_status"),
                site.get("notice_status"),
                site.get("ratio_status"),
                dbload.parse_timestamp(site.get("updated_at")),
                site.get("currency_symbol"),
                dbload.numeric(site.get("currency_unit_price")),
                dbload.numeric(site.get("quota_per_unit")),
                dbload.numeric(site.get("token_price_multiplier")),
                dbload.numeric(site.get("request_price_multiplier")),
                bool(site.get("display_in_currency", True)),
                Jsonb(site),
            )
        )
    cur.executemany(
        """
        INSERT INTO sites (
          generation_id, id, origin, domain, root_domain, name, sort_index, status,
          registration_status, register_enabled, password_register_enabled, model_count,
          lowest_ratio, notice, notifications, notice_tags, tags, providers, groups,
          billing_types, status_status, home_page_content_status, pricing_status,
          notice_status, ratio_status, updated_at, currency_symbol, currency_unit_price,
          quota_per_unit, token_price_multiplier, request_price_multiplier,
          display_in_currency, payload
        )
        VALUES (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (generation_id, id)
        DO UPDATE SET
          origin = EXCLUDED.origin,
          domain = EXCLUDED.domain,
          root_domain = EXCLUDED.root_domain,
          name = EXCLUDED.name,
          sort_index = COALESCE(sites.sort_index, EXCLUDED.sort_index),
          status = EXCLUDED.status,
          registration_status = EXCLUDED.registration_status,
          register_enabled = EXCLUDED.register_enabled,
          password_register_enabled = EXCLUDED.password_register_enabled,
          model_count = EXCLUDED.model_count,
          lowest_ratio = EXCLUDED.lowest_ratio,
          notice = EXCLUDED.notice,
          notifications = EXCLUDED.notifications,
          notice_tags = EXCLUDED.notice_tags,
          tags = EXCLUDED.tags,
          providers = EXCLUDED.providers,
          groups = EXCLUDED.groups,
          billing_types = EXCLUDED.billing_types,
          status_status = EXCLUDED.status_status,
          home_page_content_status = EXCLUDED.home_page_content_status,
          pricing_status = EXCLUDED.pricing_status,
          notice_status = EXCLUDED.notice_status,
          ratio_status = EXCLUDED.ratio_status,
          updated_at = EXCLUDED.updated_at,
          currency_symbol = EXCLUDED.currency_symbol,
          currency_unit_price = EXCLUDED.currency_unit_price,
          quota_per_unit = EXCLUDED.quota_per_unit,
          token_price_multiplier = EXCLUDED.token_price_multiplier,
          request_price_multiplier = EXCLUDED.request_price_multiplier,
          display_in_currency = EXCLUDED.display_in_currency,
          payload = EXCLUDED.payload
        """,
        rows,
    )


def insert_site_models(cur, Jsonb, generation_id, sites):
    inserted = 0
    for site in sites:
        site_id = site.get("id")
        status = site.get("status")
        for model in site.get("models") or []:
            payload = dict(model)
            cur.execute(
                """
                INSERT INTO site_models (
                  generation_id, site_id, model, raw_model, provider, model_ratio,
                  completion_ratio, cache_ratio, create_cache_ratio, model_price, quota_type,
                  min_group_ratio, currency_symbol, currency_unit_price, quota_per_unit,
                  token_price_multiplier, request_price_multiplier, display_in_currency,
                  avg_latency_ms, success_rate, avg_tps, status, payload
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    generation_id,
                    site_id,
                    model.get("model"),
                    model.get("raw_model"),
                    model.get("provider") or "Other",
                    dbload.numeric(model.get("model_ratio")),
                    dbload.numeric(model.get("completion_ratio")),
                    dbload.numeric(model.get("cache_ratio")),
                    dbload.numeric(model.get("create_cache_ratio")),
                    dbload.numeric(model.get("model_price")),
                    model.get("quota_type"),
                    dbload.numeric(model.get("min_group_ratio")),
                    model.get("currency_symbol"),
                    dbload.numeric(model.get("currency_unit_price")),
                    dbload.numeric(model.get("quota_per_unit")),
                    dbload.numeric(model.get("token_price_multiplier")),
                    dbload.numeric(model.get("request_price_multiplier")),
                    bool(model.get("display_in_currency", True)),
                    dbload.numeric(model.get("avg_latency_ms")),
                    dbload.numeric(model.get("success_rate")),
                    dbload.numeric(model.get("avg_tps")),
                    status,
                    Jsonb(payload),
                ),
            )
            model_id = cur.fetchone()["id"]
            inserted += 1
            group_rows = [
                (model_id, group_name, dbload.numeric(ratio))
                for group_name, ratio in (model.get("group_ratios") or {}).items()
                if group_name
            ]
            if group_rows:
                cur.executemany(
                    """
                    INSERT INTO site_model_groups (site_model_id, group_name, group_ratio)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    group_rows,
                )
    return inserted


def load_similar_notice_index(cur):
    cur.execute(
        """
        SELECT id, origin, content, first_seen_at
        FROM announcements
        WHERE source = 'notice'
        """
    )
    index = defaultdict(list)
    for row in cur.fetchall():
        index[row["origin"] or ""].append(
            {
                "id": row["id"],
                "content": row["content"] or "",
                "first_seen_at": row["first_seen_at"],
            }
        )
    return index


def upsert_announcements(cur, Jsonb, generation_id, site_ids, announcements_by_site):
    if site_ids:
        cur.execute(
            """
            UPDATE announcements
            SET is_active = false
            WHERE site_id = ANY(%s)
              AND source IN ('notice', 'status_announcements')
            """,
            (list(site_ids),),
        )
    if not announcements_by_site:
        return 0
    similar_notice_index = load_similar_notice_index(cur)
    total = 0
    for site_id, announcements in announcements_by_site.items():
        rows = []
        for index, item in enumerate(announcements):
            item = dict(item)
            item["id"] = dbload.resolve_similar_announcement_id(item, similar_notice_index)
            if (item.get("source") or "unknown") == "notice" and (item.get("time_source") or "observed") == "observed":
                similar_notice_index[item.get("origin") or ""].append(
                    {
                        "id": item.get("id"),
                        "content": item.get("content") or "",
                        "first_seen_at": dbload.parse_timestamp(item.get("first_seen_at")),
                    }
                )
            rows.append(
                (
                    item.get("id"),
                    index,
                    item.get("site_id"),
                    item.get("site_name"),
                    item.get("origin"),
                    item.get("content_hash"),
                    item.get("content") or "",
                    item.get("tags") or [],
                    item.get("registration_status") or "unknown",
                    dbload.parse_timestamp(item.get("first_seen_at")),
                    generation_id,
                    generation_id,
                    item.get("source") or "unknown",
                    item.get("source_type"),
                    item.get("source_id"),
                    str(item.get("source_id") or ""),
                    Jsonb(item),
                )
            )
        if rows:
            cur.executemany(
                """
                INSERT INTO announcements (
                  id, sort_index, site_id, site_name, origin, content_hash, content, tags,
                  registration_status, first_seen_at, first_generation_id, last_generation_id,
                  is_active, source, source_type, source_id, source_key, payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, %s)
                ON CONFLICT (id)
                DO UPDATE SET
                  sort_index = EXCLUDED.sort_index,
                  last_seen_at = now(),
                  last_generation_id = EXCLUDED.last_generation_id,
                  is_active = true,
                  site_name = EXCLUDED.site_name,
                  origin = EXCLUDED.origin,
                  content = EXCLUDED.content,
                  tags = EXCLUDED.tags,
                  registration_status = EXCLUDED.registration_status,
                  first_seen_at = CASE
                    WHEN announcements.first_seen_at > now() + interval '5 minutes'
                      AND EXCLUDED.first_seen_at <= now() + interval '5 minutes'
                    THEN EXCLUDED.first_seen_at
                    ELSE COALESCE(announcements.first_seen_at, EXCLUDED.first_seen_at)
                  END,
                  first_generation_id = COALESCE(announcements.first_generation_id, EXCLUDED.first_generation_id),
                  source = EXCLUDED.source,
                  source_type = EXCLUDED.source_type,
                  source_id = EXCLUDED.source_id,
                  source_key = EXCLUDED.source_key,
                  payload = EXCLUDED.payload
                """,
                rows,
            )
            total += len(rows)
    return total


def site_payload_for_model(site_row, model_row):
    payload = dict(model_row["payload"] or {})
    site_payload = dict(site_row["site_payload"] or {})
    payload.update(
        {
            "site_id": site_row["site_id"],
            "site_name": site_row["site_name"],
            "origin": site_row["origin"],
            "model": model_row["model"],
            "raw_model": model_row["raw_model"],
            "provider": model_row["provider"],
            "status": site_row["site_status"],
            "registration_status": site_row["registration_status"],
            "_site_model_id": model_row["site_model_id"],
        }
    )
    for key in [
        "currency_symbol",
        "currency_unit_price",
        "quota_per_unit",
        "token_price_multiplier",
        "request_price_multiplier",
        "display_in_currency",
    ]:
        if payload.get(key) is None and site_payload.get(key) is not None:
            payload[key] = site_payload.get(key)
    return payload


def raw_models_from_entries(entries):
    raw_models = []
    for entry in entries:
        raw_models.append(
            {
                "model": entry.get("model") or "",
                "provider": entry.get("provider") or "Other",
                "sites": [entry],
            }
        )
    return raw_models


def load_existing_canonical_entries(cur, generation_id, affected_keys, site_ids):
    entries_by_key = defaultdict(list)
    old_ids = []
    old_sort_indexes = {}
    for provider_lower, canonical_key in sorted(affected_keys):
        cur.execute(
            """
            SELECT
              cm.id AS canonical_model_id,
              cm.sort_index,
              cms.site_model_id,
              cms.site_id,
              cms.site_payload
            FROM canonical_models cm
            LEFT JOIN canonical_model_sites cms
              ON cms.generation_id = cm.generation_id
             AND cms.canonical_model_id = cm.id
            WHERE cm.generation_id = %s
              AND lower(cm.provider) = %s
              AND cm.canonical_key = %s
            """,
            (generation_id, provider_lower, canonical_key),
        )
        key = (provider_lower, canonical_key)
        for row in cur.fetchall():
            old_ids.append(row["canonical_model_id"])
            if row["sort_index"] is not None:
                old_sort_indexes[key] = min(row["sort_index"], old_sort_indexes.get(key, row["sort_index"]))
            if not row["site_id"] or row["site_id"] in site_ids:
                continue
            payload = dict(row["site_payload"] or {})
            payload["_site_model_id"] = row["site_model_id"]
            entries_by_key[key].append(payload)
    return entries_by_key, sorted(set(old_ids)), old_sort_indexes


def load_changed_site_model_entries(cur, server, generation_id, affected_keys, site_ids):
    entries_by_key = defaultdict(list)
    if not site_ids:
        return entries_by_key
    cur.execute(
        """
        SELECT
          sm.id AS site_model_id,
          sm.model,
          sm.raw_model,
          sm.provider,
          sm.payload,
          s.id AS site_id,
          s.name AS site_name,
          s.origin,
          s.status AS site_status,
          s.registration_status,
          s.payload AS site_payload
        FROM site_models sm
        JOIN sites s
          ON s.generation_id = sm.generation_id
         AND s.id = sm.site_id
        WHERE sm.generation_id = %s
          AND sm.site_id = ANY(%s)
        """,
        (generation_id, list(site_ids)),
    )
    for row in cur.fetchall():
        key = canonical_key_for(server, row["provider"], row["model"])
        if key in affected_keys:
            entries_by_key[key].append(site_payload_for_model(row, row))
    return entries_by_key


def delete_old_canonical_models(cur, generation_id, old_ids):
    if not old_ids:
        return
    cur.execute(
        "DELETE FROM canonical_model_sites WHERE generation_id = %s AND canonical_model_id = ANY(%s)",
        (generation_id, old_ids),
    )
    cur.execute(
        "DELETE FROM canonical_models WHERE generation_id = %s AND id = ANY(%s)",
        (generation_id, old_ids),
    )


def insert_canonical_payload(cur, Jsonb, server, generation_id, sort_index, provider_lower, canonical_key, model_payload):
    cur.execute(
        """
        INSERT INTO canonical_models (
          generation_id, sort_index, provider, canonical_key, display_model, aliases,
          site_count, min_ratio, max_success_rate, min_latency_ms, max_tps,
          perf_site_count, release_sort_key, payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            generation_id,
            sort_index,
            model_payload.get("provider") or provider_lower.title() or "Other",
            canonical_key,
            model_payload.get("model") or canonical_key,
            model_payload.get("aliases") or [],
            int(model_payload.get("site_count") or 0),
            dbload.numeric(model_payload.get("min_ratio")),
            dbload.numeric(model_payload.get("success_rate")),
            dbload.numeric(model_payload.get("avg_latency_ms")),
            dbload.numeric(model_payload.get("avg_tps")),
            int(model_payload.get("perf_site_count") or 0),
            Jsonb(server.model_release_sort_key(model_payload.get("model") or canonical_key)),
            Jsonb(model_payload),
        ),
    )
    canonical_id = cur.fetchone()["id"]
    cms_rows = []
    for site_index, site in enumerate(model_payload.get("sites") or []):
        bucket = dbload.billing_bucket(site)
        site_model_id = site.get("_site_model_id")
        stored_site = {key: value for key, value in site.items() if key != "_site_model_id"}
        cms_rows.append(
            (
                generation_id,
                canonical_id,
                site_model_id,
                site_index,
                site.get("site_id"),
                site.get("provider") or model_payload.get("provider") or "Other",
                site.get("model") or model_payload.get("model"),
                bucket,
                dbload.request_price(site) if bucket == "request" else dbload.input_price(site),
                None if bucket == "request" else dbload.output_price(site),
                None if bucket == "request" else dbload.cache_input_price(site),
                None if bucket == "request" else dbload.cache_write_price(site),
                dbload.numeric(site.get("success_rate")),
                dbload.numeric(site.get("avg_latency_ms")),
                dbload.numeric(site.get("avg_tps")),
                Jsonb(stored_site),
            )
        )
    if cms_rows:
        cur.executemany(
            """
            INSERT INTO canonical_model_sites (
              generation_id, canonical_model_id, site_model_id, sort_index, site_id, provider, model,
              billing_bucket, input_price, output_price, cache_input_price,
              cache_write_price, success_rate, avg_latency_ms, avg_tps, site_payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            cms_rows,
        )


def rebuild_canonical_keys(cur, Jsonb, server, generation_id, affected_keys, site_ids):
    if not affected_keys:
        return 0
    entries_by_key, old_ids, old_sort_indexes = load_existing_canonical_entries(cur, generation_id, affected_keys, site_ids)
    changed_entries = load_changed_site_model_entries(cur, server, generation_id, affected_keys, site_ids)
    for key, entries in changed_entries.items():
        entries_by_key[key].extend(entries)

    delete_old_canonical_models(cur, generation_id, old_ids)

    cur.execute("SELECT COALESCE(max(sort_index), 0) AS max_sort FROM canonical_models WHERE generation_id = %s", (generation_id,))
    next_sort = int((cur.fetchone() or {}).get("max_sort") or 0) + 1
    rebuilt = 0
    for key in sorted(affected_keys):
        entries = entries_by_key.get(key) or []
        if not entries:
            continue
        merged = server.merge_equivalent_models(raw_models_from_entries(entries))
        for model_payload in merged:
            payload_key = canonical_key_for(server, model_payload.get("provider"), model_payload.get("model"))
            if payload_key != key:
                continue
            sort_index = old_sort_indexes.get(key)
            if sort_index is None:
                sort_index = next_sort
                next_sort += 1
            insert_canonical_payload(cur, Jsonb, server, generation_id, sort_index, key[0], key[1], model_payload)
            rebuilt += 1
    return rebuilt


def update_summary_and_version(cur, Jsonb, generation_id, generated_at):
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
    meta = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "sites": row["sites"],
        "online_sites": row["online_sites"],
        "models": row["models"],
        "announcements": row["announcements"],
        "incremental": True,
    }
    cur.execute(
        """
        UPDATE data_generations
        SET site_count = %s,
            online_site_count = %s,
            model_count = %s,
            announcement_count = %s,
            meta = %s,
            finished_at = now()
        WHERE id = %s
        """,
        (row["sites"], row["online_sites"], row["models"], row["announcements"], Jsonb(meta), generation_id),
    )
    data_version = str(int(datetime.now(timezone.utc).timestamp() * 1000000))
    cur.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES ('active_data_version', %s, now())
        ON CONFLICT (key)
        DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """,
        (data_version,),
    )
    return meta, data_version


def run(args):
    conninfo = args.database_url or os.environ.get("DATABASE_URL")
    if not conninfo:
        raise SystemExit("DATABASE_URL is required.")
    server = import_server_helpers(conninfo)
    origins = read_origin_set(args.changed_origins)
    rows = list(iter_selected_rows(args.raw, origins))
    sites, announcements_by_site, generated_at = normalize_rows(rows)
    if args.limit:
        sites = sites[: args.limit]
        keep = {site["id"] for site in sites}
        announcements_by_site = {site_id: items for site_id, items in announcements_by_site.items() if site_id in keep}
    if not sites:
        print(json.dumps({"updated_sites": 0, "affected_models": 0, "announcements": 0}, ensure_ascii=False))
        return

    psycopg, Jsonb, dict_row = import_psycopg()
    result = None
    with psycopg.connect(conninfo, row_factory=dict_row, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value::bigint AS id FROM app_state WHERE key = 'active_generation_id'")
            row = cur.fetchone()
            if not row:
                raise SystemExit("No active database generation")
            generation_id = row["id"]
            sites, replace_sites, preserved_sites, site_ids, replace_site_ids, affected_keys = classify_site_model_updates(
                cur,
                server,
                generation_id,
                sites,
            )
            announcement_total = sum(len(items) for items in announcements_by_site.values())
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "generation_id": generation_id,
                            "updated_sites": len(sites),
                            "model_preserved_sites": len(preserved_sites),
                            "affected_models": len(affected_keys),
                            "announcements": announcement_total,
                            "dry_run": True,
                        },
                        ensure_ascii=False,
                    )
                )
                return

        with conn.transaction():
            with conn.cursor() as cur:

                delete_site_models(cur, generation_id, replace_site_ids)
                upsert_sites(cur, Jsonb, generation_id, sites)
                inserted_models = insert_site_models(cur, Jsonb, generation_id, replace_sites)
                announcement_count = upsert_announcements(cur, Jsonb, generation_id, site_ids, announcements_by_site)
                rebuilt = rebuild_canonical_keys(cur, Jsonb, server, generation_id, affected_keys, replace_site_ids)
                meta, data_version = update_summary_and_version(cur, Jsonb, generation_id, generated_at)
                result = {
                    "generation_id": generation_id,
                    "data_version": data_version,
                    "updated_sites": len(sites),
                    "model_preserved_sites": len(preserved_sites),
                    "inserted_site_models": inserted_models,
                    "affected_models": len(affected_keys),
                    "rebuilt_models": rebuilt,
                    "announcements": announcement_count,
                    "summary": meta,
                }
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--raw", default="../api_config_results.jsonl")
    parser.add_argument("--changed-origins", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
