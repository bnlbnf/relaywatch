<div align="center">
  <h1>RelayWatch</h1>

  <p><strong>NewAPI / Sub2API 中转站采集、模型比价、公告监测与 AI API 状态看板</strong></p>

  <p>
    <a href="README.md">简体中文</a> |
    <a href="README.zh_TW.md">繁體中文</a> |
    <a href="README.en.md">English</a> |
    <a href="README.ja.md">日本語</a> |
    <a href="README.fr.md">Français</a>
  </p>

  <p>
    <a href="http://relaywatch.online/">在线演示</a>
    ·
    <a href="#运行截图">运行截图</a>
    ·
    <a href="#快速启动">快速启动</a>
    ·
    <a href="#环境变量">环境变量</a>
  </p>

  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white">
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white">
    <img alt="React" src="https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=111111">
    <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-Optional-4169E1?logo=postgresql&logoColor=white">
    <img alt="License" src="https://img.shields.io/github/license/bnlbnf/relaywatch">
  </p>
</div>

---

RelayWatch 是一个面向 AI API 中转站生态的聚合监控平台，用来把分散的中转站信息整理成可搜索、可比较、可追踪的目录。它支持全网站点发现、NewAPI/Sub2API 站点采集、模型价格对比、公告流、官方 API 状态、AI 热点资讯、在线对话和接口可用性检测。

> English keywords: AI relay dashboard, NewAPI, Sub2API, model price comparison, API status monitor, announcement feed, AI news aggregator.

## 项目亮点

| 能力 | 说明 |
| --- | --- |
| 全网站点聚合 | 整理公开可访问的 AI 中转站入口，记录状态、模型数量、最低倍率、供应商、分组和公告。 |
| NewAPI / Sub2API 生态适配 | 面向常见中转站接口结构做采集、归一化、去重、探活和比价展示。 |
| 模型比价 | 按模型维度对比不同站点的输入、输出、缓存价格、成功率、延迟和 TPS。 |
| 公告监测 | 聚合维护通知、价格调整、活动优惠、风险提醒，并保留时间线。 |
| 官方 API 状态 | 汇总 OpenAI、Claude、Gemini、DeepSeek 等官方状态页，辅助判断上游波动。 |
| 模型检测 | 使用自己的 API Key 对指定站点和模型做协议检测、质量检测和实时调用验证。 |
| AI 资讯 | 聚合 AI 资讯、模型动态、教程文章、社区讨论和开源项目。 |
| 双存储模式 | 支持本地 JSON 快速运行，也支持 PostgreSQL 分代导入和原子切换。 |

## 运行截图

| 站点聚合 | 模型比价 |
| --- | --- |
| ![站点聚合](docs/images/site-aggregation.png) | ![模型比价](docs/images/model-pricing.png) |

| 模型检测 | 公告流 |
| --- | --- |
| ![模型检测](docs/images/model-detection.png) | ![公告流](docs/images/announcements.png) |

| AI 资讯 | 关于本站 |
| --- | --- |
| ![AI 资讯](docs/images/ai-news.png) | ![关于本站](docs/images/about.png) |

## 功能模块

- **站点聚合**：展示中转站状态、模型数量、最低倍率、公告、供应商和可用分组。
- **模型比价**：按模型维度对比不同站点的输入、输出、缓存价格、成功率、延迟和 TPS。
- **模型检测**：使用自己的 API Key 对指定站点和模型做协议检测、质量检测和实时调用验证。
- **在线对话**：支持 OpenAI 兼容接口，自动获取模型列表并流式输出。
- **公告流**：聚合站点公告、维护通知、价格调整和活动优惠，支持搜索和标签筛选。
- **官方 API 状态**：汇总 OpenAI、Claude、Gemini、DeepSeek 等官方状态页。
- **AI 资讯**：采集 AI 资讯、模型动态、教程文章、社区讨论和开源项目。
- **数据入库**：支持本地 JSON 模式，也支持 PostgreSQL 分代导入和原子切换。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 后端 | Python, FastAPI, Uvicorn |
| 前端 | React, Vite, lucide-react, react-markdown |
| 数据处理 | JSON 归一化、增量刷新、站点探活、价格标准化 |
| 数据库 | JSON 文件模式 / PostgreSQL 分代存储 |
| 部署 | Uvicorn, systemd, Docker 或反向代理 |

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

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 设置后启用 PostgreSQL 模式 |
| `RELAYWATCH_ADMIN_TOKEN` | 后台维护接口管理员口令 |
| `RELAYWATCH_DETECTOR_BASE_URL` | 模型检测服务地址 |
| `GITHUB_TOKEN` / `RELAYWATCH_GITHUB_TOKEN` | 提高 GitHub 项目采集限额 |
| `RELAYWATCH_LINUXDO_COOKIE` | 可选，用于访问受 Cloudflare 保护的信息源 |
| `RELAYWATCH_AI_NEWS_TTL` | AI 资讯缓存时间 |
| `RELAYWATCH_OFFICIAL_STATUS_TTL` | 官方状态缓存时间 |

## 数据存储

RelayWatch 支持两种运行方式：

- **JSON 模式**：读取 `data/sites.json`、`data/models.json`、`data/announcements.json`、`data/summary.json`。
- **PostgreSQL 模式**：设置 `DATABASE_URL` 后，从数据库读取分代数据，适合线上长期运行和大数据量检索。

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

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/summary` | 首页统计 |
| `GET` | `/api/sites` | 站点列表 |
| `GET` | `/api/sites/{site_id}` | 站点详情 |
| `GET` | `/api/models` | 模型比价列表 |
| `GET` | `/api/model-sites` | 指定模型的站点价格 |
| `GET` | `/api/announcements` | 公告流 |
| `GET` | `/api/official-status` | 官方 API 状态 |
| `GET` | `/api/ai-news` | AI 资讯 |
| `POST` | `/api/chat/models` | 获取模型列表 |
| `POST` | `/api/chat/proxy` | 在线对话代理 |
| `POST` | `/api/detections` | 创建检测任务 |

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
