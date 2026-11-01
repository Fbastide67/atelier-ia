"""Le salon : qui parle, avec quel contexte, et quand on s'arrête.

Deux façons de travailler (réglage « mode » du projet) :
  - « tour » : chacun son tour, une intervention à la fois.
  - « equipe » : tous les membres travaillent en même temps, comme une vraie équipe. Pendant leur
    travail, leurs messages intermédiaires deviennent des mini-rapports, transmis en direct dans la
    discussion et dans le journal de l'équipe (.atelier/journal-equipe.md, lisible par les autres).
    À la fin de chaque séance, le coordinateur fait un point d'étape : le travail va-t-il dans le bon sens ?

Règles de parole (mode « tour ») :
  1. Si tu as donné la parole à quelqu'un (bouton « Faire parler » ou message privé), c'est lui.
  2. Sinon, la dernière mention @Nom d'un membre dans le dernier message désigne qui parle
     (les IA terminent par @Nom ; une mention plus haut dans le message ne compte pas).
  3. Sinon, tour de rôle dans l'ordre de l'équipe.
  4. Un message qui s'adresse à @Humain (dernière mention) met la discussion en pause jusqu'à ta
     réponse (sauf si le projet est réglé sur « Questions : jamais »). Citer @Humain au milieu d'un
     message, puis passer la parole à un membre, ne met pas en pause.
"""

import threading
import time

import documents
import fournisseurs
import stockage
from catalogue import CONSIGNE_CHOIX, QUESTIONS, TYPES_PROJET

ACCES = {
    "aucun": "- Tu n'as pas accès aux fichiers du projet. Si un fichier doit être créé ou modifié, "
             "décris ce qu'il faut faire ou demande-le à un membre qui peut modifier les fichiers.",
    "lecture": "- Tu peux lire les fichiers du projet (dossier courant) mais pas les modifier. "
               "Appuie tes remarques sur ce que tu y lis, en citant les fichiers.",
    "ecriture": "- Tu peux créer et modifier les fichiers du projet, uniquement dans le dossier courant, et y lancer "
                "des commandes (shell : installer, compiler, tester, lancer un logiciel du projet…). "
                "Mets les livrables (textes, documents, code…) dans des fichiers, pas dans la discussion. "
                "Écris les fichiers texte en UTF-8. "
                "Dans ton message, résume ce que tu as fait et liste les fichiers créés ou modifiés.",
}
LIBELLES_ACCES = {"aucun": "sans accès aux fichiers", "lecture": "lecture seule",
                  "ecriture": "modifie les fichiers et lance des commandes"}

MODES = {"tour": "Chacun son tour", "equipe": "En équipe, en même temps"}

IGNORES = {".atelier", ".git", "node_modules", "__pycache__", ".venv", "venv", ".claude", ".codex"}
TAILLE_MESSAGE = 4000
TAILLE_DERNIER = 10000
TAILLE_RAPPORT = 600          # caractères d'un mini-rapport recopiés dans la discussion
ECART_RAPPORTS = 30           # secondes minimum entre deux mini-rapports d'un membre dans la discussion
SIMULTANES_MAX = 6            # membres qui travaillent en même temps, au plus
JOURNAL = ".atelier/journal-equipe.md"
HUMAIN = {"id": "humain", "nom": "Humain"}


# --- Construction du contexte -----------------------------------------------------------

def _humain(parametres):
    prenom = (parametres.get("nom_utilisateur") or "").strip()
    return prenom or "l'humain"


def prompt_systeme(profil, projet=None, parametres=None):
    parametres = parametres or stockage.parametres()
    regle_questions = QUESTIONS.get((projet or {}).get("questions", "normal"), QUESTIONS["normal"])
    lignes = [
        f"Tu es « {profil['nom']} », membre d'une équipe d'IA qui travaille avec {_humain(parametres)} "
        "sur un projet commun.", "",
        "# Ton rôle", profil.get("role") or "(rôle non précisé : aide l'équipe au mieux)", "",
    ]
    if (profil.get("instructions") or "").strip():
        lignes += ["# Tes consignes permanentes", profil["instructions"].strip(), ""]
    lignes += [
        "# Règles de l'équipe",
        f"- Réponds en {parametres.get('langue') or 'français'}, avec des messages courts et utiles "
        "(en général moins de 250 mots, sauf si on te demande un livrable). Pas de formules de politesse, "
        "ne résume pas ce que les autres viennent de dire.",
        "- Fais avancer le projet : propose, décide dans ton domaine, ou signale un problème précis.",
        "- Pour donner la parole à un membre précis, termine par @Nom.",
        f"- {regle_questions['consigne']}",
    ]
    if regle_questions["pause"]:
        lignes.append(f"- {CONSIGNE_CHOIX}")
    lignes += [
        "- N'invente jamais d'information factuelle sur le projet (dates, prix, adresses, noms, chiffres) : "
        "écris [À COMPLÉTER : ...].",
        "- Ne prétends jamais avoir fait quelque chose que tu n'as pas fait.",
        ACCES[profil.get("acces", "aucun")],
    ]
    if profil.get("web"):
        lignes.append("- Tu peux chercher sur internet. Cite tes sources (adresse des pages) et vérifie leur date.")
    return "\n".join(lignes) + "\n"


def lister_fichiers(espace, limite=200):
    fichiers = []
    for chemin in sorted(espace.rglob("*")):
        relatif = chemin.relative_to(espace)
        if any(partie in IGNORES for partie in relatif.parts) or not chemin.is_file():
            continue
        fichiers.append({"chemin": relatif.as_posix(), "taille": chemin.stat().st_size})
        if len(fichiers) >= limite:
            break
    return fichiers


def _tronquer(texte, n):
    return texte if len(texte) <= n else texte[:n] + f"\n[… message tronqué, {len(texte) - n} caractères de plus]"


def visibles_par(messages, profil_id):
    """Les messages privés ne sont lus que par leur destinataire."""
    return [m for m in messages if not m.get("prive_pour") or m["prive_pour"] == profil_id]


def interventions(messages):
    """Messages qui comptent dans la discussion : ni messages de l'Atelier, ni mini-rapports en cours de travail."""
    return [m for m in messages if m["auteur"] != "systeme" and not m.get("rapport")]


def prompt_tour(projet, profil, equipe, messages, espace, parametres=None, consigne=None, section_documents=""):
    parametres = parametres or stockage.parametres()
    humain = _humain(parametres)
    lignes = [f"# Projet : {projet['nom']} ({TYPES_PROJET.get(projet.get('type'), 'projet').lower()})", ""]
    lignes += [f"## Brief du projet (écrit par {humain})", projet.get("brief") or "(pas encore de brief)", ""]
    if (projet.get("consignes") or "").strip():
        lignes += ["## Consignes générales du projet (pour toute l'équipe)", projet["consignes"].strip(), ""]
    perso = (projet.get("consignes_membres") or {}).get(profil["id"], "").strip()
    if perso:
        lignes += ["## Consignes pour toi dans ce projet", perso, ""]
    if (parametres.get("memoire_generale") or "").strip():
        lignes += [f"## Ce qu'il faut savoir sur {humain} (valable pour tous ses projets)",
                   parametres["memoire_generale"].strip(), ""]
    lignes += ["## Mémoire du projet (décisions déjà prises)", projet.get("memoire") or "(vide)", ""]

    presentation = f"{humain}, " if humain != "l'humain" else ""
    lignes += ["## L'équipe", f"- @Humain : {presentation}la personne qui pilote le projet et valide les décisions importantes."]
    for membre in equipe:
        role = (membre.get("role") or "").strip().split("\n")[0][:200]
        moi = "  ← c'est toi" if membre["id"] == profil["id"] else ""
        lignes.append(f"- @{membre['nom']} : {role} [{LIBELLES_ACCES[membre.get('acces', 'aucun')]}]{moi}")
    lignes.append("")

    fichiers = lister_fichiers(espace)
    lignes.append("## Fichiers actuels du projet")
    lignes += [f"- {f['chemin']} ({max(1, f['taille'] // 1024)} Ko)" for f in fichiers] or ["(aucun fichier pour l'instant)"]
    lignes.append("")
    if section_documents:
        lignes.append(section_documents)

    # Les mini-rapports en cours de travail sont dans le journal de l'équipe : pas besoin de les relire ici.
    visibles = [m for m in visibles_par(messages, profil["id"]) if not m.get("rapport")]
    recents = visibles[-max(1, projet.get("historique_max", 12)):]
    if recents:
        lignes.append(f"## Discussion ({len(recents)} derniers messages sur {len(visibles)})")
        for i, m in enumerate(recents):
            limite = TAILLE_DERNIER if i == len(recents) - 1 else TAILLE_MESSAGE
            nom = humain.capitalize() if m["auteur"] == "humain" and humain != "l'humain" else m["nom"]
            prive = " (message privé pour toi)" if m.get("prive_pour") else ""
            etape = " (point d'étape)" if m.get("point_etape") else ""
            lignes += [f"### #{m['n']} — {nom}{prive}{etape}", _tronquer(m["texte"], limite), ""]
    else:
        lignes += ["## Discussion", "(la discussion commence : c'est toi qui ouvres)", ""]

    lignes.append(consigne or f"## À toi\nTu es @{profil['nom']}. Écris maintenant ta prochaine intervention "
                               "dans la discussion, en suivant ton rôle.")
    return "\n".join(lignes)


def consigne_equipe(profil, equipe, seance):
    """Ce qu'un membre doit savoir quand toute l'équipe travaille en même temps que lui."""
    autres = [f"@{m['nom']}" for m in equipe if m["id"] != profil["id"]]
    lignes = [
        f"## À toi : travail d'équipe en simultané (séance {seance})",
        f"Tu es @{profil['nom']}. En ce moment, {', '.join(autres)} travaillent EN MÊME TEMPS que toi sur le projet.",
        "- Fais ta part selon ton rôle et la répartition décidée dans la discussion (dernier point d'étape). "
        "Ne refais pas ce qu'un coéquipier fait déjà.",
        "- Ne modifie pas un fichier qu'un coéquipier est en train de modifier : crée tes propres fichiers, "
        "ou signale le besoin dans ton rapport.",
        "- Pendant ton travail, fais des mini-rapports : avant chaque étape importante, écris une phrase courte "
        "(ce que tu fais, ce que tu as trouvé, ce qui bloque). Ils sont transmis en direct à l'équipe.",
    ]
    if profil.get("acces", "aucun") != "aucun":
        lignes.append(f"- Le journal de l'équipe ({JOURNAL}) contient en direct les mini-rapports des autres : "
                      "relis-le avant de commencer et avant de toucher à un fichier partagé.")
    lignes.append("- Termine par ton rapport de séance (moins de 150 mots) : Fait · En cours · Prochaine étape · "
                  "Besoin (avec @Nom si tu as besoin d'un coéquipier, @Humain seulement pour une vraie question).")
    return "\n".join(lignes)


def consigne_point_etape(seance):
    return "\n".join([
        f"## Point d'étape (fin de la séance {seance})",
        "Ta tâche : vérifier que le travail de l'équipe va dans le bon sens. Compare les rapports de cette séance "
        "(derniers messages) au brief, aux consignes et aux décisions de la mémoire ; regarde les fichiers si tu y as accès.",
        "Réponds en moins de 200 mots :",
        "- Première ligne : « CAP : BON » ou « CAP : À CORRIGER ».",
        "- Une ligne par membre : @Nom : ce qui va, et ce qu'il doit corriger ou faire à la prochaine séance.",
        "- Les doublons ou conflits repérés (deux membres sur le même fichier, travail en double, contradiction).",
        "Ne fais pas de travail de fond ici : tu vérifies et tu répartis.",
    ])


# --- Choix de l'orateur -------------------------------------------------------------------

def derniere_mention(texte, candidats):
    """Profil mentionné le plus tard dans le texte (@Nom, insensible à la casse)."""
    bas = texte.casefold()
    meilleur = None
    for profil in candidats:
        cle = "@" + profil["nom"].casefold()
        debut = bas.rfind(cle)
        while debut != -1:
            apres = bas[debut + len(cle):debut + len(cle) + 1]
            avant = bas[debut - 1:debut] if debut else ""
            courriel = avant.isalnum() or avant in ("_", ".", "-", "+")  # devis@redacteur.fr n'est pas une mention
            if not (apres.isalnum() or apres == "_" or courriel):
                rang = (debut, len(cle))  # à même position, le nom le plus long gagne (@Dev front > @Dev)
                if meilleur is None or rang > meilleur[0]:
                    meilleur = (rang, profil)
                break
            debut = bas.rfind(cle, 0, debut + len(cle) - 1)
    return meilleur[1] if meilleur else None


def destinataire(texte, equipe, auteur=None):
    """À qui s'adresse un message : la dernière mention d'un membre (autre que l'auteur) ou de @Humain."""
    candidats = [p for p in equipe if p["id"] != auteur] + ([HUMAIN] if auteur != HUMAIN["id"] else [])
    return derniere_mention(texte, candidats)


def s_adresse_a_humain(texte, equipe=(), auteur=None):
    cible = destinataire(texte, list(equipe), auteur)
    return bool(cible) and cible["id"] == HUMAIN["id"]


def choisir_orateur(equipe, messages, force=None):
    if not equipe:
        return None
    ids = [p["id"] for p in equipe]
    if force in ids:
        return equipe[ids.index(force)]
    comptent = interventions(messages)
    dernier = comptent[-1] if comptent else None
    if dernier:
        if dernier.get("prive_pour") in ids:
            return equipe[ids.index(dernier["prive_pour"])]
        cible = destinataire(dernier["texte"], equipe, dernier["auteur"])
        if cible and cible["id"] in ids:
            return cible
    passes = [m["auteur"] for m in comptent if m["auteur"] in ids]
    if not passes:
        return equipe[0]
    return equipe[(ids.index(passes[-1]) + 1) % len(equipe)]


# --- Le salon d'un projet -----------------------------------------------------------------

class Salon:
    def __init__(self, projet_id):
        self.projet_id = projet_id
        self.verrou = threading.Lock()
        self.verrou_etat = threading.Lock()
        self.thread = None
        self.appels = set()
        self.stop_demande = False
        self.actifs = {}          # profil_id -> {"tache", "debut", "rapport"} : qui travaille en ce moment
        self.derniers_rapports = {}
        self.memoire_echec_n = 0  # dernier message au moment d'une mise à jour de la mémoire ratée
        self.info = ""

    def statut(self):
        with self.verrou_etat:
            actifs = [{"id": pid, "tache": a["tache"], "depuis": round(time.time() - a["debut"]),
                       "rapport": a["rapport"]} for pid, a in self.actifs.items()]
        premier = actifs[0] if actifs else {}
        return {
            "en_cours": bool(self.thread and self.thread.is_alive()),
            "orateur": premier.get("id"),
            "tache": premier.get("tache", ""),
            "depuis": premier.get("depuis", 0),
            "actifs": actifs,
            "info": self.info,
        }

    def _demarrer(self, cible, *args):
        with self.verrou:
            if self.thread and self.thread.is_alive():
                return False
            self.stop_demande = False
            self.info = ""
            self.thread = threading.Thread(target=self._proteger, args=(cible, *args), daemon=True)
            self.thread.start()
            return True

    def lancer(self, tours, premier=None):
        return self._demarrer(self._discussion, max(1, min(int(tours), 50)), premier)

    def resumer_memoire(self, profil_id=None):
        return self._demarrer(self._memoire, profil_id)

    def arreter(self):
        self.stop_demande = True
        with self.verrou_etat:
            appels = list(self.appels)
        for appel in appels:
            try:
                appel.arreter()
            except Exception:  # un appel récalcitrant ne doit pas empêcher d'arrêter les autres
                pass

    def attendre(self, delai=None):
        if self.thread:
            self.thread.join(delai)

    def _proteger(self, cible, *args):
        try:
            cible(*args)
        except fournisseurs.Annule:
            self.info = "Arrêté."
        except Exception as e:  # une erreur ne doit jamais bloquer le salon
            self._systeme(f"Erreur interne de l'Atelier : {e}", erreur=True)
            self.info = "En pause après une erreur."
        finally:
            with self.verrou_etat:
                self.actifs.clear()
                self.appels.clear()

    def _systeme(self, texte, **extra):
        stockage.ajouter_message(self.projet_id, "systeme", "Atelier", texte, **extra)

    def _contexte(self):
        projet = stockage.projets.get(self.projet_id)
        if not projet:
            raise RuntimeError("projet introuvable")
        profils = {p["id"]: p for p in stockage.profils.tous()}
        equipe = [profils[i] for i in projet["equipe"] if i in profils]
        return projet, equipe, stockage.espace_projet(projet)

    def _faire_parler(self, profil, projet, equipe, espace, messages, consigne=None, sobre=False, tache="parle",
                      progression=None):
        """Un appel à une IA. Renvoie (texte, erreur, duree) ; erreur = None si tout va bien.
        sobre=True : sans outils ni internet (pour les tâches internes comme la mémoire).
        Plusieurs appels peuvent tourner en même temps (mode équipe)."""
        compte = stockage.comptes.get(profil.get("compte_id"))
        if not compte:
            return None, f"le profil {profil['nom']} n'a pas de compte associé.", 0
        if sobre:
            profil = dict(profil, acces="aucun", web=False)
        parametres = stockage.parametres()
        docs = documents.pour(projet, profil)
        appel = fournisseurs.Appel()
        debut = time.time()
        with self.verrou_etat:
            self.actifs[profil["id"]] = {"tache": tache, "debut": debut, "rapport": ""}
            self.appels.add(appel)
        if self.stop_demande:
            appel.arreter()

        def suivre(texte):
            with self.verrou_etat:
                if profil["id"] in self.actifs:
                    self.actifs[profil["id"]]["rapport"] = texte
            if progression:
                progression(texte)

        try:
            texte, _ = fournisseurs.appeler(
                compte, profil, prompt_systeme(profil, projet, parametres),
                prompt_tour(projet, profil, equipe, messages, espace, parametres, consigne,
                            documents.section(docs, profil.get("acces", "aucun"))),
                espace, appel, documents.dossiers_lisibles(projet, profil), suivre)
            return texte, None, time.time() - debut
        except fournisseurs.ErreurIA as e:
            return None, str(e), time.time() - debut
        finally:
            with self.verrou_etat:
                self.actifs.pop(profil["id"], None)
                self.appels.discard(appel)

    def _pause_question(self, projet, equipe, profil, texte):
        return QUESTIONS.get(projet["questions"], QUESTIONS["normal"])["pause"] and \
            s_adresse_a_humain(texte, equipe, profil["id"])

    def _discussion(self, tours, premier=None):
        projet, equipe, espace = self._contexte()
        if not equipe:
            self.info = "Ajoute au moins un profil à l'équipe du projet."
            return
        # « Faire parler » un membre précis (ou lui écrire en privé) reste une intervention seule.
        if projet.get("mode") == "equipe" and len(equipe) > 1 and not premier:
            return self._travail_equipe(max(1, round(tours / len(equipe))))
        for i in range(tours):
            if self.stop_demande:
                self.info = "Arrêté."
                return
            projet, equipe, espace = self._contexte()  # réglages modifiables pendant la discussion
            messages = stockage.messages(self.projet_id)
            profil = choisir_orateur(equipe, messages, premier if i == 0 else None)
            try:
                texte, erreur, duree = self._faire_parler(profil, projet, equipe, espace, messages)
            except fournisseurs.Annule:
                self.info = "Arrêté."
                self._systeme(f"Intervention de {profil['nom']} interrompue.")
                return
            if erreur:
                self._systeme(f"{profil['nom']} n'a pas pu répondre : {erreur}", erreur=True)
                self.info = "En pause après une erreur."
                return
            question = self._pause_question(projet, equipe, profil, texte)
            stockage.ajouter_message(self.projet_id, profil["id"], profil["nom"], texte,
                                     duree=round(duree, 1), **({"question": True} if question else {}))
            self._memoire_si_besoin()
            if question:
                self.info = f"{profil['nom']} attend ta réponse."
                return
        self.info = "Tours terminés. Écris un message ou relance."

    # --- Mode équipe : tout le monde travaille en même temps ---------------------------------

    def _journal(self, espace, ligne, nouveau=False):
        chemin = espace / JOURNAL
        with self.verrou_etat:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            with open(chemin, "w" if nouveau else "a", encoding="utf-8") as f:
                f.write(ligne + "\n")

    def _rapport(self, profil, espace, texte):
        """Un mini-rapport envoyé pendant le travail : journal de l'équipe (tout) et discussion (sans inonder)."""
        texte = texte.strip()
        self._journal(espace, f"- [{time.strftime('%H:%M:%S')}] {profil['nom']} : {' '.join(texte.split())[:1500]}")
        maintenant = time.time()
        if maintenant - self.derniers_rapports.get(profil["id"], 0) < ECART_RAPPORTS:
            return
        self.derniers_rapports[profil["id"]] = maintenant
        stockage.ajouter_message(self.projet_id, profil["id"], profil["nom"], _tronquer(texte, TAILLE_RAPPORT),
                                 rapport=True)

    def _travail_equipe(self, seances):
        for n in range(1, seances + 1):
            if self.stop_demande:
                self.info = "Arrêté."
                return
            suite = self._seance(n)
            if suite != "continuer":
                return
        self.info = "Séances terminées. Écris un message ou relance."

    def _seance(self, n):
        projet, equipe, espace = self._contexte()
        messages = stockage.messages(self.projet_id)
        noms = ", ".join(m["nom"] for m in equipe)
        self._journal(espace, f"# Journal de l'équipe — séance {n} ({time.strftime('%d/%m %H:%M')})\n"
                              f"Mini-rapports en direct de : {noms}\n", nouveau=True)
        self._systeme(f"Séance {n} : {noms} travaillent en même temps.")
        self.derniers_rapports = {}
        resultats = {}
        places = threading.Semaphore(SIMULTANES_MAX)

        def travailler(profil):
            with places:
                if self.stop_demande:
                    return
                try:
                    texte, erreur, duree = self._faire_parler(
                        profil, projet, equipe, espace, messages, consigne_equipe(profil, equipe, n),
                        tache="travaille", progression=lambda t: self._rapport(profil, espace, t))
                except fournisseurs.Annule:
                    resultats[profil["id"]] = "arrete"
                    return
                except Exception as e:  # un membre en panne ne doit pas arrêter les autres
                    texte, erreur, duree = None, f"erreur interne : {e}", 0
            if erreur:
                resultats[profil["id"]] = "erreur"
                self._systeme(f"{profil['nom']} n'a pas pu répondre : {erreur}", erreur=True)
                return
            question = self._pause_question(projet, equipe, profil, texte)
            resultats[profil["id"]] = "question" if question else "ok"
            self._journal(espace, f"- [{time.strftime('%H:%M:%S')}] {profil['nom']} a terminé sa séance.")
            stockage.ajouter_message(self.projet_id, profil["id"], profil["nom"], texte, duree=round(duree, 1),
                                     seance=n, **({"question": True} if question else {}))

        threads = [threading.Thread(target=travailler, args=(p,), daemon=True) for p in equipe]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if self.stop_demande or "arrete" in resultats.values():
            self.info = "Arrêté."
            return "stop"
        if resultats and all(r == "erreur" for r in resultats.values()):
            self.info = "En pause après une erreur."
            return "stop"
        en_attente = [p["nom"] for p in equipe if resultats.get(p["id"]) == "question"]
        if en_attente:
            self._memoire_si_besoin()
            self.info = f"{', '.join(en_attente)} attend{'ent' if len(en_attente) > 1 else ''} ta réponse."
            return "stop"
        if projet.get("points_etape", True):
            if self._point_etape(n) == "stop":
                return "stop"
        self._memoire_si_besoin()
        return "continuer"

    def _point_etape(self, n):
        projet, equipe, espace = self._contexte()
        coordinateur = next((p for p in equipe if p["id"] == projet.get("coordinateur")), None) or equipe[0]
        messages = stockage.messages(self.projet_id)
        try:
            texte, erreur, duree = self._faire_parler(coordinateur, projet, equipe, espace, messages,
                                                      consigne_point_etape(n), tache="point")
        except fournisseurs.Annule:
            self.info = "Arrêté."
            return "stop"
        if erreur:
            self._systeme(f"Point d'étape impossible ({coordinateur['nom']}) : {erreur}", erreur=True)
            return "continuer"
        question = self._pause_question(projet, equipe, coordinateur, texte)
        stockage.ajouter_message(self.projet_id, coordinateur["id"], coordinateur["nom"], texte,
                                 duree=round(duree, 1), point_etape=True, seance=n,
                                 **({"question": True} if question else {}))
        if question:
            self.info = f"{coordinateur['nom']} attend ta réponse."
            return "stop"
        return "continuer"

    # --- Mémoire --------------------------------------------------------------------------

    def _memoire_si_besoin(self):
        projet, equipe, _ = self._contexte()
        frequence = int(projet.get("memoire_auto") or 0)
        if frequence <= 0 or self.stop_demande:
            return
        ids = {p["id"] for p in equipe}
        # Après un échec, on attend autant de nouvelles interventions avant de réessayer (pas d'échec en boucle).
        depuis = max(projet.get("memoire_maj_n", 0), self.memoire_echec_n)
        nouveaux = [m for m in interventions(stockage.messages(self.projet_id, depuis)) if m["auteur"] in ids]
        if len(nouveaux) >= frequence:
            self._memoire(projet.get("memoire_profil") or None)

    def _memoire(self, profil_id=None):
        projet, equipe, espace = self._contexte()
        profil = (next((p for p in equipe if p["id"] == profil_id), None) or stockage.profils.get(profil_id or "")
                  or (equipe[0] if equipe else None))
        if not profil:
            self.info = "Aucun profil pour rédiger la mémoire."
            return
        messages = stockage.messages(self.projet_id)
        consigne = ("## Mémoire du projet à rédiger\n"
                    "Ta tâche n'est pas de participer à la discussion : mets à jour la « Mémoire du projet » à partir "
                    "de la mémoire actuelle et de la discussion. Garde ce qui est encore vrai, retire ce qui est dépassé. "
                    "Rubriques : Décisions prises · Avancement · Questions ouvertes · Prochaines étapes. "
                    "Listes à puces concises (moins de 350 mots). Réponds UNIQUEMENT avec le nouveau texte de la mémoire.")
        projet_large = dict(projet, historique_max=max(projet.get("historique_max", 12), 40))
        texte, erreur, _ = self._faire_parler(profil, projet_large, equipe, espace, messages, consigne,
                                              sobre=True, tache="memoire")
        dernier = messages[-1]["n"] if messages else 0
        if erreur:
            self.memoire_echec_n = dernier
            self._systeme(f"Mise à jour de la mémoire impossible : {erreur}", erreur=True)
            return
        stockage.enregistrer_memoire(self.projet_id, texte, profil["nom"], jusqu_au=dernier)
        self._systeme(f"Mémoire du projet mise à jour par {profil['nom']}.")
        self.info = "Mémoire mise à jour."


_salons = {}
_verrou_salons = threading.Lock()


def salon(projet_id):
    with _verrou_salons:
        if projet_id not in _salons:
            _salons[projet_id] = Salon(projet_id)
        return _salons[projet_id]


def tester_profil(profil):
    """Petit appel pour vérifier qu'un profil fonctionne avec son modèle et son effort."""
    compte = stockage.comptes.get(profil.get("compte_id"))
    if not compte:
        return False, "Ce profil n'a pas de compte associé."
    espace = stockage.DONNEES / "test"
    espace.mkdir(parents=True, exist_ok=True)
    leger = dict(profil, acces="aucun", web=False)
    try:
        debut = time.time()
        texte, _ = fournisseurs.appeler(
            compte, leger, prompt_systeme(leger),
            "Test de connexion : réponds seulement « Prêt » suivi de ton nom et du nom de ton modèle si tu le connais.",
            espace, fournisseurs.Appel())
        return True, f"{texte}  ({time.time() - debut:.0f} s)"
    except fournisseurs.ErreurIA as e:
        return False, str(e)
