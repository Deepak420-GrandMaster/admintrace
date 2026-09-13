"""Build app/query/glossary.json.

French explanations are taken from the official definitions published inside
the corpus wherever one exists, and are marked with the identifier they came
from. Where the corpus has no definition, the entry is written by hand and
marked as such, so the two are never confused.

English glosses and aliases are always written by hand: the source is French
only. They describe what a word means. They never state a deadline, a fee, a
document requirement or an eligibility condition — those reach a user only
through retrieval.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from app.config import get_settings
from app.ingest.parse import ParseReport, deduplicate, parse_directory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET = PROJECT_ROOT / "app" / "query" / "glossary.json"


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").strip()


# term: (english gloss, english explanation, aliases, authored french fallback)
TERMS: dict[str, tuple[str, str, list[str], str]] = {
    "titre de séjour": ("residence permit", "The document proving you are legally resident in France as a foreign national.", ["residence permit", "residency card", "residence card"], "Document attestant du droit de séjourner en France pour un ressortissant étranger."),
    "carte de séjour": ("residence card", "A residence permit issued as a card, valid for a set period and a stated purpose such as study or work.", ["residence card", "residency permit card"], "Titre de séjour délivré sous forme de carte, pour une durée et un motif déterminés."),
    "VLS-TS": ("long-stay visa acting as a residence permit", "A long-stay visa that serves as a residence permit once validated after arrival, instead of a separate card.", ["long stay visa", "long-stay visa equivalent to residence permit", "vls ts"], "Visa de long séjour valant titre de séjour, qui tient lieu de titre de séjour après validation."),
    "récépissé": ("receipt of application", "A temporary document proving an application has been filed and is being processed.", ["receipt", "temporary permit", "proof of application", "recepisse"], "Document remis lors du dépôt d'une demande, attestant que celle-ci est en cours d'instruction."),
    "attestation de dépôt": ("filing confirmation", "Written confirmation that an application has been submitted.", ["filing receipt", "submission confirmation", "proof of filing"], "Document confirmant le dépôt d'une demande auprès d'une administration."),
    "préfecture": ("prefecture", "The local branch of the State in a département, responsible for residence permits among other things.", ["prefecture", "local state office"], "Représentation de l'État dans le département."),
    "OFII": ("French Office for Immigration and Integration", "The public body handling parts of the arrival process for foreign nationals.", ["office of immigration", "immigration office", "ofii"], "Office français de l'immigration et de l'intégration."),
    "ANEF": ("digital portal for foreign nationals", "The online platform where many residence-permit procedures are carried out.", ["anef", "foreigners online portal", "immigration portal"], "Administration numérique pour les étrangers en France, plateforme en ligne des démarches de séjour."),
    "changement de statut": ("change of status", "Moving from one category of residence permit to another, for example from student to employee.", ["change of status", "status change", "switch permit type"], "Passage d'une catégorie de titre de séjour à une autre."),
    "numéro de sécurité sociale": ("social security number", "The personal number identifying you in the French health and social security system.", ["social security number", "ssn", "insurance number"], "Numéro identifiant une personne dans le système de sécurité sociale française."),
    "attestation de droits": ("proof of health cover", "A document stating what health cover you are currently entitled to.", ["proof of entitlement", "certificate of health cover", "insurance certificate"], "Document indiquant les droits à l'assurance maladie dont une personne bénéficie."),
    "carte Vitale": ("health insurance card", "The card that lets healthcare providers bill the health insurance system directly.", ["health card", "health insurance card", "vitale card"], "Carte à puce permettant la prise en charge des soins par l'assurance maladie."),
    "CPAM": ("local health insurance office", "The local office administering health insurance for residents of an area.", ["health insurance office", "cpam", "local health authority"], "Caisse primaire d'assurance maladie, organisme local de l'assurance maladie."),
    "médecin traitant": ("registered GP", "The doctor you register as your main point of contact for healthcare.", ["family doctor", "primary care doctor", "gp", "referring doctor"], "Médecin déclaré par un assuré comme son interlocuteur principal pour son suivi médical."),
    "mutuelle": ("supplementary health insurance", "Optional or employer-provided cover that pays part of what the public system does not.", ["top up insurance", "complementary health insurance", "supplemental insurance"], "Organisme de complémentaire santé prenant en charge une part des frais non remboursés."),
    "feuille de soins": ("paper treatment form", "A paper form used to claim reimbursement when the card was not used.", ["treatment form", "claim form", "reimbursement form"], "Formulaire papier permettant de demander le remboursement de soins."),
    "CAF": ("family benefits office", "The body paying family and housing benefits.", ["family allowance office", "caf", "benefits office"], "Caisse d'allocations familiales, organisme versant les prestations familiales et sociales."),
    "APL": ("housing benefit", "A housing benefit paid towards rent.", ["housing benefit", "housing allowance", "rent assistance", "apl"], "Aide personnalisée au logement, prestation contribuant au paiement du loyer."),
    "attestation de loyer": ("landlord rent certificate", "A form the landlord completes confirming the tenancy and the rent.", ["rent certificate", "landlord certificate", "rent attestation"], "Document rempli par le bailleur attestant de la location et du montant du loyer."),
    "traducteur assermenté": ("sworn translator", "A translator authorised by a court, whose translations administrations accept.", ["sworn translator", "certified translator", "official translator"], "Traducteur habilité par une cour d'appel, dont les traductions sont reconnues par l'administration."),
    "acte de naissance": ("birth certificate", "The civil-status record of a birth.", ["birth certificate", "birth record"], "Acte d'état civil constatant la naissance d'une personne."),
    "justificatif de domicile": ("proof of address", "A document accepted as evidence of where you live.", ["proof of address", "address proof", "proof of residence"], "Document servant à prouver l'adresse du domicile d'une personne."),
    "attestation d'hébergement": ("proof of accommodation by a host", "A statement from someone confirming they house you at their address.", ["proof of accommodation", "host certificate", "accommodation letter"], "Attestation par laquelle une personne déclare héberger quelqu'un à son domicile."),
    "RIB": ("bank account details", "The slip giving your bank account identifiers for payments.", ["bank details", "account details", "bank identity statement", "rib"], "Relevé d'identité bancaire, document indiquant les identifiants d'un compte bancaire."),
    "IBAN": ("international bank account number", "The internationally formatted identifier of a bank account.", ["iban", "international account number"], "Numéro de compte bancaire au format international."),
    "dossier locataire": ("rental application file", "The set of documents a prospective tenant provides to a landlord.", ["rental application", "tenant file", "rental file"], "Ensemble des documents fournis par un candidat à la location."),
    "garant": ("guarantor", "Someone who undertakes to pay the rent if the tenant does not.", ["guarantor", "cosigner", "surety"], "Personne qui s'engage à payer à la place du locataire en cas de défaut."),
    "caution solidaire": ("joint and several guarantee", "A guarantee allowing the landlord to claim from the guarantor directly.", ["joint guarantee", "solidary guarantee", "cosigner guarantee"], "Engagement par lequel une personne peut être appelée à payer directement la dette du locataire."),
    "dépôt de garantie": ("rental deposit", "A sum held by the landlord during the tenancy and returned afterwards under conditions.", ["deposit", "security deposit", "rental deposit"], "Somme versée au bailleur à la signature du bail et restituée après le départ du locataire."),
    "quittance de loyer": ("rent receipt", "A receipt confirming rent and charges were paid.", ["rent receipt", "proof of rent payment"], "Document attestant du paiement du loyer et des charges."),
    "état des lieux": ("property condition report", "A written record of a property's condition at move-in and move-out.", ["inventory", "condition report", "check in report", "walkthrough"], "Constat écrit de l'état d'un logement à l'entrée et à la sortie du locataire."),
    "bail": ("lease", "The rental contract between landlord and tenant.", ["lease", "rental contract", "tenancy agreement"], "Contrat de location entre un bailleur et un locataire."),
    "CDI": ("permanent employment contract", "An employment contract with no end date.", ["permanent contract", "open ended contract", "cdi"], "Contrat de travail à durée indéterminée."),
    "CDD": ("fixed-term employment contract", "An employment contract with a defined end.", ["fixed term contract", "temporary contract", "cdd"], "Contrat de travail à durée déterminée."),
    "période d'essai": ("probation period", "An initial period during which either party may end the contract more easily.", ["probation", "trial period", "probationary period"], "Période initiale d'un contrat de travail pendant laquelle il peut être rompu plus librement."),
    "bulletin de paie": ("payslip", "The statement itemising pay and deductions.", ["payslip", "pay stub", "salary slip", "fiche de paie"], "Document remis au salarié détaillant la rémunération et les cotisations."),
    "congés payés": ("paid leave", "Paid holiday earned through work.", ["paid leave", "paid holiday", "annual leave", "vacation days"], "Jours de congé rémunérés acquis au titre du travail effectué."),
    "convention de stage": ("internship agreement", "The tripartite agreement between student, school and host organisation.", ["internship agreement", "internship contract", "traineeship agreement"], "Convention signée entre le stagiaire, l'établissement d'enseignement et l'organisme d'accueil."),
    "avis d'imposition": ("tax assessment notice", "The statement the tax authority issues after a return, often required as proof of income.", ["tax notice", "tax assessment", "tax statement"], "Document émis par l'administration fiscale après la déclaration de revenus."),
    "numéro fiscal": ("tax number", "The number identifying you to the tax authority.", ["tax number", "tax id", "fiscal number"], "Numéro identifiant un contribuable auprès de l'administration fiscale."),
    "prélèvement à la source": ("pay-as-you-earn withholding", "Income tax deducted directly from income as it is paid.", ["withholding tax", "paye", "tax at source", "source deduction"], "Mode de recouvrement de l'impôt consistant à le prélever au moment du versement des revenus."),
    "URSSAF": ("social contributions collection body", "The body collecting social security contributions, including from the self-employed.", ["social contributions office", "urssaf"], "Organisme chargé du recouvrement des cotisations sociales."),
    "auto-entrepreneur": ("sole trader under the simplified regime", "A simplified self-employed status with lighter reporting.", ["sole trader", "freelancer status", "micro entrepreneur", "self employed status"], "Régime simplifié d'exercice d'une activité indépendante."),
    "SIRET": ("business establishment number", "The number identifying a specific business establishment.", ["business number", "company registration number", "siret"], "Numéro identifiant un établissement d'une entreprise."),
    "FranceConnect": ("shared government login", "A single sign-in used across many public online services.", ["government login", "single sign on", "franceconnect"], "Dispositif permettant de se connecter à plusieurs services publics en ligne avec un même compte."),
    "mairie": ("town hall", "The municipal administration of a commune.", ["town hall", "city hall", "municipality"], "Administration de la commune."),
    "ANTS": ("national agency for secure documents", "The agency handling official documents such as licences and identity papers.", ["national agency for secure documents", "ants"], "Agence nationale des titres sécurisés."),
    # --- additions beyond the seed list ---------------------------------
    "carte de résident": ("long-term resident card", "A longer-duration residence card for established residents.", ["resident card", "long term residence card"], "Titre de séjour de longue durée délivré à certains étrangers."),
    "regroupement familial": ("family reunification", "The procedure for bringing close family to join a resident.", ["family reunification", "family reunion"], "Procédure permettant de faire venir sa famille proche en France."),
    "PACS": ("civil partnership", "A registered partnership between two people.", ["civil partnership", "civil union", "pacs"], "Pacte civil de solidarité, contrat entre deux personnes majeures organisant leur vie commune."),
    "état civil": ("civil status", "The official record of births, marriages and deaths.", ["civil status", "vital records", "registry office"], "Ensemble des éléments relatifs à l'identité et à la situation familiale d'une personne."),
    "CROUS": ("student services agency", "The regional body handling student housing and grants.", ["student services", "student housing agency", "crous"], "Centre régional des œuvres universitaires et scolaires."),
    "bourse sur critères sociaux": ("means-tested student grant", "A student grant awarded on financial criteria.", ["student grant", "means tested grant", "scholarship"], "Aide financière attribuée à un étudiant selon des critères sociaux."),
    "DSE": ("student social file", "The single application used for student grants and housing.", ["student social file", "dse", "student aid application"], "Dossier social étudiant, demande unique de bourse et de logement."),
    "ameli": ("health insurance online account", "The online account for health insurance matters.", ["health insurance account", "ameli"], "Service en ligne de l'assurance maladie."),
    "tiers payant": ("direct billing", "An arrangement where you do not pay the insured part upfront.", ["direct billing", "no upfront payment"], "Dispositif dispensant le patient d'avancer les frais pris en charge."),
    "arrêt de travail": ("medical leave certificate", "A doctor's certificate justifying absence from work.", ["sick leave", "medical certificate", "work stoppage"], "Document médical justifiant une interruption de travail."),
    "visale": ("public rent guarantee", "A public scheme acting as guarantor for a tenant.", ["rent guarantee", "public guarantor", "visale"], "Dispositif public de garantie des loyers."),
    "colocation": ("flatshare", "A tenancy shared by several tenants.", ["flatshare", "shared flat", "roommates", "house share"], "Location d'un même logement par plusieurs locataires."),
    "préavis": ("notice period", "The notice one party must give before ending a contract or tenancy.", ["notice", "notice period"], "Délai à respecter avant la fin d'un contrat ou d'un bail."),
    "charges locatives": ("rental charges", "Costs billed alongside rent for shared services.", ["service charges", "rental charges", "utilities charges"], "Sommes dues par le locataire en plus du loyer pour certains services."),
    "taxe d'habitation": ("residence tax", "A local tax linked to occupying a dwelling.", ["residence tax", "council tax", "housing tax"], "Impôt local lié à l'occupation d'un logement."),
    "déclaration de revenus": ("income tax return", "The annual declaration of income to the tax authority.", ["tax return", "income declaration", "tax filing"], "Déclaration annuelle des revenus à l'administration fiscale."),
    "résident fiscal": ("tax resident", "Someone whose tax obligations attach to France.", ["tax resident", "fiscal residence", "tax residency"], "Personne dont le domicile fiscal est situé en France."),
    "attestation Pôle emploi": ("employment certificate for benefits", "An employer document used when claiming unemployment benefits.", ["employment certificate", "unemployment certificate"], "Document remis par l'employeur permettant de faire valoir ses droits au chômage."),
    "solde de tout compte": ("final settlement", "The statement of everything owed when employment ends.", ["final settlement", "final pay statement"], "Document récapitulant les sommes versées au salarié à la fin du contrat."),
    "rupture conventionnelle": ("mutually agreed termination", "An agreed end to a permanent employment contract.", ["mutual termination", "settlement agreement"], "Rupture d'un contrat à durée indéterminée d'un commun accord."),
    "titre de séjour pluriannuel": ("multi-year residence permit", "A residence permit covering several years at once.", ["multi year permit", "multiannual permit"], "Titre de séjour délivré pour une durée de plusieurs années."),
    "passeport talent": ("talent passport permit", "A residence permit category for certain qualified profiles.", ["talent passport", "talent permit"], "Catégorie de titre de séjour destinée à certains profils qualifiés."),
    "autorisation de travail": ("work authorisation", "Permission required for a foreign national to take up certain employment.", ["work permit", "work authorisation"], "Autorisation requise pour exercer une activité salariée en France."),
    "SIREN": ("business identification number", "The number identifying a business as a legal unit.", ["company number", "business id", "siren"], "Numéro identifiant une entreprise."),
    "TVA": ("value added tax", "The tax added to the price of goods and services.", ["vat", "value added tax", "sales tax"], "Taxe sur la valeur ajoutée."),
}


def match_official(term: str, official: dict[str, tuple[str, str, str]]
                   ) -> tuple[str, str] | None:
    """Find the official definition for a term.

    Published definition titles carry qualifiers the term itself does not, for
    example a parenthetical naming the area of law it applies in. Matching is
    therefore tried exactly first, then against the title with any qualifier
    removed, and only then as a prefix, so that a loose match can never win
    over an exact one.
    """
    key = fold(term)
    if key in official:
        ref, body, _ = official[key]
        return ref, body

    for candidate, (ref, body, bare) in official.items():
        if bare == key:
            return ref, body

    prefixed = [
        (candidate, value) for candidate, value in official.items()
        if candidate.startswith(key + " ") or value[2] == key
    ]
    if len(prefixed) == 1:
        return prefixed[0][1][0], prefixed[0][1][1]
    return None


def official_definitions() -> dict[str, tuple[str, str, str]]:
    settings = get_settings()
    report = ParseReport()
    for segment in settings.feed_segments:
        directory = settings.extracted_dir / settings.feed_version / segment
        if directory.exists():
            parse_directory(directory, segment, report)
    documents, _ = deduplicate(report.documents)

    found: dict[str, tuple[str, str, str]] = {}
    for document in documents:
        for ref, term, body in document.definitions:
            key = fold(term)
            # Title without any trailing qualifier, e.g. "(location immobiliere)".
            bare = fold(re.sub(r"\s*\([^)]*\)\s*$", "", term))
            if key not in found and body:
                found[key] = (ref, re.sub(r"\s+", " ", body).strip(), bare)
    return found


def main() -> None:
    official = official_definitions()
    glossary: dict[str, dict] = {}
    matched = 0

    for term, (en, explanation_en, aliases, authored_fr) in TERMS.items():
        entry = {
            "en": en,
            "explanation_en": explanation_en,
            "aliases_en": sorted({a.lower() for a in aliases} | {term.lower()}),
        }
        hit = match_official(term, official)
        if hit is not None:
            ref, body = hit
            entry["explanation_fr"] = body
            entry["source"] = "service-public.gouv.fr"
            entry["definition_id"] = ref
            matched += 1
        else:
            entry["explanation_fr"] = authored_fr
            entry["source"] = "authored"
        glossary[term] = entry

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"terms                      : {len(glossary)}")
    print(f"official French definition : {matched}")
    print(f"authored French definition : {len(glossary) - matched}")
    print(f"written to {TARGET.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
