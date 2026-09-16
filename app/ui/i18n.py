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
        "headline": "What do you need help with?",
        "promise": "French administration, made clear.",
        "subhead": "Ask in English or French. Every answer comes from an "
                   "official government page.",
        "eyebrow": "Official sources only",
        "trust": "Every answer cites the page it came from, and the date that "
                 "page was last updated.",
        "send": "Send",
        "skip": "Skip to the question",
        "sections": "Sections",
        "study_prompt": "Studying in France? Tell me where and I can be more "
                        "precise — your institution decides which préfecture "
                        "and which CROUS you deal with.",
        "tagline": "You've just arrived in France. Ask in English or French — "
                   "every answer comes from an official government page, with "
                   "the link and the date it was last checked.",
        "disclaimer_lead": "This isn't legal advice.",
        "disclaimer_body": "Claré tells you what the official pages say, and "
                           "shows you which ones. It can't advise you on a "
                           "refusal, an appeal, or your own particular case — "
                           "for that you need the administration itself.",
        "tab_ask": "Ask",
        "tab_glossary": "Words",
        "tab_corpus": "Sources",
        "placeholder": "Residence permit, housing, documents, appointments…",
        "submit": "Ask",
        "try": "Not sure where to start?",
        "site_lang": "Language",
        "answer_lang": "Reply in",
        "lang_auto": "Auto",
        "lang_auto_help": "Same language as my question",
        "debug_title": "How this answer was found",
        "glossary_intro": "The French words you'll meet at a counter or in a "
                          "letter. The ones marked official are defined by the "
                          "government itself; the rest we wrote plainly.",
        "glossary_search": "Search a word…",
        "corpus_intro": "Everything Claré has read, and where it came from.",
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
                          "stranger. The one exception is a bug report — that "
                          "is saved, because it has to be, and only when you "
                          "send one.",
        "limit_title": "Out of tokens for now",
        "limit_body": "Nothing is broken and nothing you did caused this — the "
                      "meter simply ran out. Go and drink a glass of water, "
                      "slowly, sip by sip, and it will be back.",
        "limit_wait": "Back in about {mins} minutes.",
        "limit_wait_soon": "Back in under a minute.",
        "limit_local": "In a hurry? Point it at a local model and there is no "
                       "meter at all.",
        "report_open": "Report a problem",
        "report_cancel": "Cancel",
        "report_title": "Tell us what went wrong",
        "report_intro": "Write in any language — yours is fine. We'll translate "
                        "it. If an answer was wrong, saying what you were "
                        "actually told is the most useful thing you can give us.",
        "report_placeholder": "What happened, and what did you expect instead?",
        "report_send": "Send the report",
        "report_keeps": "This is written to a file on this computer — there is "
                        "no server to send it to. It keeps what you write, the "
                        "question you asked and which pages the answer used. "
                        "Nothing else, and nothing about who you are.",
        "report_thanks": "Thank you — saved on this computer.",
        "report_emailed": "Thank you — your report has been recorded and sent.",
        "report_email_failed": "Saved on this computer. The email notification "
                               "couldn't be delivered, so nothing was lost but "
                               "no one has been paged.",
        "report_doing": "What were you trying to do? (optional)",
        "report_doing_placeholder": "I was checking which papers I needed…",
        "report_saved_as": "Filed as",
        "report_empty": "Write a line or two first, and I'll take it from there.",
        "report_untriaged": "Saved in your own words. The translation step "
                            "couldn't run just now, so a person will read the "
                            "original.",
        "also_called": "Also written",
        "scope_live_verified": "checked just now",
        "scope_fresh": "current",
        "scope_stale": "stored copy",
        "scope_unknown": "never retrieved",
        "scope_unavailable": "site unreachable",
        "updated_on": "last updated {q}",
        "updated_unknown": "update date unavailable",
        "updated_suspect": "last updated {q} (date as published; appears mistyped)",
        "provider_reachable": "reachable",
        "provider_unavailable": "unavailable",
        "gloss_official": "Official definition",
        "gloss_authored": "Written for Claré",
        "study_label": "Studying somewhere? (optional)",
        "study_placeholder": "Start typing your university or school…",
        "study_none": "Not a student",
        "study_why": "Your institution tells us your département, and that is "
                     "what decides which préfecture handles your file.",
        "study_dept": "Département",
        "study_aca": "Académie",
        "study_site": "Official site",
        "copy": "Copy",
        "copied": "Copied",
        "sources_head": "Where this came from",
        "peek": "see the exact wording",
        "pop_source": "What this page says",
        "pop_service": "This opens the official service",
        "sources_toggle_one": "Check it against the official page",
        "sources_toggle_many": "Check it against the {n} official pages",
        "you": "You",
        "assistant": "Claré",
        "thinking": "Reading the official pages",
        "ask_another": "Ask another question",
        "new_question": "Start over",
        "copy_answer": "Copy",
        "copied_answer": "Copied",
        "helpful": "This helped",
        "not_helpful": "This didn't help",
        "thanks_feedback": "Noted — thank you.",
        "followup_ph": "Ask a follow-up…",
        "cat_head": "Start with a topic",
        "cat_permit": "Residence permits",
        "cat_permit_sub": "Renewals, first applications, changes",
        "cat_housing": "Housing",
        "cat_housing_sub": "Deposits, leases, what a landlord may ask",
        "cat_docs": "Documents",
        "cat_docs_sub": "Proof of address, civil status, translations",
        "cat_student": "Studying in France",
        "cat_student_sub": "Student work, CROUS, enrolment",
        "cat_work": "Work",
        "cat_work_sub": "CDI, CDD, trial period, payslips",
        "cat_appt": "Appointments",
        "cat_appt_sub": "Prefecture slots, and what to do without one",
        "err_network": "We couldn't reach the service. Try again in a moment.",
        "err_unknown": "Something went wrong while preparing the answer.",
        "err_retry": "Try again",
        "source_gap": "I couldn't find an official page that confirms this. "
                      "Rather than show you pages that are close but about "
                      "something else, here is nothing — which is the honest "
                      "answer.",
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
        "clarify_head": "Which one do you mean?",
        "clarify_body": "Name the school or university and I'll look it up. I "
                        "won't guess — an answer about the wrong institution "
                        "is worse than no answer.",
        "entity_head": "That's {name}'s own procedure",
        "entity_body": "What I've read covers French public administration. An "
                       "institution sets and publishes its own admission and "
                       "enrolment rules, so that answer comes from them, not "
                       "from here.",
        "entity_register": "{name} in the official register",
        "fresh_live": "Read from the official site just now.",
        "fresh_cached": "From the official site, read recently.",
        "fresh_stale": "From a stored copy — the official site couldn't be "
                       "reached just now, so this may have moved on.",
        "fresh_unavailable": "I couldn't reach the official site just now.",
        "live_source_head": "Official source",
        "source_is": "Source: {name}",
        "clarify_place_head": "Where in France are you?",
        "clarify_place_body": "This one depends on your préfecture, and they "
                              "don't all ask for the same things. Tell me the "
                              "town or the département and I'll use theirs.",
        "authority_down_head": "I couldn't reach {name} just now",
        "authority_down_body": "Rather than give you a requirement that may "
                               "have changed, I'd rather say so. Try again in "
                               "a little while, or check their site directly.",
        "local_authority_is": "Local authority: {name}",
        "authority_partial": "{name} is the authority for this, and I couldn't "
                             "check its current page just now — so treat the "
                             "below as background rather than as their rule.",
        "say_note": "Suggested wording — not an official text.",
        "no_match": "No word matches “{q}”.",
    },
    "fr": {
        "headline": "De quoi avez-vous besoin ?",
        "promise": "L'administration française, en clair.",
        "subhead": "Posez votre question en français ou en anglais. Chaque "
                   "réponse vient d'une page officielle.",
        "eyebrow": "Uniquement des sources officielles",
        "trust": "Chaque réponse cite la page dont elle vient, et la date de "
                 "sa dernière mise à jour.",
        "send": "Envoyer",
        "skip": "Aller à la question",
        "sections": "Rubriques",
        "study_prompt": "Vous étudiez en France ? Dites-moi où et je serai plus "
                        "précis : votre établissement détermine la préfecture "
                        "et le CROUS dont vous dépendez.",
        "tagline": "Vous venez d'arriver en France. Posez vos questions en "
                   "français ou en anglais — chaque réponse vient d'une page "
                   "officielle, avec le lien et la date.",
        "disclaimer_lead": "Ceci n'est pas un conseil juridique.",
        "disclaimer_body": "Claré vous dit ce que disent les pages "
                           "officielles, et vous montre lesquelles. Il ne peut "
                           "pas vous conseiller sur un refus, un recours ou "
                           "votre situation personnelle — pour cela, adressez-"
                           "vous à l'administration concernée.",
        "tab_ask": "Ma question",
        "tab_glossary": "Les mots",
        "tab_corpus": "Les sources",
        "placeholder": "Titre de séjour, logement, papiers, rendez-vous…",
        "submit": "Demander",
        "try": "Vous ne savez pas par où commencer ?",
        "site_lang": "Langue du site",
        "answer_lang": "Répondre en",
        "lang_auto": "Auto",
        "lang_auto_help": "La langue de ma question",
        "debug_title": "Comment cette réponse a été trouvée",
        "glossary_intro": "Les mots français que vous croiserez à un guichet ou "
                          "dans un courrier. Ceux marqués officiel sont définis "
                          "par l'administration ; les autres, nous les avons "
                          "écrits simplement.",
        "glossary_search": "Chercher un mot…",
        "corpus_intro": "Tout ce que Claré a lu, et d'où cela vient.",
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
                          "donc rien que vous ne confieriez pas à un inconnu. "
                          "Seule exception : un signalement de bug, qui est "
                          "enregistré, parce qu'il le faut, et seulement si "
                          "vous en envoyez un.",
        "limit_title": "Plus de jetons pour le moment",
        "limit_body": "Rien n'est cassé et vous n'y êtes pour rien — le "
                      "compteur est simplement arrivé au bout. Allez boire un "
                      "verre d'eau, doucement, gorgée par gorgée, et ça "
                      "revient.",
        "limit_wait": "De retour dans environ {mins} minutes.",
        "limit_wait_soon": "De retour dans moins d'une minute.",
        "limit_local": "Pressé ? Branchez un modèle local et il n'y a plus de "
                       "compteur du tout.",
        "report_open": "Signaler un problème",
        "report_cancel": "Annuler",
        "report_title": "Dites-nous ce qui s'est passé",
        "report_intro": "Écrivez dans la langue que vous voulez — la vôtre "
                        "convient très bien, nous traduirons. Si une réponse "
                        "était fausse, nous dire ce qu'on vous a réellement dit "
                        "est ce qui nous aide le plus.",
        "report_placeholder": "Que s'est-il passé, et à quoi vous attendiez-vous ?",
        "report_send": "Envoyer",
        "report_keeps": "Ceci est écrit dans un fichier sur cet ordinateur — "
                        "il n'y a pas de serveur où l'envoyer. Sont conservés : "
                        "ce que vous écrivez, la question posée et les pages "
                        "utilisées. Rien d'autre, et rien sur qui vous êtes.",
        "report_thanks": "Merci — enregistré sur cet ordinateur.",
        "report_emailed": "Merci — votre signalement a été enregistré et envoyé.",
        "report_email_failed": "Enregistré sur cet ordinateur. La notification "
                               "par e-mail n'a pas pu être envoyée : rien n'est "
                               "perdu, mais personne n'a encore été prévenu.",
        "report_doing": "Que cherchiez-vous à faire ? (facultatif)",
        "report_doing_placeholder": "Je vérifiais quels papiers il me fallait…",
        "report_saved_as": "Enregistré sous",
        "report_empty": "Écrivez une ou deux lignes, et je m'occupe du reste.",
        "report_untriaged": "Enregistré dans vos mots. La traduction n'a pas pu "
                            "se faire à l'instant : une personne lira "
                            "l'original.",
        "also_called": "Aussi écrit",
        "scope_live_verified": "vérifié à l'instant",
        "scope_fresh": "à jour",
        "scope_stale": "copie enregistrée",
        "scope_unknown": "jamais consulté",
        "scope_unavailable": "site injoignable",
        "updated_on": "mise à jour le {q}",
        "updated_unknown": "date de mise à jour inconnue",
        "updated_suspect": "mise à jour le {q} (date telle que publiée ; semble erronée)",
        "provider_reachable": "joignable",
        "provider_unavailable": "indisponible",
        "gloss_official": "Définition officielle",
        "gloss_authored": "Rédigé pour Claré",
        "study_label": "Vous étudiez quelque part ? (facultatif)",
        "study_placeholder": "Tapez le nom de votre université ou école…",
        "study_none": "Je ne suis pas étudiant",
        "study_why": "Votre établissement indique votre département, et c'est "
                     "lui qui détermine la préfecture qui traite votre dossier.",
        "study_dept": "Département",
        "study_aca": "Académie",
        "study_site": "Site officiel",
        "copy": "Copier",
        "copied": "Copié",
        "sources_head": "D'où vient cette réponse",
        "peek": "voir le texte exact",
        "pop_source": "Ce que dit cette page",
        "pop_service": "Ouvre le service officiel",
        "sources_toggle_one": "Vérifier sur la page officielle",
        "sources_toggle_many": "Vérifier sur les {n} pages officielles",
        "you": "Vous",
        "assistant": "Claré",
        "thinking": "Lecture des pages officielles",
        "ask_another": "Poser une autre question",
        "new_question": "Recommencer",
        "copy_answer": "Copier",
        "copied_answer": "Copié",
        "helpful": "Ça m'a aidé",
        "not_helpful": "Ça ne m'a pas aidé",
        "thanks_feedback": "C'est noté — merci.",
        "followup_ph": "Poser une question complémentaire…",
        "cat_head": "Commencer par un sujet",
        "cat_permit": "Titres de séjour",
        "cat_permit_sub": "Renouvellement, première demande, changement",
        "cat_housing": "Logement",
        "cat_housing_sub": "Dépôt de garantie, bail, ce qu'un propriétaire exige",
        "cat_docs": "Papiers",
        "cat_docs_sub": "Justificatif de domicile, état civil, traductions",
        "cat_student": "Études en France",
        "cat_student_sub": "Travail étudiant, CROUS, inscription",
        "cat_work": "Travail",
        "cat_work_sub": "CDI, CDD, période d'essai, bulletin de paie",
        "cat_appt": "Rendez-vous",
        "cat_appt_sub": "Créneaux en préfecture, et que faire sans",
        "err_network": "Le service est injoignable. Réessayez dans un instant.",
        "err_unknown": "Un problème est survenu pendant la préparation de la réponse.",
        "err_retry": "Réessayer",
        "source_gap": "Je n'ai pas trouvé de page officielle qui le "
                      "confirme. Plutôt que de vous montrer des pages proches "
                      "mais qui parlent d'autre chose, je ne montre rien : "
                      "c'est la réponse honnête.",
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
        "clarify_head": "Duquel s'agit-il ?",
        "clarify_body": "Donnez-moi le nom de l'école ou de l'université et je "
                        "la retrouve. Je ne devine pas : une réponse sur le "
                        "mauvais établissement est pire que pas de réponse.",
        "entity_head": "C'est la procédure propre à {name}",
        "entity_body": "Ce que j'ai lu couvre l'administration publique "
                       "française. Un établissement fixe et publie lui-même ses "
                       "règles d'admission et d'inscription : cette réponse "
                       "vient de lui, pas d'ici.",
        "entity_register": "{name} dans l'annuaire officiel",
        "fresh_live": "Lu à l'instant sur le site officiel.",
        "fresh_cached": "Depuis le site officiel, consulté récemment.",
        "fresh_stale": "Depuis une copie enregistrée — le site officiel est "
                       "injoignable pour le moment, cela a pu changer depuis.",
        "fresh_unavailable": "Le site officiel est injoignable pour le moment.",
        "live_source_head": "Source officielle",
        "source_is": "Source : {name}",
        "clarify_place_head": "Où êtes-vous en France ?",
        "clarify_place_body": "Cela dépend de votre préfecture, et elles ne "
                              "demandent pas toutes la même chose. Dites-moi "
                              "la ville ou le département et j'utiliserai la "
                              "bonne.",
        "authority_down_head": "Je n'ai pas pu joindre {name} à l'instant",
        "authority_down_body": "Plutôt que de vous donner une exigence qui a "
                               "peut-être changé, je préfère vous le dire. "
                               "Réessayez un peu plus tard, ou consultez "
                               "directement leur site.",
        "local_authority_is": "Autorité locale : {name}",
        "authority_partial": "{name} fait autorité ici, et je n'ai pas pu "
                             "consulter sa page à l'instant : prenez ce qui "
                             "suit comme un éclairage, pas comme leur règle.",
        "say_note": "Formulation suggérée — ce n'est pas un texte officiel.",
        "no_match": "Aucun mot ne correspond à « {q} ».",
    },
}


# Each category carries the question it asks, so a tap is a question and not
# a filter that goes nowhere.
CATEGORIES = {
    "en": [
        ("cat_permit",  "permit",  "How do I renew my residence permit, and what do I need?"),
        ("cat_housing", "housing", "How much deposit can a landlord ask for, and when do I get it back?"),
        ("cat_docs",    "docs",    "What counts as proof of address when I have just arrived?"),
        ("cat_student", "student", "I am a student in France — what am I allowed to work?"),
        ("cat_work",    "work",    "What is the difference between a CDI and a CDD?"),
        ("cat_appt",    "appt",    "There are no appointment slots at my préfecture. What can I do?"),
    ],
    "fr": [
        ("cat_permit",  "permit",  "Comment renouveler mon titre de séjour, et que faut-il fournir ?"),
        ("cat_housing", "housing", "Quel dépôt de garantie un propriétaire peut-il demander, et quand est-il rendu ?"),
        ("cat_docs",    "docs",    "Qu'est-ce qui est accepté comme justificatif de domicile quand on vient d'arriver ?"),
        ("cat_student", "student", "Je suis étudiant en France — combien puis-je travailler ?"),
        ("cat_work",    "work",    "Quelle est la différence entre un CDI et un CDD ?"),
        ("cat_appt",    "appt",    "Il n'y a aucun créneau à ma préfecture. Que puis-je faire ?"),
    ],
}

LANGUAGES = [("en", "English"), ("fr", "Français")]


def t(language: str, key: str, **kwargs: object) -> str:
    table = STRINGS.get(language, STRINGS["en"])
    text = table.get(key) or STRINGS["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text
