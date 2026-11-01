"""Atelier IA — serveur local et API de l'interface graphique.

Lancement :  python app.py        (ouvre le navigateur sur http://127.0.0.1:8765)
Options   :  --port 9000   --pas-de-navigateur

L'interface peut aussi être ouverte depuis un site public (GitHub Pages…) : ce serveur devient alors
le « moteur local » du site. Seuls les sites listés dans site.txt (écrit par l'installateur) ou dans
la variable ATELIER_SITES ont le droit de le piloter.
"""

import argparse
import base64
import binascii
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import catalogue
import documents
import fournisseurs
import orchestrateur
import stockage

RACINE = Path(__file__).resolve().parent
WEB = RACINE / "web"
FICHIER_SITE = RACINE / "site.txt"
TAILLE_MAX_APERCU = 400_000
TAILLE_MAX_REQUETE = 40 * 1024 * 1024

ROUTES = []


def route(methode, motif):
    def enregistrer(fonction):
        ROUTES.append((methode, re.compile(f"^{motif}$"), fonction))
        return fonction
    return enregistrer


class ErreurAPI(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class Fichier:
    """Réponse qui n'est pas du JSON (téléchargement)."""

    def __init__(self, contenu, type_contenu, nom=None):
        self.contenu = contenu
        self.type_contenu = type_contenu
        self.nom = nom


def _exiger(objet, quoi):
    if not objet:
        raise ErreurAPI(f"{quoi} introuvable", 404)
    return objet


def sites_autorises():
    """Origines des sites publics qui peuvent piloter ce moteur (ex. https://moi.github.io).
    Uniquement en https, sauf pour un site de test sur ce PC (http://localhost:8000)."""
    adresses = os.environ.get("ATELIER_SITES", "").split()
    if FICHIER_SITE.is_file():
        adresses += FICHIER_SITE.read_text(encoding="utf-8").split()
    origines = set()
    for adresse in adresses:
        u = urlparse(adresse.strip())
        if u.netloc and (u.scheme == "https" or (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1"))):
            origines.add(f"{u.scheme}://{u.netloc}")
    return origines


# --- État général -------------------------------------------------------------------

@route("GET", "/api/ping")
def ping(req):
    """Le site public cherche le moteur sur le PC du visiteur : seule route lisible depuis n'importe quel site."""
    return {"atelier": True, "autorise": req.site_autorise}


def _compte_public(compte):
    multi = fournisseurs.FOURNISSEURS[compte["fournisseur"]]["multi_comptes"]
    dossier = stockage.dossier_compte(compte) if multi else None
    return dict(compte, dossier=str(dossier) if dossier else None,
                instructions=fournisseurs.instructions_connexion(compte))


def _projet_public(projet):
    return dict(projet, espace=str(stockage.espace_projet(projet)),
                statut=orchestrateur.salon(projet["id"]).statut())


@route("GET", "/api/etat")
def etat(_):
    return {
        "fournisseurs": fournisseurs.etat_fournisseurs(),
        "efforts": fournisseurs.EFFORTS,
        "comptes": [_compte_public(c) for c in stockage.comptes.tous()],
        "profils": stockage.profils.tous(),
        "projets": [_projet_public(p) for p in stockage.projets.tous()],
        "documents": stockage.documents.tous(),
        "parametres": stockage.parametres(),
        "types_projet": catalogue.TYPES_PROJET,
        "questions": {k: {"nom": v["nom"], "aide": v["aide"]} for k, v in catalogue.QUESTIONS.items()},
        "modes": orchestrateur.MODES,
        "modeles_roles": catalogue.MODELES_ROLES,
        "modeles_equipes": catalogue.MODELES_EQUIPES,
        "pdf_disponible": documents.PDF_DISPONIBLE,
        "windows": fournisseurs.WINDOWS,
        "dossier_donnees": str(stockage.DONNEES),
    }


@route("PUT", "/api/parametres")
def modifier_parametres(req):
    return stockage.modifier_parametres(req.corps)


# --- Comptes --------------------------------------------------------------------------

@route("POST", "/api/comptes")
def creer_compte(req):
    if req.corps.get("fournisseur") not in fournisseurs.FOURNISSEURS:
        raise ErreurAPI("Service inconnu")
    if not (req.corps.get("nom") or "").strip():
        raise ErreurAPI("Donne un nom au compte.")
    return _compte_public(stockage.comptes.creer(req.corps))


@route("PUT", "/api/comptes/(?P<id>\\w+)")
def modifier_compte(req, id):
    champs = {k: v for k, v in req.corps.items() if k == "nom"}  # le service d'un compte ne change pas
    return _compte_public(_exiger(stockage.comptes.modifier(id, champs), "Compte"))


@route("DELETE", "/api/comptes/(?P<id>\\w+)")
def supprimer_compte(_, id):
    utilise = [p["nom"] for p in stockage.profils.tous() if p["compte_id"] == id]
    if utilise:
        raise ErreurAPI(f"Ce compte est utilisé par : {', '.join(utilise)}. Change d'abord leur compte.")
    return {"ok": stockage.comptes.supprimer(id)}


@route("POST", "/api/comptes/(?P<id>\\w+)/connexion")
def connexion_compte(_, id):
    compte = _exiger(stockage.comptes.get(id), "Compte")
    try:
        ouverte = fournisseurs.ouvrir_fenetre_connexion(compte)
    except fournisseurs.ErreurIA as e:
        raise ErreurAPI(str(e))
    return {"fenetre": ouverte, "instructions": fournisseurs.instructions_connexion(compte)}


@route("POST", "/api/comptes/(?P<id>\\w+)/verifier")
def verifier_compte(_, id):
    connecte, texte = fournisseurs.verifier_compte(_exiger(stockage.comptes.get(id), "Compte"))
    return {"connecte": connecte, "texte": texte}


@route("GET", "/api/comptes/(?P<id>\\w+)/modeles")
def modeles_compte(_, id):
    try:
        modeles, brut = fournisseurs.lister_modeles(_exiger(stockage.comptes.get(id), "Compte"))
    except fournisseurs.ErreurIA as e:
        raise ErreurAPI(str(e))
    return {"modeles": modeles, "brut": brut}


# --- Profils --------------------------------------------------------------------------

def _valider_profil(corps):
    if "nom" in corps:
        corps["nom"] = re.sub(r"\s+", " ", corps["nom"] or "").strip().lstrip("@")
        if not corps["nom"]:
            raise ErreurAPI("Donne un nom au profil.")
        if corps["nom"].casefold() == "humain":
            raise ErreurAPI("« Humain » est réservé : c'est toi dans la discussion.")
    if "web" in corps:
        corps["web"] = bool(corps["web"])
    return corps


@route("POST", "/api/profils")
def creer_profil(req):
    return stockage.profils.creer(_valider_profil(req.corps))


@route("PUT", "/api/profils/(?P<id>\\w+)")
def modifier_profil(req, id):
    return _exiger(stockage.profils.modifier(id, _valider_profil(req.corps)), "Profil")


@route("DELETE", "/api/profils/(?P<id>\\w+)")
def supprimer_profil(_, id):
    for projet in stockage.projets.tous():
        if id in projet["equipe"] or id in projet["consignes_membres"]:
            stockage.projets.modifier(projet["id"], {
                "equipe": [i for i in projet["equipe"] if i != id],
                "consignes_membres": {k: v for k, v in projet["consignes_membres"].items() if k != id},
            })
    for doc in stockage.documents.tous():
        if doc["cible"] == "profil" and doc["cible_id"] == id:
            stockage.supprimer_document(doc["id"])
        elif id in doc["membres"]:
            stockage.documents.modifier(doc["id"], {"membres": [m for m in doc["membres"] if m != id]})
    return {"ok": stockage.profils.supprimer(id)}


@route("POST", "/api/profils/(?P<id>\\w+)/tester")
def tester_profil(_, id):
    ok, texte = orchestrateur.tester_profil(_exiger(stockage.profils.get(id), "Profil"))
    return {"ok": ok, "texte": texte}


# --- Projets --------------------------------------------------------------------------

def _valider_projet(corps):
    if "dossier" in corps:
        dossier = (corps.get("dossier") or "").strip()
        if dossier and not Path(dossier).expanduser().is_absolute():
            raise ErreurAPI("Le dossier du projet doit être un chemin complet (ex. C:\\Users\\moi\\mon-projet).")
        corps["dossier"] = dossier
    if "type" in corps and corps["type"] not in catalogue.TYPES_PROJET:
        raise ErreurAPI("Type de projet inconnu")
    if "questions" in corps and corps["questions"] not in catalogue.QUESTIONS:
        raise ErreurAPI("Fréquence de questions inconnue")
    if "mode" in corps and corps["mode"] not in orchestrateur.MODES:
        raise ErreurAPI("Façon de travailler inconnue")
    if "points_etape" in corps:
        corps["points_etape"] = bool(corps["points_etape"])
    if "coordinateur" in corps:
        corps["coordinateur"] = corps["coordinateur"] or ""
    for champ, minimum, maximum in (("tours_par_lancement", 1, 50), ("historique_max", 2, 200), ("memoire_auto", 0, 100)):
        if champ in corps:
            try:
                corps[champ] = max(minimum, min(maximum, int(corps[champ])))
            except (TypeError, ValueError):
                raise ErreurAPI(f"Valeur invalide pour {champ}")
    if "consignes_membres" in corps:
        corps["consignes_membres"] = {k: v for k, v in (corps["consignes_membres"] or {}).items() if (v or "").strip()}
    return corps


def creer_equipe(type_projet, compte_id):
    """Profils adaptés au type de projet, en réutilisant ceux qui existent déjà avec ce compte."""
    existants = stockage.profils.tous()
    equipe = []
    for nom in catalogue.MODELES_EQUIPES.get(type_projet, []):
        profil = next((p for p in existants if p["nom"] == nom and p["compte_id"] == compte_id), None)
        if not profil:
            profil = stockage.profils.creer(dict(catalogue.modele_role(nom), compte_id=compte_id))
        equipe.append(profil["id"])
    return equipe


@route("POST", "/api/projets")
def creer_projet(req):
    corps = _valider_projet(dict(req.corps))
    if not (corps.get("nom") or "").strip():
        raise ErreurAPI("Donne un nom au projet.")
    compte_auto = corps.pop("equipe_auto", None)
    if compte_auto:
        _exiger(stockage.comptes.get(compte_auto), "Compte")
        corps["equipe"] = creer_equipe(corps.get("type", "autre"), compte_auto)
    return _projet_public(stockage.projets.creer(corps))


@route("PUT", "/api/projets/(?P<id>\\w+)")
def modifier_projet(req, id):
    corps = _valider_projet(dict(req.corps))
    corps.pop("memoire", None)  # la mémoire passe par /memoire pour garder l'historique
    return _projet_public(_exiger(stockage.projets.modifier(id, corps), "Projet"))


@route("DELETE", "/api/projets/(?P<id>\\w+)")
def supprimer_projet(_, id):
    if orchestrateur.salon(id).statut()["en_cours"]:
        raise ErreurAPI("Arrête la discussion avant de supprimer le projet.")
    _exiger(stockage.projets.get(id), "Projet")
    stockage.projets.supprimer(id)
    stockage.mettre_projet_a_la_corbeille(id)
    return {"ok": True}


@route("GET", "/api/projets/(?P<id>\\w+)/messages")
def lire_messages(req, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    depuis = int(req.params.get("depuis", ["0"])[0])
    return {"messages": stockage.messages(id, depuis),
            "statut": orchestrateur.salon(id).statut(),
            "memoire": projet["memoire"]}


@route("POST", "/api/projets/(?P<id>\\w+)/messages")
def ecrire_message(req, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    texte = (req.corps.get("texte") or "").strip()
    if not texte:
        raise ErreurAPI("Message vide")
    prive = req.corps.get("prive_pour") or None
    if prive and prive not in projet["equipe"]:
        raise ErreurAPI("Ce profil ne fait pas partie de l'équipe.")
    extra = {"prive_pour": prive, "prive_nom": stockage.profils.get(prive)["nom"]} if prive else {}
    salon = orchestrateur.salon(id)
    if not salon.statut()["en_cours"]:
        salon.info = ""  # « X attend ta réponse » n'est plus d'actualité
    return stockage.ajouter_message(id, "humain", "Humain", texte, **extra)


@route("DELETE", "/api/projets/(?P<id>\\w+)/messages")
def effacer_messages(_, id):
    if orchestrateur.salon(id).statut()["en_cours"]:
        raise ErreurAPI("Arrête la discussion avant de l'archiver.")
    stockage.effacer_messages(id)
    orchestrateur.salon(id).info = ""
    return {"ok": True}


@route("POST", "/api/projets/(?P<id>\\w+)/lancer")
def lancer(req, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    if not projet["equipe"]:
        raise ErreurAPI("Ajoute au moins un profil à l'équipe (onglet Réglages).")
    orateur = req.corps.get("orateur")
    if orateur and orateur not in projet["equipe"]:
        raise ErreurAPI("Ce profil ne fait pas partie de l'équipe.")
    tours = req.corps.get("tours") or projet["tours_par_lancement"]
    if not orchestrateur.salon(id).lancer(tours, orateur):
        raise ErreurAPI("La discussion est déjà en cours.")
    return {"ok": True}


@route("POST", "/api/projets/(?P<id>\\w+)/stop")
def stop(_, id):
    orchestrateur.salon(id).arreter()
    return {"ok": True}


@route("GET", "/api/projets/(?P<id>\\w+)/memoire")
def lire_memoire(_, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    return {"memoire": projet["memoire"], "historique": list(reversed(stockage.historique_memoire(id)))}


@route("PUT", "/api/projets/(?P<id>\\w+)/memoire")
def ecrire_memoire(req, id):
    _exiger(stockage.projets.get(id), "Projet")
    projet = stockage.enregistrer_memoire(id, req.corps.get("texte") or "", "Toi")
    return {"memoire": projet["memoire"]}


@route("POST", "/api/projets/(?P<id>\\w+)/memoire")
def resumer_memoire(req, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    profil_id = req.corps.get("profil_id") or projet["memoire_profil"] or (projet["equipe"][0] if projet["equipe"] else None)
    if not profil_id:
        raise ErreurAPI("Aucun profil pour rédiger la mémoire.")
    if not orchestrateur.salon(id).resumer_memoire(profil_id):
        raise ErreurAPI("Attends la fin de la discussion en cours.")
    return {"ok": True}


@route("GET", "/api/projets/(?P<id>\\w+)/export")
def exporter(_, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    prenom = stockage.parametres()["nom_utilisateur"] or "Toi"
    lignes = [f"# {projet['nom']}", "", f"Type : {catalogue.TYPES_PROJET.get(projet['type'], '')}", "",
              "## Brief", "", projet["brief"] or "(vide)", "", "## Mémoire du projet", "", projet["memoire"] or "(vide)",
              "", "## Discussion", ""]
    for m in stockage.messages(id):
        auteur = prenom if m["auteur"] == "humain" else m["nom"]
        prive = f" (privé pour {m.get('prive_nom', '?')})" if m.get("prive_pour") else ""
        genre = " (mini-rapport)" if m.get("rapport") else " (point d'étape)" if m.get("point_etape") else ""
        lignes += [f"### #{m['n']} · {auteur}{prive}{genre} · {m['date'].replace('T', ' ')}", "", m["texte"], ""]
    nom = re.sub(r"[^\w\- ]", "", projet["nom"]).strip() or "projet"
    return Fichier("\n".join(lignes).encode("utf-8"), "text/markdown; charset=utf-8", f"{nom}.md")


@route("GET", "/api/projets/(?P<id>\\w+)/fichiers")
def fichiers(_, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    return {"fichiers": orchestrateur.lister_fichiers(stockage.espace_projet(projet), limite=1000)}


@route("GET", "/api/projets/(?P<id>\\w+)/fichier")
def fichier(req, id):
    projet = _exiger(stockage.projets.get(id), "Projet")
    espace = stockage.espace_projet(projet)
    chemin = (espace / req.params.get("chemin", [""])[0]).resolve()
    if espace not in chemin.parents or not chemin.is_file():
        raise ErreurAPI("Fichier introuvable", 404)
    if chemin.stat().st_size > TAILLE_MAX_APERCU:
        return {"contenu": None, "info": "Fichier trop gros pour l'aperçu : ouvre le dossier."}
    try:
        return {"contenu": chemin.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        return {"contenu": None, "info": "Fichier binaire (image, archive…) : ouvre le dossier pour le voir."}


def _ouvrir(chemin):
    if fournisseurs.WINDOWS:
        os.startfile(chemin)
    elif fournisseurs.MACOS:
        subprocess.Popen(["open", str(chemin)])
    else:
        subprocess.Popen(["xdg-open", str(chemin)])


SCRIPT_CHOIX_DOSSIER = """
import sys, tkinter
from tkinter import filedialog
racine = tkinter.Tk()
racine.withdraw()
racine.attributes("-topmost", True)
print(filedialog.askdirectory(parent=racine, initialdir=sys.argv[1] or None, mustexist=False,
                              title="Dossier où l'équipe travaille") or "")
"""


@route("POST", "/api/choisir-dossier")
def choisir_dossier(req):
    """Fenêtre de Windows (ou du système) pour choisir n'importe quel dossier du PC comme dossier du projet."""
    depart = (req.corps.get("depart") or "").strip()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")  # sinon un dossier accentué revient en cp1252, illisible
    try:
        r = subprocess.run([sys.executable, "-c", SCRIPT_CHOIX_DOSSIER, depart if Path(depart).is_dir() else ""],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
    except subprocess.TimeoutExpired:
        raise ErreurAPI("Aucun dossier choisi : la fenêtre est restée ouverte plus de 10 minutes. Clique à nouveau sur « Choisir… ».")
    except OSError as e:
        raise ErreurAPI(f"Impossible d'ouvrir la fenêtre de choix : {e}")
    if r.returncode != 0:
        raise ErreurAPI("Impossible d'ouvrir la fenêtre de choix (tkinter absent ?) : colle le chemin à la main.")
    choix = r.stdout.strip()
    return {"dossier": str(Path(choix)) if choix else ""}


@route("POST", "/api/projets/(?P<id>\\w+)/ouvrir-dossier")
def ouvrir_dossier(_, id):
    espace = stockage.espace_projet(_exiger(stockage.projets.get(id), "Projet"))
    _ouvrir(espace)
    return {"ok": True, "dossier": str(espace)}


# --- Documents ------------------------------------------------------------------------

def _valider_membres(doc_cible_id, membres):
    projet = stockage.projets.get(doc_cible_id)
    return [m for m in (membres or []) if projet and m in projet["equipe"]]


@route("POST", "/api/documents")
def importer_document(req):
    c = req.corps
    if c.get("cible") == "profil":
        _exiger(stockage.profils.get(c.get("cible_id", "")), "Profil")
        membres = []
    elif c.get("cible") == "projet":
        _exiger(stockage.projets.get(c.get("cible_id", "")), "Projet")
        membres = _valider_membres(c["cible_id"], c.get("membres"))
    else:
        raise ErreurAPI("Le document doit être rattaché à un profil ou à un projet.")
    try:
        contenu = base64.b64decode(c.get("contenu") or "", validate=True)
    except (binascii.Error, ValueError):
        raise ErreurAPI("Fichier illisible")
    if not contenu:
        raise ErreurAPI("Fichier vide")
    try:
        return documents.importer(c["cible"], c["cible_id"], c.get("nom") or "document", contenu, membres)
    except ValueError as e:
        raise ErreurAPI(str(e))


@route("PUT", "/api/documents/(?P<id>\\w+)")
def modifier_document(req, id):
    doc = _exiger(stockage.documents.get(id), "Document")
    if doc["cible"] != "projet":
        raise ErreurAPI("Seuls les documents de projet ont des destinataires.")
    return stockage.documents.modifier(id, {"membres": _valider_membres(doc["cible_id"], req.corps.get("membres"))})


@route("DELETE", "/api/documents/(?P<id>\\w+)")
def supprimer_document(_, id):
    return {"ok": stockage.supprimer_document(id)}


@route("GET", "/api/documents/(?P<id>\\w+)/texte")
def texte_document(_, id):
    doc = _exiger(stockage.documents.get(id), "Document")
    texte = documents.texte(doc)
    return {"texte": texte[:TAILLE_MAX_APERCU], "tronque": len(texte) > TAILLE_MAX_APERCU}


@route("POST", "/api/documents/(?P<id>\\w+)/ouvrir")
def ouvrir_document(_, id):
    _ouvrir(documents.chemin_original(_exiger(stockage.documents.get(id), "Document")))
    return {"ok": True}


# --- Serveur HTTP -----------------------------------------------------------------------

class Serveur(ThreadingHTTPServer):
    # Sous Windows, SO_REUSEADDR laisse deux Ateliers écouter le même port (le second ne reçoit rien) :
    # on demande le port en exclusivité pour passer au port suivant s'il est pris.
    allow_reuse_address = not fournisseurs.WINDOWS

    def server_bind(self):
        if fournisseurs.WINDOWS:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Requete:
    def __init__(self, corps, params, site_autorise=False):
        self.corps = corps
        self.params = params
        self.site_autorise = site_autorise


class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "AtelierIA"

    def log_message(self, *args):
        pass  # terminal silencieux

    def _origines_autorisees(self):
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _envoyer(self, code, corps, type_contenu="application/json; charset=utf-8", nom=None):
        if isinstance(corps, (dict, list)):
            corps = json.dumps(corps, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", type_contenu)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for cle, valeur in getattr(self, "entetes_cors", {}).items():
            self.send_header(cle, valeur)
        if nom:
            ascii_ = nom.encode("ascii", "ignore").decode() or "export.md"
            self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nom)}")
        self.end_headers()
        self.wfile.write(corps)

    def _site_public(self):
        """L'origine de la requête si c'est un site public autorisé (le moteur répond alors en CORS)."""
        origine = self.headers.get("Origin")
        return origine if origine and origine in sites_autorises() else None

    def _traiter(self, methode):
        # Protection : seul ce PC, ou le site public autorisé, peut piloter l'Atelier (pas un site malveillant).
        self.entetes_cors = {"Vary": "Origin"}
        if self.headers.get("Host") not in self._origines_autorisees():
            return self._envoyer(403, {"erreur": "Hôte refusé"})
        url = urlparse(self.path)
        origine = self.headers.get("Origin")
        site = self._site_public()
        local = not origine or urlparse(origine).netloc in self._origines_autorisees()
        if url.path == "/api/ping":
            self.entetes_cors["Access-Control-Allow-Origin"] = "*"
        elif site:
            self.entetes_cors["Access-Control-Allow-Origin"] = site
        elif not local and (methode != "GET" or url.path.startswith("/api/")):
            return self._envoyer(403, {"erreur": "Origine refusée"})

        if methode == "GET" and not url.path.startswith("/api/"):
            return self._statique(url.path)

        for m, motif, fonction in ROUTES:
            correspondance = motif.match(url.path)
            if m == methode and correspondance:
                try:
                    longueur = int(self.headers.get("Content-Length") or 0)
                    if longueur > TAILLE_MAX_REQUETE:
                        raise ErreurAPI("Fichier trop gros (25 Mo maximum).", 413)
                    corps = json.loads(self.rfile.read(longueur) or b"{}") if longueur else {}
                    if not isinstance(corps, dict):
                        raise ErreurAPI("Requête invalide")
                    requete = Requete(corps, parse_qs(url.query), site_autorise=bool(site) or local)
                    resultat = fonction(requete, **correspondance.groupdict())
                    if isinstance(resultat, Fichier):
                        return self._envoyer(200, resultat.contenu, resultat.type_contenu, resultat.nom)
                    return self._envoyer(200, resultat)
                except ErreurAPI as e:
                    return self._envoyer(e.code, {"erreur": str(e)})
                except json.JSONDecodeError:
                    return self._envoyer(400, {"erreur": "Requête invalide"})
                except Exception as e:
                    return self._envoyer(500, {"erreur": f"Erreur interne : {e}"})
        return self._envoyer(404, {"erreur": "Route inconnue"})

    def _statique(self, chemin):
        fichier = (WEB / (chemin.lstrip("/") or "index.html")).resolve()
        if WEB not in fichier.parents or not fichier.is_file():
            return self._envoyer(404, b"Introuvable", "text/plain; charset=utf-8")
        type_contenu = mimetypes.guess_type(fichier.name)[0] or "application/octet-stream"
        if type_contenu.startswith("text/") or type_contenu.endswith("javascript"):
            type_contenu += "; charset=utf-8"
        self._envoyer(200, fichier.read_bytes(), type_contenu)

    def do_OPTIONS(self):
        # Pré-requête du navigateur avant un appel du site public (POST JSON, réseau local…).
        self.entetes_cors = {"Vary": "Origin"}
        site = self._site_public()
        if self.headers.get("Host") not in self._origines_autorisees() or not site:
            return self._envoyer(403, b"", "text/plain")
        self.entetes_cors.update({
            "Access-Control-Allow-Origin": site,
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600",
        })
        self._envoyer(204, b"", "text/plain")

    def do_GET(self):
        self._traiter("GET")

    def do_POST(self):
        self._traiter("POST")

    def do_PUT(self):
        self._traiter("PUT")

    def do_DELETE(self):
        self._traiter("DELETE")


def deja_lance(port):
    """Un Atelier répond déjà sur ce port (ex. le moteur lancé au démarrage de Windows)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return json.loads(r.read()).get("atelier") is True
    except (OSError, ValueError):
        return False


def page_d_accueil(adresse_locale):
    """Moteur installé depuis le site public : on ouvre le site (le lien partagé), sinon l'interface locale."""
    if FICHIER_SITE.is_file():
        site = FICHIER_SITE.read_text(encoding="utf-8").split()
        if site and urlparse(site[0]).scheme in ("http", "https"):
            return site[0]
    return adresse_locale


def main():
    parser = argparse.ArgumentParser(description="Atelier IA : fais travailler plusieurs IA ensemble.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--pas-de-navigateur", action="store_true")
    args = parser.parse_args()

    if deja_lance(args.port):
        print(f"L'Atelier tourne déjà : http://127.0.0.1:{args.port}")
        if not args.pas_de_navigateur:
            webbrowser.open(page_d_accueil(f"http://127.0.0.1:{args.port}"))
        return

    mimetypes.add_type("text/javascript", ".js")
    for port in range(args.port, args.port + 20):
        try:
            serveur = Serveur(("127.0.0.1", port), Gestionnaire)
            break
        except OSError:
            continue
    else:
        sys.exit("Aucun port libre trouvé.")

    adresse = f"http://127.0.0.1:{port}"
    print(f"Atelier IA lancé : {adresse}")
    print("Garde cette fenêtre ouverte. Ctrl+C pour arrêter.")
    for site in sorted(sites_autorises()):
        print(f"  Site public autorisé à utiliser ce moteur : {site}")
    for cle, infos in fournisseurs.etat_fournisseurs().items():
        if cle != "simulation":
            print(f"  {infos['nom']:<42} {infos['executable'] or 'introuvable'}")
    if not documents.PDF_DISPONIBLE:
        print("  Extraction du texte des PDF : désactivée (installer-pdf.bat pour l'activer)")
    if not args.pas_de_navigateur:
        webbrowser.open(page_d_accueil(adresse))
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
