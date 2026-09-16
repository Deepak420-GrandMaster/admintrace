"""What the conversation is still trying to do, across turns.

The bug this exists to kill: someone asks how to validate their visa, Claré
asks where in France they are, they answer "i live in antibes", and Claré asks
where in France they are. The reply was read as a brand-new question, the
original task was gone, and the reader was asked to repeat themselves.

The fix is to stop inferring the conversation's state from the last thing
typed. A clarification opens a :class:`PendingTask` that names the original
question, what is missing, and which field was asked for. The next message is
read *against* that task: not "what is this question about" but "is this the
answer to what I asked". Only if it plainly is not do we treat it as a new
question.

Three rules hold this together:

* the original task survives the clarification turn — it is carried, not
  re-derived from the text;
* a field, once filled, is never asked for again (:func:`resolve` refuses to
  return the same request twice, and a test asserts it);
* the reader can always walk away from the question — a clear new topic
  closes the pending task instead of being forced into the field we wanted.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.query import dates as question_dates
from app.query import entity
from app.sources import jurisdiction

#: How many unrelated turns a pending task survives before it is dropped.
DEFAULT_EXPIRY_TURNS = 3


class TurnType(str, Enum):
    """What a turn *is*, recorded rather than guessed at afterwards."""

    USER_QUESTION = "user_question"
    ASSISTANT_CLARIFICATION = "assistant_clarification"
    USER_CLARIFICATION_RESPONSE = "user_clarification_response"
    ASSISTANT_ANSWER = "assistant_answer"
    SYSTEM_EVENT = "system_event"


class Field(str, Enum):
    """The kinds of missing information Claré knows how to ask for."""

    LOCATION = "location"
    ENTITY = "entity"
    PURPOSE = "purpose"
    USER_STATUS = "user_status"
    DATE = "date"


class Status(str, Enum):
    AWAITING_CLARIFICATION = "awaiting_clarification"
    READY_FOR_RETRIEVAL = "ready_for_retrieval"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    SWITCHED = "switched"


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.replace("-", " ").split())


@dataclass
class PendingTask:
    """A question waiting on one missing piece of information."""

    original_question: str = ""
    normalized_question: str = ""
    intent: str = ""
    purpose: str = ""
    missing_fields: list[str] = field(default_factory=list)
    requested_clarification: str = ""
    context: dict = field(default_factory=dict)
    status: str = Status.AWAITING_CLARIFICATION.value
    created_at: str = ""
    #: Fields already answered. A field in here is never requested again.
    filled_fields: list[str] = field(default_factory=list)
    turns_waited: int = 0

    @property
    def awaiting(self) -> bool:
        return self.status == Status.AWAITING_CLARIFICATION.value

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "PendingTask | None":
        if not raw:
            return None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


def start(question: str, *, requested: Field, intent: str = "",
          purpose: str = "", context: dict | None = None) -> PendingTask:
    """Open a pending task for a question that cannot be answered yet."""
    return PendingTask(
        original_question=question,
        normalized_question=" ".join((question or "").split()),
        intent=intent,
        purpose=purpose,
        missing_fields=[requested.value],
        requested_clarification=requested.value,
        context={"country": "France", "location": None, "institution_id": None,
                 "user_status": None, "date": None, **(context or {})},
        status=Status.AWAITING_CLARIFICATION.value,
        created_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    )


# ------------------------------------------------------- reading the reply --

#: A reply that plainly starts a different question rather than answering ours.
_NEW_QUESTION = re.compile(
    r"(?i)\b(actually|instead|forget that|never mind|nevermind|different "
    r"question|another question|new question|change of subject|"
    r"en fait|plutot|plutôt|autre question|laisse tomber|oubliez)\b"
)

#: Yes and no, in both languages, as a whole word.
_YES = re.compile(r"(?i)^\W*(yes|yeah|yep|yup|correct|right|i am|oui|si|"
                  r"exact|exactement|tout a fait|tout à fait)\b")
_NO = re.compile(r"(?i)^\W*(no|nope|not|i'm not|i am not|non|pas|ne pas)\b")

#: What a reader calls themselves when asked about their status.
_STATUS_WORDS = {
    "student": "student", "etudiant": "student", "etudiante": "student",
    "worker": "worker", "employee": "worker", "salarie": "worker",
    "salariee": "worker", "travailleur": "worker",
    "visitor": "visitor", "visiteur": "visitor", "tourist": "visitor",
    "refugee": "refugee", "refugie": "refugee", "refugiee": "refugie",
    "intern": "intern", "stagiaire": "intern",
    "researcher": "researcher", "chercheur": "researcher",
    "family": "family", "famille": "family", "spouse": "family",
    "conjoint": "family",
}


@dataclass
class Resolution:
    """What a reply to a clarification turned out to be."""

    #: The pending task, updated. None when the reader switched topic.
    task: PendingTask | None = None
    #: True when the original question can now be answered.
    resume: bool = False
    #: True when the reader asked something else entirely.
    switched: bool = False
    #: True when the reply did not answer what was asked.
    unresolved: bool = False
    #: The question to route on, original task and new context together.
    question: str = ""
    #: Which field this reply filled, if any.
    filled: str = ""
    #: Human-readable note for diagnostics. Never shown to a reader.
    note: str = ""


def looks_like_a_new_question(message: str, *, requested: str = "") -> bool:
    """Whether a reply is plainly a different question, not an answer.

    Deliberately conservative. A short reply is almost always the answer to
    what was asked — "Antibes" is not a new question — so only an explicit
    pivot, or a full question about something else, counts. Getting this
    wrong in the permissive direction reintroduces the bug it guards.
    """
    text = (message or "").strip()
    if not text:
        return False
    if _NEW_QUESTION.search(text):
        return True
    words = text.split()
    if len(words) < 5:
        # Too short to be a question in its own right; it is an answer.
        return False
    # A question mark plus interrogative wording, on a reply long enough to
    # stand alone, is somebody changing the subject.
    interrogative = re.search(
        r"(?i)\b(how|what|where|when|why|which|can i|do i|is there|"
        r"comment|quoi|quel|quelle|ou|où|quand|pourquoi|est ce que)\b", text)
    return bool(interrogative) and (
        "?" in text or len(words) >= 7)


def _fill_location(task: PendingTask, message: str) -> Resolution:
    place = jurisdiction.resolve_reply(message)
    if place.needs_clarification and place.candidates:
        names = ", ".join(c.name for c in place.candidates)
        task.context["location_candidates"] = [c.code for c in place.candidates]
        return Resolution(task=task, unresolved=True,
                          note=f"{place.city} is in more than one: {names}")
    if not place.known:
        return Resolution(task=task, unresolved=True,
                          note="no place recognised in the reply")

    task.context["location"] = place.city or place.department.name
    task.context["department"] = place.department.name
    task.context["department_code"] = place.department.code
    task.context["region"] = place.region.name if place.region else ""
    task.context["authority_source_id"] = place.authority_source_id
    task.context["authority_name"] = place.authority_name
    return Resolution(task=task, filled=Field.LOCATION.value,
                      note=f"location = {task.context['location']}")


def _fill_entity(task: PendingTask, message: str) -> Resolution:
    history = [task.original_question]
    who = entity.resolve(message, history)
    if who.institution is None:
        return Resolution(task=task, unresolved=True,
                          note="no institution recognised in the reply")
    task.context["institution_id"] = next(
        iter(entity.canonical_ids(who.institution)), "")
    task.context["institution_name"] = who.institution.name
    if who.institution.departement:
        task.context.setdefault("department", who.institution.departement)
    return Resolution(task=task, filled=Field.ENTITY.value,
                      note=f"entity = {who.institution.name}")


def _fill_user_status(task: PendingTask, message: str) -> Resolution:
    text = (message or "").strip()
    folded = _fold(text)
    for word, value in _STATUS_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", folded):
            task.context["user_status"] = value
            return Resolution(task=task, filled=Field.USER_STATUS.value,
                              note=f"user_status = {value}")
    # "yes" answers the question that was asked, whatever it was about; the
    # word itself is never worth retrieving on.
    if _YES.match(text):
        task.context["user_status"] = task.context.get("status_if_yes") or "yes"
        return Resolution(task=task, filled=Field.USER_STATUS.value,
                          note="answered yes")
    if _NO.match(text):
        task.context["user_status"] = "no"
        return Resolution(task=task, filled=Field.USER_STATUS.value,
                          note="answered no")
    return Resolution(task=task, unresolved=True,
                      note="reply was not a status or a yes/no")


def _fill_purpose(task: PendingTask, message: str) -> Resolution:
    from app.sources import purpose as purposes

    found = purposes.detect(message)
    if not found:
        return Resolution(task=task, unresolved=True,
                          note="no purpose recognised in the reply")
    task.context["purpose"] = found[0].id
    task.purpose = found[0].id
    return Resolution(task=task, filled=Field.PURPOSE.value,
                      note=f"purpose = {found[0].id}")


def _fill_date(task: PendingTask, message: str) -> Resolution:
    asked = question_dates.parse(message)
    on = getattr(asked, "on", None) or getattr(asked, "date", None)
    if not on and not getattr(asked, "is_historical", False):
        # A bare month and year is still a date even when the parser only
        # reports a period.
        if not re.search(r"\b(19|20)\d{2}\b", message or ""):
            return Resolution(task=task, unresolved=True,
                              note="no date recognised in the reply")
    task.context["date"] = (message or "").strip()
    return Resolution(task=task, filled=Field.DATE.value,
                      note=f"date = {task.context['date']}")


_FILLERS = {
    Field.LOCATION.value: _fill_location,
    Field.ENTITY.value: _fill_entity,
    Field.USER_STATUS.value: _fill_user_status,
    Field.PURPOSE.value: _fill_purpose,
    Field.DATE.value: _fill_date,
}


def resolve_clarification_response(task: PendingTask | None, message: str,
                                   *, expiry: int = DEFAULT_EXPIRY_TURNS
                                   ) -> Resolution:
    """Read a message as the answer to the clarification we asked.

    Returns a :class:`Resolution` saying whether the original task can now
    resume, whether the reader changed the subject, or whether the reply did
    not answer the question. The task itself is carried through and updated —
    never rebuilt from the message, which is what lost it before.
    """
    if task is None or not task.awaiting:
        return Resolution(task=task, switched=True, question=message,
                          note="nothing was pending")

    if looks_like_a_new_question(message, requested=task.requested_clarification):
        task.status = Status.SWITCHED.value
        return Resolution(task=None, switched=True, question=message,
                          note="the reader asked something else")

    filler = _FILLERS.get(task.requested_clarification)
    outcome = filler(task, message) if filler else Resolution(
        task=task, unresolved=True, note="no reader for this field")

    if outcome.unresolved:
        task.turns_waited += 1
        if task.turns_waited >= expiry:
            task.status = Status.EXPIRED.value
            return Resolution(task=None, switched=True, question=message,
                              note="pending task expired unanswered")
        outcome.question = resumed_question(task)
        return outcome

    # Filled. The hard invariant: this field is never requested again.
    answered = task.requested_clarification
    if answered not in task.filled_fields:
        task.filled_fields.append(answered)
    task.missing_fields = [f for f in task.missing_fields if f != answered]
    task.requested_clarification = ""
    task.turns_waited = 0
    task.status = (Status.READY_FOR_RETRIEVAL.value if not task.missing_fields
                   else Status.AWAITING_CLARIFICATION.value)
    outcome.resume = True
    outcome.question = resumed_question(task)
    return outcome


def resumed_question(task: PendingTask) -> str:
    """The question to retrieve on once a clarification has been answered.

    The original question, essentially unchanged. A place does *not* get
    appended to it: "how to validate the visa Antibes" is a worse semantic
    query than "how to validate the visa", because the corpus has no pages
    about Antibes and the city only dilutes what the question is actually
    about. Where the reader is matters to *routing* — which préfecture, which
    authority — and it travels structurally, as a Place, not as text glued
    onto a sentence.

    An institution is the exception and is appended, because it changes what
    the question means rather than where it applies: "what are the entry
    requirements" and "what are MBS's entry requirements" are different
    questions, and the second cannot be retrieved without the name.
    """
    question = task.original_question.strip()
    named = (task.context or {}).get("institution_name")
    if named and named.lower() not in question.lower():
        question = f"{question} ({named})"
    return question


def retrieval_context(task: PendingTask | None) -> dict:
    """The structured context a resumed task carries into retrieval.

    Separate from the question text on purpose — §9 of the brief, and the
    right shape anyway: intent and jurisdiction are fields, not words.
    """
    if task is None:
        return {}
    context = task.context or {}
    return {
        "intent": task.intent or "",
        "purpose": task.purpose or "",
        "location": {
            "city": context.get("location") or "",
            "department": context.get("department") or "",
            "department_code": context.get("department_code") or "",
            "region": context.get("region") or "",
            "country": context.get("country") or "France",
        } if context.get("department") else {},
        "institution_id": context.get("institution_id") or "",
        "user_status": context.get("user_status") or "",
        "date": context.get("date") or "",
    }


def may_ask_for(task: PendingTask | None, requested: Field | str) -> bool:
    """Whether a field may still be asked for.

    False once it has been answered. This is the invariant that makes the
    original bug unrepeatable: having been told "Antibes", the system cannot
    ask where you are again, whatever else goes wrong downstream.
    """
    if task is None:
        return True
    wanted = requested.value if isinstance(requested, Field) else str(requested)
    return wanted not in (task.filled_fields or [])


def place_of(task: PendingTask | None):
    """The place a pending task already knows about, as a Place.

    Rebuilt from the département code rather than re-parsed from text: once
    the reader has said where they are, that fact is carried, not guessed at
    again on every turn.
    """
    if task is None:
        return None
    code = (task.context or {}).get("department_code")
    if not code:
        return None
    department = jurisdiction.department_by_code(str(code))
    if department is None:
        return None
    return jurisdiction.Place(
        city=(task.context or {}).get("location", "") or "",
        department=department,
        region=jurisdiction.region_by_id(department.region))


# ------------------------------------------------------------- metrics -----

#: The counter that matters. A valid reply to a clarification must never be
#: followed by the same clarification, so this stays at zero; anything else
#: means the bug is back.
REPEATED = "clarification_repeated"

_EVENTS = (
    "clarification_count", "clarification_resolved", REPEATED,
    "clarification_abandoned", "task_resumed", "task_switched",
)


def trace(stage: str, **fields) -> None:
    """Developer trace of one conversation turn, off unless asked for.

    Set CLARE_TRACE_CONVERSATION=1 to print the state transitions to the
    application's own stdout. Never reaches a reader, and never prints what
    they typed beyond the field being resolved — enough to answer "what did
    the app think happened", which is the question a clarification bug needs.
    """
    import os
    import sys

    if os.environ.get("CLARE_TRACE_CONVERSATION") != "1":
        return
    detail = " ".join(f"{k}={v!r}" for k, v in fields.items() if v not in (None, ""))
    print(f"[clare.conversation] {stage} {detail}", file=sys.stderr, flush=True)


def record(event: str, settings=None, **fields) -> None:
    """Note a conversation event in the same audit log as everything else.

    Nothing about the reader is written — the field that was asked for and
    whether it was answered, never what they typed. The log already carries
    source decisions; conversation decisions belong beside them so a bug
    report about being asked twice can be checked rather than argued about.
    """
    from app.sources import store

    if event not in _EVENTS:
        return
    store.audit(f"conversation.{event}", settings, **fields)


def metrics(settings=None) -> dict[str, int]:
    """How the clarification flow has actually behaved."""
    from app.sources import store

    counts = {name: 0 for name in _EVENTS}
    path = store.root(settings) / "audit.jsonl"
    if not path.exists():
        return counts
    try:
        import json

        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            event = str(row.get("event", ""))
            if event.startswith("conversation."):
                name = event.split(".", 1)[1]
                if name in counts:
                    counts[name] += 1
    except OSError:
        return counts
    return counts
