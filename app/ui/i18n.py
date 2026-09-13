"""Interface text, in English and French.

Written the way a person would say it, not the way a form would print it. The
interface is the first thing a newcomer reads, and it sets the expectation for
whether this thing is going to talk down to them.

A language is named in its own language — "English", "Français" — rather than
by a flag. A flag is a country, and neither of these languages belongs to one
country; French speakers arriving from Dakar or Montréal are not served by
being handed a tricolore.
"""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "tagline": "You've just arrived in France. Ask anything about the "
                   "paperwork, in English or French — every answer comes "
                   "straight from official government sources, with a link.",
        "disclaimer_lead": "This isn't legal advice.",
        "disclaimer_body": "Repères tells you what the official pages say, and "
                           "shows you which ones. It can't advise you on a "
                           "refusal, an appeal, or your own particular case — "
                           "for that you need the administration itself.",
        "tab_ask": "Ask",
        "tab_glossary": "Words",
        "tab_corpus": "Sources",
        "placeholder": "Ask anything — how to renew a permit, what a landlord "
                       "can ask for, which papers you need…",
        "submit": "Ask",
        "try": "Not sure where to start?",
        "site_lang": "Language",
        "answer_lang": "Answer in",
        "lang_auto": "Whatever I ask in",
        "debug_title": "How this answer was found",
        "glossary_intro": "The French words you'll meet at a counter or in a "
                          "letter. The ones marked official are defined by the "
                          "government itself; the rest we wrote plainly.",
        "glossary_search": "Search a word…",
        "corpus_intro": "Everything Repères has read, and where it came from.",
        "refresh": "Refresh",
        "empty": "Ask a question and the answer will appear here.",
        "debug_empty": "Ask something, and this panel will show exactly how the "
                       "answer was found — what was searched, what was "
                       "retrieved, and what was thrown away.",
        "copy": "Copy",
        "copied": "Copied",
        "sources_head": "Where this came from",
        "stage_search": "Looking through the official pages…",
        "stage_read": "Reading what they say…",
        "stage_write": "Putting it together…",
        "grounded_one": "Based on 1 official page",
        "grounded_many": "Based on {n} official pages",
        "refused": "The sources don't cover this",
        "say_note": "Suggested wording — not an official text.",
        "no_match": "No word matches “{q}”.",
    },
    "fr": {
        "tagline": "Vous venez d'arriver en France. Posez vos questions sur les "
                   "démarches, en français ou en anglais — chaque réponse vient "
                   "directement des sources officielles, avec le lien.",
        "disclaimer_lead": "Ceci n'est pas un conseil juridique.",
        "disclaimer_body": "Repères vous dit ce que disent les pages "
                           "officielles, et vous montre lesquelles. Il ne peut "
                           "pas vous conseiller sur un refus, un recours ou "
                           "votre situation personnelle — pour cela, adressez-"
                           "vous à l'administration concernée.",
        "tab_ask": "Poser une question",
        "tab_glossary": "Les mots",
        "tab_corpus": "Les sources",
        "placeholder": "Posez votre question — renouveler un titre, ce qu'un "
                       "propriétaire peut demander, quels papiers fournir…",
        "submit": "Demander",
        "try": "Vous ne savez pas par où commencer ?",
        "site_lang": "Langue du site",
        "answer_lang": "Répondre en",
        "lang_auto": "La langue de ma question",
        "debug_title": "Comment cette réponse a été trouvée",
        "glossary_intro": "Les mots français que vous croiserez à un guichet ou "
                          "dans un courrier. Ceux marqués officiel sont définis "
                          "par l'administration ; les autres, nous les avons "
                          "écrits simplement.",
        "glossary_search": "Chercher un mot…",
        "corpus_intro": "Tout ce que Repères a lu, et d'où cela vient.",
        "refresh": "Actualiser",
        "empty": "Posez une question, la réponse apparaîtra ici.",
        "debug_empty": "Posez une question et ce panneau montrera exactement "
                       "comment la réponse a été trouvée : ce qui a été "
                       "cherché, ce qui a été retenu, ce qui a été écarté.",
        "copy": "Copier",
        "copied": "Copié",
        "sources_head": "D'où vient cette réponse",
        "stage_search": "Recherche dans les pages officielles…",
        "stage_read": "Lecture des textes…",
        "stage_write": "Rédaction de la réponse…",
        "grounded_one": "D'après 1 page officielle",
        "grounded_many": "D'après {n} pages officielles",
        "refused": "Les sources ne couvrent pas cette question",
        "say_note": "Formulation suggérée — ce n'est pas un texte officiel.",
        "no_match": "Aucun mot ne correspond à « {q} ».",
    },
}

EXAMPLES = {
    "en": [
        "I'm a student — when do I need to renew my residence permit?",
        "How much deposit can a landlord ask for?",
        "What counts as proof of address when I've just arrived?",
        "There are no appointment slots at my préfecture. What can I do?",
    ],
    "fr": [
        "Je viens d'arriver, comment valider mon VLS-TS ?",
        "Quel dépôt de garantie un propriétaire peut-il demander ?",
        "Qu'est-ce qui est accepté comme justificatif de domicile ?",
        "Il n'y a aucun créneau à ma préfecture. Que puis-je faire ?",
    ],
}

LANGUAGES = [("en", "English"), ("fr", "Français")]


def t(language: str, key: str, **kwargs: object) -> str:
    table = STRINGS.get(language, STRINGS["en"])
    text = table.get(key) or STRINGS["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text
