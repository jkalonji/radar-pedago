from entities import EntityIndex
from text import clean_html, guess_lang, normalize, tokens


def test_normalize_and_clean():
    assert normalize("Pédagogie Été") == "pedagogie ete"
    assert clean_html("<p>Hello&nbsp;<b>world</b> &amp; co</p>") == "Hello world & co"


def test_guess_lang():
    assert guess_lang("Évaluer par compétences en première année d'école d'ingénieurs") == "fr"
    assert guess_lang("Project-based learning in engineering education: a systematic review of the literature") == "en"
    assert guess_lang("Generative AI feedback on programming assignments for students") == "en"


def test_tokens_drop_stopwords_and_numbers():
    toks = tokens("Les 3 enjeux de l’approche par compétences : retour d'expérience 2026")
    assert "les" not in toks and "2026" not in toks and "enjeux" not in toks
    assert "approche" not in toks   # analysis words are generic here
    assert "compétences" in toks and "expérience" in toks


def test_school_tagging_and_sources():
    idx = EntityIndex.load()
    assert idx.tag("L'Institut Villebon - Georges Charpak ouvre un parcours") == ["villebon_charpak"]
    assert idx.tag("Le VILLEBON-CHARPAK innove") == ["villebon_charpak"]
    assert idx.tag("Rien à voir") == []
    src = idx.school_sources()
    assert src and src[0]["domain"] == "benchmark" and src[0]["school"] == "villebon_charpak"
    assert "when:" in src[0]["query"]


def test_relevance_filter():
    idx = EntityIndex.load()
    assert idx.is_relevant("Les étudiants de master face à l'évaluation", "superieur")
    assert idx.is_relevant("Project-based learning for engineering students", "superieur")
    assert not idx.is_relevant("La lecture au cours préparatoire : une étude de cas", "superieur")
    assert idx.is_relevant("n'importe quoi", "inconnu")
