# RelayWatch Database Design Draft

本文档用于把当前 JSON/内存数据流迁移到数据库前先理顺结构、约束和查询语句。

## Current Pipeline

```text
FOFA/Shodan/raw origins
  -> collect_api_configs_httpx.py
  -> api_config_results.jsonl
  -> relaywatch/normalize_data.py
  -> relaywatch/data/sites.json
  -> relaywatch/data/models.json
  -> relaywatch/data/announcements.json
  -> relaywatch/data/summary.json
  -> relaywatch/server.py startup load into memory
```

当前服务启动时一次性读取 `relaywatch/data/*.json` 到内存：

- `sites.json`: 站点维度，包含站点基础信息、公告摘要、模型预览、最多 120 个模型明细。
- `models.json`: 模型比价维度，包含 canonical/聚合后的模型和站点报价列表。
- `announcements.json`: 公告流。
- `summary.json`: 汇总统计。

数据库后目标链路：

```text
collect api_config_results.jsonl
  -> normalize/write_db
  -> PostgreSQL
  -> FastAPI paginated SQL
  -> frontend
```

`api_config_results.jsonl` 继续保留，作为原始快照和可重放来源。

## PostgreSQL DDL

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE TABLE scan_runs (
  id BIGSERIAL PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  collector TEXT NOT NULL DEFAULT 'httpx',
  source_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  origin_groups_total INTEGER,
  rows_total INTEGER,
  rows_with_any_ok INTEGER
);

CREATE TABLE raw_site_snapshots (
  id BIGSERIAL PRIMARY KEY,
  scan_run_id BIGINT REFERENCES scan_runs(id) ON DELETE CASCADE,
  origin TEXT NOT NULL,
  domain TEXT,
  scanned_at TIMESTAMPTZ,
  any_ok BOOLEAN NOT NULL DEFAULT false,
  endpoints JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (scan_run_id, origin)
);

CREATE TABLE sites (
  id TEXT PRIMARY KEY,
  origin TEXT NOT NULL UNIQUE,
  domain TEXT,
  root_domain TEXT,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  model_count INTEGER NOT NULL DEFAULT 0,
  lowest_ratio DOUBLE PRECISION,
  notice TEXT,
  notifications TEXT,
  status_status INTEGER,
  home_page_content_status INTEGER,
  pricing_status INTEGER,
  notice_status INTEGER,
  ratio_status INTEGER,
  updated_at TIMESTAMPTZ,
  currency_symbol TEXT,
  currency_unit_price DOUBLE PRECISION,
  quota_per_unit DOUBLE PRECISION,
  token_price_multiplier DOUBLE PRECISION,
  request_price_multiplier DOUBLE PRECISION,
  display_in_currency BOOLEAN NOT NULL DEFAULT true,
  tags TEXT[] NOT NULL DEFAULT '{}',
  providers TEXT[] NOT NULL DEFAULT '{}',
  groups TEXT[] NOT NULL DEFAULT '{}',
  billing_types TEXT[] NOT NULL DEFAULT '{}',
  models_preview TEXT[] NOT NULL DEFAULT '{}',
  notice_tags TEXT[] NOT NULL DEFAULT '{}',
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE site_models (
  id BIGSERIAL PRIMARY KEY,
  site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  raw_model TEXT,
  provider TEXT NOT NULL,
  model_ratio DOUBLE PRECISION,
  completion_ratio DOUBLE PRECISION,
  cache_ratio DOUBLE PRECISION,
  create_cache_ratio DOUBLE PRECISION,
  model_price DOUBLE PRECISION,
  quota_type INTEGER,
  min_group_ratio DOUBLE PRECISION,
  currency_symbol TEXT,
  currency_unit_price DOUBLE PRECISION,
  quota_per_unit DOUBLE PRECISION,
  token_price_multiplier DOUBLE PRECISION,
  request_price_multiplier DOUBLE PRECISION,
  display_in_currency BOOLEAN NOT NULL DEFAULT true,
  avg_latency_ms DOUBLE PRECISION,
  success_rate DOUBLE PRECISION,
  avg_tps DOUBLE PRECISION,
  status TEXT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (site_id, provider, model, COALESCE(raw_model, ''))
);

CREATE TABLE site_model_groups (
  site_model_id BIGINT NOT NULL REFERENCES site_models(id) ON DELETE CASCADE,
  group_name TEXT NOT NULL,
  group_ratio DOUBLE PRECISION,
  PRIMARY KEY (site_model_id, group_name)
);

CREATE TABLE canonical_models (
  id BIGSERIAL PRIMARY KEY,
  provider TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  display_model TEXT NOT NULL,
  aliases TEXT[] NOT NULL DEFAULT '{}',
  site_count INTEGER NOT NULL DEFAULT 0,
  min_ratio DOUBLE PRECISION,
  max_success_rate DOUBLE PRECISION,
  min_latency_ms DOUBLE PRECISION,
  max_tps DOUBLE PRECISION,
  perf_site_count INTEGER NOT NULL DEFAULT 0,
  release_sort_key JSONB,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, canonical_key)
);

CREATE TABLE canonical_model_sites (
  canonical_model_id BIGINT NOT NULL REFERENCES canonical_models(id) ON DELETE CASCADE,
  site_model_id BIGINT NOT NULL REFERENCES site_models(id) ON DELETE CASCADE,
  PRIMARY KEY (canonical_model_id, site_model_id)
);

CREATE TABLE announcements (
  id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  site_name TEXT NOT NULL,
  origin TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  first_seen_at TIMESTAMPTZ,
  source TEXT NOT NULL,
  source_type TEXT,
  source_id TEXT,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (site_id, source, COALESCE(source_id, ''), content_hash)
);
```

## Indexes

```sql
CREATE INDEX idx_sites_status ON sites(status);
CREATE INDEX idx_sites_root_domain ON sites(root_domain);
CREATE INDEX idx_sites_name_trgm ON sites USING gin (name gin_trgm_ops);
CREATE INDEX idx_sites_origin_trgm ON sites USING gin (origin gin_trgm_ops);
CREATE INDEX idx_sites_tags ON sites USING gin (tags);
CREATE INDEX idx_sites_providers ON sites USING gin (providers);
CREATE INDEX idx_sites_groups ON sites USING gin (groups);
CREATE INDEX idx_sites_billing_types ON sites USING gin (billing_types);
CREATE INDEX idx_sites_lowest_ratio ON sites(lowest_ratio);

CREATE INDEX idx_site_models_site ON site_models(site_id);
CREATE INDEX idx_site_models_provider ON site_models(provider);
CREATE INDEX idx_site_models_model_trgm ON site_models USING gin (model gin_trgm_ops);
CREATE INDEX idx_site_models_raw_model_trgm ON site_models USING gin (raw_model gin_trgm_ops);
CREATE INDEX idx_site_models_ratio ON site_models(model_ratio, min_group_ratio);
CREATE INDEX idx_site_models_perf ON site_models(success_rate, avg_latency_ms, avg_tps);

CREATE INDEX idx_site_model_groups_name ON site_model_groups(group_name);
CREATE INDEX idx_site_model_groups_ratio ON site_model_groups(group_ratio);

CREATE INDEX idx_canonical_models_provider ON canonical_models(provider);
CREATE INDEX idx_canonical_models_key ON canonical_models(canonical_key);
CREATE INDEX idx_canonical_models_display_trgm ON canonical_models USING gin (display_model gin_trgm_ops);
CREATE INDEX idx_canonical_models_aliases ON canonical_models USING gin (aliases);
CREATE INDEX idx_canonical_models_popularity ON canonical_models(site_count DESC, min_ratio NULLS LAST);

CREATE INDEX idx_canonical_model_sites_model ON canonical_model_sites(canonical_model_id);
CREATE INDEX idx_canonical_model_sites_site_model ON canonical_model_sites(site_model_id);

CREATE INDEX idx_announcements_time ON announcements(first_seen_at DESC NULLS LAST);
CREATE INDEX idx_announcements_site ON announcements(site_id);
CREATE INDEX idx_announcements_tags ON announcements USING gin (tags);
CREATE INDEX idx_announcements_content_trgm ON announcements USING gin (content gin_trgm_ops);

CREATE INDEX idx_raw_site_snapshots_origin ON raw_site_snapshots(origin);
CREATE INDEX idx_raw_site_snapshots_scan ON raw_site_snapshots(scan_run_id);
```

## Price Expressions

当前前端价格计算逻辑需要在 SQL 查询里复用。

按量输入价：

```sql
model_ratio * group_ratio * token_price_multiplier
```

按量输出价：

```sql
model_ratio * group_ratio * token_price_multiplier * completion_ratio
```

缓存输入：

```sql
model_ratio * group_ratio * token_price_multiplier * cache_ratio
```

缓存写入：

```sql
model_ratio * group_ratio * token_price_multiplier * create_cache_ratio
```

按次：

```sql
model_price * group_ratio * request_price_multiplier
```

建议建立视图，减少 API SQL 重复：

```sql
CREATE VIEW site_model_group_prices AS
SELECT
  sm.id AS site_model_id,
  sm.site_id,
  sm.model,
  sm.raw_model,
  sm.provider,
  sm.model_ratio,
  sm.completion_ratio,
  sm.cache_ratio,
  sm.create_cache_ratio,
  sm.model_price,
  sm.quota_type,
  sm.currency_symbol,
  sm.token_price_multiplier,
  sm.request_price_multiplier,
  sm.success_rate,
  sm.avg_latency_ms,
  sm.avg_tps,
  sm.status,
  g.group_name,
  g.group_ratio,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN 'request'
    WHEN sm.currency_symbol = '$' THEN 'usd'
    WHEN sm.currency_symbol IN ('¥', '￥') THEN 'cny'
    ELSE 'other'
  END AS billing_bucket,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0
      THEN sm.model_price * COALESCE(g.group_ratio, 1) * COALESCE(sm.request_price_multiplier, 1)
    ELSE sm.model_ratio * COALESCE(g.group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1)
  END AS input_price,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN NULL
    ELSE sm.model_ratio * COALESCE(g.group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1) * COALESCE(sm.completion_ratio, 0)
  END AS output_price,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN NULL
    ELSE sm.model_ratio * COALESCE(g.group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1) * COALESCE(sm.cache_ratio, 0)
  END AS cache_input_price,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN NULL
    ELSE sm.model_ratio * COALESCE(g.group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1) * COALESCE(sm.create_cache_ratio, 0)
  END AS cache_write_price
FROM site_models sm
LEFT JOIN site_model_groups g ON g.site_model_id = sm.id;
```

## API Query Drafts

### Summary

```sql
SELECT
  (SELECT COUNT(*) FROM sites) AS sites,
  (SELECT COUNT(*) FROM sites WHERE status = 'online') AS online_sites,
  (SELECT COUNT(*) FROM canonical_models) AS models,
  (SELECT COUNT(*) FROM announcements) AS announcements;
```

### Filters

```sql
SELECT provider AS value, COUNT(*) AS count
FROM site_models
GROUP BY provider
ORDER BY count DESC, provider;

SELECT group_name AS value, COUNT(DISTINCT site_id) AS count
FROM site_model_group_prices
GROUP BY group_name
ORDER BY count DESC, group_name;

SELECT unnest(tags) AS value, COUNT(*) AS count
FROM announcements
GROUP BY value
ORDER BY count DESC, value;
```

### Sites List

```sql
SELECT *
FROM sites
WHERE
  (:status = 'all' OR status = :status)
  AND (:provider = 'all' OR providers @> ARRAY[:provider]::text[])
  AND (:group_name = 'all' OR groups @> ARRAY[:group_name]::text[])
  AND (:billing = 'all' OR billing_types @> ARRAY[:billing]::text[])
  AND (
    :model_query = ''
    OR EXISTS (
      SELECT 1
      FROM site_models sm
      WHERE sm.site_id = sites.id
        AND (sm.model ILIKE '%' || :model_query || '%' OR sm.raw_model ILIKE '%' || :model_query || '%')
    )
  )
  AND (
    :q = ''
    OR name ILIKE '%' || :q || '%'
    OR origin ILIKE '%' || :q || '%'
    OR EXISTS (
      SELECT 1 FROM site_models sm
      WHERE sm.site_id = sites.id AND sm.model ILIKE '%' || :q || '%'
    )
  )
ORDER BY
  CASE WHEN :sort = 'online' THEN CASE status WHEN 'online' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END END,
  CASE WHEN :sort = 'price' THEN lowest_ratio END NULLS LAST,
  CASE WHEN :sort = 'models' THEN model_count END DESC,
  name ASC
LIMIT :page_size OFFSET :offset;
```

### Site Detail

```sql
SELECT * FROM sites WHERE id = :site_id;

SELECT sm.*, jsonb_object_agg(g.group_name, g.group_ratio) AS group_ratios
FROM site_models sm
LEFT JOIN site_model_groups g ON g.site_model_id = sm.id
WHERE sm.site_id = :site_id
GROUP BY sm.id
ORDER BY provider, model;
```

### Model Price Index

第一层取 canonical models：

```sql
SELECT cm.*
FROM canonical_models cm
WHERE
  (:provider = 'all' OR cm.provider = :provider)
  AND (
    :q = ''
    OR cm.display_model ILIKE '%' || :q || '%'
    OR cm.canonical_key ILIKE '%' || :q || '%'
    OR cm.aliases && ARRAY[:q]::text[]
  )
ORDER BY
  cm.site_count DESC,
  cm.min_ratio NULLS LAST,
  cm.display_model
LIMIT :page_size OFFSET :offset;
```

第二层取某个模型的站点报价，替代当前 `/api/model-sites`：

```sql
SELECT
  s.name AS site_name,
  s.origin,
  sm.*,
  jsonb_object_agg(p.group_name, p.group_ratio) AS group_ratios,
  MIN(p.input_price) AS sort_price
FROM canonical_model_sites cms
JOIN site_models sm ON sm.id = cms.site_model_id
JOIN sites s ON s.id = sm.site_id
JOIN site_model_group_prices p ON p.site_model_id = sm.id
WHERE
  cms.canonical_model_id = :canonical_model_id
  AND p.billing_bucket = :sort
  AND (:min_success IS NULL OR sm.success_rate >= :min_success)
  AND (:max_latency IS NULL OR sm.avg_latency_ms <= :max_latency)
  AND (:min_tps IS NULL OR sm.avg_tps >= :min_tps)
GROUP BY s.name, s.origin, sm.id
ORDER BY sort_price NULLS LAST, s.name
LIMIT :page_size OFFSET :offset;
```

### Announcements

```sql
SELECT *
FROM announcements
WHERE
  (:tag = 'all' OR tags @> ARRAY[:tag]::text[])
  AND (
    :q = ''
    OR site_name ILIKE '%' || :q || '%'
    OR origin ILIKE '%' || :q || '%'
    OR content ILIKE '%' || :q || '%'
  )
ORDER BY first_seen_at DESC NULLS LAST, id DESC
LIMIT :page_size OFFSET :offset;
```

## Migration Plan

1. Keep collector output as JSONL.
2. Add `normalize_to_db.py` that reuses current parsing functions from `normalize_data.py`.
3. Load one scan into PostgreSQL tables inside a transaction.
4. Build canonical model grouping in Python first, write `canonical_models` and `canonical_model_sites`.
5. Replace `/api/summary`, `/api/announcements` first because they are simplest.
6. Replace `/api/sites`.
7. Replace `/api/models` and `/api/model-sites`.
8. Keep JSON output temporarily as fallback until DB API parity is confirmed.

## Notes

- Current `server.py` canonical model grouping is complex and should initially stay in Python during normalize/write-db.
- PostgreSQL is preferred over MySQL for `jsonb`, arrays, trigram search, GIN indexes, and materialized views.
- For multi-user traffic, avoid returning giant nested model/site arrays by default; keep lazy `/api/model-sites`.
- Later optimization: convert `site_model_group_prices` from a view to a materialized table if price sorting becomes hot.
