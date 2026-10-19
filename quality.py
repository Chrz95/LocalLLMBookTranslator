"""Book-specific translation guidance and bounded, source-only context."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from settings_manager import atomic_write

GLOSSARY_CATEGORIES = ("character", "title", "geography", "magical_ability", "faction",
                       "invented_language", "recurring_metaphor", "other")
GLOSSARY_CATEGORY_LABELS = ("Character names", "Titles & honorifics", "Geography", "Magical abilities",
                            "Factions", "Invented languages", "Recurring metaphors", "Other special terms")
MAX_GLOSSARY_ENTRIES = 5000


@dataclass(frozen=True)
class QualityProfile:
    use_context: bool = True
    neighboring_chunks: int = 2
    context_chars: int = 8000
    glossary: list[dict[str, str]] = field(default_factory=list)
    character_notes: str = ""
    style_guide: str = ""
    examples: list[dict[str, str]] = field(default_factory=list)

    def validate(self) -> None:
        if type(self.use_context) is not bool:
            raise ValueError("Context preference must be a boolean.")
        if type(self.neighboring_chunks) is not int or not 0 <= self.neighboring_chunks <= 5:
            raise ValueError("Neighboring chunks must be between 0 and 5.")
        if type(self.context_chars) is not int or not 1000 <= self.context_chars <= 24000:
            raise ValueError("Context budget must be between 1,000 and 24,000 characters.")
        for text in (self.character_notes, self.style_guide):
            if not isinstance(text, str) or len(text) > 6000:
                raise ValueError("Character notes and style guide must each be at most 6,000 characters.")
        for entries, required, allowed, maximum in (
            (self.glossary, {"source", "target"},
             {"source", "target", "notes", "category", "evidence", "origin", "occurrences"}, MAX_GLOSSARY_ENTRIES),
            (self.examples, {"source", "translation"}, {"source", "translation"}, 200)):
            if not isinstance(entries, list) or len(entries) > maximum:
                raise ValueError(f"Guidance must be a list of at most {maximum} entries.")
            for entry in entries:
                if (not isinstance(entry, dict) or not required.issubset(entry) or not set(entry).issubset(allowed)
                    or any(not isinstance(v, str) or len(v) > 8000 for v in entry.values())
                    or any(not entry[k].strip() for k in required)):
                    raise ValueError("Invalid glossary or approved-example entry.")
        for entry in self.glossary:
            if entry.get("category", "other") not in GLOSSARY_CATEGORIES:
                raise ValueError("Invalid glossary category.")
            if "occurrences" in entry and not entry["occurrences"].isdigit():
                raise ValueError("Glossary occurrence counts must contain digits.")
        names = [entry["source"].strip().casefold() for entry in self.glossary]
        if len(set(names)) != len(names):
            raise ValueError("Glossary source terms must be unique.")

    @classmethod
    def load(cls, path: Path) -> QualityProfile:
        if not path.exists():
            return cls()
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict) or values.get("version") != 1:
            raise ValueError("Invalid quality profile. Repair quality.json before changing guidance.")
        try:
            profile = cls(**values["profile"])
            profile.validate()
            return profile
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid quality profile fields.") from exc

    def save(self, path: Path) -> None:
        self.validate()
        atomic_write(path, json.dumps({"version": 1, "profile": asdict(self)}, ensure_ascii=False, indent=2))

    def guidance(self, source: str) -> dict:
        """Retrieve relevant terms and human-approved examples within fixed budgets."""
        glossary = []
        remaining = 6000
        for entry in self.glossary:
            if re.search(r"(?<!\w)" + re.escape(entry["source"]) + r"(?!\w)", source, re.I):
                # Evidence/provenance is for human review, not repeated in every request.
                guidance = {k: v for k, v in entry.items() if k in ("source", "target", "notes", "category")}
                cost = len(json.dumps(guidance, ensure_ascii=False))
                if cost <= remaining:
                    glossary.append(guidance)
                    remaining -= cost
        words = set(re.findall(r"\w+", source.casefold()))
        ranked = sorted(enumerate(self.examples), key=lambda item: (
            len(words & set(re.findall(r"\w+", item[1]["source"].casefold()))), item[0]), reverse=True)
        examples = []
        remaining = 6000
        for _, entry in ranked:
            if not words.intersection(re.findall(r"\w+", entry["source"].casefold())):
                continue
            cost = len(entry["source"]) + len(entry["translation"])
            if cost <= remaining:
                examples.append(entry)
                remaining -= cost
            if len(examples) == 3:
                break
        return {"glossary": glossary, "character_notes": self.character_notes,
                "style_guide": self.style_guide, "approved_examples": examples}


def build_context(book, chunks, index: int, profile: QualityProfile, paragraph=None) -> dict:
    """Never depend on unfinished neighboring translations or cross EPUB files."""
    if not profile.use_context:
        return {}
    target = chunks[index]
    unit = book.units[target.unit_index]
    paragraph = paragraph if paragraph is not None else [c for c in chunks if c.unit_index == target.unit_index or
                 (unit.member and unit.paragraph >= 0 and
                  book.units[c.unit_index].member == unit.member and
                  book.units[c.unit_index].paragraph == unit.paragraph)]
    nearby = list(chunks[max(0, index-profile.neighboring_chunks):index]) + list(
        chunks[index+1:index+1+profile.neighboring_chunks])
    candidates = {c.index: c for c in paragraph + nearby if c.index != index and
                  (not unit.member or book.units[c.unit_index].member == unit.member)}
    # Spend the budget on the nearest context first, then restore source order.
    context = []
    budget = profile.context_chars
    for candidate in sorted(candidates.values(), key=lambda c: (abs(c.index-index), c.index)):
        if budget <= 0:
            break
        value = candidate.text[:budget] if candidate.index > index else candidate.text[-budget:]
        if value:
            context.append({"chunk_id": candidate.index, "text": value,
                            "same_paragraph": candidate in paragraph,
                            "truncated": len(value) < len(candidate.text)})
            budget -= len(value)
    context.sort(key=lambda item: item["chunk_id"])
    return {"target_chunk_id": index, "source_segments": context,
            "context_truncated": len(context) < len(candidates) or any(c["truncated"] for c in context)}
