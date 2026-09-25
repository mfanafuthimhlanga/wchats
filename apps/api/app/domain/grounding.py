"""Grounding: is each sentence of an answer carried by the retrieved text? A rule, not a Judge.

WHAT IT MEASURES
    An answer is split into sentences. Each sentence is scored against the
    passages the agent retrieved: the share of the sentence's content words that
    the best passage carries, or, when that falls under the floor, that the best
    passage and the passage adding the most words it lacks carry together (two
    words at least, and never for a sentence asserting a reason or a
    consequence), plus every number in the sentence having to appear somewhere
    in the retrieved text. A sentence at or above the carried floor with no
    missing number is grounded. The answer's score is the grounded
    share of its scoreable sentences, and that share is what `eval_results`
    stores under `faithfulness` (ADR 0015): the column keeps its name and its
    gate, the instrument behind it is this rule.

WHY A RULE
    A model-generated label never gates a deploy (CLAUDE.md, ADR 0012). The
    faithfulness Judge's recall on planted sentences was 10 of 10 and its
    precision never settled in six calibration passes; on one real run it flagged
    289 claims over 46 answers, most of them an agent speculating. A rule flags by
    a threshold anyone can read, is measured on the same planted benchmark with
    no model call, and when it flags wrongly the fix is a number here, not a
    labelling sitting.

THE RULE IS THE READING AID, PROMOTED
    Word overlap with suffix stemming to a four-letter floor, a tie between
    passages broken by density, the passage cut at sentence ends to about 300
    characters: the overlap rule the claims bench and the console's reading aid
    use to light a passage. Since #298 both aids port every rule here as a named
    twin and show this module's `reason` on the card, so what the owner sees lit
    is what the gate scored; `tests/unit/test_claims_benchmark.py` runs the aids'
    fixture through `ground()` to keep it so.

A DECLINE ASSERTS NOTHING
    "The documentation does not specify the port" carries no claim the
    retrieved text could support, so it is grounded with its reason saying so.
    Three fences keep a claim from hiding behind one: the subject must name the
    DOCUMENTS (corpus, documentation, sources, knowledge base), never the system
    ("the workflow does not roll back" is a claim); the verb must be one of
    saying (specify, state, mention, document), never one of doing; and the
    sentence must be the decline alone, so "the corpus does not name the port;
    it documents Playwright" is scored on its words like any other sentence.

Rung: `app.domain` imports the standard library and its domain siblings. This
module imports the standard library and `app.domain.judge_identity`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.judge_identity import JudgeIdentity

#: The identity `eval_results` stores for a row this rule scored. `model` names
#: the rule so a calibration reader can tell it from a Judge; the version moves
#: whenever a number below moves, so rows scored under two rules never share a
#: calibration population.
GROUNDING_RULE_VERSION = "grounding-v2"
GROUNDING_MODEL = "rule:grounding"
GROUNDING_IDENTITY = JudgeIdentity(
    model=GROUNDING_MODEL, reasoning_effort="none", prompt_version=GROUNDING_RULE_VERSION
)

#: A sentence is carried when at least this share of its content words appear in
#: the best passage, or in the best two together. Measured on the planted benchmark (tests/unit/test_grounding.py):
#: 0.4 and 0.5 flag the same nine of ten plants and 0.4 flags fewer real sentences,
#: and 0.4 is where the reading aids already tint a sentence bone.
CARRIED_FLOOR = 0.4

#: A sentence that asserts a relation between facts: a reason, a consequence, a
#: purpose. Two passages can carry the facts and neither the relation, so such a
#: sentence gets no second reading; it stands or falls on one passage.
_INFERENCE_RE = re.compile(
    r"\b(because|therefore|thus|hence|consequently|so that|which means|this means|"
    r"that means|as a result|in order to|favou?rs?|why)\b",
    re.IGNORECASE,
)

#: How many of a sentence's content words the second passage has to add beyond
#: the best one before the two are read together. One shared word is what any
#: passage on the same subject offers by accident.
SECOND_PASSAGE_MIN_WORDS = 2

#: A passage is a run of the retrieved chunk cut at sentence ends, at least this long.
PASSAGE_MIN_CHARS = 300

_STOP = frozenset(
    (
        "the and for that with this from are was were has have had not but all any can you your they them "
        "their there then than when what which who whom how why into onto over under out off per via about after before "
        "between during each every some such only also more most other same both been being does did doing done will would "
        "should could may might must shall here where while because since though although upon among along around these "
        "those very much many just like within without across toward towards doesn didn aren wasn weren hasn haven"
    ).split()
)
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
# Python's look-behind is fixed width, so the optional closing quote (straight or curly) is two alternatives
_SENT_RE = re.compile("(?:(?<=[.!?])|(?<=[.!?][\"')\\]\u201d\u2019]))\\s+(?=[*`\"'(\\[A-Z0-9\u201c\u2018])")
#: A figure: 1,000,000 with its three-digit groups, 5.5, 20480, never "3,4" read as one
_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
#: A whole sentence that declines rather than asserts: the documents, as subject,
#: do not SAY something. A system noun, a doing verb or a second clause is not one.
_DOC_NOUN = r"(?:corpus|documentation|documents?|docs|material|knowledge base|sources?|readmes?|portfolio documentation|retrieved (?:text|material|documentation|context))"
_SAY_VERB = r"(?:specify|specifies|say|says|state|states|mention|mentions|cover|covers|document|documents|describe|describes|address|addresses|provide|provides|name|names|list|lists|show|shows|explain|explains|detail|details|confirm|confirms|establish|establishes|define|defines|indicate|indicates|record|records|give|gives|include|includes|contain|contains)"
_DECLINE_RE = re.compile(
    r"^\W*(?:(?:however|but|also|note that|based on [^,;]{1,40}|according to [^,;]{1,40}),?\s*)?(?:\*\*[^*]+\*\*\s*)?(?:"
    r"(?:the|this|our|my|that|these|its)\s+(?:\w+\s+){0,2}?" + _DOC_NOUN + r"\s+(?:does not|doesn't|did not|didn't|do not|don't|never)\s+(?:\w+\s+)?" + _SAY_VERB + r"\b"
    r"|i (?:don't|do not) have (?:[\w-]+\s+){0,3}?(?:information|documentation|record|details?)\b"
    r"|(?:there is|there's) no (?:documented|recorded|stated|documentation|mention|information|record)\b"
    r"|no (?:information|documentation|record|mention) (?:is|was|exists|about|on|of|in)\b"
    r")",
    re.IGNORECASE,
)
_SECOND_CLAUSE_RE = re.compile(r";|\s(?:but|yet|although|though|whereas)\s|\bit does\b|\bit is\b", re.IGNORECASE)
# A comma before "and" or "or" joins a list item or a second clause. A clause carries its own
# subject (a determiner and one to three words, or a capitalised word and up to two) and a finite
# verb with a word after it; a list item has neither. "...port, and the Fastify server listens on
# 8080" is a clause; "who approves, how provenance is stored, or how cache entries are invalidated"
# is a list. Case-sensitive so the capitalised branch can tell a name from a list word.
_CLAUSE_SUBJECT = (
    r"(?:the|this|that|these|those|a|an|its|our|my|their|your)\s+(?:[\w-]+\s+){1,3}?"
    r"|[A-Z][\w-]*\s+(?:[\w-]+\s+){0,2}?"
)
_FINITE_VERB = (
    r"(?:is|are|was|were|has|have|had|does|do|did|will|would|can|cannot|could|should|must|may|might|shall"
    r"|(?!(?:this|its|thus|plus|across|always|perhaps|less|unless|various|previous|serious|obvious)\b)\w{2,}s)\b\s+\w"
)
_CLAUSE_COMMA_RE = re.compile(r",\s*(?:and|or)\s+(?:" + _CLAUSE_SUBJECT + r")" + _FINITE_VERB)


def _number_key(raw: str) -> str:
    """20,480 and 20480 are one number; 5.5 stays 5.5."""
    return raw.replace(",", "")


def is_decline(sentence: str) -> bool:
    """True when the whole sentence says the documents do not say."""
    return (
        _DECLINE_RE.match(sentence) is not None
        and _SECOND_CLAUSE_RE.search(sentence) is None
        and _CLAUSE_COMMA_RE.search(sentence) is None
    )
_LIST_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s+")
_CITATIONS_RE = re.compile(r"(^|\n)\s*CITATIONS\s*:")
_SOURCE_MARK_RE = re.compile(r"\*\(([^()]*)\)\*")


def stem(word: str) -> str:
    """"deployed", "deploys" and "deploy" are one word. Suffixes only, never below four letters."""
    for suffix in ("ies", "ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-3] + "y" if suffix == "ies" else word[: -len(suffix)]
    return word


def norm_word(raw: str) -> str:
    """A word normalised for overlap, or "" when it carries no weight."""
    word = raw.lower().rstrip("._/-")
    if not word:
        return ""
    if word[0].isdigit():
        return word  # numbers and versions carry weight at any length
    if any(c in word for c in "._/-"):
        return word  # a command, a path or a dotted identifier
    if len(word) < 4 or word in _STOP:
        return ""
    return stem(word)


def tokens_of(text: str) -> frozenset[str]:
    return frozenset(t for t in (norm_word(m.group(0)) for m in _WORD_RE.finditer(text)) if t)


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_RE.split(text)]
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        # a fragment this short cannot be read alone, so it rides with its neighbour
        if out and (len(part) < 28 or len(out[-1]) < 28):
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def response_sentences(response: str) -> list[str]:
    """The answer's prose sentences: bullets kept, code fences and the CITATIONS block dropped."""
    text = response or ""
    cut = _CITATIONS_RE.search(text)
    body = text[: cut.start()] if cut else text
    sentences: list[str] = []
    in_fence = False
    for line in body.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip():
            continue
        marker = _LIST_RE.match(line)
        rest = line[marker.end() :] if marker else line.strip()
        sentences.extend(split_sentences(rest))
    return sentences


def passages_of(contexts: Sequence[str]) -> list[str]:
    """Each retrieved chunk cut at sentence ends into passages of about PASSAGE_MIN_CHARS or more."""
    passages: list[str] = []
    for raw in contexts:
        body = (raw or "").replace("\r", "").strip()
        if not body:
            continue
        cuts = [m.end() for m in _SENT_RE.finditer(body)] + [len(body)]
        start = 0
        for cut in cuts:
            if cut - start >= PASSAGE_MIN_CHARS or cut == len(body):
                piece = body[start:cut].rstrip()
                if piece.strip():
                    passages.append(piece)
                start = cut
    return passages


# a source marker names a document rather than asserting anything, so it stays out of the score
def _score_text(sentence: str) -> str:
    return _SOURCE_MARK_RE.sub(" ", sentence).replace("*", " ").replace("`", " ")


@dataclass(frozen=True)
class SentenceGrounding:
    """One sentence and where the rule found it."""

    statement: str
    passage: int
    carried: float
    missing_numbers: tuple[str, ...]
    supported: bool
    decline: bool = False
    #: The second passage the words were read against when the best alone fell
    #: below the floor, or -1 when one passage decided it.
    spanned_with: int = -1

    @property
    def reason(self) -> str:
        if self.decline:
            return "a decline asserts nothing the documents would carry"
        if self.passage < 0:
            return "no passage shares a word with it"
        if self.spanned_with >= 0:
            parts = [
                f"passages {self.passage + 1} and {self.spanned_with + 1} together carry "
                f"{self.carried:.0%} of its words"
            ]
        else:
            parts = [f"passage {self.passage + 1} carries {self.carried:.0%} of its words"]
        if self.missing_numbers:
            parts.append("number " + ", ".join(self.missing_numbers) + " appears in no passage")
        return "; ".join(parts)


@dataclass(frozen=True)
class Grounding:
    """An answer's grounding: its sentences, and the grounded share as the score."""

    sentences: tuple[SentenceGrounding, ...]

    @property
    def score(self) -> float | None:
        """The grounded share, or None when the answer had no scoreable sentence."""
        if not self.sentences:
            return None
        return sum(1 for s in self.sentences if s.supported) / len(self.sentences)

    @property
    def claims(self) -> list[dict]:
        """The `Claim` payloads `eval_results.claims` stores: one per sentence, in answer order."""
        return [{"statement": s.statement, "supported": s.supported, "reason": s.reason} for s in self.sentences]


def _ranked_passages(
    tokens: frozenset[str], passage_tokens: Sequence[frozenset[str]]
) -> list[tuple[int, float, float]]:
    """(index, carried share, density) per passage, best first.

    Ordered by carried share, then density: a tie goes to the denser passage,
    the one about these words rather than the overview that mentions them.
    """
    ranked = []
    for i, ptokens in enumerate(passage_tokens):
        shared = len(tokens & ptokens)
        carried = shared / len(tokens)
        density = shared / len(ptokens) if ptokens else 0.0
        ranked.append((i, carried, density))
    ranked.sort(key=lambda r: (r[1], r[2]), reverse=True)
    return ranked


def _best_passage(tokens: frozenset[str], passage_tokens: Sequence[frozenset[str]]) -> tuple[int, float]:
    """(index, carried share) of the passage sharing most of the tokens, or (-1, 0.0)."""
    ranked = _ranked_passages(tokens, passage_tokens)
    if not ranked or ranked[0][1] == 0.0:
        return -1, 0.0
    return ranked[0][0], ranked[0][1]


def _second_reading(
    tokens: frozenset[str],
    best: int,
    carried: float,
    passage_tokens: Sequence[frozenset[str]],
    carried_floor: float,
) -> tuple[float, int]:
    """(carried share, second passage) after reading the sentence against two passages.

    A sentence that draws on two passages can be carried by neither alone. It is
    read once more against the best passage joined with the passage that adds
    the most words the best one lacks, and only when that passage adds at least
    SECOND_PASSAGE_MIN_WORDS of them: one shared word from an unrelated passage
    is what any passage offers by accident. The floor is the same floor. Returns
    the single reading and -1 when no second passage qualifies or the two
    together still fall under the floor; the caller decided the sentence is a
    statement of facts, not a reason or a consequence (_INFERENCE_RE).
    """
    best_tokens = passage_tokens[best]
    second, added = -1, 0
    for i, ptokens in enumerate(passage_tokens):
        if i == best:
            continue
        new_words = len((tokens & ptokens) - best_tokens)
        if new_words > added:
            second, added = i, new_words
    if second < 0 or added < SECOND_PASSAGE_MIN_WORDS:
        return carried, -1
    together = len(tokens & (best_tokens | passage_tokens[second])) / len(tokens)
    if together < carried_floor:
        return carried, -1
    return together, second


def _ground_sentence(
    statement: str,
    passage_tokens: Sequence[frozenset[str]],
    all_numbers: frozenset[str],
    carried_floor: float,
) -> SentenceGrounding | None:
    """One sentence's grounding, or None when it has no content word to score."""
    tokens = tokens_of(_score_text(statement))
    if not tokens:
        return None
    best, carried = _best_passage(tokens, passage_tokens)
    if is_decline(statement):
        return SentenceGrounding(statement, best, carried, (), True, decline=True)
    missing = tuple(n for n in _NUMBER_RE.findall(statement) if _number_key(n) not in all_numbers)
    spanned_with = -1
    if 0 <= best and carried < carried_floor and not _INFERENCE_RE.search(statement):
        carried, spanned_with = _second_reading(tokens, best, carried, passage_tokens, carried_floor)
    supported = best >= 0 and carried >= carried_floor and not missing
    return SentenceGrounding(statement, best, carried, missing, supported, spanned_with=spanned_with)


def ground(response: str, contexts: Sequence[str], *, carried_floor: float = CARRIED_FLOOR) -> Grounding:
    """Score an answer against the passages the agent retrieved. Pure.

    Args:
        response: the agent's answer, as it was sent.
        contexts: the retrieved chunks the agent was given for that turn.
        carried_floor: the share of a sentence's content words the best passage,
            or the best two together, must carry. `CARRIED_FLOOR` unless a benchmark run is exploring it.
    """
    passages = passages_of(contexts)
    passage_tokens = [tokens_of(p) for p in passages]
    all_numbers = frozenset(_number_key(m.group(0)) for p in passages for m in _NUMBER_RE.finditer(p))
    graded = (
        _ground_sentence(statement, passage_tokens, all_numbers, carried_floor)
        for statement in response_sentences(response)
    )
    return Grounding(tuple(g for g in graded if g is not None))
