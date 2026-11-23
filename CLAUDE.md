# Radar Pédago — notes pour Claude

Veille de l'ingénierie pédagogique (même socle que Luxury-monitoring, modèle multi-domaines de Veille-IA). Voir
`README.md` pour l'usage et `SPECS.md` pour les décisions.

## Conventions
- Code et commentaires en anglais ; documentation, dashboard et prompts en français.
- Python 3.12 en CI. Pas de framework : modules à plat à la racine.
- `python -m pytest` doit rester vert et hors réseau ; les tests réseau portent `@pytest.mark.live`, les tests
  navigateur `@pytest.mark.browser` (Playwright, transformers.js remplacé par `tests/fakes.FAKE_TRANSFORMERS_JS`).
- Ajouter une source / une école / un sigle = éditer `sources.json` / `entities.json` / `glossary.json`, pas le code.
- Les 6 domaines, leurs catégories (avec une note qui sert au prompt ET au repli par règles) et les types de contenu
  vivent dans `models.py` ; le dashboard les reçoit dans le JSON embarqué. L'ordre des domaines fixe leur couleur
  (`--dom-1` à `--dom-6`, palette validée daltonisme) : ne pas le réordonner sans revalider.
- `dashboard_template.html` duplique `text.tokens()` en JavaScript (nuage de mots) : garder les deux synchronisés.

## Pièges connus
- Le texte est normalisé (accents retirés) avant les regex de `classify.py` et `entities.py`.
- `embeddings.bin` est aligné sur l'ordre des articles de la page : il est supprimé si l'export se fait sans vecteurs.
  Python (`embeddings.Encoder`) et le navigateur doivent utiliser le même fichier ONNX (`model_quantized.onnx` =
  `dtype: "q8"` dans transformers.js), mean pooling + normalisation L2.
- `published_is_estimated` = vrai pour les liens détectés sur une page : la date est celle de la découverte.
- Le filtre de source (`filter`) n'est pas stocké : `main.source_filters` le retrouve depuis `sources.json` pour les
  contenus renvoyés au LLM (ceux qui n'avaient eu que le repli par règles, `classified = false`).
- `Store` : Supabase si `SUPABASE_URL`/`SUPABASE_KEY`, sinon SQLite (`LOCAL_DB`, défaut `data/local.db`).
- Nouvelle colonne = l'ajouter à `ARTICLE_COLUMNS`, à `_ADDED_COLUMNS` (migration SQLite) et à `schema.sql`, avec un
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (projets Supabase existants : la collecte échoue tant qu'il n'est pas rejoué).
- Agent de recherche (`discover.py`) : contenus `origin = 'agent'`, filtre `agent` (non stocké, retrouvé par
  `main.py` d'après `origin`). Sans vérification LLM, `apply_fallback` les rend non pertinents (jamais affichés).
  Le LLM n'écrit que des requêtes, jamais d'URL. Les sources proposées sont calculées à l'export
  (`dashboard.candidate_sources`, groupées par `discover.site_key`), jamais ajoutées seules à `sources.json`.
- Sources ajoutées / retirées depuis `sources.html` : journal Supabase `source_changes` (clé publique `SUPABASE_ANON_KEY`,
  lecture + ajout seulement, voir `schema.sql`), appliqué par `registry.apply_changes`. Toujours charger les sources via
  `fetchers.load_sources(..., store)` / `registry.all_sources`, jamais `sources.json` seul. Les règles de rejeu sont
  dupliquées en JavaScript dans `sources_template.html` (`state()`) : garder les deux synchronisées. La table est
  publique : chaque ligne est revalidée (`registry.valid_change`) et échappée à l'affichage.
- `reasoning_effort` n'est envoyé qu'aux modèles qui le supportent (`llm.llm_kwargs`).
- Ne jamais mettre de clé dans le dépôt : `.env` est ignoré, les secrets passent par GitHub Actions.
