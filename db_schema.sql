CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE TABLE IF NOT EXISTS data_generations (
  id BIGSERIAL PRIMARY KEY,
  kind TEXT NOT NULL,
  scan_run_id BIGINT,
  based_on_generation_id BIGINT,
  status TEXT NOT NULL DEFAULT 'building',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  site_count INTEGER NOT NULL DEFAULT 0,
  online_site_count INTEGER NOT NULL DEFAULT 0,
  model_count INTEGER NOT NULL DEFAULT 0,
  announcement_count INTEGER NOT NULL DEFAULT 0,
  meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_runs (
  id BIGSERIAL PRIMARY KEY,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  total_count INTEGER NOT NULL DEFAULT 0,
  ok_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  meta JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS scan_runs (
  id BIGSERIAL PRIMARY KEY,
  job_run_id BIGINT REFERENCES job_runs(id) ON DELETE SET NULL,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  source_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  origin_groups_total INTEGER,
  rows_total INTEGER,
  rows_with_any_ok INTEGER
);

CREATE TABLE IF NOT EXISTS origin_candidates (
  id BIGSERIAL PRIMARY KEY,
  origin TEXT NOT NULL UNIQUE,
  domain TEXT,
  root_domain TEXT,
  sources TEXT[] NOT NULL DEFAULT '{}',
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_scanned_at TIMESTAMPTZ,
  last_ok_at TIMESTAMPTZ,
  fail_count INTEGER NOT NULL DEFAULT 0,
  disabled BOOLEAN NOT NULL DEFAULT false,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS raw_site_snapshots (
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

CREATE TABLE IF NOT EXISTS sites (
  generation_id BIGINT NOT NULL REFERENCES data_generations(id) ON DELETE CASCADE,
  id TEXT NOT NULL,
  origin TEXT NOT NULL,
  domain TEXT,
  root_domain TEXT,
  name TEXT NOT NULL,
  sort_index INTEGER,
  status TEXT NOT NULL,
  registration_status TEXT NOT NULL DEFAULT 'unknown',
  register_enabled BOOLEAN,
  password_register_enabled BOOLEAN,
  model_count INTEGER NOT NULL DEFAULT 0,
  lowest_ratio DOUBLE PRECISION,
  notice TEXT,
  notifications TEXT,
  notice_tags TEXT[] NOT NULL DEFAULT '{}',
  tags TEXT[] NOT NULL DEFAULT '{}',
  providers TEXT[] NOT NULL DEFAULT '{}',
  groups TEXT[] NOT NULL DEFAULT '{}',
  billing_types TEXT[] NOT NULL DEFAULT '{}',
  status_status INTEGER,
  home_page_content_status INTEGER,
  pricing_status INTEGER,
  notice_status INTEGER,
  ratio_status INTEGER,
  updated_at TIMESTAMPTZ,
  last_checked_at TIMESTAMPTZ,
  last_ok_at TIMESTAMPTZ,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  currency_symbol TEXT,
  currency_unit_price DOUBLE PRECISION,
  quota_per_unit DOUBLE PRECISION,
  token_price_multiplier DOUBLE PRECISION,
  request_price_multiplier DOUBLE PRECISION,
  display_in_currency BOOLEAN NOT NULL DEFAULT true,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (generation_id, id),
  UNIQUE (generation_id, origin)
);

CREATE TABLE IF NOT EXISTS site_models (
  id BIGSERIAL PRIMARY KEY,
  generation_id BIGINT NOT NULL REFERENCES data_generations(id) ON DELETE CASCADE,
  site_id TEXT NOT NULL,
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
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS site_model_groups (
  site_model_id BIGINT NOT NULL REFERENCES site_models(id) ON DELETE CASCADE,
  group_name TEXT NOT NULL,
  group_ratio DOUBLE PRECISION,
  PRIMARY KEY (site_model_id, group_name)
);

CREATE TABLE IF NOT EXISTS canonical_models (
  generation_id BIGINT NOT NULL REFERENCES data_generations(id) ON DELETE CASCADE,
  id BIGSERIAL,
  sort_index INTEGER,
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
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (generation_id, id),
  UNIQUE (generation_id, provider, canonical_key)
);

CREATE TABLE IF NOT EXISTS canonical_model_sites (
  generation_id BIGINT NOT NULL REFERENCES data_generations(id) ON DELETE CASCADE,
  canonical_model_id BIGINT NOT NULL,
  site_model_id BIGINT,
  sort_index INTEGER,
  site_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  billing_bucket TEXT NOT NULL,
  input_price DOUBLE PRECISION,
  output_price DOUBLE PRECISION,
  cache_input_price DOUBLE PRECISION,
  cache_write_price DOUBLE PRECISION,
  success_rate DOUBLE PRECISION,
  avg_latency_ms DOUBLE PRECISION,
  avg_tps DOUBLE PRECISION,
  site_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (generation_id, canonical_model_id, site_id, provider, model, billing_bucket)
);

CREATE TABLE IF NOT EXISTS announcements (
  id TEXT PRIMARY KEY,
  sort_index INTEGER,
  site_id TEXT NOT NULL,
  site_name TEXT NOT NULL,
  origin TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT[] NOT NULL DEFAULT '{}',
  registration_status TEXT NOT NULL DEFAULT 'unknown',
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_active BOOLEAN NOT NULL DEFAULT true,
  first_generation_id BIGINT REFERENCES data_generations(id) ON DELETE SET NULL,
  last_generation_id BIGINT REFERENCES data_generations(id) ON DELETE SET NULL,
  source TEXT NOT NULL,
  source_type TEXT,
  source_id TEXT,
  source_key TEXT NOT NULL DEFAULT '',
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

DROP INDEX IF EXISTS idx_announcements_dedupe;
CREATE INDEX IF NOT EXISTS idx_announcements_source_hash
  ON announcements (site_id, source, source_key, content_hash);

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
);

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
);

ALTER TABLE sites ADD COLUMN IF NOT EXISTS sort_index INTEGER;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS registration_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE sites ADD COLUMN IF NOT EXISTS register_enabled BOOLEAN;
ALTER TABLE sites ADD COLUMN IF NOT EXISTS password_register_enabled BOOLEAN;
ALTER TABLE canonical_models ADD COLUMN IF NOT EXISTS sort_index INTEGER;
ALTER TABLE canonical_model_sites ADD COLUMN IF NOT EXISTS sort_index INTEGER;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS sort_index INTEGER;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS registration_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS first_generation_id BIGINT REFERENCES data_generations(id) ON DELETE SET NULL;
ALTER TABLE announcements ADD COLUMN IF NOT EXISTS last_generation_id BIGINT REFERENCES data_generations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_app_state_key ON app_state(key);
CREATE INDEX IF NOT EXISTS idx_origin_candidates_root ON origin_candidates(root_domain);
CREATE INDEX IF NOT EXISTS idx_origin_candidates_scan ON origin_candidates(last_scanned_at);
CREATE INDEX IF NOT EXISTS idx_origin_candidates_disabled ON origin_candidates(disabled);
CREATE INDEX IF NOT EXISTS idx_raw_site_snapshots_origin ON raw_site_snapshots(origin);
CREATE INDEX IF NOT EXISTS idx_raw_site_snapshots_scan ON raw_site_snapshots(scan_run_id);

CREATE INDEX IF NOT EXISTS idx_sites_generation_status ON sites(generation_id, status);
CREATE INDEX IF NOT EXISTS idx_sites_generation_registration ON sites(generation_id, registration_status);
CREATE INDEX IF NOT EXISTS idx_sites_generation_root ON sites(generation_id, root_domain);
CREATE INDEX IF NOT EXISTS idx_sites_generation_lowest ON sites(generation_id, lowest_ratio);
CREATE INDEX IF NOT EXISTS idx_sites_generation_sort ON sites(generation_id, sort_index);
CREATE INDEX IF NOT EXISTS idx_sites_name_trgm ON sites USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sites_origin_trgm ON sites USING gin (origin gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sites_lower_name_trgm ON sites USING gin (lower(name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sites_lower_origin_trgm ON sites USING gin (lower(origin) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sites_lower_domain_trgm ON sites USING gin (lower(coalesce(domain, '')) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sites_lower_notice_trgm ON sites USING gin (lower(coalesce(notice, '')) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_sites_tags ON sites USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_sites_providers ON sites USING gin (providers);
CREATE INDEX IF NOT EXISTS idx_sites_groups ON sites USING gin (groups);
CREATE INDEX IF NOT EXISTS idx_sites_billing_types ON sites USING gin (billing_types);

CREATE INDEX IF NOT EXISTS idx_site_models_generation_site ON site_models(generation_id, site_id);
CREATE INDEX IF NOT EXISTS idx_site_models_generation_provider ON site_models(generation_id, provider);
CREATE UNIQUE INDEX IF NOT EXISTS idx_site_models_unique_model
  ON site_models (generation_id, site_id, provider, model, COALESCE(raw_model, ''));
CREATE INDEX IF NOT EXISTS idx_site_models_model_trgm ON site_models USING gin (model gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_site_models_raw_model_trgm ON site_models USING gin (raw_model gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_site_models_lower_model_trgm ON site_models USING gin (lower(model) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_site_models_perf ON site_models(generation_id, success_rate, avg_latency_ms, avg_tps);
CREATE INDEX IF NOT EXISTS idx_site_model_groups_name ON site_model_groups(group_name);
CREATE INDEX IF NOT EXISTS idx_site_model_groups_ratio ON site_model_groups(group_ratio);

CREATE INDEX IF NOT EXISTS idx_canonical_models_generation_provider ON canonical_models(generation_id, provider);
CREATE INDEX IF NOT EXISTS idx_canonical_models_generation_sort ON canonical_models(generation_id, sort_index);
CREATE INDEX IF NOT EXISTS idx_canonical_models_display_trgm ON canonical_models USING gin (display_model gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_canonical_models_aliases ON canonical_models USING gin (aliases);
CREATE INDEX IF NOT EXISTS idx_canonical_models_popularity ON canonical_models(generation_id, site_count DESC, min_ratio NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_canonical_model_sites_generation_model ON canonical_model_sites(generation_id, canonical_model_id);
CREATE INDEX IF NOT EXISTS idx_canonical_model_sites_generation_site ON canonical_model_sites(generation_id, site_id);
CREATE INDEX IF NOT EXISTS idx_canonical_model_sites_lower_model_trgm ON canonical_model_sites USING gin (lower(model) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_canonical_model_sites_generation_sort ON canonical_model_sites(generation_id, canonical_model_id, sort_index);
CREATE INDEX IF NOT EXISTS idx_canonical_model_sites_bucket_price ON canonical_model_sites(generation_id, billing_bucket, input_price);
CREATE INDEX IF NOT EXISTS idx_canonical_model_sites_perf ON canonical_model_sites(generation_id, success_rate, avg_latency_ms, avg_tps);

CREATE INDEX IF NOT EXISTS idx_announcements_time ON announcements(first_seen_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_announcements_active_time ON announcements(is_active, first_seen_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_announcements_registration ON announcements(registration_status);
CREATE INDEX IF NOT EXISTS idx_announcements_tags ON announcements USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_announcements_content_trgm ON announcements USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_announcements_lower_site_name_trgm ON announcements USING gin (lower(site_name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_announcements_lower_origin_trgm ON announcements USING gin (lower(origin) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_announcements_lower_content_trgm ON announcements USING gin (lower(content) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_announcements_sort ON announcements(sort_index);

CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_articles_active_time ON articles(is_active, published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
CREATE INDEX IF NOT EXISTS idx_articles_provider ON articles(provider);
CREATE INDEX IF NOT EXISTS idx_articles_kind ON articles(kind);
CREATE INDEX IF NOT EXISTS idx_articles_title_trgm ON articles USING gin (lower(title) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_articles_summary_trgm ON articles USING gin (lower(summary) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status);

CREATE OR REPLACE VIEW site_model_group_prices AS
SELECT
  sm.id AS site_model_id,
  sm.generation_id,
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
  COALESCE(g.group_ratio, sm.min_group_ratio, 1) AS group_ratio,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN 'request'
    WHEN sm.currency_symbol = '$' THEN 'usd'
    ELSE 'cny'
  END AS billing_bucket,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0
      THEN sm.model_price * COALESCE(g.group_ratio, sm.min_group_ratio, 1) * COALESCE(sm.request_price_multiplier, 1)
    ELSE sm.model_ratio * COALESCE(g.group_ratio, sm.min_group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1)
  END AS input_price,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN NULL
    ELSE sm.model_ratio * COALESCE(g.group_ratio, sm.min_group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1) * COALESCE(sm.completion_ratio, 0)
  END AS output_price,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN NULL
    ELSE sm.model_ratio * COALESCE(g.group_ratio, sm.min_group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1) * COALESCE(sm.cache_ratio, 0)
  END AS cache_input_price,
  CASE
    WHEN COALESCE(sm.model_price, 0) > 0 THEN NULL
    ELSE sm.model_ratio * COALESCE(g.group_ratio, sm.min_group_ratio, 1) * COALESCE(sm.token_price_multiplier, 1) * COALESCE(sm.create_cache_ratio, 0)
  END AS cache_write_price
FROM site_models sm
LEFT JOIN site_model_groups g ON g.site_model_id = sm.id;
