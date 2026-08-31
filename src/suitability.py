"""The question that has to be asked before a recommendation is honest.

WHY THIS IS CODE AND NOT AN INSTRUCTION

Each vendor carries its own trade knowledge, injected into the instruction per
call. The furniture note says, in as many words:

    Ask how many hours a day it will be sat in, and by how many people. A
    chair rated for a single shift fails in a call centre and the warranty
    will say so. This is the single question that decides whether a
    recommendation is honest.

That text advises. It enforces nothing. The desk can quote a 299 dollar task
chair to a 24-hour dispatch office, never ask, and no part of the system
notices until the chair fails in eight months and the warranty is void because
the customer exceeded its rated duty.

The measured case against leaving it as prose is blunt. "Before the Tool Call:
Deterministic Pre-Action Authorization for Autonomous AI Agents" (arXiv
2603.20953) reports social engineering succeeding 74.6% of the time under
permissive policies when enforcement relies primarily on model judgement, and
states the reason: alignment "shifts behavior across the distribution but does
not guarantee any individual output". A rule a model can talk past is not a
rule. It is the same argument this project already made about routing, which is
why guards.py exists at all.

WHY THE POLICY LIVES HERE AND THE ENFORCEMENT LIVES IN guards.py

That paper separates the Policy Pack, which is declarative and readable, from
the Authorization Engine, which evaluates it. Same split here. This file is a
table a furniture buyer could read and correct without touching the callback
that enforces it, and adding a fifth trade is a dictionary entry rather than a
change to the guard.

WHY ONLY TWO FAMILIES

Because only two have a question where the ANSWER CHANGES THE PRODUCT, and
where getting it wrong voids cover the customer thinks they have. A chair has a
duty rating. A consumer television's warranty excludes commercial and public
display use, so a set mounted in a dining room is uncovered from the day it
goes up and nobody finds out until it fails.

Every other trade note is advice about how to talk, and advice belongs in the
instruction. This is not advice. It is a fact about what we are allowed to sell
somebody, so it belongs where it cannot be talked past.
"""

from __future__ import annotations

from . import db


class Question:
    """One thing that must be established before we can quote a family."""

    def __init__(self, ask: str, why: str, fields: tuple[str, ...],
                 refusing: str) -> None:
        self.ask = ask
        self.why = why
        self.fields = fields
        self.refusing = refusing


REQUIRED: dict[str, Question] = {
    "office chair": Question(
        ask="how many hours a day it will be sat in, and by how many people",
        why=("chairs carry a duty rating, and one rated for a single shift "
             "fails in a call centre with the warranty voided for exceeding "
             "it"),
        fields=("hours_per_day",),
        refusing=("Ask how many hours a day the chair will be sat in and by "
                  "how many people, then call note_how_it_will_be_used. A "
                  "chair rated for one shift fails in a 24 hour office and "
                  "the warranty says so, which makes quoting one before "
                  "asking a recommendation we cannot stand behind."),
    ),
    "television": Question(
        ask="where it is going and how many hours a day it will run",
        why=("a consumer television's warranty excludes commercial and public "
             "display use, so a set mounted in a dining room is uncovered "
             "from the day it goes up"),
        fields=("where_it_goes",),
        refusing=("Ask where the screen is going, specifically whether it is "
                  "somewhere the public sees it, then call "
                  "note_how_it_will_be_used. A consumer set's warranty "
                  "EXCLUDES commercial and public display use. If it is going "
                  "in a dining room or a shop floor they need the commercial "
                  "line, and they will not find that out until it fails."),
    ),
}

# The same rule reaches these under another name.
ALIASES = {
    "commercial display": "television",
    "desk chair": "office chair",
    "task chair": "office chair",
}


def what_must_be_asked(family: str) -> Question | None:
    """The question owed before this family can be quoted, if there is one."""
    f = (family or "").strip().lower()
    if not f:
        return None
    return REQUIRED.get(ALIASES.get(f, f))


def families_in(items: list, dealer_id: str) -> list[str]:
    """Which families an order's line items belong to.

    DELIBERATELY CONSERVATIVE. A line is only claimed for a family when the
    catalogue says so or the words are unambiguous, because the cost of being
    wrong runs one way: a false match refuses an order somebody wanted, in
    front of a customer, for a reason that will not make sense to them.

    Items arrive as SKUs or as free description, so all three are tried.
    """
    found: list[str] = []

    with db.connect() as c:
        for raw in items or []:
            item = str(raw or "").strip()
            if not item:
                continue

            # A PART IS NEVER THE PRODUCT THIS RULE IS ABOUT.
            #
            # `parts.families` records what a part FITS, not what the line is.
            # Reading it as the line's own family made a replacement gas lift
            # resolve to "office chair", so somebody buying a spare cylinder
            # was asked how many hours a day they sit in it. The duty rating
            # question is about buying a chair; a component for a chair they
            # already own has nothing to do with it.
            #
            # So a recognised part ends the matching for this line rather than
            # contributing a family.
            part = c.execute(
                "SELECT sku FROM parts WHERE dealer_id=? AND sku=?",
                (dealer_id, item.upper())).fetchone()
            if part:
                continue

            # Products, matched loosely. A caller says "the WorkPro 1000" and
            # the shelf reads "WorkPro 1000 Series Ergonomic Mesh Mid-Back",
            # so an equality test finds nothing and the gate silently never
            # fires, which is the more dangerous failure of the two.
            row = c.execute(
                """SELECT family FROM product_stock
                   WHERE dealer_id=?
                     AND (LOWER(model_number) LIKE LOWER(?)
                          OR LOWER(?) LIKE '%' || LOWER(model_number) || '%'
                          OR LOWER(manufacturer || ' ' || model_number)
                              LIKE LOWER(?))
                   LIMIT 1""",
                (dealer_id, f"%{item}%", item, f"%{item}%")).fetchone()
            if row and row["family"]:
                found.append(row["family"].strip().lower())
                continue

            # Last resort, and only on a whole word, so "chairman" and
            # "television aerial bracket" do not trip it.
            words = {w.strip(".,;:").lower() for w in item.split()}
            for name in list(REQUIRED) + list(ALIASES):
                head = name.split(" ")[-1]
                if head in words:
                    found.append(name)
                    break

    return sorted(set(found))


def already_answered(state: dict, family: str) -> bool:
    """Whether this call has established what the guard is asking for."""
    q = what_must_be_asked(family)
    if q is None:
        return True

    known = (state or {}).get("use_established") or {}
    canonical = ALIASES.get((family or "").strip().lower(),
                            (family or "").strip().lower())
    answer = known.get(canonical)
    if not answer:
        return False

    return all(answer.get(f) not in (None, "", 0) for f in q.fields)


def unanswered_for(items: list, dealer_id: str, state: dict) -> list[dict]:
    """Every family on this order whose question has not been answered yet."""
    out = []
    for family in families_in(items, dealer_id):
        q = what_must_be_asked(family)
        if q is None or already_answered(state, family):
            continue
        out.append({"family": family, "ask": q.ask, "why": q.why,
                    "do_this": q.refusing})
    return out
