"""Per-turn relevance loadout for the direct Google Workspace toolsets.

With every Google integration connected the agent has ~103 tool schemas, and
attaching all of them to every model request costs roughly 8.5k input tokens per
call whether or not the turn touches Google at all. This module decides which
products a turn actually needs, and gates the rest off — without removing any
capability, because :func:`load_google_toolkit` can pull a product back in
mid-run and the newly activated tools appear on the next model request.

Three pieces of state, at three different lifetimes:

``sticky``
    Conversation-scoped. Products whose tools were actually *called* recently, so
    a follow-up like "send that to Nikki" still finds Gmail. Bounded by
    :data:`STICKY_TTL_TURNS` and :data:`STICKY_MAX_PRODUCTS` — without eviction
    inheritance would monotonically creep back toward the full catalog, which is
    the thing this module exists to prevent.

``selected`` / ``activated``
    Run-scoped. What the router picked from this turn's prompt, plus whatever
    :func:`load_google_toolkit` activated part-way through the run.

``resume_only``
    Set once, when a run pauses for tool approval. The resume exists to write a
    closing sentence, so it keeps only what could still be needed.

Ordering is deliberate everywhere: products serialize in
:data:`GOOGLE_PRODUCT_ORDER` and tools in their registration order, so a given
conversation state always produces byte-identical tool definitions. Provider
prompt caching keys on a stable prefix, and a catalog that reshuffles between
requests cannot be cached even when it is small.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic_ai import RunContext
from pydantic_ai.toolsets import WrapperToolset

from app.agents.google_apis.products import DIRECT_GOOGLE_PRODUCTS
from app.agents.google_workspace_api import CALENDAR_TOOLS, GMAIL_TOOLS

# The canonical global order. Everything that serializes a loadout walks this
# tuple, so two turns with the same active set emit the same bytes.
GOOGLE_PRODUCT_ORDER: tuple[str, ...] = (
    "gmail",
    "calendar",
    "drive",
    "docs",
    "sheets",
    "slides",
    "chat",
    "contacts",
)

GOOGLE_PRODUCT_LABELS: dict[str, str] = {
    "gmail": "Gmail",
    "calendar": "Google Calendar",
    "drive": "Google Drive",
    "docs": "Google Docs",
    "sheets": "Google Sheets",
    "slides": "Google Slides",
    "chat": "Google Chat",
    "contacts": "Google Contacts",
}

#: A product drops out of the sticky loadout after this many turns without use.
STICKY_TTL_TURNS = 3
#: At most this many products are inherited by a follow-up turn (least recently
#: used evicted first).
STICKY_MAX_PRODUCTS = 4

LOADER_TOOL_NAME = "load_google_toolkit"


def product_tools(product: str) -> tuple[tuple[str, str], ...]:
    """The ``(tool name, description)`` pairs a product exposes, in canonical order."""
    if product == "gmail":
        return GMAIL_TOOLS
    if product == "calendar":
        return CALENDAR_TOOLS
    entry = DIRECT_GOOGLE_PRODUCTS.get(product)
    return entry.tools if entry is not None else ()


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

# Terms that name a product. A term mapping to several products widens the
# loadout to all of them rather than guessing one: "put it in the file" should
# reach Drive, Docs, Sheets and Slides, because picking wrong costs a wasted
# round-trip through the loader while picking wide costs a few hundred tokens.
#
# Common English words that merely *could* refer to a product ("space", "book",
# "people", "range") are deliberately absent. They fire on ordinary conversation,
# and a router that matches everything saves nothing.
_PRODUCT_TERMS: dict[str, tuple[str, ...]] = {
    "gmail": (
        "gmail",
        "email",
        "emails",
        "e-mail",
        "e-mails",
        "inbox",
        "mailbox",
        "unread",
        "bcc",
        "subject line",
        "mail thread",
        "email thread",
    ),
    "calendar": (
        "calendar",
        "calendars",
        "meeting",
        "meetings",
        "appointment",
        "appointments",
        "agenda",
        "reschedule",
        "availability",
        "free slot",
        "free slots",
        "free time",
        "time slot",
        "timeslot",
        "standup",
        "stand-up",
        "rsvp",
        "all-day",
        "my schedule",
        "on my calendar",
    ),
    "drive": (
        "drive",
        "folder",
        "folders",
        "subfolder",
        "my files",
        "shared drive",
        "trash",
        "starred",
        "upload",
        "download",
        "permission",
        "permissions",
    ),
    "docs": (
        "doc",
        "docs",
        "document",
        "documents",
        "google doc",
        "write-up",
        "writeup",
    ),
    "sheets": (
        "sheet",
        "sheets",
        "spreadsheet",
        "spreadsheets",
        "cell",
        "cells",
        "row",
        "rows",
        "column",
        "columns",
        "formula",
        "worksheet",
    ),
    "slides": (
        "slide",
        "slides",
        "presentation",
        "presentations",
        "deck",
        "decks",
        "slideshow",
        "powerpoint",
        "keynote",
    ),
    "chat": (
        "google chat",
        "chat space",
        "chat spaces",
        "chat message",
        "chat messages",
        "chat room",
        "direct message",
    ),
    "contacts": (
        "contact",
        "contacts",
        "address book",
        "contact group",
        "phone number",
        "phone numbers",
        "email address",
        "directory",
    ),
}

# Terms that genuinely span products. Matching one selects the whole set.
# Everything here maps to two or more products by definition — a term that means
# exactly one product belongs in _PRODUCT_TERMS above, because that is where
# route_products' arity rule will treat it as an explicit naming either way.
_CROSS_PRODUCT_TERMS: dict[str, tuple[str, ...]] = {
    "file": ("drive", "docs", "sheets", "slides"),
    "files": ("drive", "docs", "sheets", "slides"),
    "export": ("drive", "docs", "sheets", "slides"),
    "attachment": ("gmail", "drive"),
    "attachments": ("gmail", "drive"),
    "attach": ("gmail", "drive"),
    "send": ("gmail", "chat"),
    "reply": ("gmail", "chat"),
    "notify": ("gmail", "chat"),
    "message": ("gmail", "chat"),
    "messages": ("gmail", "chat"),
    "draft": ("gmail", "docs"),
    "drafts": ("gmail", "docs"),
    "compose": ("gmail", "docs"),
    "note": ("docs", "drive"),
    "notes": ("docs", "drive"),
    "invite": ("calendar", "contacts"),
    "invitation": ("calendar", "contacts"),
    "share": ("drive", "gmail"),
    "shared": ("drive", "gmail"),
}

# "Something in Google, product unspecified" — widen to everything available.
_GENERIC_TERMS: tuple[str, ...] = (
    "google workspace",
    "google account",
    "my google",
    "g suite",
    "gsuite",
    "workspace",
    "google",
)

# An action aimed at something already in the conversation ("send that",
# "put it in"). The object is implicit, so the prompt alone cannot name a
# product — this is what the sticky loadout is for. Written as verb+pronoun
# pairs rather than bare pronouns, which would fire on nearly every sentence.
# "forward" lives here rather than in the term table: "forward it to Nikki" is a
# Gmail action, but "move the refactor forward" is ordinary English, and only the
# verb+pronoun shape tells them apart.
_DEICTIC_RE = re.compile(
    r"\b(?:send|share|save|put|add|record|file|attach|upload|post|copy|move|email|drop|forward)\s+"
    r"(?:the\s+)?(?:that|this|it|them|those|these|the\s+same)\b"
)


_TERM_TO_PRODUCTS: dict[str, tuple[str, ...]] = {
    **{term: (product,) for product, terms in _PRODUCT_TERMS.items() for term in terms},
    **_CROSS_PRODUCT_TERMS,
}

# One alternation over every term, longest first so phrases beat their words
# ("chat spaces" must win over "chat space").
_TERM_RE = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(term)
        for term in sorted({*_TERM_TO_PRODUCTS, *_GENERIC_TERMS}, key=lambda t: (-len(t), t))
    )
    + r")\b"
)

# Every spelling of a product name the loader will accept, so it understands
# whatever the router understands. Built from the same tables rather than a
# second hand-maintained list, which would drift the moment a term is added.
_PRODUCT_ALIASES: dict[str, str] = {
    **{product: product for product in GOOGLE_PRODUCT_ORDER},
    **{label.casefold(): product for product, label in GOOGLE_PRODUCT_LABELS.items()},
    **{term: hits[0] for term, hits in _TERM_TO_PRODUCTS.items() if len(hits) == 1},
    "mail": "gmail",
    "people": "contacts",
}


def resolve_product(text: str) -> str | None:
    """A product name as the model might write it, or None if it names no product."""
    return _PRODUCT_ALIASES.get(text.strip().casefold())


@dataclass(frozen=True)
class Routing:
    """What a piece of text asks for, before sticky state is folded in."""

    products: frozenset[str]
    """Everything the text could plausibly mean, including the full expansion of
    any cross-product term. This is what a turn loads."""

    explicit: frozenset[str]
    """Only the products named by a term that means exactly one of them. Used
    where widening would defeat the purpose — the approval-resume retention set,
    where "send" must not drag Google Chat along behind Gmail."""

    widen: bool
    """The text points at Google without naming a product — a generic mention
    ("sort out my Google stuff") or a deictic action ("send that over"). The
    caller resolves this against sticky state, falling back to everything."""


def route_products(text: str) -> Routing:
    """Map free text onto Google products. Deterministic, no model involved.

    Conservative by construction: a cross-product term selects every product it
    could mean, and a generic Google mention asks the caller to widen. The cost
    of over-selecting is tokens; the cost of under-selecting is a failed turn or
    an extra round-trip, so the asymmetry is deliberate.
    """
    lowered = (text or "").casefold()
    products: set[str] = set()
    explicit: set[str] = set()
    generic = False
    for match in _TERM_RE.finditer(lowered):
        term = match.group(0)
        if term in _GENERIC_TERMS:
            generic = True
            continue
        hits = _TERM_TO_PRODUCTS.get(term, ())
        products.update(hits)
        # Arity decides explicitness: a term that can only mean one product names
        # it, whatever table it was declared in.
        if len(hits) == 1:
            explicit.update(hits)
    return Routing(
        products=frozenset(products),
        explicit=frozenset(explicit),
        widen=generic or bool(_DEICTIC_RE.search(lowered)),
    )


# --------------------------------------------------------------------------- #
# Loadout state
# --------------------------------------------------------------------------- #


@dataclass
class GoogleLoadout:
    """Which Google products this turn may see, and why.

    One instance per conversation. ``begin_turn`` resets the run-scoped fields
    and re-routes; ``end_turn`` folds what was used into the sticky set.
    """

    sticky: OrderedDict[str, int] = field(default_factory=OrderedDict)
    turn: int = 0
    #: Tool-name prefix -> product, for the integrations attached to this turn.
    connections: dict[str, str] = field(default_factory=dict)
    selected: frozenset[str] = frozenset()
    activated: set[str] = field(default_factory=set)
    resume_only: frozenset[str] | None = None

    # -- per-turn lifecycle -------------------------------------------------- #

    def begin_turn(self, prompt: str) -> None:
        """Start a turn: clear run state, age out sticky memory, then route *prompt*.

        Eviction happens here rather than only at the end of a turn, because the
        inherited set is read during this call. Ageing on the way out would leave
        every product one turn stale — it would survive its own expiry turn.
        """
        self.turn += 1
        self.connections = {}
        self.activated = set()
        self.resume_only = None
        self._evict_stale()
        routing = route_products(prompt)
        inherited = frozenset(self.sticky)
        if routing.widen and not routing.products and not inherited:
            # Google is clearly in play but nothing says which product and there
            # is no history to lean on. Load everything rather than guess.
            self.selected = frozenset(GOOGLE_PRODUCT_ORDER)
        else:
            self.selected = routing.products | inherited

    def register(self, *, prefix: str, product: str) -> None:
        """Record a Google integration attached to this turn."""
        self.connections[prefix] = product

    def end_turn(self, used_tool_names: list[str]) -> None:
        """Fold the products actually called into sticky memory, then evict.

        Only *used* products refresh their recency. A product that was merely
        selected and ignored must age out, or one over-wide routing decision
        would pin itself in the loadout forever.
        """
        used = self.products_for_tools(used_tool_names)
        for product in GOOGLE_PRODUCT_ORDER:
            if product in used:
                self.sticky.pop(product, None)
                self.sticky[product] = self.turn
        self._evict_stale()

    def _evict_stale(self) -> None:
        """Drop products unused for too long, then cap the set least-recently-used first."""
        for product, last_used in list(self.sticky.items()):
            if self.turn - last_used >= STICKY_TTL_TURNS:
                del self.sticky[product]
        while len(self.sticky) > STICKY_MAX_PRODUCTS:
            self.sticky.popitem(last=False)

    # -- queries ------------------------------------------------------------- #

    def available_products(self) -> tuple[str, ...]:
        """Products the user actually has connected, in canonical order."""
        connected = set(self.connections.values())
        return tuple(p for p in GOOGLE_PRODUCT_ORDER if p in connected)

    def active_products(self) -> tuple[str, ...]:
        """Products whose tools are exposed to the model right now."""
        wanted = (
            self.resume_only if self.resume_only is not None else self.selected | self.activated
        )
        return tuple(p for p in self.available_products() if p in wanted)

    def inactive_products(self) -> tuple[str, ...]:
        """Connected products currently gated off — what the loader can reach."""
        active = set(self.active_products())
        return tuple(p for p in self.available_products() if p not in active)

    def is_active(self, product: str) -> bool:
        return product in self.active_products()

    def product_for_tool(self, tool_name: str) -> str | None:
        """Map a prefixed tool name back to its product.

        Longest prefix wins: a connection named "gmail" and one named
        "gmail_work" both produce tools starting with ``gmail_``.
        """
        prefix = max(
            (p for p in self.connections if tool_name.startswith(f"{p}_")),
            key=len,
            default=None,
        )
        return self.connections[prefix] if prefix is not None else None

    def products_for_tools(self, tool_names: list[str]) -> frozenset[str]:
        return frozenset(
            product
            for product in (self.product_for_tool(name) for name in tool_names)
            if product is not None
        )

    # -- mutations ----------------------------------------------------------- #

    def activate(self, product: str) -> tuple[tuple[str, str], ...]:
        """Turn a product on for the rest of the run; return its ``(tool, description)``.

        Takes effect on the next model request: pydantic-ai re-resolves every
        toolset's ``get_tools`` before each request, and the gate reads this
        state, so the newly activated schemas are in the very next call.
        """
        self.activated.add(product)
        if self.resume_only is not None:
            self.resume_only = self.resume_only | {product}
        prefixes = sorted(p for p, kind in self.connections.items() if kind == product)
        return tuple(
            (f"{prefix}_{tool}", description)
            for prefix in prefixes
            for tool, description in product_tools(product)
        )

    def restrict_for_resume(self, *, pending_tool_names: list[str], messages: list[Any]) -> None:
        """Narrow the catalog for the model request that follows an approval.

        The resume usually exists to write one closing sentence, so it keeps
        only what could still be needed:

        - the products holding the approvals being resolved,
        - products already used during this run,
        - products *referenced* anywhere in the conversation — "used" alone is
          too narrow, because "send the approved email, then record it in
          Calendar" has Calendar planned but not yet touched at approval time.

        Anything missed is still reachable through :func:`load_google_toolkit`,
        which stays visible whenever a connected product is gated off.
        """
        retained: set[str] = set(self.products_for_tools(pending_tool_names))
        retained |= self.products_for_tools(_called_tool_names(messages))
        # Unambiguously named products only. A generic "google" or a term like
        # "send" that spans Gmail and Chat must not widen the resume back toward
        # the full catalog — whatever the run genuinely needs is already covered
        # by the pending and used sets above.
        retained |= route_products(_user_text(messages)).explicit
        self.resume_only = frozenset(retained)


def _user_text(messages: list[Any]) -> str:
    """Every user prompt in the run, concatenated, for the reference scan."""
    from pydantic_ai.messages import UserPromptPart

    chunks: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                chunks.append(part.content)
    return "\n".join(chunks)


def _called_tool_names(messages: list[Any]) -> list[str]:
    from pydantic_ai.messages import ToolCallPart

    return [
        part.tool_name
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolCallPart)
    ]


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #


class LoadoutDeps(Protocol):
    """The one thing the gate needs off ``RunContext.deps``.

    Naming it makes a rename of the field a type error rather than a silent
    regression: the gate fails *open*, so a typo would quietly disable gating and
    re-send the whole catalog with every test still passing.
    """

    google_loadout: GoogleLoadout | None


@dataclass
class ProductGatedToolset(WrapperToolset[Any]):
    """Hides a whole product's toolset unless it is active for this request.

    Gating at the toolset level rather than per tool matters: pydantic-ai calls
    ``get_tools`` before *every* model request, and a per-tool filter would build,
    prefix and compact all ~103 schemas only to discard the ~93 it then rejects
    one at a time. Returning early skips that work entirely — measured at roughly
    2.8ms to 0.25ms per request across eight connected products.

    Re-evaluated each step, so a mid-run ``activate()`` lands on the next call.
    When deps carry no loadout (channel traffic, direct callers, tests) nothing
    is hidden.
    """

    product: str = ""

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, Any]:
        loadout: GoogleLoadout | None = ctx.deps.google_loadout
        if loadout is not None and not loadout.is_active(self.product):
            return {}
        return await super().get_tools(ctx)


def gate_google_toolset(toolset: Any, *, product: str) -> Any:
    """Wrap *toolset* so it disappears while its product is gated off."""
    return ProductGatedToolset(wrapped=toolset, product=product)


def describe_activation(product: str, tools: tuple[tuple[str, str], ...]) -> str:
    """The loader's return value.

    It has to enumerate what became available. A bare "loaded" leaves the model
    with no names to call and it re-invokes the loader instead of using it.
    """
    label = GOOGLE_PRODUCT_LABELS.get(product, product)
    if not tools:
        return f"No {label} integration is connected for this user, so no tools were loaded."
    return "\n".join(
        [
            f"{label} tools are now available — call them directly on your next step:",
            *(f"- {name} — {description}" for name, description in tools),
        ]
    )
