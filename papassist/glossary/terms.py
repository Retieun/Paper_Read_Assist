"""Terms: what the paper defines, found in definition environments and inline."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from ..ingest.document import Block, Document
from .textutil import PH_ANY_RE, PH_RE, canonical_term, split_sentences, words_only


@dataclass
class TermEntry:
    term: str                     # canonical key (lower-case words, singular)
    display: str                  # as written, with $math$
    definition_block: Optional[str]
    definition_text: str          # plain text with $math$ (sentence or whole block)
    source: str                   # paper_definition | paper_inline | llm
    confidence: str
    aliases: list[str] = field(default_factory=list)
    cite_hints: list[str] = field(default_factory=list)
    status: str = "defined"       # defined | undefined
    section: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


EMPH_STOP = {"not", "against", "only", "all", "any", "every", "some", "no", "never", "always", "both", "either", "neither",
             "same", "different", "very", "more", "less", "most", "least", "one", "two", "first", "second", "before", "after",
             "and", "or", "but", "if", "iff", "then", "else", "up to", "a priori", "per se", "e.g", "i.e", "cf", "vs", "via",
             "exactly", "precisely", "does", "do", "is", "are", "was", "were", "be", "been", "has", "have", "had", "in", "on",
             "at", "to", "of", "for", "by", "with", "from", "that", "this", "these", "those", "there", "here", "where", "when",
             "which", "who", "whose", "what", "why", "how", "yes", "no", "true", "false", "proof", "remark", "example", "note",
             "warning", "caution", "sketch", "conversely", "moreover", "however", "hence", "thus", "therefore", "namely"}

INLINE_AFTER = re.compile(r"^\s*(?:\(\w{1,8}\)\s*)?(?:is|are|means|if|when|whenever|to be|as|:|,?\s*i\.e\.|of|iff|if and only if|provided|consists|denotes|refers)\b", re.I)
INLINE_BEFORE = re.compile(r"(?:called|call|termed|named|say that .{1,80}? is|is said to be|are said to be|referred to as|known as|define[sd]? (?:the|a|an)?|we define|definition of|the notion of|the concept of|introduce (?:the|a|an)?|(?:a|an|the) so-called|will be called|shall be called)\s*$", re.I)


def _abbrev_alias(text_after: str) -> Optional[str]:
    m = re.match(r"^\s*\(\s*([A-Za-z][\w\-]{0,10})\s*\)", text_after)
    return m.group(1).lower() if m else None


def _find_emph_span(block: Block, phrase: str) -> Optional[tuple[int, int]]:
    """Locate an emphasised phrase (recorded without placeholders) in block.text_ph."""
    # phrase is plain text with $math$; text_ph has placeholders. Match on the words only.
    words = words_only(phrase)
    if not words:
        return None
    text = block.text_ph
    plain = PH_ANY_RE.sub(lambda m: " " * len(m.group(0)), text)   # same length: offsets stay valid
    pattern = r"\b" + r"\W+".join(re.escape(w) for w in words.split()) + r"\b"
    m = re.search(pattern, plain, re.I)
    if not m:
        return None
    return m.start(), m.end()


TRAILING_STOP = {"of", "in", "on", "for", "to", "with", "by", "at", "from", "and", "or", "the", "a", "an", "as", "into", "over"}


def clean_key(key: str) -> str:
    parts = key.split()
    while parts and parts[-1] in TRAILING_STOP:
        parts.pop()
    while parts and parts[0] in ("the", "a", "an"):
        parts.pop(0)
    return " ".join(parts)


def extra_aliases(display: str, key: str) -> list[str]:
    """'$P$-indexed persistence module' also answers to 'persistence module'."""
    out: list[str] = []
    m = re.match(r"^\s*\$[^$]*\$-\w+\s+(.+)$", display)
    if m:
        alias = canonical_term(m.group(1))
        if alias and alias != key:
            out.append(alias)
    return out


def _looks_like_runin_heading(phrase: str) -> bool:
    p = phrase.strip()
    return bool(re.match(r"^\(?[a-zA-Z0-9]{1,3}\)", p) or p.endswith((".", ":")) or re.match(r"^(Step|Case|Part|Claim)\b", p))


def extract_terms(doc: Document) -> list[TermEntry]:
    entries: list[TermEntry] = []
    seen: set[tuple[str, str]] = set()
    for block in doc.blocks:
        if block.kind in ("title", "heading"):
            continue
        is_def_block = block.kind == "theorem" and block.thm_kind in ("definition", "construction")
        if is_def_block and block.title:
            title_core = re.sub(r"\[[^\]]*\]", " ", block.title)          # drop [Chazal et al. 2016]
            title_core = re.split(r"[,;:(]", title_core)[0]                  # keep the head phrase
            key = clean_key(canonical_term(title_core))
            if key and len(key) >= 3 and (key, block.id) not in seen:
                seen.add((key, block.id))
                entries.append(TermEntry(term=key, display=title_core.strip(), definition_block=block.id, definition_text=block.text,
                                         source="paper_definition", confidence="medium", aliases=[], cite_hints=[c["key"] for c in block.cites],
                                         section=block.section))
        phrases = list(block.emph) + (list(block.strong) if is_def_block else [])
        for phrase in phrases:
            if _looks_like_runin_heading(phrase) and not is_def_block:
                continue
            words = words_only(phrase)
            if not words or words in EMPH_STOP or len(words) < 3:
                continue
            if all(w in EMPH_STOP for w in words.split()):
                continue
            key = clean_key(canonical_term(phrase))
            if not key:
                continue
            span = _find_emph_span(block, phrase)
            after = block.text_ph[span[1]:span[1] + 60] if span else ""
            before = block.text_ph[max(0, span[0] - 90):span[0]] if span else ""
            alias = _abbrev_alias(after)
            # "the \emph{vertex prime} $\mathfrak p_i$": a term immediately followed by its symbol
            term_symbol = re.match(r"^\s*[\u27e6\u27ea](m\d+)[\u27e7\u27eb]", after)
            if is_def_block:
                source, conf = "paper_definition", "high"
                definition_text = block.text
            else:
                if span is None:
                    continue
                preceded_by_article = bool(re.search(r"\b(?:the|a|an)\s*$", before, re.I))
                if not (INLINE_AFTER.search(after) or INLINE_BEFORE.search(before) or (preceded_by_article and term_symbol)):
                    continue
                source, conf = "paper_inline", "medium"
                # the sentence containing the phrase, plus the next one if it starts a clarification
                definition_text = block.text
                sents = split_sentences(block.text_ph)
                for i, (s, e) in enumerate(sents):
                    if s <= span[0] < e:
                        end = e
                        if i + 1 < len(sents) and re.match(r"^\s*(That is|Here|Equivalently|In other words|Explicitly)\b", block.text_ph[sents[i + 1][0]:sents[i + 1][1]]):
                            end = sents[i + 1][1]
                        definition_text = PH_ANY_RE.sub(lambda m: "$" + doc.math[m.group(1)].tex + "$" if m.group(1) in doc.math else "", block.text_ph[s:end]).strip()
                        break
            if (key, block.id) in seen:
                continue
            seen.add((key, block.id))
            cite_hints = [c["key"] for c in block.cites]
            aliases = ([alias] if alias else []) + extra_aliases(phrase, key)
            entries.append(TermEntry(term=key, display=phrase, definition_block=block.id, definition_text=definition_text,
                                     source=source, confidence=conf, aliases=aliases, cite_hints=cite_hints,
                                     section=block.section))
    return entries


def term_symbol_links(doc: Document) -> list[tuple[str, str, str, str]]:
    """(block id, math id, term phrase, sentence) for 'the \emph{term} $symbol$' occurrences."""
    out = []
    for block in doc.blocks:
        for phrase in block.emph:
            span = _find_emph_span(block, phrase)
            if span is None:
                continue
            after = block.text_ph[span[1]:span[1] + 20]
            m = re.match(r"^\s*[\u27e6\u27ea](m\d+)[\u27e7\u27eb]", after)
            before = block.text_ph[max(0, span[0] - 12):span[0]]
            if m and re.search(r"\b(?:the|a|an)\s*$", before, re.I):
                out.append((block.id, m.group(1), phrase, block.text))
    return out
