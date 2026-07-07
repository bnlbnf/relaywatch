# RelayWatch

[简体中文](README.md) | [繁體中文](README.zh_TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [Français](README.fr.md)

NewAPI/Sub2API 中转站采集、AI 模型比价、公告监测与接口状态看板。

RelayWatch 是一个面向 AI API 中转站生态的聚合监控平台，用来把分散的中转站信息整理成可搜索、可比较、可追踪的目录。它支持全网站点发现、NewAPI/Sub2API 站点采集、模型价格对比、公告流、官方 API 状态、AI 热点资讯、在线对话和接口可用性检测。

演示站点：[http://relaywatch.online/](http://relaywatch.online/)

> English keywords: AI relay dashboard, NewAPI, Sub2API, model price comparison, API status monitor, announcement feed, AI news aggregator.

## 主要功能

- 站点聚合：展示中转站状态、模型数量、最低倍率、公告、供应商和可用分组。
- 模型比价：按模型维度对比不同站点的输入、输出、缓存价格、成功率、延迟和 TPS。
- 模型检测：使用自己的 API Key 对指定站点和模型做协议检测、质量检测和实时调用验证。
- 在线对话：支持 OpenAI 兼容接口，自动获取模型列表并流式输出。
- 公告流：聚合站点公告、维护通知、价格调整和活动优惠，支持搜索和标签筛选。
- 官方 API 状态：汇总 OpenAI、Claude、Gemini、DeepSeek 等官方状态页。
- AI 资讯：采集 AI 资讯、模型动态、教程文章、社区讨论和开源项目。
- 数据入库：支持本地 JSON 模式，也支持 PostgreSQL 分代导入和原子切换。

## 运行截图

### 站点聚合

![站点聚合](docs/images/site-aggregation.png)

### 模型比价

![模型比价](docs/images/model-pricing.png)

### 模型检测

![模型检测](docs/images/model-detection.png)

### 公告流

![公告流](docs/images/announcements.png)

### AI 资讯

![AI 资讯](docs/images/ai-news.png)

### 关于本站

![关于本站](docs/images/about.png)

## 技术栈

- 后端：Python、FastAPI、Uvicorn
- 前端：React、Vite、lucide-react、react-markdown
- 数据处理：JSON 归一化、增量刷新、PostgreSQL 可选存储
- 数据库：JSON 文件模式或 PostgreSQL 分代存储
- 部署：可直接运行 Uvicorn，也可放到 systemd、Docker、反向代理后面

## 目录结构

```text
relaywatch/
  server.py              FastAPI 应用和 API 接口
  normalize_data.py      将采集结果归一化为站点、模型和公告 JSON
  refresh_sites.py       站点探活与增量刷新
  load_json_to_db.py     将 JSON 数据导入 PostgreSQL
  incremental_import.py  增量导入辅助逻辑
  db_schema.sql          PostgreSQL 表结构
  web/                   React + Vite 前端源码
  static/                前端构建输出目录
  data/                  运行时数据目录，真实数据不提交到仓库
```

## 快速启动

安装 Python 依赖：

```bash
cd relaywatch
python -m pip install -r requirements.txt
```

安装并构建前端：

```bash
cd web
npm install
npm run build
cd ..
```

准备本地 JSON 数据：

```bash
python normalize_data.py --input ../api_config_results.json --out-dir data
```

启动服务：

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

访问：

```text
http://127.0.0.1:8765
```

## 环境变量

复制 `.env.example` 为 `.env`，按需填写自己的配置。真实密钥、数据库密码、Cookie、采集平台 Key 不要提交到仓库。

常用变量：

- `DATABASE_URL`：设置后启用 PostgreSQL 模式。
- `RELAYWATCH_ADMIN_TOKEN`：后台维护接口管理员口令。
- `RELAYWATCH_DETECTOR_BASE_URL`：模型检测服务地址。
- `GITHUB_TOKEN` 或 `RELAYWATCH_GITHUB_TOKEN`：提高 GitHub 项目采集限额。
- `RELAYWATCH_LINUXDO_COOKIE`：可选，用于访问受 Cloudflare 保护的信息源。
- `RELAYWATCH_AI_NEWS_TTL`：AI 资讯缓存时间。
- `RELAYWATCH_OFFICIAL_STATUS_TTL`：官方状态缓存时间。

## 数据存储

RelayWatch 支持两种运行方式：

- JSON 模式：读取 `data/sites.json`、`data/models.json`、`data/announcements.json`、`data/summary.json`。
- PostgreSQL 模式：设置 `DATABASE_URL` 后，从数据库读取分代数据，适合线上长期运行和大数据量检索。

PostgreSQL 导入示例：

```bash
export DATABASE_URL='postgresql://relaywatch:change-me@127.0.0.1:5432/relaywatch'

python load_json_to_db.py \
  --data-dir data \
  --schema db_schema.sql \
  --kind json_import
```

导入脚本会创建新的数据 generation，写入完成后再原子切换到最新版本。如果导入失败，旧版本仍然可用。

## API 示例

- `GET /api/summary`
- `GET /api/sites`
- `GET /api/sites/{site_id}`
- `GET /api/models`
- `GET /api/model-sites`
- `GET /api/announcements`
- `GET /api/official-status`
- `GET /api/ai-news`
- `POST /api/chat/models`
- `POST /api/chat/proxy`
- `POST /api/detections`

## 开源说明

仓库只包含源码、示例配置和说明文档。以下内容默认忽略：

- `.env` 和真实密钥
- 采集平台 Key、API Key、Cookie、管理员口令
- PostgreSQL 连接密码、数据库 dump
- `data/*.json` 等生成数据
- 日志、缓存、构建产物和 `node_modules`

如果你要公开自己的 fork，请先做密钥扫描，确认没有泄露私人数据。

## License

MIT License. See [LICENSE](LICENSE).
