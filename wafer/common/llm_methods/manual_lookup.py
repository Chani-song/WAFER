"""Deterministic page-indexed lookup over the bundled BatPaC manual."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

MANUAL_PATH = (
    Path(__file__).resolve().parent
    / "semantic_assets"
    / "batpac_v5_manual_full_extracted.txt"
)
MANUAL_TITLE = "A Manual for BatPaC v5.0"
MANUAL_WORKBOOK = "BatPaC 5.0 2022-02-09.xlsm"
TOKEN_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)?", re.IGNORECASE)
SECTION_RE = re.compile(
    r"^(?:\d+(?:\.\d+){0,3}\s+)?[A-Z][A-Za-z0-9 ,/&()\-]{3,100}$"
)

# Deterministic concept expansion supplies the semantic component of the
# hybrid lexical/concept score without requiring an external embedding model.

PAGE_SECTION_BOUNDARIES: Tuple[Tuple[int, str], ...] = (
    (1, "Front matter"),
    (21, "1. Introduction"),
    (24, "2. Battery Design for BatPaC"),
    (32, "3. Spreadsheet Model and Instructions"),
    (54, "4. Adding New Chemistries and Vehicles"),
    (67, "5. Modeling of Battery Design and Performance"),
    (68, "5.2 Cell Design and Governing Equations"),
    (70, "5.2.2 ASI Model"),
    (77, "5.2.3 Model Solution and Additional Constraints"),
    (78, "5.2.5 Cell Dimensions"),
    (82, "5.3 Module Design"),
    (85, "5.4 Module Row Rack Design"),
    (86, "5.5 Pack Design"),
    (91, "5.6 Sizing Module and Pack Connections"),
    (92, "5.7 Influence of Vehicle Type"),
    (95, "6. Charging of Electric Vehicle Batteries"),
    (101, "7. Battery Management System"),
    (108, "8. Thermal Management System"),
    (120, "9. Manufacturing Cost"),
    (143, "10. Recoverable Materials"),
    (145, "11. References"),
)


def _section_for_page(page_number: int) -> str:
    section = PAGE_SECTION_BOUNDARIES[0][1]
    for start_page, title in PAGE_SECTION_BOUNDARIES:
        if page_number < start_page:
            break
        section = title
    return section

CONCEPT_ALIASES: Mapping[str, Tuple[str, ...]] = {
    "cathode": ("positive", "electrode", "thickness", "active", "material"),
    "thickness": ("electrode", "delta", "positive", "asi", "area"),
    "x_t": ("cathode", "positive", "electrode", "thickness"),
    "chemistry": ("electrode", "couple", "lfp", "nmc811", "ocv", "capacity"),
    "lfp": ("lithium", "iron", "phosphate", "chemistry", "electrode"),
    "nmc811": ("nickel", "manganese", "cobalt", "chemistry", "electrode"),
    "power": ("current", "voltage", "impedance", "asi", "pulse"),
    "energy": ("capacity", "voltage", "electrode", "area", "thickness"),
    "voltage": ("current", "series", "ocv", "power"),
    "capacity": ("energy", "electrode", "area", "thickness", "parallel"),
    "asi": ("area", "specific", "impedance", "current", "electrode"),
    "impedance": ("asi", "resistance", "current", "voltage", "electrode"),
    "module": ("cells", "interconnect", "row", "pack", "enclosure"),
    "row": ("module", "rack", "pack", "bus", "bar", "width", "length"),
    "pack": ("module", "row", "length", "width", "jacket", "bus", "bar"),
    "cost": ("manufacturing", "material", "pack", "price", "dollar"),
}


@dataclass(frozen=True)
class ManualChunk:
    chunk_id: str
    page: int
    section: str
    text: str
    tokens: Tuple[str, ...]


def _tokens(text: str) -> List[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def _expanded_query_tokens(query: str) -> List[str]:
    base = _tokens(query)
    expanded = list(base)
    for token in base:
        expanded.extend(CONCEPT_ALIASES.get(token, ()))
    # Preserve stable order while removing duplicates.
    return list(dict.fromkeys(expanded))


def _section_title(page_text: str, fallback: str, page_number: int) -> str:
    mapped = _section_for_page(page_number)
    lines = [" ".join(raw.split()).strip() for raw in page_text.splitlines()]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines[:20]):
        if re.fullmatch(r"\d+(?:\.\d+){0,3}", line) and index + 1 < len(lines):
            title = lines[index + 1]
            if (
                len(title) <= 100
                and title[:1].isupper()
                and not title.startswith(("Figure", "Table"))
                and not any(symbol in title for symbol in ("=", "Eq.", "where "))
            ):
                return f"{line} {title}"
        if re.match(r"^\d+(?:\.\d+){0,3}\s+[A-Z]", line):
            if len(line) <= 110 and not line.startswith(("Figure", "Table")):
                return line
    return mapped or fallback


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4.0))


class ManualLookupIndex:
    """Small deterministic BM25 plus concept-expansion retrieval index."""

    def __init__(self, manual_text: str) -> None:
        self.manual_text = manual_text
        self.manual_sha256 = _sha256_text(manual_text)
        raw_pages = manual_text.split("\f")
        self.chunks: List[ManualChunk] = []
        last_section = MANUAL_TITLE
        for page_number, raw_page in enumerate(raw_pages, start=1):
            page_text = "\n".join(line.rstrip() for line in raw_page.splitlines()).strip()
            if not page_text:
                continue
            section = _section_title(page_text, last_section, page_number)
            last_section = section
            chunk_id = f"manual_page_{page_number:03d}"
            self.chunks.append(
                ManualChunk(
                    chunk_id=chunk_id,
                    page=page_number,
                    section=section,
                    text=page_text,
                    tokens=tuple(_tokens(page_text)),
                )
            )
        self.document_count = len(self.chunks)
        self.avg_length = (
            sum(len(chunk.tokens) for chunk in self.chunks) / self.document_count
            if self.document_count
            else 1.0
        )
        self.document_frequency: Dict[str, int] = {}
        for chunk in self.chunks:
            for token in set(chunk.tokens):
                self.document_frequency[token] = self.document_frequency.get(token, 0) + 1

    def _idf(self, token: str) -> float:
        frequency = self.document_frequency.get(token, 0)
        return math.log(1.0 + (self.document_count - frequency + 0.5) / (frequency + 0.5))

    def _score(self, query: str, chunk: ManualChunk) -> float:
        query_tokens = _expanded_query_tokens(query)
        if not query_tokens:
            return 0.0
        counts: Dict[str, int] = {}
        for token in chunk.tokens:
            counts[token] = counts.get(token, 0) + 1
        k1 = 1.5
        b = 0.75
        length_norm = 1.0 - b + b * len(chunk.tokens) / max(self.avg_length, 1.0)
        score = 0.0
        for token in query_tokens:
            tf = counts.get(token, 0)
            if tf:
                score += self._idf(token) * (tf * (k1 + 1.0)) / (tf + k1 * length_norm)
        lowered_query = " ".join(_tokens(query))
        lowered_text = " ".join(chunk.tokens)
        if lowered_query and lowered_query in lowered_text:
            score += 5.0
        # Favor pages whose section label directly contains a query concept.
        section_tokens = set(_tokens(chunk.section))
        score += 0.35 * len(section_tokens.intersection(query_tokens))
        if chunk.page < 21 and not {"symbol", "abbreviation"}.intersection(query_tokens):
            score *= 0.45
        return score

    def lookup(
        self,
        query: str,
        top_k: int = 3,
        max_return_tokens: int = 1800,
    ) -> Dict[str, Any]:
        cleaned_query = " ".join(str(query).split()).strip()
        if not cleaned_query:
            raise ValueError("Manual lookup query must be non-empty")
        ranked = sorted(
            ((self._score(cleaned_query, chunk), chunk) for chunk in self.chunks),
            key=lambda item: (-item[0], item[1].page),
        )
        selected: List[Dict[str, Any]] = []
        remaining = max(1, int(max_return_tokens))
        for score, chunk in ranked:
            if len(selected) >= max(1, int(top_k)) or remaining <= 0:
                break
            if score <= 0.0:
                continue
            text = chunk.text
            estimated = estimate_tokens(text)
            if estimated > remaining:
                # Character truncation is deterministic and logged explicitly.
                text = text[: max(200, remaining * 4)].rstrip()
                estimated = estimate_tokens(text)
            selected.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "section": chunk.section,
                    "pages": [chunk.page],
                    "score": round(float(score), 8),
                    "estimated_tokens": estimated,
                    "text": text,
                }
            )
            remaining -= estimated
        return {
            "source": MANUAL_TITLE,
            "manual_workbook": MANUAL_WORKBOOK,
            "manual_sha256": self.manual_sha256,
            "query": cleaned_query,
            "top_k": int(top_k),
            "max_return_tokens": int(max_return_tokens),
            "returned_estimated_tokens": sum(item["estimated_tokens"] for item in selected),
            "results": selected,
        }


@lru_cache(maxsize=1)
def default_manual_index() -> ManualLookupIndex:
    text = MANUAL_PATH.read_text(encoding="utf-8")
    return ManualLookupIndex(text)


def lookup_batpac_manual(
    query: str,
    top_k: int = 3,
    max_return_tokens: int = 1800,
) -> Dict[str, Any]:
    return default_manual_index().lookup(
        query=query,
        top_k=top_k,
        max_return_tokens=max_return_tokens,
    )
