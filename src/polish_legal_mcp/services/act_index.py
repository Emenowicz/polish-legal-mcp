"""Act index for find_legal_basis: titles, ELI keywords, statute headings and act texts.

Built only from ELI: `/acts/search` pages of statutes and regulations in force; for base statutes
also `/struct` (book/title/division/chapter headings: a code's title says nothing about
"urlop macierzyński", its "Dział ósmy - Uprawnienia pracowników związane z rodzicielstwem" does);
for statutes and regulations the units (articles, §, attachment blocks) of the newest text that
has HTML (consolidated text, else the original).

Two rankers, as measured (scripts/eval_legal_basis.py, 2026-11-01): BM25 in memory over titles +
keywords + headings, and SQLite FTS5 over the texts. find_legal_basis keeps its three best
lexical hits on top and interleaves the two (numbers in docs/architecture.md, decision 9).

Storage: one local SQLite file (`PLMCP_ACT_INDEX_PATH`), a rebuildable cache of public ELI data,
refreshed incrementally: an act's struct and text are refetched only when its text key (newest
consolidated-text notice in ELI references + HTML flag) changes. The index only proposes
candidates; wording and verification always come from ELI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from polish_legal_mcp.clients.eli import EliClient
from polish_legal_mcp.errors import LegalMcpError, UpstreamUnavailableError
from polish_legal_mcp.models import ActId
from polish_legal_mcp.parsers.legal_html import (
    CONTAINER_TYPES,
    UnitLookupError,
    display_name,
    parse_html,
)
from polish_legal_mcp.services.legal import (
    _WORD_RE,
    CONSOLIDATED_TEXTS,
    STOPWORDS,
    gather_limited,
    is_basis_candidate,
    issue_terms,
    summarize_act,
)

logger = logging.getLogger(__name__)

TYPES = ("Ustawa", "Rozporządzenie")
PAGE = 500
HEADING_TYPES = {"book", "titl", "bran", "chpt", "schp"}
# "Przepisy ogólne/przejściowe i końcowe", "Zmiany w przepisach obowiązujących": in every act.
# "Przepisy szczególne dotyczące lokali mieszkalnych" is not generic and stays.
GENERIC_WORDS = {
    "przepisy", "ogólne", "wstępne", "przejściowe", "końcowe", "dostosowujące", "wprowadzające",
    "porządkowe", "uchylające", "i", "oraz",
}  # fmt: skip
GENERIC_PREFIXES = ("zmiany w przepisach", "zmiany w ustawach", "zmiany w obowiązujących")
DATE_RE = re.compile(r"\s*z dnia \d{1,2} \S+ \d{4}\s*r\.\s*-?\s*")
# Fields of an /acts/search item that summarize_act reads.
SUMMARY_KEYS = (
    "ELI", "title", "type", "displayAddress", "volume", "status", "inForce", "announcementDate",
    "promulgation", "entryIntoForce", "validFrom", "repealDate", "expirationDate",
    "legalStatusDate", "textHTML", "textPDF", "keywords", "keywordsNames", "address",
)  # fmt: skip
CONCURRENCY = 4
# Changes are written per batch: bounded memory on the Pi, and a killed build resumes.
BATCH = 200
# One HTML parsed at a time: an lxml tree of the Civil Code is many times its 3 MB of HTML,
# and four at once roughly tripled the build's peak memory (Linux arm64, 2026-11-01).
_PARSING = asyncio.Semaphore(1)
NOTICES_SCANNED = 3  # newest consolidated texts checked for HTML (2025+ ones are PDF-only)
# A refresh that sees far fewer acts than the stored index is treated as an ELI glitch.
MIN_KEPT_SHARE = 0.8
SCHEMA_VERSION = "2"  # a file with another version is rebuilt from scratch
SCHEMA = """
CREATE TABLE IF NOT EXISTS acts(
    eli TEXT PRIMARY KEY, summary TEXT NOT NULL, text_key TEXT NOT NULL, headings TEXT NOT NULL,
    source TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS articles USING fts5(act UNINDEXED, unit UNINDEXED, stems);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def subject(title: str) -> str:
    """Title without the date part: "Ustawa o ochronie praw lokatorów, …"."""
    return DATE_RE.sub(" ", title, count=1).strip().rstrip(".")


def is_generic(heading: str) -> bool:
    lowered = heading.lower()
    words = re.findall(r"\w+", lowered)
    return all(w in GENERIC_WORDS for w in words) or lowered.startswith(GENERIC_PREFIXES)


def headings(nodes: list[dict[str, Any]]) -> list[str]:
    """Distinct, non-generic headings of books, titles, divisions, chapters and subchapters."""
    found: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        if node.get("type") in HEADING_TYPES:
            _, _, text = str(node.get("title") or "").partition(" - ")
            text = text.strip().rstrip(".")
            if text and not is_generic(text) and text not in found:
                found.append(text)
        for child in node.get("children") or []:
            walk(child)

    for node in nodes:
        walk(node)
    return found


def prefix(word: str) -> str:
    """Fixed 5/6-letter prefixes so inflected forms meet ("pracownika"/"pracowników"); on the
    test queries better than find_legal_basis' _stem."""
    return word[:5] if len(word) < 8 else word[:6]


def stems(text: str) -> list[str]:
    """Distinct prefixes of the description's words (stop words dropped)."""
    return list(dict.fromkeys(prefix(w) for w in issue_terms(text)))


def text_tokens(text: str) -> str:
    """Prefixes of every word of an article, repeats kept (term frequency counts in FTS5)."""
    return " ".join(
        prefix(w) for w in _WORD_RE.findall(text.lower()) if len(w) >= 4 and w not in STOPWORDS
    )


def text_units(html: str) -> list[tuple[str, str]]:
    """(unit id, text) of the act's own articles and § (every id part before them must be a
    container, which skips units quoted in a notice's text) and of top-level attachment blocks
    (ELI ids `none_…`: lists and tables of regulations, e.g. prohibited work for minors)."""
    found = []
    for el in parse_html(html).xpath("//*[@id]"):
        unit_id = str(el.get("id"))
        if "unit" not in (el.get("class") or "").split():
            continue
        *parents, last = unit_id.split("-")
        kind = last.split("_")[0]
        in_act = all(p.split("_")[0] in CONTAINER_TYPES for p in parents)
        if (kind in ("arti", "para") and in_act) or (kind == "none" and not parents):
            found.append((unit_id, re.sub(r"\s+", " ", el.text_content()).strip()))
    return found


def unit_label(unit_id: str) -> str:
    """'chpt_2-arti_180_1' → 'art. 180¹', 'para_3' → '§ 3', 'none_I' → 'załącznik' (as
    get_legal_provision and legal texts write them)."""
    kind, _, number = unit_id.rsplit("-", 1)[-1].partition("_")
    if kind == "arti":
        return f"art. {display_name(number)}"
    if kind == "para":
        return f"§ {display_name(number)}"
    return "załącznik"


def text_key(item: dict[str, Any]) -> str:
    """Changes when an act gets a new consolidated text or its own HTML appears."""
    refs = (item.get("references") or {}).get(CONSOLIDATED_TEXTS) or []
    ids = sorted((ActId.parse(r["id"]) for r in refs), key=lambda a: (a.year, a.position))
    return f"{ids[-1].eli if ids else ''}|{bool(item.get('textHTML'))}"


class Bm25:
    """BM25 over word prefixes, pure Python; each text counts a prefix once."""

    def __init__(self, texts: list[str], k1: float = 1.2, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._lengths: list[int] = []
        self._postings: dict[str, list[int]] = {}
        for n, text in enumerate(texts):
            found = set(stems(text))
            self._lengths.append(len(found))
            for stem in found:
                self._postings.setdefault(stem, []).append(n)
        self._avg = (sum(self._lengths) / len(self._lengths)) if self._lengths else 1.0

    def scores(self, query: str) -> dict[int, float]:
        result: dict[int, float] = {}
        total = len(self._lengths)
        for stem in stems(query):
            docs = self._postings.get(stem)
            if not docs:
                continue
            idf = math.log(1 + (total - len(docs) + 0.5) / (len(docs) + 0.5))
            for n in docs:
                norm = 1 + self._k1 * (1 - self._b + self._b * self._lengths[n] / self._avg)
                result[n] = result.get(n, 0.0) + idf * (self._k1 + 1) / norm
        return result


@dataclass
class Hit:
    act: dict[str, Any]  # ELI search item reduced to SUMMARY_KEYS
    label: str  # "tytuł i hasła", "nagłówek „…”" or "art. 13 (tekst jednolity DU/2024/1835)"


@dataclass
class ActIndex:
    """Titles + headings ranked in memory; article texts ranked by FTS5 in ``path``."""

    path: Path
    acts: list[dict[str, Any]]
    headings: list[list[str]]
    built: date
    sources: list[str] = field(
        default_factory=list
    )  # ELI id of the text whose articles are indexed
    _owners: list[tuple[int, str | None]] = field(init=False, repr=False)
    _bm25: Bm25 = field(init=False, repr=False)
    _by_eli: dict[str, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        texts: list[str] = []
        self._owners = []
        for n, act in enumerate(self.acts):
            name = subject(str(act.get("title", "")))
            texts.append(f"{name}. {', '.join(map(str, act.get('keywords') or []))}")
            self._owners.append((n, None))
            for heading in self.headings[n]:
                texts.append(f"{name} — {heading}")
                self._owners.append((n, heading))
        self._bm25 = Bm25(texts)
        self._by_eli = {str(a["ELI"]): n for n, a in enumerate(self.acts)}

    def titles(self, query: str, k: int = 20) -> list[Hit]:
        best: dict[int, tuple[float, str | None]] = {}
        for text_no, score in self._bm25.scores(query).items():
            act_no, heading = self._owners[text_no]
            if score > best.get(act_no, (-math.inf, None))[0]:
                best[act_no] = (score, heading)
        top = sorted(best.items(), key=lambda item: -item[1][0])[:k]
        return [Hit(self.acts[n], f"nagłówek „{h}”" if h else "tytuł i hasła") for n, (_, h) in top]

    async def articles(self, query: str, k: int = 20) -> list[Hit]:
        match = " OR ".join(f'"{s}"' for s in stems(query))
        if not match:
            return []
        rows = await asyncio.to_thread(_search_articles, self.path, match)
        hits: list[Hit] = []
        seen: set[str] = set()
        for eli_id, unit in rows:
            if eli_id in seen or eli_id not in self._by_eli:
                continue
            seen.add(eli_id)
            n = self._by_eli[eli_id]
            source = self.sources[n] if n < len(self.sources) else ""
            text = "tekst pierwotny" if source == eli_id else f"tekst jednolity {source}"
            hits.append(Hit(self.acts[n], f"{unit_label(unit)} ({text})"))
            if len(hits) >= k:
                break
        return hits


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")  # readers are not blocked while a refresh writes
    return db


def _search_articles(path: Path, match: str) -> list[tuple[str, str]]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        sql = "SELECT act, unit FROM articles WHERE articles MATCH ? ORDER BY rank LIMIT 500"
        return db.execute(sql, (match,)).fetchall()
    finally:
        db.close()


@dataclass
class _Stored:
    acts: list[dict[str, Any]]
    headings: list[list[str]]
    sources: list[str]
    keys: dict[str, str]
    built: date | None


def _load(path: Path) -> _Stored:
    db = _connect(path)
    try:
        try:
            version = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        except sqlite3.OperationalError:  # new file
            version = None
        if version != (SCHEMA_VERSION,):
            with db:
                db.executescript(
                    "DROP TABLE IF EXISTS acts; DROP TABLE IF EXISTS articles;"
                    "DROP TABLE IF EXISTS meta;" + SCHEMA
                )
                db.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
        sql = "SELECT summary, headings, text_key, source FROM acts ORDER BY eli"
        rows = db.execute(sql).fetchall()
        built = db.execute("SELECT value FROM meta WHERE key = 'built'").fetchone()
    finally:
        db.close()
    acts = [json.loads(r[0]) for r in rows]
    return _Stored(
        acts,
        [json.loads(r[1]) for r in rows],
        [r[3] for r in rows],
        {str(a["ELI"]): r[2] for a, r in zip(acts, rows, strict=True)},
        date.fromisoformat(built[0]) if built else None,
    )


async def load_act_index(path: Path) -> ActIndex | None:
    """The stored index (start-up), or None when nothing was built yet."""
    stored = await asyncio.to_thread(_load, path)
    if not stored.acts or stored.built is None:
        return None
    return await asyncio.to_thread(
        ActIndex, path, stored.acts, stored.headings, stored.built, stored.sources
    )


@dataclass
class _Change:
    item: dict[str, Any]
    key: str
    headings: list[str] | None = None  # None: same text key, keep the stored ones
    articles: list[tuple[str, str]] = field(default_factory=list)
    source: str = ""  # ELI id of the text the articles come from ("" when none had HTML)


async def _act_text(
    eli: EliClient, item: dict[str, Any]
) -> tuple[str, list[tuple[str, str]], bool]:
    """(source ELI id, unit tokens, complete). Not complete when ELI was unavailable (5xx,
    timeout, rate limit): the act is then stored without its text key and retried on the next
    refresh. Every other failure is ELI's stable answer and that source is skipped, not retried
    on every refresh: 404, malformed JSON, an HTML body over the size limit (a few regulations
    with large attachments), an empty or unparsable HTML body (some old acts)."""
    refs = (item.get("references") or {}).get(CONSOLIDATED_TEXTS) or []
    notices = sorted(
        (ActId.parse(r["id"]) for r in refs), key=lambda a: (a.year, a.position), reverse=True
    )
    sources, complete = [], True
    for notice in notices[:NOTICES_SCANNED]:
        try:
            if (await eli.act(notice)).get("textHTML"):
                sources.append(notice)
                break
        except UpstreamUnavailableError:
            complete = False
        except LegalMcpError:
            continue
    if item.get("textHTML"):
        sources.append(ActId.parse(str(item["ELI"])))
    for source in sources:
        try:
            html = await eli.html_uncached(source)
        except UpstreamUnavailableError:
            complete = False
            continue
        except LegalMcpError:
            continue
        if not html.strip():
            continue
        async with _PARSING:
            try:
                units = await asyncio.to_thread(text_units, html)
            except UnitLookupError:
                continue
            tokens = [(unit, text_tokens(text)) for unit, text in units]
        return source.eli, tokens, complete
    return "", [], complete  # no HTML text (PDF-only): titles and headings are still indexed


def _store(path: Path, changes: list[_Change], gone: list[str], built: date | None) -> None:
    db = _connect(path)
    try:
        with db:
            for eli_id in gone:
                db.execute("DELETE FROM acts WHERE eli = ?", (eli_id,))
                db.execute("DELETE FROM articles WHERE act = ?", (eli_id,))
            for change in changes:
                eli_id = str(change.item["ELI"])
                summary = json.dumps(
                    {k: change.item[k] for k in SUMMARY_KEYS if k in change.item},
                    ensure_ascii=False,
                )
                if change.headings is None:  # same text: metadata (status, dates) may differ
                    db.execute("UPDATE acts SET summary = ? WHERE eli = ?", (summary, eli_id))
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO acts VALUES (?, ?, ?, ?, ?)",
                    (
                        eli_id,
                        summary,
                        change.key,
                        json.dumps(change.headings, ensure_ascii=False),
                        change.source,
                    ),
                )
                db.execute("DELETE FROM articles WHERE act = ?", (eli_id,))
                db.executemany(
                    "INSERT INTO articles VALUES (?, ?, ?)",
                    [(eli_id, unit, tokens) for unit, tokens in change.articles],
                )
            if built is not None:  # set only once the whole refresh has been stored
                db.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (built.isoformat(),))
    finally:
        db.close()


async def refresh_act_index(eli: EliClient, path: Path, today: date) -> ActIndex:
    """Bring the stored index up to date with ELI and return it. The first run fetches every act
    (~1,200 statute structs, ~6,500 HTML texts of statutes and regulations, notice metadata:
    ~15,000 requests); later runs only acts whose text key changed. Parsing and SQLite run in
    worker threads."""
    items: dict[str, dict[str, Any]] = {}
    for act_type in TYPES:
        offset = 0
        while True:
            params: dict[str, str | int] = {
                "type": act_type, "inForce": "1", "limit": PAGE, "offset": offset,
            }  # fmt: skip
            data = await eli.search(params)
            for item in data["items"]:
                if is_basis_candidate(summarize_act(item)):
                    # Keep only what the index stores or needs; search items carry much more.
                    refs = (item.get("references") or {}).get(CONSOLIDATED_TEXTS) or []
                    small = {k: item[k] for k in SUMMARY_KEYS if k in item}
                    small["references"] = {CONSOLIDATED_TEXTS: [{"id": r["id"]} for r in refs]}
                    items.setdefault(str(item["ELI"]), small)
            offset += PAGE
            if offset >= int(data.get("totalCount") or 0) or not data["items"]:
                break
    stored = (await asyncio.to_thread(_load, path)).keys
    if stored and len(items) < MIN_KEPT_SHARE * len(stored):
        raise LegalMcpError(
            f"ELI zwróciło {len(items)} aktów, indeks ma {len(stored)}: odświeżenie pominięte."
        )

    async def change(item: dict[str, Any]) -> _Change:
        key = text_key(item)
        eli_id = str(item["ELI"])
        if stored.get(eli_id) == key:
            return _Change(item, key)
        found, struct_ok = [], True
        if item.get("type") == "Ustawa":  # headings: statutes only (regulations rarely have any)
            try:
                found = headings(await eli.struct_uncached(ActId.parse(eli_id)))
            except UpstreamUnavailableError:
                struct_ok = False
            except LegalMcpError:  # 404, malformed JSON (seen for two statutes): no headings
                pass
        source, units, text_ok = await _act_text(eli, item)
        # An empty key never matches a real key: incomplete fetches are retried.
        return _Change(item, key if struct_ok and text_ok else "", found, units, source)

    fetched = 0
    pending = list(items.values())
    for start in range(0, len(pending), BATCH):
        batch = pending[start : start + BATCH]
        changes = await gather_limited([lambda i=i: change(i) for i in batch], CONCURRENCY)
        fetched += sum(1 for c in changes if c.headings is not None)
        await asyncio.to_thread(_store, path, changes, [], None)
    gone = [eli_id for eli_id in stored if eli_id not in items]
    await asyncio.to_thread(_store, path, [], gone, today)
    logger.info(
        "act index refreshed",
        extra={"fields": {"acts": len(items), "fetched": fetched, "removed": len(gone)}},
    )
    index = await load_act_index(path)
    assert index is not None
    return index
