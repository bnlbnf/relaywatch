# RelayWatch

[简体中文](README.md) | [繁體中文](README.zh_TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [Français](README.fr.md)

An AI relay site discovery, NewAPI/Sub2API collection, model price comparison, announcement monitoring, and API status dashboard.

RelayWatch is an aggregation and monitoring platform for the AI API relay ecosystem. It turns scattered relay-site data into a searchable, comparable, and trackable directory. It supports site discovery, NewAPI/Sub2API collection, model price comparison, announcement feeds, official API status, AI news, online chat, and model/API availability detection.

Demo: [http://relaywatch.online/](http://relaywatch.online/)

Keywords: AI relay dashboard, NewAPI, Sub2API, model price comparison, API status monitor, announcement feed, AI news aggregator.

## Features

- Site aggregation: status, model count, lowest ratio, announcements, providers, and available groups.
- Model pricing: compare input, output, cache pricing, success rate, latency, and TPS across sites.
- Model detection: run protocol and quality checks against a specific site/model with your own API key.
- Online chat: OpenAI-compatible chat page with model-list fetching and streaming output.
- Announcement feed: track maintenance, price changes, activities, and relay-site notices.
- Official API status: summarize OpenAI, Claude, Gemini, DeepSeek, and other upstream status pages.
- AI news: collect AI news, model releases, tutorials, community discussions, and open-source projects.
- Data storage: local JSON mode and optional PostgreSQL generation-based imports.

## Screenshots

### Site Aggregation

![Site Aggregation](docs/images/site-aggregation.png)

### Model Pricing

![Model Pricing](docs/images/model-pricing.png)

### Model Detection

![Model Detection](docs/images/model-detection.png)

### Announcements

![Announcements](docs/images/announcements.png)

### AI News

![AI News](docs/images/ai-news.png)

### About

![About](docs/images/about.png)

## Tech Stack

- Backend: Python, FastAPI, Uvicorn
- Frontend: React, Vite, lucide-react, react-markdown
- Data processing: JSON normalization, incremental refresh, optional PostgreSQL storage
- Database: JSON files or PostgreSQL generation-based storage

## Quick Start

Install Python dependencies:

```bash
cd relaywatch
python -m pip install -r requirements.txt
```

Install and build the frontend:

```bash
cd web
npm install
npm run build
cd ..
```

Prepare local JSON data:

```bash
python normalize_data.py --input ../api_config_results.json --out-dir data
```

Start the service:

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`.

## Configuration

Copy `.env.example` to `.env` and fill in your own values. Never commit real API keys, cookies, database passwords, collector keys, or admin tokens.

Common variables:

- `DATABASE_URL`: enables PostgreSQL mode.
- `RELAYWATCH_ADMIN_TOKEN`: admin token for maintenance endpoints.
- `RELAYWATCH_DETECTOR_BASE_URL`: detector service URL.
- `GITHUB_TOKEN` or `RELAYWATCH_GITHUB_TOKEN`: optional GitHub token for higher collection limits.
- `RELAYWATCH_LINUXDO_COOKIE`: optional cookie for protected feeds.

## Storage

RelayWatch supports two modes:

- JSON mode: reads `data/sites.json`, `data/models.json`, `data/announcements.json`, and `data/summary.json`.
- PostgreSQL mode: reads versioned generations from PostgreSQL after setting `DATABASE_URL`.

PostgreSQL import example:

```bash
export DATABASE_URL='postgresql://relaywatch:change-me@127.0.0.1:5432/relaywatch'

python load_json_to_db.py \
  --data-dir data \
  --schema db_schema.sql \
  --kind json_import
```

## Open Source Notes

This repository contains source code, example configuration, and documentation only. Generated data, logs, caches, frontend build output, `node_modules`, `.env`, and secrets are ignored by git.

## License

MIT License. See [LICENSE](LICENSE).
