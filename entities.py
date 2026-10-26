"""Tracked schools (benchmark): loading, mention tagging, relevance filter for generalist journals."""

import json
import re
from dataclasses import dataclass

from text import normalize


@dataclass
class School:
    id: str
    label: str
    aliases: list[str]
    group: str = ""
    gnews: bool = False
    gnews_query: str = ""


class EntityIndex:
    def __init__(self, data: dict):
        self.schools: list[School] = []
        for s in data.get("schools", []):
            if s.get("enabled", True) is False:
                continue
            self.schools.append(School(
                id=s["id"], label=s["label"], aliases=s.get("aliases") or [s["label"]], group=s.get("group", ""),
                gnews=bool(s.get("gnews", False)), gnews_query=s.get("gnews_query") or f'"{s["label"]}"',
            ))
        self.by_id = {s.id: s for s in self.schools}
        # One regex per school; whole-word, accent/case insensitive.
        self._patterns = {
            s.id: re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % "|".join(
                re.escape(normalize(a)) for a in sorted(s.aliases, key=len, reverse=True)))
            for s in self.schools
        }
        self._relevance: dict[str, tuple[list, list]] = {}
        for name, kw in (data.get("relevance_keywords") or {}).items():
            self._relevance[name] = ([self._kw_pattern(k) for k in kw.get("strong", [])],
                                     [self._kw_pattern(k) for k in kw.get("weak", [])])

    @staticmethod
    def _kw_pattern(keyword: str) -> "re.Pattern":
        """Whole-word keyword regex (plural 's' tolerated)."""
        return re.compile(r"(?<![a-z0-9])" + re.escape(normalize(keyword)) + r"(?:s|es)?(?![a-z0-9])")

    @classmethod
    def load(cls, path: str = "entities.json") -> "EntityIndex":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def tag(self, text: str) -> list[str]:
        """Ids of every tracked school mentioned in `text`."""
        norm = normalize(text)
        return [sid for sid, pat in self._patterns.items() if pat.search(norm)]

    def is_relevant(self, text: str, filter_name: str) -> bool:
        """Keyword relevance for a filtered source: one strong keyword or two weak ones (unknown filter = keep)."""
        if filter_name not in self._relevance:
            return True
        strong, weak = self._relevance[filter_name]
        norm = normalize(text)
        if any(p.search(norm) for p in strong):
            return True
        return sum(1 for p in weak if p.search(norm)) >= 2

    def school_sources(self) -> list[dict]:
        """One Google News source per school flagged `gnews`, in the benchmark domain."""
        return [{"name": f"{s.label} (Google News)", "domain": "benchmark", "type": "gnews",
                 "query": f"{s.gnews_query} when:3d", "school": s.id} for s in self.schools if s.gnews]

    def alias_words(self) -> set[str]:
        """Normalized single tokens of school names (kept out of the word cloud)."""
        words: set[str] = set()
        for s in self.schools:
            for a in s.aliases:
                words.update(re.findall(r"[a-z0-9]+", normalize(a)))
        return words

    def label(self, school_id: str) -> str:
        s = self.by_id.get(school_id)
        return s.label if s else school_id
