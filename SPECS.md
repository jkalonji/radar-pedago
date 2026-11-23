# Spécifications — Radar Pédago (nom provisoire)

Outil de veille pour l'équipe d'ingénierie pédagogique d'EPITA. **Prototype public** : il servira de base à une future version propriétaire et privée, hébergée sur l'infrastructure de l'école.

## 1. Vision

Les ingénieurs pédagogiques suivent six sujets. Chacun ne s'intéresse pas aux mêmes.

L'outil collecte chaque jour les sources choisies par l'équipe et produit un **titre et un résumé en français** de chaque contenu. Il classe ces contenus par sujet et par catégorie, puis publie un dashboard. Chaque utilisateur peut y filtrer :
- par **tags** prédéfinis ;
- par une **barre de recherche sémantique**, où « évaluation par compétences » trouve aussi « APC ».

Le dashboard offre une vue d'ensemble des tendances, avec un nuage de mots, les volumes par sujet, les sujets chauds et les termes émergents.

Chaque semaine, un **agent de recherche** cherche aussi sur le web, hors des sources suivies. Il apporte de nouveaux contenus, signalés comme tels, et propose de nouvelles sources que l'équipe valide.

**Hors périmètre pour ce prototype** :
- comptes utilisateurs ;
- récap personnalisé par e-mail ;
- distribution (e-mail, Teams) ;
- favoris et annotations partagés.

Les utilisateurs accèdent au dashboard par un simple lien.

## 2. Décisions d'architecture

| Sujet | Décision |
|---|---|
| Socle | Reprise de la structure de **Luxury-monitoring** : pipeline modulaire, tests, `check_setup.py`, dashboard exporté en HTML statique depuis `dashboard_template.html`. On y ajoute le **modèle à deux niveaux de Veille-IA** : `domain` (les 6 sujets), puis une `category` propre à chaque domaine. |
| Repo / hébergement | Repo **public** sur le GitHub de Jérémie. Dashboard publié sur **GitHub Pages**. Conséquence : aucune donnée non publique, aucun secret côté navigateur. Le benchmark repose uniquement sur des sources publiques. |
| Langue | Affichage **exclusivement en français**. Les revues anglophones de la liste de l'équipe sont gardées : titre traduit, résumé rédigé en français, titre original visible. Les recherches Google News (sources complémentaires) ne gardent que les résultats en français. |
| Contenu lu | Uniquement les **titres et extraits** fournis par les flux, jamais d'article payant. Chaque contenu affiche un résumé de 2 à 3 phrases, reformulé, ainsi que le lien vers l'original. |
| LLM | **Groq** pour l'instant, avec `GROQ_MODEL` (défaut : `openai/gpt-oss-120b`). Tous les appels passent par un client unique compatible OpenAI (`llm.py`). Passer au **vLLM de l'école** reviendra à changer `LLM_BASE_URL` et `LLM_MODEL`. Si le LLM est indisponible, la classification bascule sur des règles (mots-clés). |
| Stockage | **Supabase** (gratuit), avec un repli SQLite en local. L'historique est indispensable pour les tendances et les termes émergents. |
| Rythme | **Collecte et enrichissement quotidiens**, comme Veille-IA. **Publication du tableau de bord le lundi**, avant 8 h (heure de Paris). |
| Personnalisation | 100 % côté navigateur. Les filtres actifs (tags, recherche, période) sont mémorisés dans `localStorage` et reflétés dans l'URL, ce qui permet de partager une vue filtrée. La page s'affiche correctement même si le stockage est bloqué. |
| Recherche sémantique | Sans serveur (§5), modèle `Xenova/paraphrase-multilingual-MiniLM-L12-v2`. Les embeddings des articles sont calculés dans le pipeline. Le navigateur encode la requête avec **le même modèle** (transformers.js), puis calcule la similarité cosinus localement. |
| Agent de recherche | `discover.py`, **une fois par semaine** avant la publication, sur **les 6 domaines**. Le LLM écrit 4 requêtes par domaine (2 en **français**, 2 en **anglais**), sur des catégories qui tournent chaque semaine et sur les sujets chauds. Moteurs : **Tavily** et **Google News**. Les résultats sont enrichis comme les autres contenus (affichage **toujours en français**), avec le filtre de pertinence `agent`, et ne sont affichés qu'après vérification par le LLM. Champs `origin = 'agent'`, `agent_query`, `site`. Le LLM n'écrit jamais d'URL. Dans le dashboard : badge « 🔎 Découvert par l'agent », filtre d'origine, section « Découvertes de l'agent ». Un site hors veille qui fournit au moins 2 contenus pertinents sur 26 semaines devient une **source proposée** (`sources.html`), avec un bouton « Ajouter à la veille » (journal `source_changes`, sinon issue GitHub pré-remplie) : c'est toujours une personne qui ajoute, jamais l'agent. |

## 3. Domaines et taxonomies (à valider avec l'équipe, modifiables dans `models.py`)

| `domain` | Libellé | Catégories |
|---|---|---|
| `design_ux` | Design UX | Recherche utilisateur & méthodes · Accessibilité & inclusion · Interfaces & design systems · Théorie & recherche en design · UX des dispositifs d'apprentissage |
| `pedagogie` | Pédagogie | Approche par compétences & évaluation · Pédagogies actives · Hybridation & distanciel · Ludification & simulation · Engagement & accompagnement · Développement pédagogique des enseignants |
| `recherche` | Recherches scientifiques | Pédagogie du supérieur · Formation des ingénieurs · Sciences cognitives & apprentissage · Conception & design · Technologies éducatives · Transitions & enjeux socio-écologiques · Didactique & sciences de l'éducation |
| `enseignement_sup` | Enseignement supérieur | Politiques publiques & réglementation · Financement & modèles économiques · Vie étudiante & insertion · International & mobilité · Classements & accréditations |
| `benchmark` | Benchmark écoles d'ingénieurs | Nouvelles formations · Partenariats · Innovation pédagogique · Classements & accréditations · Campus & investissements · Communication & recrutement |
| `innovation_ia` | Innovation & IA | IA générative en formation · Outils & EdTech · Usages étudiants & intégrité · Cadres & politiques de l'IA · Tech & entrepreneuriat |

En plus de sa catégorie, chaque contenu reçoit un **type de contenu**, qui remplace le sentiment de Luxury-monitoring : Étude / recherche · Retour d'expérience · Outil / ressource · Actualité · Réglementation · Événement · Podcast / vidéo.

Les **tags du dashboard** sont les 6 domaines. Leurs catégories apparaissent en sous-tags dès qu'un domaine est sélectionné.

### Benchmark : entités suivies

Le fichier `entities.json` reprend le format des maisons de Luxury-monitoring. Chaque école y est décrite par :
- son nom ;
- ses alias (détection insensible aux accents et à la casse, sur mots entiers) ;
- son groupe (Ionis / hors Ionis) ;
- l'URL de sa page actualités, si elle existe.

Ajouter une école ne demande aucune modification de code. **La liste reste à fournir.**

## 4. Sources

La liste fournie par l'équipe est dans `sources.json` (72 sources actives). Elle peut être complétée depuis `sources.html`. Les ajouts, retraits et réactivations sont écrits dans la table `source_changes` (journal, lecture et ajout ouverts à la clé publique, aucun contrôle d'accès pour l'instant), puis appliqués par `registry.py` à la collecte suivante. Les sources ajoutées sont de type `auto`. Chaque source porte un champ `domain` et un `type` :

- `rss` : flux connu (revues OpenEdition et Érudit, carnets Hypothèses…).
- `auto` : le site est lu ; flux annoncé, sinon chemins courants (WordPress, SPIP, OJS…), sinon détection des nouveaux liens de la page.
- `page` : détection des nouveaux liens d'une page sans flux. Le premier passage sert de référence.
- `crossref` : nouveaux articles d'une revue via l'API Crossref (ScienceDirect, Taylor & Francis, Wiley, ACM, Emerald, Cairn…).
- `youtube` : nouvelles vidéos d'une chaîne.
- `gnews` : recherche Google News en français.

Chaque source peut avoir une source de repli (`fallback`). À défaut, un site en échec bascule sur une recherche Google News `site:`.

Le filtre `superieur` s'applique aux revues « à cibler plus précisément » : le LLM n'y garde que ce qui est transposable au supérieur.

LinkedIn est désactivé, car il interdit la collecte automatique. Quelques sources complémentaires (`"suggested": true`) étoffent l'enseignement supérieur, le benchmark et l'IA : elles sont à valider.

Le workflow « Check sources » teste toutes les sources chaque mois et à la demande.

## 5. Recherche sémantique sans serveur

1. **Dans le pipeline** (GitHub Actions) : onnxruntime encode le titre et le résumé de chaque article avec un modèle multilingue léger. Modèle retenu : `Xenova/paraphrase-multilingual-MiniLM-L12-v2` (ONNX quantifié), le même fichier que celui chargé par transformers.js ; il rapproche aussi une requête en français d'un article en anglais. Les vecteurs sont quantifiés en int8 et exportés dans `site/embeddings.bin`, dans l'ordre des contenus de la page.
2. **Dans le navigateur** : transformers.js charge le même modèle une seule fois (mis en cache ensuite), encode la requête et classe les articles par similarité cosinus.
3. **Recherche hybride** : le score sémantique est combiné à une correspondance par mots-clés et à un petit **glossaire de sigles** (`glossary.json` : APC → approche par compétences, AFEST, EIAH…). Le glossaire évite les ratés sur les acronymes métier.
4. **Repli** : si le modèle ne charge pas (réseau, vieux navigateur), la recherche se fait par mots-clés et glossaire, avec un message discret.

Volume estimé : quelques centaines d'articles par semaine. Une année tient en quelques Mo, ce qui est compatible avec GitHub Pages.

## 6. Dashboard

| Section | Contenu |
|---|---|
| **Barre de recherche + tags** | Recherche sémantique, tags des domaines, sous-tags de catégories, type de contenu, période (4 / 12 / 26 semaines). Filtres mémorisés. |
| **Chiffres de la semaine** | Articles collectés, sujets chauds, termes émergents, écoles actives. |
| **Tendances par sujet** | Volume hebdomadaire par domaine sur la période choisie (barres empilées). Un clic sur un domaine affiche ses catégories. |
| **Nuage de mots** | Termes les plus fréquents des titres et résumés, avec mots vides français et regroupement des formes proches. Un clic applique le filtre. Le nuage respecte les filtres actifs. |
| **Sujets chauds** | Regroupement des titres par n-grammes, scopé par domaine comme dans Veille-IA. Chaque groupe reçoit un label en français généré par le LLM. Seuil : au moins 3 articles provenant d'au moins 2 sources dans la semaine. |
| **Termes émergents** | Termes dont le volume de la semaine atteint au moins 2 × la moyenne des 4 semaines précédentes, avec un plancher. Section actif après 4 semaines d'historique. |
| **Benchmark écoles** | Affiché seulement quand le tag Benchmark est sélectionné. Carte de chaleur écoles × catégories, et dernières actualités par école. |
| **Qui en parle ?** | Pour un terme ou un sujet, les sources classées par nombre d'articles. |
| **Articles** | Titre, source, date, résumé en français, tags et lien. Tri par date ou par pertinence quand une recherche est active. |

Contraintes : responsive (lisible sur téléphone), thème clair et sombre automatique, aucun secret dans la page.

## 7. Collecte et workflows

**Décision : collecte quotidienne, publication le lundi.** Chaque jour, les nouveaux contenus sont collectés et enrichis par le LLM, au plus 300 par exécution pour rester dans le quota gratuit de Groq ; le surplus repasse les jours suivants. Le lundi, le même workflow calcule les sujets chauds, les termes émergents et les vecteurs, puis publie le tableau de bord.

| Fichier | Déclencheur | Rôle |
|---|---|---|
| `daily.yml` | tous les jours à 05:30 UTC + manuel | collecte et enrichissement ; le lundi (ou sur demande), agent de recherche, puis export et déploiement du dashboard |
| `dashboard.yml` | manuel | reconstruit et publie le dashboard sans collecter |
| `sources.yml` | le 1er du mois + manuel | diagnostic de chaque source (tableau dans le résumé du job) |
| `check.yml` | manuel | vérifie les secrets et les services (Supabase, Groq) |
| `ci.yml` | push / PR | tests hors réseau |

Le cron `30 5 * * *` (UTC) tombe à 6 h 30 l'hiver et 7 h 30 l'été à Paris : le tableau de bord est en ligne avant 8 h toute l'année. Le jour de publication est déterminé à l'heure de Paris.

Secrets : `SUPABASE_URL`, `SUPABASE_KEY` (service_role), `GROQ_API_KEY`, `TAVILY_API_KEY` (agent de recherche). Variables : `GROQ_MODEL`, puis plus tard `LLM_BASE_URL` / `LLM_MODEL`.

## 8. Structure

```
main.py            collecte + enrichissement quotidiens     check_sources.py diagnostic des sources
fetchers.py        rss / auto / page / crossref / youtube / gnews   llm.py   client compatible OpenAI (Groq → vLLM)
classify.py        domaine, catégorie, type, résumé FR      topics.py       sujets chauds, termes émergents
embeddings.py      encodage + export binaire                store.py        Supabase / SQLite
entities.py        écoles, alias, filtre de pertinence      models.py       taxonomies, dataclass Article
text.py            normalisation, langue, mots              demo_data.py    données fictives pour l'aperçu
dashboard.py       export site/ (dashboard_template.html)   check_setup.py  diagnostic
discover.py        agent de recherche hebdomadaire (Tavily + Google News), sources proposées
registry.py        liste effective : sources.json + journal source_changes (ajouts / retraits depuis sources.html)
sources.json  entities.json  glossary.json  schema.sql
```

## 9. Passage vers la version privée (plus tard)

- LLM : `LLM_BASE_URL` pointe vers le vLLM de l'école. Aucun autre changement de code.
- Stockage : migration de Supabase vers un PostgreSQL interne (même `schema.sql`).
- Ajout et retrait de sources : ouverts à tous pour l'instant. Derrière l'authentification de la DSI, l'auteur pourra être pris de l'identité de la personne connectée et l'écriture limitée à l'équipe (policy RLS sur `source_changes`).
- Hébergement : remplacement de GitHub Pages par un serveur interne avec l'authentification de la DSI. Le dashboard reste statique, donc il suffit de le servir derrière cette authentification.
- Embeddings : déjà calculés localement, aucune dépendance externe à migrer.

## 10. Feuille de route

Les phases 0 à 3 sont livrées dans le prototype (pipeline, dashboard, recherche sémantique, benchmark). Les termes émergents s'activent seuls après 4 semaines d'historique.

Suites possibles : récap par e-mail ou Teams, favoris partagés, passage sur l'infra de l'école (§9).

## 11. Points ouverts

- [x] Liste des sources (fournie par l'équipe, intégrée).
- [x] Collecte quotidienne.
- [ ] Liste des écoles du benchmark : seul l'Institut Villebon – Georges Charpak est fourni.
- [ ] Validation des taxonomies (§3), des types de contenu et des sources suggérées.
- [ ] Premier passage du workflow « Check sources » : certaines pages sans flux peuvent demander un `link_pattern`.
- [ ] Nom définitif du repo et du produit.
- [ ] Agent de recherche : relire les premières requêtes et sources proposées (résumé du job « Research agent »), puis ajuster la consigne de pertinence `agent` (`classify.py`) et le seuil de proposition (`CANDIDATE_MIN`, `dashboard.py`).
