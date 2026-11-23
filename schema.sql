-- Radar Pédago — schéma Supabase (Postgres).
-- À exécuter UNE fois dans Supabase : SQL Editor > New query > coller > Run.

CREATE TABLE IF NOT EXISTS articles (
    url                    TEXT PRIMARY KEY,
    title                  TEXT NOT NULL,                     -- titre d'origine
    title_fr               TEXT DEFAULT '',                   -- titre en français (traduit si besoin)
    source                 TEXT,
    domain                 TEXT NOT NULL,                     -- domaine principal (celui de la source)
    domains                JSONB NOT NULL DEFAULT '[]',       -- domaine principal + domaines secondaires
    published              TIMESTAMPTZ NOT NULL,
    published_is_estimated BOOLEAN NOT NULL DEFAULT FALSE,    -- vrai pour les liens détectés sur une page (date = découverte)
    description            TEXT DEFAULT '',                   -- extrait du flux / résumé d'auteur, jamais le texte intégral
    lang                   TEXT,
    kind                   TEXT NOT NULL DEFAULT 'article',   -- article | paper | page | video
    summary                TEXT DEFAULT '',                   -- résumé en français
    category               TEXT,
    content_type           TEXT,
    entities               JSONB NOT NULL DEFAULT '[]',       -- écoles de entities.json
    classified             BOOLEAN NOT NULL DEFAULT FALSE,    -- faux tant que le LLM n'est pas passé (repli par règles)
    relevant               BOOLEAN NOT NULL DEFAULT TRUE,     -- faux = écarté par le filtre 'superieur' ou 'agent' (gardé pour ne pas le retraiter)
    origin                 TEXT NOT NULL DEFAULT 'source',    -- 'source' (sources.json) | 'agent' (agent de recherche, discover.py)
    agent_query            TEXT DEFAULT '',                   -- requête de l'agent qui a trouvé le contenu
    site                   TEXT DEFAULT '',                   -- site de l'éditeur (contenus de l'agent : sources proposées)
    collected_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Projets créés avant l'agent de recherche : ajoute les colonnes manquantes (sans effet si elles existent déjà).
ALTER TABLE articles ADD COLUMN IF NOT EXISTS origin      TEXT NOT NULL DEFAULT 'source';
ALTER TABLE articles ADD COLUMN IF NOT EXISTS agent_query TEXT DEFAULT '';
ALTER TABLE articles ADD COLUMN IF NOT EXISTS site        TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS articles_published_idx ON articles (published DESC);
CREATE INDEX IF NOT EXISTS articles_classified_idx ON articles (classified) WHERE classified = FALSE;

-- Liens déjà vus sur les pages sans flux (détection des nouveautés).
CREATE TABLE IF NOT EXISTS seen_links (
    source     TEXT NOT NULL,
    url        TEXT NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, url)
);

-- Ajouts et retraits de sources faits depuis la page « Sources de la veille » (journal : on n'y modifie ni n'y
-- supprime rien, la dernière action sur un nom l'emporte). Fusionnés avec sources.json par registry.py.
CREATE TABLE IF NOT EXISTS source_changes (
    id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    action TEXT NOT NULL CHECK (action IN ('add', 'remove', 'restore')),
    name   TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    domain TEXT CHECK (domain IS NULL OR domain IN
                       ('design_ux', 'pedagogie', 'recherche', 'enseignement_sup', 'benchmark', 'innovation_ia')),
    url    TEXT CHECK (url IS NULL OR (url ~* '^https?://[^[:space:]]+$' AND char_length(url) <= 500)),
    author TEXT NOT NULL DEFAULT '' CHECK (char_length(author) <= 80),
    CHECK (action <> 'add' OR (domain IS NOT NULL AND url IS NOT NULL))
);

-- Le pipeline utilise la clé service_role (côté GitHub Actions uniquement) : activer RLS sans policy
-- garantit que la clé publique anon ne peut rien lire.
ALTER TABLE articles   ENABLE ROW LEVEL SECURITY;
ALTER TABLE seen_links ENABLE ROW LEVEL SECURITY;

-- La page (clé publique anon / publishable) lit le journal des sources et y ajoute des lignes ; pour l'instant
-- n'importe quel visiteur le peut. Elle ne peut ni modifier ni supprimer une ligne, ni choisir `id` ou `at`.
ALTER TABLE source_changes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS source_changes_read ON source_changes;
DROP POLICY IF EXISTS source_changes_add  ON source_changes;
CREATE POLICY source_changes_read ON source_changes FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY source_changes_add  ON source_changes FOR INSERT TO anon, authenticated WITH CHECK (true);
REVOKE ALL ON source_changes FROM anon, authenticated;
GRANT SELECT ON source_changes TO anon, authenticated;
GRANT INSERT (action, name, domain, url, author) ON source_changes TO anon, authenticated;
