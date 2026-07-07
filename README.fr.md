# RelayWatch

[简体中文](README.md) | [繁體中文](README.zh_TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [Français](README.fr.md)

Tableau de bord pour la collecte de relais NewAPI/Sub2API, la comparaison de prix des modèles IA, la surveillance des annonces et le suivi de statut des API.

RelayWatch est une plateforme d'agrégation et de supervision pour l'écosystème des relais d'API IA. Elle transforme des informations dispersées sur les sites relais en un répertoire consultable, comparable et traçable. Elle prend en charge la découverte de sites, la collecte NewAPI/Sub2API, la comparaison des prix des modèles, les annonces, le statut officiel des API, les actualités IA, le chat en ligne et les tests de disponibilité.

Démo : [http://relaywatch.online/](http://relaywatch.online/)

## Fonctionnalités

- Agrégation de sites : statut, nombre de modèles, ratio minimal, annonces, fournisseurs et groupes disponibles.
- Comparaison de modèles : prix d'entrée/sortie/cache, taux de succès, latence et TPS entre plusieurs sites.
- Détection de modèles : tests de protocole et de qualité avec votre propre API Key.
- Chat en ligne : API compatible OpenAI, récupération de la liste des modèles et sortie en streaming.
- Flux d'annonces : maintenance, changements de prix, promotions et annonces publiques.
- Statut officiel des API : OpenAI, Claude, Gemini, DeepSeek et autres pages de statut.
- Actualités IA : actualités, sorties de modèles, tutoriels, discussions communautaires et projets open source.
- Stockage : mode JSON local ou import PostgreSQL par générations.

## Captures d'écran

![Site Aggregation](docs/images/site-aggregation.png)
![Model Pricing](docs/images/model-pricing.png)
![Model Detection](docs/images/model-detection.png)
![Announcements](docs/images/announcements.png)
![AI News](docs/images/ai-news.png)
![About](docs/images/about.png)

## Démarrage rapide

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

Ouvrez `http://127.0.0.1:8765`.

## Configuration

Copiez `.env.example` vers `.env` et remplissez vos propres valeurs. Ne committez jamais de véritables API keys, cookies, mots de passe de base de données, clés de collecte ou jetons administrateur.

## License

MIT License. See [LICENSE](LICENSE).
