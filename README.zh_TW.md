# RelayWatch

[简体中文](README.md) | [繁體中文](README.zh_TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [Français](README.fr.md)

NewAPI/Sub2API 中轉站採集、AI 模型比價、公告監測與介面狀態看板。

RelayWatch 是一個面向 AI API 中轉站生態的聚合監控平台，用來把分散的中轉站資訊整理成可搜尋、可比較、可追蹤的目錄。它支援全網站點發現、NewAPI/Sub2API 站點採集、模型價格對比、公告流、官方 API 狀態、AI 熱點資訊、線上對話和介面可用性檢測。

演示站點：[http://relaywatch.online/](http://relaywatch.online/)

## 主要功能

- 站點聚合：展示中轉站狀態、模型數量、最低倍率、公告、供應商和可用分組。
- 模型比價：按模型維度對比不同站點的輸入、輸出、快取價格、成功率、延遲和 TPS。
- 模型檢測：使用自己的 API Key 對指定站點和模型做協議檢測、品質檢測和即時呼叫驗證。
- 線上對話：支援 OpenAI 相容介面，自動取得模型列表並串流輸出。
- 公告流：聚合站點公告、維護通知、價格調整和活動優惠。
- 官方 API 狀態：匯總 OpenAI、Claude、Gemini、DeepSeek 等官方狀態頁。
- AI 資訊：採集 AI 資訊、模型動態、教學文章、社群討論和開源專案。
- 資料入庫：支援本機 JSON 模式，也支援 PostgreSQL 分代匯入和原子切換。

## 運行截圖

![站點聚合](docs/images/site-aggregation.png)
![模型比價](docs/images/model-pricing.png)
![模型檢測](docs/images/model-detection.png)
![公告流](docs/images/announcements.png)
![AI 資訊](docs/images/ai-news.png)
![關於本站](docs/images/about.png)

## 快速啟動

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

訪問 `http://127.0.0.1:8765`。

## 配置

複製 `.env.example` 為 `.env`，填入自己的配置。請勿提交真實 API Key、Cookie、資料庫密碼、採集平台 Key 或管理員口令。

## License

MIT License. See [LICENSE](LICENSE).
