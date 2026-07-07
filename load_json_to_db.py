import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse


TOKEN_PRICE_MULTIPLIER = 14
REQUEST_PRICE_MULTIPLIER = 7
SECOND_LEVEL_SUFFIXES = {"ac", "co", "com", "edu", "gov", "net", "org"}
COUNTRY_SUFFIXES = {"au", "br", "cn", "hk", "in", "jp", "kr", "nz", "sg", "tw", "uk", "za"}
ANNOUNCEMENT_SIMILARITY_THRESHOLD = 0.94
ANNOUNCEMENT_SIMILARITY_MIN_CHARS = 20
DB_INSERT_BATCH_SIZE = int(os.environ.get("RELAYWATCH_DB_INSERT_BATCH_SIZE", "2000"))


def import_psycopg():
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: psycopg. Install with `pip install psycopg[binary]`."
        ) from exc
    return psycopg, Jsonb


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def batched_executemany(cur, sql, rows, batch_size=DB_INSERT_BATCH_SIZE):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            cur.executemany(sql, batch)
            batch.clear()
    if batch:
        cur.executemany(sql, batch)


def strip_nul(value):
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, set):
        return [strip_nul(item) for item in sorted(value, key=str)]
    if isinstance(value, list):
        return [strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {
            strip_nul(key): strip_nul(item)
            for key, item in value.items()
            if not (isinstance(key, str) and key.startswith("_"))
        }
    return value


def read_data_dir(data_dir):
    root = Path(data_dir)
    return {
        "sites": strip_nul(load_json(root / "sites.json")),
        "models": strip_nul(load_json(root / "models.json")),
        "announcements": strip_nul(load_json(root / "announcements.json")),
        "summary": strip_nul(load_json(root / "summary.json")),
    }


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


def dedupe_sites_for_import(sites):
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


def load_api_model_index(data_dir):
    module_data_dir = Path(__file__).parent / "data"
    if Path(data_dir).resolve() != module_data_dir.resolve():
        return None
    old_database_url = os.environ.pop("DATABASE_URL", None)
    old_models_only = os.environ.get("RELAYWATCH_MODELS_ONLY")
    os.environ["RELAYWATCH_MODELS_ONLY"] = "1"
    try:
        import server
        return strip_nul(server.MODELS)
    finally:
        if old_database_url is not None:
            os.environ["DATABASE_URL"] = old_database_url
        if old_models_only is None:
            os.environ.pop("RELAYWATCH_MODELS_ONLY", None)
        else:
            os.environ["RELAYWATCH_MODELS_ONLY"] = old_models_only


def numeric(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def host_from_origin(origin):
    return urlparse(origin or "").netloc.split("@")[-1].split(":")[0].lower().strip(".")


def group_ratio(item, group=None):
    ratios = item.get("group_ratios") or {}
    if group and group in ratios:
        value = numeric(ratios.get(group))
        if value is not None:
            return value
    value = numeric(item.get("min_group_ratio"))
    return 1.0 if value is None else value


def multiplied(left, right):
    left = numeric(left)
    right = numeric(right)
    if left is None or right is None:
        return None
    return left * right


def effective_ratio(item, group=None):
    return multiplied(item.get("model_ratio"), group_ratio(item, group))


def token_multiplier(item):
    value = numeric(item.get("token_price_multiplier"))
    return TOKEN_PRICE_MULTIPLIER if value is None else value


def request_multiplier(item):
    value = numeric(item.get("request_price_multiplier"))
    return REQUEST_PRICE_MULTIPLIER if value is None else value


def input_price(item, group=None):
    return multiplied(effective_ratio(item, group), token_multiplier(item))


def output_price(item, group=None):
    return multiplied(input_price(item, group), item.get("completion_ratio"))


def cache_input_price(item, group=None):
    return multiplied(input_price(item, group), item.get("cache_ratio"))


def cache_write_price(item, group=None):
    return multiplied(input_price(item, group), item.get("create_cache_ratio"))


def request_price(item, group=None):
    price = numeric(item.get("model_price"))
    if price is None or price <= 0:
        return None
    return multiplied(multiplied(price, group_ratio(item, group)), request_multiplier(item))


def billing_bucket(item):
    if request_price(item) is not None:
        return "request"
    if item.get("currency_symbol") == "$":
        return "usd"
    return "cny"


def parse_timestamp(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed


def normalize_announcement_text(value):
    text = str(value or "").lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。；：、,.!！?？|｜`*_#>\-—~～\[\]()（）{}【】<>《》\"']+", "", text)
    return text


def announcement_numbers(value):
    return re.findall(r"\d+(?:\.\d+)?", str(value or ""))


def announcement_similarity(left, right):
    left_norm = normalize_announcement_text(left)
    right_norm = normalize_announcement_text(right)
    if len(left_norm) < ANNOUNCEMENT_SIMILARITY_MIN_CHARS or len(right_norm) < ANNOUNCEMENT_SIMILARITY_MIN_CHARS:
        return 0
    if announcement_numbers(left) != announcement_numbers(right):
        return 0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def is_similar_announcement(left, right):
    return announcement_similarity(left, right) >= ANNOUNCEMENT_SIMILARITY_THRESHOLD


def load_similar_notice_index(cur):
    cur.execute(
        """
        SELECT id, origin, content, first_seen_at
        FROM announcements
        WHERE source = 'notice'
          AND length(content) >= %s
          AND COALESCE(payload->>'time_source', 'observed') = 'observed'
        ORDER BY origin, first_seen_at NULLS LAST, id
        """,
        (ANNOUNCEMENT_SIMILARITY_MIN_CHARS,),
    )
    index = defaultdict(list)
    for row in cur.fetchall():
        origin = row[1]
        index[origin].append(
            {
                "id": row[0],
                "content": row[2] or "",
                "first_seen_at": row[3],
            }
        )
    return index


def resolve_similar_announcement_id(item, similar_notice_index):
    if (item.get("time_source") or "observed") != "observed":
        return item.get("id")
    if (item.get("source") or "unknown") != "notice":
        return item.get("id")
    content = item.get("content") or ""
    if len(normalize_announcement_text(content)) < ANNOUNCEMENT_SIMILARITY_MIN_CHARS:
        return item.get("id")
    candidates = similar_notice_index.get(item.get("origin") or "") or []
    best = None
    best_score = 0
    for candidate in candidates:
        score = announcement_similarity(content, candidate["content"])
        if score > best_score:
            best = candidate
            best_score = score
    if best and best_score >= ANNOUNCEMENT_SIMILARITY_THRESHOLD:
        return best["id"]
    return item.get("id")


def run_schema(conn, schema_path):
    sql = Path(schema_path).read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)


def create_generation(cur, Jsonb, kind, summary):
    cur.execute(
        """
        INSERT INTO data_generations
          (kind, status, site_count, online_site_count, model_count, announcement_count, meta)
        VALUES (%s, 'building', %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            kind,
            int(summary.get("sites") or 0),
            int(summary.get("online_sites") or 0),
            int(summary.get("models") or 0),
            int(summary.get("announcements") or 0),
            Jsonb(summary),
        ),
    )
    return cur.fetchone()[0]


def insert_sites(cur, Jsonb, generation_id, sites):
    rows = []
    for index, site in enumerate(sites):
        origin = site.get("origin") or ""
        rows.append(
            (
                generation_id,
                site.get("id"),
                origin,
                site.get("domain") or host_from_origin(origin),
                site.get("root_domain"),
                site.get("name") or host_from_origin(origin),
                index,
                site.get("status") or "unknown",
                site.get("registration_status") or "unknown",
                site.get("register_enabled"),
                site.get("password_register_enabled"),
                int(site.get("model_count") or 0),
                numeric(site.get("lowest_ratio")),
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
                parse_timestamp(site.get("updated_at")),
                site.get("currency_symbol"),
                numeric(site.get("currency_unit_price")),
                numeric(site.get("quota_per_unit")),
                numeric(site.get("token_price_multiplier")),
                numeric(site.get("request_price_multiplier")),
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
        """,
        rows,
    )


def insert_site_models(cur, Jsonb, generation_id, sites):
    insert_sql = """
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
        ON CONFLICT DO NOTHING
    """

    def iter_model_rows():
        for site in sites:
            site_id = site.get("id")
            status = site.get("status")
            for model in site.get("models") or []:
                payload = dict(model)
                yield (
                    generation_id,
                    site_id,
                    model.get("model"),
                    model.get("raw_model"),
                    model.get("provider") or "Other",
                    numeric(model.get("model_ratio")),
                    numeric(model.get("completion_ratio")),
                    numeric(model.get("cache_ratio")),
                    numeric(model.get("create_cache_ratio")),
                    numeric(model.get("model_price")),
                    model.get("quota_type"),
                    numeric(model.get("min_group_ratio")),
                    model.get("currency_symbol"),
                    numeric(model.get("currency_unit_price")),
                    numeric(model.get("quota_per_unit")),
                    numeric(model.get("token_price_multiplier")),
                    numeric(model.get("request_price_multiplier")),
                    bool(model.get("display_in_currency", True)),
                    numeric(model.get("avg_latency_ms")),
                    numeric(model.get("success_rate")),
                    numeric(model.get("avg_tps")),
                    status,
                    Jsonb(payload),
                )

    batched_executemany(cur, insert_sql, iter_model_rows())

    cur.execute(
        """
        SELECT id, site_id, provider, model, COALESCE(raw_model, '')
        FROM site_models
        WHERE generation_id = %s
        """,
        (generation_id,),
    )
    id_by_key = {
        (site_id, provider, model, raw_model or ""): model_id
        for model_id, site_id, provider, model, raw_model in cur.fetchall()
    }
    def iter_group_rows():
        for site in sites:
            site_id = site.get("id")
            for model in site.get("models") or []:
                key = (
                    site_id,
                    model.get("provider") or "Other",
                    model.get("model"),
                    model.get("raw_model") or "",
                )
                model_id = id_by_key.get(key)
                if not model_id:
                    continue
                for group_name, ratio in (model.get("group_ratios") or {}).items():
                    if group_name:
                        yield (model_id, group_name, numeric(ratio))

    batched_executemany(
        cur,
        """
        INSERT INTO site_model_groups (site_model_id, group_name, group_ratio)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        iter_group_rows(),
    )


def insert_announcements(cur, Jsonb, generation_id, announcements):
    similar_notice_index = load_similar_notice_index(cur)
    cur.execute("UPDATE announcements SET is_active = false")

    def iter_rows():
        for index, item in enumerate(announcements):
            item = dict(item)
            item["id"] = resolve_similar_announcement_id(item, similar_notice_index)
            if (item.get("source") or "unknown") == "notice" and (item.get("time_source") or "observed") == "observed":
                similar_notice_index[item.get("origin") or ""].append(
                    {
                        "id": item.get("id"),
                        "content": item.get("content") or "",
                        "first_seen_at": parse_timestamp(item.get("first_seen_at")),
                    }
                )
            yield (
                item.get("id"),
                index,
                item.get("site_id"),
                item.get("site_name"),
                item.get("origin"),
                item.get("content_hash"),
                item.get("content") or "",
                item.get("tags") or [],
                item.get("registration_status") or "unknown",
                parse_timestamp(item.get("first_seen_at")),
                generation_id,
                generation_id,
                item.get("source") or "unknown",
                item.get("source_type"),
                item.get("source_id"),
                str(item.get("source_id") or ""),
                Jsonb(item),
            )

    batched_executemany(
        cur,
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
        iter_rows(),
    )


def insert_canonical_models(cur, Jsonb, generation_id, models):
    for index, model in enumerate(models):
        canonical_key = (model.get("model") or "").lower()
        cur.execute(
            """
            INSERT INTO canonical_models (
              generation_id, sort_index, provider, canonical_key, display_model, aliases,
              site_count, min_ratio, max_success_rate, min_latency_ms, max_tps,
              perf_site_count, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                generation_id,
                index,
                model.get("provider") or "Other",
                canonical_key,
                model.get("model") or canonical_key,
                model.get("aliases") or [],
                int(model.get("site_count") or 0),
                numeric(model.get("min_ratio")),
                numeric(model.get("success_rate")),
                numeric(model.get("avg_latency_ms")),
                numeric(model.get("avg_tps")),
                int(model.get("perf_site_count") or 0),
                Jsonb(model),
            ),
        )
        canonical_id = cur.fetchone()[0]

        def iter_site_rows():
            for site_index, site in enumerate(model.get("sites") or []):
                bucket = billing_bucket(site)
                yield (
                    generation_id,
                    canonical_id,
                    site_index,
                    site.get("site_id"),
                    site.get("provider") or model.get("provider") or "Other",
                    site.get("model") or model.get("model"),
                    bucket,
                    request_price(site) if bucket == "request" else input_price(site),
                    None if bucket == "request" else output_price(site),
                    None if bucket == "request" else cache_input_price(site),
                    None if bucket == "request" else cache_write_price(site),
                    numeric(site.get("success_rate")),
                    numeric(site.get("avg_latency_ms")),
                    numeric(site.get("avg_tps")),
                    Jsonb(site),
                )

        batched_executemany(
            cur,
            """
            INSERT INTO canonical_model_sites (
              generation_id, canonical_model_id, sort_index, site_id, provider, model,
              billing_bucket, input_price, output_price, cache_input_price,
              cache_write_price, success_rate, avg_latency_ms, avg_tps, site_payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            iter_site_rows(),
        )


def activate_generation(cur, generation_id):
    cur.execute(
        """
        UPDATE data_generations
        SET status = 'archived'
        WHERE status = 'active' AND id <> %s
        """,
        (generation_id,),
    )
    cur.execute(
        """
        UPDATE data_generations
        SET status = 'active', finished_at = now()
        WHERE id = %s
        """,
        (generation_id,),
    )
    cur.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES ('active_generation_id', %s, now())
        ON CONFLICT (key)
        DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """,
        (str(generation_id),),
    )
    cur.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES ('active_data_version', (extract(epoch from clock_timestamp()) * 1000000)::bigint::text, now())
        ON CONFLICT (key)
        DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """
    )


def cleanup_old_generations(cur, keep_generations):
    if keep_generations <= 0:
        return 0
    cur.execute(
        """
        WITH keep AS (
          SELECT id
          FROM data_generations
          WHERE status IN ('active', 'archived')
          ORDER BY id DESC
          LIMIT %s
        ),
        removed AS (
          DELETE FROM data_generations
          WHERE id NOT IN (SELECT id FROM keep)
          RETURNING id
        )
        SELECT count(*) FROM removed
        """,
        (keep_generations,),
    )
    row = cur.fetchone()
    return row[0] if row else 0


def import_data(args):
    conninfo = args.database_url or os.environ.get("DATABASE_URL")
    if not conninfo:
        raise SystemExit("DATABASE_URL is required.")
    psycopg, Jsonb = import_psycopg()
    data = read_data_dir(args.data_dir)
    data["sites"] = dedupe_sites_for_import(data["sites"])
    api_models = load_api_model_index(args.data_dir)
    if api_models is not None:
        data["models"] = api_models
    data["summary"]["sites"] = len(data["sites"])
    data["summary"]["online_sites"] = sum(1 for item in data["sites"] if item.get("status") == "online")
    data["summary"]["models"] = len(data["models"])
    schema_path = args.schema or Path(__file__).with_name("db_schema.sql")

    with psycopg.connect(conninfo) as conn:
        with conn.transaction():
            run_schema(conn, schema_path)
        with conn.transaction():
            with conn.cursor() as cur:
                generation_id = create_generation(cur, Jsonb, args.kind, data["summary"])
                insert_sites(cur, Jsonb, generation_id, data["sites"])
                insert_site_models(cur, Jsonb, generation_id, data["sites"])
                insert_announcements(cur, Jsonb, generation_id, data["announcements"])
                insert_canonical_models(cur, Jsonb, generation_id, data["models"])
                activate_generation(cur, generation_id)
                removed_generations = cleanup_old_generations(cur, args.keep_generations)
    print(
        json.dumps(
            {
                "generation_id": generation_id,
                "removed_generations": removed_generations,
                "sites": len(data["sites"]),
                "models": len(data["models"]),
                "announcements": len(data["announcements"]),
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--data-dir", default=str(Path(__file__).parent / "data"))
    parser.add_argument("--schema", default=None)
    parser.add_argument("--kind", default="json_import")
    parser.add_argument(
        "--keep-generations",
        type=int,
        default=int(os.environ.get("RELAYWATCH_KEEP_GENERATIONS", "3")),
    )
    args = parser.parse_args()
    import_data(args)


if __name__ == "__main__":
    main()
