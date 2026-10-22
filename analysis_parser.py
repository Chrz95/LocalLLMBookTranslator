"""Normalize common model JSON variations without accepting ungrounded terms."""
from __future__ import annotations

import json
import re
import unicodedata

from lmstudio import APIError
from quality import GLOSSARY_CATEGORIES, GLOSSARY_CATEGORY_LABELS


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def keys(value: dict) -> dict:
    result = {}
    for key, item in value.items():
        name = re.sub(r"[\s-]+", "_", key.strip().casefold())
        if name in result:
            raise APIError(f"Duplicate analysis field: {name}.")
        result[name] = item
    return result


def field(entry: dict, name: str, aliases: tuple[str, ...] = ()):
    values = [entry[k] for k in (name, *aliases) if k in entry and entry[k] is not None]
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise APIError(f"Conflicting values for '{name}'.")
    return values[0]


def text(value, name: str, limit: int, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise APIError(f"'{name}' must be text; received {type(value).__name__}.")
    value = value.strip()
    if len(value) > limit:
        raise APIError(f"'{name}' exceeds {limit} characters.")
    if not value and not optional:
        raise APIError(f"'{name}' must not be empty.")
    return value


def category(value) -> str:
    if value is None or value == "":
        return "other"
    value = text(value, "category", 80)
    key = re.sub(r"[\s-]+", "_", value.casefold())
    aliases = {re.sub(r"[\s-]+", "_", label.casefold()): name
               for name, label in zip(GLOSSARY_CATEGORIES, GLOSSARY_CATEGORY_LABELS)}
    for canonical, choices in (
        ("character", ("characters", "character_name", "name", "names", "person", "people")),
        ("title", ("titles", "rank", "ranks", "honorific", "honorifics", "titles_and_honorifics")),
        ("geography", ("place", "places", "place_name", "place_names", "location", "locations")),
        ("magical_ability", ("magic", "magical_abilities", "magic_system", "spell", "spells")),
        ("faction", ("factions", "organization", "organizations", "organisation", "organisations", "order")),
        ("invented_language", ("invented_languages", "fictional_language", "fictional_languages")),
        ("recurring_metaphor", ("metaphor", "metaphors", "recurring_metaphors", "motif", "motifs")),
        ("other", ("term", "terms", "other_special_terms")),
    ):
        aliases.update(dict.fromkeys(choices, canonical))
    result = aliases.get(key, key)
    if result not in GLOSSARY_CATEGORIES:
        raise APIError(f"Unsupported category '{value}'; use one of the eight glossary categories.")
    return result


def source_quote(passage: str, term: str) -> str:
    """Find the actual source span, preserving spelling, whitespace and Unicode."""
    clusters = []
    for index, char in enumerate(passage):
        if unicodedata.combining(char) and clusters:
            clusters[-1] = (clusters[-1][0], index + 1)
        else:
            clusters.append((index, index + 1))
    characters, starts, ends = [], [], []
    for start, end in clusters:
        folded = unicodedata.normalize("NFD", unicodedata.normalize("NFD", passage[start:end]).casefold())
        for char in folded:
            if char.isspace():
                if characters and characters[-1] == " ":
                    ends[-1] = end
                    continue
                char = " "
            characters.append(char)
            starts.append(start)
            ends.append(end)
    needle = " ".join(unicodedata.normalize("NFD", unicodedata.normalize("NFD", term).casefold()).split())
    position = "".join(characters).find(needle)
    if not needle or position < 0:
        raise APIError("Source term does not occur in the analyzed passage.")
    return passage[starts[position]:ends[position + len(needle) - 1]]


def grounded_evidence(entry: dict, passage: str, source: str) -> str:
    """Model quotations are optional; unsupported quotations are never retained."""
    try:
        evidence = field(entry, "evidence", ("quote", "source_quote", "quotation"))
    except APIError:
        evidence = None  # Conflicting model quotes do not invalidate a verified source term.
    if isinstance(evidence, str):
        evidence = evidence.strip()
        if (evidence and len(evidence) <= 600 and normalized(evidence) in normalized(passage)
                and normalized(source) in normalized(evidence)):
            return evidence
    return source_quote(passage, source)


def normalize_entry(value, passage: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise APIError(f"Entry must be an object; received {type(value).__name__}.")
    entry = keys(value)
    source = text(field(entry, "source", ("source_term", "term", "original", "original_term", "name")), "source", 200)
    target = text(field(entry, "target", ("target_term", "translation", "preferred_translation", "target_translation")), "target", 600)
    kind = category(field(entry, "category", ("type", "term_type")))
    notes = field(entry, "notes", ("note", "treatment", "treatment_notes", "usage_notes"))
    if isinstance(notes, list) and all(isinstance(item, str) for item in notes):
        notes = "; ".join(notes)
    notes = text(notes, "notes", 2000, optional=True)
    if normalized(source) not in normalized(passage):
        raise APIError("Source term does not occur in the analyzed passage.")
    evidence = grounded_evidence(entry, passage, source)
    return {"source": source, "target": target, "category": kind, "notes": notes, "evidence": evidence}


def validate_terms(data, passage: str, on_rejected=None) -> list[dict[str, str]]:
    if isinstance(data, dict):
        data = field(keys(data), "terms", ("glossary", "entries"))
    if not isinstance(data, list):
        raise APIError("Novel analysis must return a JSON terms list.")
    if len(data) > 60:
        raise APIError("Novel analysis returned more than 60 terms in one passage.")
    seen, entries = set(), []
    for number, value in enumerate(data, 1):
        try:
            entry = normalize_entry(value, passage)
        except APIError as exc:
            if on_rejected is None:
                raise APIError(f"Novel analysis entry {number}: {exc}") from exc
            on_rejected({"entry_number": number, "entry": value, "reason": str(exc)})
            continue
        key = normalized(entry["source"])
        if key not in seen:
            entries.append(entry)
            seen.add(key)
    return entries


def parse_terms(raw: str, passage: str, on_rejected=None) -> list[dict[str, str]]:
    raw = raw.strip().lstrip("\ufeff")
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\s*```", raw, re.I | re.S)
    if fenced:
        raw = fenced[1]

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    try:
        data = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise APIError(f"Novel analysis returned invalid JSON: {exc}") from exc
    return validate_terms(data, passage, on_rejected=on_rejected)
