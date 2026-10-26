# Radar Pédago — veille de l'ingénierie pédagogique

Outil de veille pour l'équipe d'ingénierie pédagogique. Chaque jour, il collecte plus de 70 sources. Chaque contenu est classé par sujet et par catégorie et reçoit un titre et un résumé en français. Chaque lundi matin, le pipeline publie un tableau de bord où chacun filtre ce qui l'intéresse.

**Prototype public** : il sert de base à une future version privée, hébergée sur l'infrastructure de l'école (voir « Passer sur l'infra de l'école »).

```
GitHub Actions (cron 05:30 UTC, tous les jours)
  └─ main.py ── collecte : flux RSS, découverte de flux, nouveautés de pages, Crossref, YouTube, Google News
              ├─ secours automatique si une source échoue (source de repli, sinon Google News site:)
              ├─ enrichissement LLM (Groq, client compatible OpenAI) : catégorie, type, titre FR, résumé FR, pertinence
              ├─ repli par règles si pas de clé ou erreur (retenté par le LLM aux exécutions suivantes)
              └─ stockage (Supabase, ou SQLite en local)
  └─ le lundi : dashboard.py --export ──► site/index.html + site/embeddings.bin ──► GitHub Pages
```

## Ce que contient le tableau de bord

| Section | Contenu |
|---|---|
| **Barre de recherche** | Recherche par mots-clés, développée avec le glossaire des sigles (APC → approche par compétences, AFEST, EIAH…), puis **recherche sémantique** : « évaluation par compétences » trouve aussi « APC », et une requête en français trouve des articles en anglais. |
| **Tags** | Les 6 sujets (sélection multiple) : les tags choisis sont entourés en vert, les autres grisés. Quand un seul sujet est choisi, ses catégories apparaissent en sous-tags. S'y ajoutent un filtre par type de contenu et la période (4, 12 ou 26 semaines). |
| **Mémorisation** | Les filtres et la recherche sont gardés dans le navigateur de chacun et repris dans l'URL : copier le lien partage la vue filtrée. |
| **Chiffres clés** | Contenus sur la période, nouveaux de la semaine, sources actives, sujets chauds. |
| **Tendances par sujet** | Barres empilées : contenus par semaine et par sujet, avec infobulle au survol. |
| **Nuage de mots** | Mots les plus fréquents des titres de la sélection. Un clic lance la recherche sur ce mot ; « Revenir au nuage de mots » rétablit le nuage complet. |
| **Sujets chauds de la semaine** | Groupes de titres partagés par au moins 2 sources sur 7 jours, calculés sujet par sujet et nommés par le LLM. Un clic sur « N contenus · N sources » déplie le détail, source par source. |
| **Termes émergents** | Termes au moins 2 fois plus fréquents cette semaine que sur les 4 précédentes. Section active après 4 semaines d'historique. |
| **Benchmark des écoles** | Affiché quand le tag Benchmark est sélectionné : carte de chaleur écoles × catégories et dernières actualités. Les écoles sont définies dans `entities.json`. |
| **Qui en parle ?** | Sources les plus actives sur la sélection. Un clic filtre la liste. |
| **Contenus** | Titre en français (titre original affiché s'il a été traduit), source, date, sujet, catégorie, type et résumé. Tri par date ou par pertinence pendant une recherche. |

Tous les graphiques suivent les filtres actifs. La page est autonome : aucune donnée ne part vers un serveur, et la recherche sémantique s'exécute dans le navigateur.

### Recherche sémantique

Le pipeline calcule un vecteur par contenu avec le modèle multilingue `Xenova/paraphrase-multilingual-MiniLM-L12-v2` (ONNX quantifié, 384 dimensions). Il exporte ces vecteurs en int8 dans `site/embeddings.bin`.

Dans la page, [transformers.js](https://huggingface.co/docs/transformers.js) charge **le même fichier de modèle** au premier clic dans la barre de recherche, puis encode la requête. Le téléchargement fait environ 120 Mo, une seule fois : le navigateur le garde en cache.

En attendant le modèle, ou si son chargement échoue, la recherche par mots-clés et sigles fonctionne seule.

## Sources

Les sources sont définies dans [`sources.json`](sources.json) : 72 actives, plus une recherche Google News par école de `entities.json`. Chaque source a un `domain` et un `type` :

| Type | Fonctionnement |
|---|---|
| `rss` | Flux connu. Les revues OpenEdition et Érudit passent par leur flux officiel. |
| `auto` | Le site est lu : flux annoncé dans la page, sinon chemins courants (WordPress, SPIP, OJS, Hugo…), sinon détection des nouveaux liens de la page. |
| `page` | Détection des nouveaux liens d'une page sans flux. Le premier passage mémorise l'existant ; seules les nouveautés deviennent des contenus, datées du jour où elles sont repérées. |
| `crossref` | Nouveaux articles d'une revue via l'API Crossref : titre, résumé d'auteur quand il est déposé, lien DOI. Utilisé pour ScienceDirect, Taylor & Francis, Wiley, ACM, Emerald, Cairn… qui n'ont pas de flux exploitable. |
| `youtube` | Nouvelles vidéos d'une chaîne (podcast À la French). |
| `gnews` | Recherche Google News en français. Seuls les résultats en français sont gardés. |

Points à connaître :

- **Les revues anglophones de votre liste sont gardées.** Leurs titres sont traduits et leurs résumés rédigés en français ; le titre original reste visible.
- **Le filtre `superieur`** s'applique aux revues de sciences de l'éducation « à cibler plus précisément ». Le LLM n'y garde que ce qui concerne le supérieur, la formation des ingénieurs ou des adultes, le design ou l'IA en formation. Les contenus écartés sont conservés en base, marqués non pertinents, pour ne pas être retraités chaque jour.
- **LinkedIn** (Design Spot, Villebon-Charpak) interdit la collecte automatique : ces deux entrées sont désactivées.
- **Les sources marquées `"suggested": true`** ont été ajoutées pour étoffer l'enseignement supérieur, le benchmark et l'IA, peu couverts par la liste d'origine. Elles sont à valider par l'équipe.
- **Le workflow « Check sources »** teste chaque source et publie un tableau dans le résumé du job : méthode utilisée (flux, page, secours), nombre d'éléments, exemple de titre. Il tourne une fois par mois et à la demande. C'est le premier réflexe après avoir ajouté une source.

Ajouter une source, une école ou un sigle se fait en éditant `sources.json`, `entities.json` ou `glossary.json`, sans toucher au code.

## Mise en route

### 1. Comptes et secrets

| Service | À faire | Secret / variable GitHub |
|---|---|---|
| **Supabase** (gratuit) | Créer un projet, puis exécuter [`schema.sql`](schema.sql) dans *SQL Editor* | `SUPABASE_URL`, `SUPABASE_KEY` (clé **service_role** ou *secret*, jamais dans le navigateur) |
| **Groq** | Créer une clé API | `GROQ_API_KEY` (+ variable `GROQ_MODEL`, défaut `openai/gpt-oss-120b`) |
| **Crossref** (optionnel) | Rien à créer | variable `CROSSREF_MAILTO` : une adresse de contact, recommandée par Crossref |

Où les saisir : *Settings → Secrets and variables → Actions*. Pour le tableau de bord : *Settings → Pages → Source : GitHub Actions*.

Sans Supabase, rien n'est conservé entre deux exécutions (le runner GitHub est éphémère). Sans clé LLM, le classement se fait par règles, sans traduction ni résumé généré.

### 2. Premier lancement

1. *Actions → Radar Pédago - Check setup → Run workflow* : vérifie Supabase et la clé LLM, sans afficher de secret.
2. *Actions → Radar Pédago - Check sources → Run workflow* : état de chaque source.
3. *Actions → Radar Pédago - Collecte quotidienne → Run workflow* avec `backfill_days = 60` et `publish` coché. Cela charge deux mois d'historique et publie le tableau de bord.
4. Le tableau de bord est en ligne à `https://<utilisateur>.github.io/<repo>/`. Ensuite, la collecte tourne chaque jour et la publication chaque lundi avant 8 h.

Le plan gratuit de Groq limite le volume par jour. Le pipeline envoie au plus 300 contenus au LLM par exécution ; le reste reçoit le classement par règles et repasse au LLM les jours suivants. Après un gros historique, comptez donc deux ou trois jours pour que tout soit traduit et résumé.

### 3. En local

```bash
pip install -r requirements-dev.txt
cp .env.example .env                       # tout est optionnel en local
python main.py --backfill-days 30          # collecte (data/local.db sans Supabase)
python dashboard.py --export               # site/index.html + site/embeddings.bin
python check_sources.py --only "RIPES"     # diagnostic d'une source
python -m pytest                           # tests hors réseau (+ tests navigateur si Playwright est installé)

# Aperçu sans aucune clé, sur des données fictives :
LOCAL_DB=data/demo.db python demo_data.py
LOCAL_DB=data/demo.db python dashboard.py --export --no-embeddings
```

Pour ouvrir le tableau de bord en local avec la recherche sémantique, servez le dossier : `python -m http.server -d site`. Un fichier ouvert directement ne peut pas charger `embeddings.bin`.

## Passer sur l'infra de l'école

| Brique | Changement |
|---|---|
| LLM | Variables `LLM_BASE_URL` (vLLM, API compatible OpenAI), `LLM_MODEL` et secret `LLM_API_KEY`. Aucun changement de code. |
| Stockage | Le même `schema.sql` sur un PostgreSQL interne, ou un `Store` dédié dans `store.py` (deux méthodes à écrire). |
| Hébergement | La page est statique : la servir derrière l'authentification de la DSI suffit. |
| Recherche sémantique | Le modèle peut être servi depuis l'infra de l'école : il suffit de changer l'URL de transformers.js et du modèle. Les vecteurs sont déjà calculés en interne par le pipeline. |

## Limites connues

- **Pages sans flux** : la détection des nouveaux liens dépend de la structure de la page. Une refonte de site est repérée (beaucoup de liens nouveaux d'un coup) et traitée comme une nouvelle référence, sans créer de contenus.
- **Crossref** : les résumés ne sont présents que si l'éditeur les dépose. Sans résumé, le texte affiché ne s'appuie que sur le titre.
- **Google News** : il renvoie des liens de redirection, et l'éditeur d'origine est indiqué dans la source.
- **Résumés et traductions** : générés par LLM à partir des titres et extraits publics, jamais du texte intégral. À vérifier avant de citer.
- **Termes émergents** : ils demandent 4 semaines d'historique. La section l'indique en attendant.

## Structure

```
main.py            collecte quotidienne              dashboard.py       export du tableau de bord (dashboard_template.html)
fetchers.py        rss / auto / page / crossref /    classify.py        catégorie, type, titre et résumé FR, pertinence
                   youtube / gnews + secours          llm.py             client compatible OpenAI (Groq → vLLM)
topics.py          sujets chauds, termes émergents    embeddings.py      vecteurs ONNX (même modèle que le navigateur)
store.py           Supabase / SQLite                  entities.py        écoles, filtre de pertinence
models.py          sujets, catégories, types         text.py            normalisation, langue, mots
check_setup.py     diagnostic des secrets             check_sources.py   diagnostic des sources
demo_data.py       données fictives pour l'aperçu     report.py          tableau d'état des sources
sources.json  entities.json  glossary.json  schema.sql
```
