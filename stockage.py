"""Persistance en fichiers JSON (aucune base de données à installer).

donnees/
  parametres.json   réglages généraux (ton prénom, langue, mémoire générale)
  comptes.json      comptes d'IA (Claude Code, Codex, Antigravity...)
  profils.json      profils d'IA (rôle, consignes, compte, modèle, effort, accès)
  projets.json      projets (brief, consignes, équipe, réglages)
  documents.json    documents importés (rattachés à un profil ou à un projet)
  profils/<id>/documents/<doc>/       fichiers des documents d'un profil
  projets/<id>/messages.jsonl         la discussion, un message par ligne
  projets/<id>/memoire.jsonl          les versions successives de la mémoire du projet
  projets/<id>/documents/<doc>/       fichiers des documents du projet
  projets/<id>/espace/                dossier de travail par défaut des IA
"""

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

RACINE = Path(__file__).resolve().parent
DONNEES = Path(os.environ.get("ATELIER_DONNEES", RACINE / "donnees")).resolve()

_verrou = threading.RLock()


def nouvel_id(prefixe):
    return f"{prefixe}_{uuid.uuid4().hex[:8]}"


def maintenant():
    return datetime.now().isoformat(timespec="seconds")


def _lire_json(chemin, defaut):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return defaut


def _ecrire_json(chemin, donnees):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(chemin.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)
    os.replace(tmp, chemin)


def _lire_jsonl(chemin):
    try:
        with open(chemin, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def _ajouter_jsonl(chemin, objet):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps(objet, ensure_ascii=False) + "\n")


class Collection:
    """Liste d'objets {id: ...} stockée dans un fichier JSON."""

    def __init__(self, nom, prefixe, defauts):
        self.chemin = DONNEES / f"{nom}.json"
        self.prefixe = prefixe
        self.defauts = defauts

    def _complet(self, objet):
        # copie des valeurs par défaut modifiables (listes, dictionnaires) pour ne jamais les partager
        return {**json.loads(json.dumps(self.defauts)), **objet}

    def tous(self):
        with _verrou:
            return [self._complet(o) for o in _lire_json(self.chemin, [])]

    def get(self, id_):
        return next((o for o in self.tous() if o["id"] == id_), None)

    def creer(self, champs):
        with _verrou:
            objets = _lire_json(self.chemin, [])
            objet = self._complet(_filtrer(champs, self.defauts))
            objet["id"] = nouvel_id(self.prefixe)
            objet["cree_le"] = maintenant()
            objets.append(objet)
            _ecrire_json(self.chemin, objets)
            return objet

    def modifier(self, id_, champs):
        with _verrou:
            objets = _lire_json(self.chemin, [])
            for o in objets:
                if o["id"] == id_:
                    o.update(_filtrer(champs, self.defauts))
                    _ecrire_json(self.chemin, objets)
                    return self._complet(o)
            return None

    def supprimer(self, id_):
        with _verrou:
            objets = _lire_json(self.chemin, [])
            restants = [o for o in objets if o["id"] != id_]
            _ecrire_json(self.chemin, restants)
            return len(restants) != len(objets)


def _filtrer(champs, defauts):
    """Ne garde que les champs connus (évite d'écrire n'importe quoi depuis l'interface)."""
    return {k: v for k, v in champs.items() if k in defauts}


comptes = Collection("comptes", "c", {
    "nom": "",
    "fournisseur": "claude_code",   # claude_code | codex | antigravity | simulation
    "compte_separe": True,          # False = utilise la connexion par défaut de l'outil
})

profils = Collection("profils", "p", {
    "nom": "",
    "couleur": "#b4532a",
    "compte_id": "",
    "modele": "",                   # vide = modèle par défaut de l'outil
    "effort": "",                   # vide = effort par défaut du modèle
    "acces": "aucun",               # aucun | lecture | ecriture (fichiers + commandes dans le dossier du projet)
    "web": False,                   # internet (Claude et ChatGPT)
    "role": "",                     # qui il est, ce qu'il fait
    "instructions": "",             # comment il doit travailler (style, format, règles)
})

projets = Collection("projets", "pr", {
    "nom": "",
    "type": "autre",
    "brief": "",
    "consignes": "",                # consignes pour toute l'équipe
    "consignes_membres": {},        # {profil_id: consignes propres à ce membre dans ce projet}
    "memoire": "",
    "memoire_auto": 0,              # mise à jour auto toutes les N interventions (0 = jamais)
    "memoire_profil": "",           # profil qui rédige la mémoire (vide = premier de l'équipe)
    "memoire_maj_n": 0,             # numéro du dernier message pris en compte par la mémoire
    "questions": "normal",          # jamais | parfois | normal | beaucoup
    "equipe": [],                   # ids de profils, dans l'ordre de parole
    "mode": "tour",                 # tour (chacun son tour) | equipe (tous en même temps)
    "points_etape": True,           # mode équipe : point d'étape du coordinateur après chaque séance
    "coordinateur": "",             # profil qui fait les points d'étape (vide = premier de l'équipe)
    "tours_par_lancement": 6,
    "historique_max": 12,
    "dossier": "",                  # vide = donnees/projets/<id>/espace
})

documents = Collection("documents", "d", {
    "nom": "",
    "type": "",                     # type MIME deviné
    "taille": 0,
    "caracteres": 0,                # longueur du texte extrait (0 = pas de texte lisible)
    "cible": "projet",              # projet | profil
    "cible_id": "",
    "membres": [],                  # documents de projet : profils qui le reçoivent ([] = toute l'équipe)
})


# --- Paramètres généraux ---------------------------------------------------------

PARAMETRES_DEFAUT = {"nom_utilisateur": "", "langue": "français", "memoire_generale": ""}


def parametres():
    with _verrou:
        return {**PARAMETRES_DEFAUT, **_lire_json(DONNEES / "parametres.json", {})}


def modifier_parametres(champs):
    with _verrou:
        nouveaux = {**parametres(), **_filtrer(champs, PARAMETRES_DEFAUT)}
        _ecrire_json(DONNEES / "parametres.json", nouveaux)
        return nouveaux


# --- Comptes : dossier de connexion propre à chaque compte ---------------------

def dossier_compte(compte):
    """Dossier où l'outil officiel range la connexion de ce compte (None = défaut)."""
    if not compte.get("compte_separe"):
        return None
    dossier = DONNEES / "comptes" / compte["id"]
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


# --- Documents -------------------------------------------------------------------

def dossier_document(doc):
    parent = "profils" if doc["cible"] == "profil" else "projets"
    return DONNEES / parent / doc["cible_id"] / "documents" / doc["id"]


def supprimer_document(doc_id):
    doc = documents.get(doc_id)
    if not doc:
        return False
    shutil.rmtree(dossier_document(doc), ignore_errors=True)
    return documents.supprimer(doc_id)


def documents_de(cible, cible_id):
    return [d for d in documents.tous() if d["cible"] == cible and d["cible_id"] == cible_id]


# --- Projets : dossier de travail, discussion, mémoire -------------------------------

def dossier_projet(projet_id):
    return DONNEES / "projets" / projet_id


def espace_projet(projet):
    if projet.get("dossier"):
        espace = Path(projet["dossier"]).expanduser()
    else:
        espace = dossier_projet(projet["id"]) / "espace"
    espace.mkdir(parents=True, exist_ok=True)
    return espace.resolve()


def _fichier_messages(projet_id):
    return dossier_projet(projet_id) / "messages.jsonl"


def messages(projet_id, depuis=0):
    with _verrou:
        tous = _lire_jsonl(_fichier_messages(projet_id))
    return [m for m in tous if m["n"] > depuis]


def ajouter_message(projet_id, auteur, nom, texte, **extra):
    with _verrou:
        existants = messages(projet_id)
        message = {
            "n": existants[-1]["n"] + 1 if existants else 1,
            "auteur": auteur,          # "humain", "systeme" ou un id de profil
            "nom": nom,
            "texte": texte,
            "date": maintenant(),
            **extra,
        }
        _ajouter_jsonl(_fichier_messages(projet_id), message)
        return message


def effacer_messages(projet_id):
    """La discussion est archivée (jamais supprimée) et la mémoire repart de zéro côté compteur."""
    with _verrou:
        chemin = _fichier_messages(projet_id)
        if chemin.exists():
            chemin.rename(chemin.with_name(f"messages_archive_{int(time.time())}.jsonl"))
        projets.modifier(projet_id, {"memoire_maj_n": 0})


def historique_memoire(projet_id):
    return _lire_jsonl(dossier_projet(projet_id) / "memoire.jsonl")


def enregistrer_memoire(projet_id, texte, auteur, jusqu_au=None):
    """Remplace la mémoire du projet et garde l'ancienne version dans l'historique."""
    with _verrou:
        projet = projets.get(projet_id)
        if projet is None:
            return None
        champs = {"memoire": texte}
        if jusqu_au is not None:
            champs["memoire_maj_n"] = jusqu_au
        if texte != projet["memoire"]:
            _ajouter_jsonl(dossier_projet(projet_id) / "memoire.jsonl",
                           {"date": maintenant(), "auteur": auteur, "texte": texte})
        return projets.modifier(projet_id, champs)


def mettre_projet_a_la_corbeille(projet_id):
    """On ne supprime jamais le travail des IA : le dossier part dans donnees/corbeille/."""
    for doc in documents_de("projet", projet_id):
        documents.supprimer(doc["id"])
    source = dossier_projet(projet_id)
    if source.exists():
        corbeille = DONNEES / "corbeille"
        corbeille.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(corbeille / f"{projet_id}_{int(time.time())}"))
