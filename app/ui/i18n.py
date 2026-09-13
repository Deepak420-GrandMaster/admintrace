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
        "headline": "The paperwork, in plain words.",
        "tagline": "You've just arrived in France. Ask in English or French — "
                   "every answer comes from an official government page, with "
                   "the link and the date it was last checked.",
        "disclaimer_lead": "This isn't legal advice.",
        "disclaimer_body": "Sésame tells you what the official pages say, and "
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
        "corpus_intro": "Everything Sésame has read, and where it came from.",
        "refresh": "Refresh",
        "empty": "Ask a question and the answer will appear here.",
        "debug_empty": "Ask something, and this panel will show exactly how the "
                       "answer was found — what was searched, what was "
                       "retrieved, and what was thrown away.",
        "privacy_lead": "Nothing about you is kept.",
        "privacy_local": "No account, no cookies, no history. Your question "
                         "isn't saved anywhere, and nothing leaves this "
                         "computer — the search and the answer both run here.",
        "privacy_hosted": "No account, no cookies, no history. Your question "
                          "isn't saved anywhere and never written to disk. The "
                          "search runs on this computer; the wording of your "
                          "question is sent to {provider} to write the answer, "
                          "so don't type anything into it you wouldn't send a "
                          "stranger.",
        "also_called": "Also written",
        "copy": "Copy",
        "copied": "Copied",
        "sources_head": "Where this came from",
        "peek": "see the exact wording",
        "pop_source": "What this page says",
        "pop_service": "This opens the official service",
        "sources_toggle_one": "Check it against the official page",
        "sources_toggle_many": "Check it against the {n} official pages",
        "services_head": "Do it here",
        "services_note": "Official government service — opens in a new tab.",
        "stage_search": "Queueing at the right counter…",
        "stage_read": "Reading the small print so you don't have to…",
        "stage_write": "Putting it in plain words…",
        "made_by": "Made with love by Deepak Prajapati",
        "made_year": "© 2026",
        "community": "Built so the next person arriving has an easier time of "
                     "it than you did. Know something the official pages don't? "
                     "That's exactly what this needs.",
        "grounded_one": "Based on 1 official page",
        "grounded_many": "Based on {n} official pages",
        "refused": "The sources don't cover this",
        "say_note": "Suggested wording — not an official text.",
        "no_match": "No word matches “{q}”.",
    },
    "fr": {
        "headline": "Les démarches, en mots simples.",
        "tagline": "Vous venez d'arriver en France. Posez vos questions en "
                   "français ou en anglais — chaque réponse vient d'une page "
                   "officielle, avec le lien et la date.",
        "disclaimer_lead": "Ceci n'est pas un conseil juridique.",
        "disclaimer_body": "Sésame vous dit ce que disent les pages "
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
        "corpus_intro": "Tout ce que Sésame a lu, et d'où cela vient.",
        "refresh": "Actualiser",
        "empty": "Posez une question, la réponse apparaîtra ici.",
        "debug_empty": "Posez une question et ce panneau montrera exactement "
                       "comment la réponse a été trouvée : ce qui a été "
                       "cherché, ce qui a été retenu, ce qui a été écarté.",
        "privacy_lead": "Rien de vous n'est conservé.",
        "privacy_local": "Pas de compte, pas de cookies, pas d'historique. "
                         "Votre question n'est enregistrée nulle part et rien "
                         "ne quitte cet ordinateur : la recherche et la réponse "
                         "se font ici.",
        "privacy_hosted": "Pas de compte, pas de cookies, pas d'historique. "
                          "Votre question n'est enregistrée nulle part ni "
                          "écrite sur le disque. La recherche se fait sur cet "
                          "ordinateur ; le texte de votre question est envoyé "
                          "à {provider} pour rédiger la réponse — n'y écrivez "
                          "donc rien que vous ne confieriez pas à un inconnu.",
        "also_called": "Aussi écrit",
        "copy": "Copier",
        "copied": "Copié",
        "sources_head": "D'où vient cette réponse",
        "peek": "voir le texte exact",
        "pop_source": "Ce que dit cette page",
        "pop_service": "Ouvre le service officiel",
        "sources_toggle_one": "Vérifier sur la page officielle",
        "sources_toggle_many": "Vérifier sur les {n} pages officielles",
        "services_head": "C'est ici que ça se passe",
        "services_note": "Service officiel de l'administration — s'ouvre dans un nouvel onglet.",
        "stage_search": "On fait la queue au bon guichet…",
        "stage_read": "On lit les petits caractères pour vous…",
        "stage_write": "On le met en mots simples…",
        "made_by": "Fait avec amour par Deepak Prajapati",
        "made_year": "© 2026",
        "community": "Fait pour que la prochaine personne qui arrive s'en sorte "
                     "mieux que vous. Vous savez quelque chose que les pages "
                     "officielles ne disent pas ? C'est exactement ce qu'il "
                     "manque ici.",
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
