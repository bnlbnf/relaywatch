# RelayWatch

[简体中文](README.md) | [繁體中文](README.zh_TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [Français](README.fr.md)

NewAPI/Sub2API リレーサイト収集、AI モデル価格比較、告知監視、API ステータスダッシュボード。

RelayWatch は AI API リレーサイト向けの集約・監視プラットフォームです。分散したリレーサイト情報を検索、比較、追跡しやすいディレクトリとして整理します。サイト発見、NewAPI/Sub2API 収集、モデル価格比較、告知フィード、公式 API ステータス、AI ニュース、オンラインチャット、モデル/API 可用性チェックに対応しています。

デモサイト: [http://relaywatch.online/](http://relaywatch.online/)

## 主な機能

- サイト集約: 状態、モデル数、最低倍率、告知、プロバイダー、利用可能グループを表示。
- モデル価格比較: 入力、出力、キャッシュ価格、成功率、レイテンシ、TPS をサイト横断で比較。
- モデル検査: 自分の API Key で指定サイトとモデルのプロトコル・品質検査を実行。
- オンラインチャット: OpenAI 互換 API、モデル一覧取得、ストリーミング出力に対応。
- 告知フィード: メンテナンス、価格変更、キャンペーン、サイト告知を追跡。
- 公式 API ステータス: OpenAI、Claude、Gemini、DeepSeek などの状態ページを集約。
- AI ニュース: AI ニュース、モデルリリース、チュートリアル、コミュニティ投稿、OSS プロジェクトを収集。
- データ保存: ローカル JSON モードと PostgreSQL 世代管理インポートに対応。

## スクリーンショット

![Site Aggregation](docs/images/site-aggregation.png)
![Model Pricing](docs/images/model-pricing.png)
![Model Detection](docs/images/model-detection.png)
![Announcements](docs/images/announcements.png)
![AI News](docs/images/ai-news.png)
![About](docs/images/about.png)

## クイックスタート

```bash
cd relaywatch
python -m pip install -r requirements.txt

cd web
npm install
npm run build
cd ..

python normalize_data.py --input ../api_config_results.json --out-dir data
python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

`http://127.0.0.1:8765` を開きます。

## 設定

`.env.example` を `.env` にコピーし、自分の環境値を設定してください。実際の API Key、Cookie、データベースパスワード、収集プラットフォームの Key、管理トークンはコミットしないでください。

## License

MIT License. See [LICENSE](LICENSE).
