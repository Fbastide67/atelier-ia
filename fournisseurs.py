"""Adaptateurs vers les outils officiels de chaque IA.

Principe : l'Atelier ne manipule jamais de jeton ni de mot de passe. Il lance
l'outil officiel non modifié (claude, codex, agy), qui utilise la connexion que
TU as faite toi-même avec ton abonnement. Chaque compte a son propre dossier de
connexion (CLAUDE_CONFIG_DIR pour Claude Code, CODEX_HOME pour Codex).
"""

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import stockage

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

EFFORTS = {
    "minimal": "Minimal : répond tout de suite",
    "low": "Faible : rapide et économique",
    "medium": "Moyen : équilibré",
    "high": "Élevé : plus réfléchi",
    "xhigh": "Très élevé : approfondi, plus lent",
    "max": "Maximum : le plus lent et le plus gourmand",
}

FOURNISSEURS = {
    "claude_code": {
        "nom": "Claude (Claude Code)",
        "court": "Claude",
        "executable": "claude",
        "multi_comptes": True,
        "web": True,
        "modeles": [
            ["opus", "Opus : le plus capable"],
            ["sonnet", "Sonnet : équilibré"],
            ["haiku", "Haiku : rapide et économique"],
            ["fable", "Fable : nécessite un accès Fable"],
            ["claude-opus-5-5", "claude-opus-5-5 (version figée)"],
            ["claude-sonnet-5-5", "claude-sonnet-5-5 (version figée)"],
            ["claude-haiku-5-5", "claude-haiku-5-5 (version figée)"],
        ],
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "installation": "https://code.claude.com/docs/en/setup",
    },
    "codex": {
        "nom": "ChatGPT (Codex CLI)",
        "court": "ChatGPT",
        "executable": "codex",
        "multi_comptes": True,
        "web": True,
        "modeles": [],   # dépend du compte : bouton « Lister les modèles » (codex debug models)
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "installation": "npm install -g @openai/codex",
    },
    "antigravity": {
        "nom": "Gemini (Antigravity CLI, expérimental)",
        "court": "Gemini",
        "executable": "agy",
        "multi_comptes": False,
        "web": False,
        "modeles": [],   # dépend du compte : bouton « Lister les modèles » (agy models)
        "efforts": ["low", "medium", "high", "max"],
        "installation": "https://antigravity.google/docs/cli",
    },
    "simulation": {
        "nom": "Simulation (test sans IA, ne consomme rien)",
        "court": "Simulation",
        "executable": None,
        "multi_comptes": False,
        "web": False,
        "modeles": [],
        "efforts": [],
        "installation": "",
    },
}

# Variables qui feraient payer à l'usage (API) au lieu d'utiliser l'abonnement.
VARIABLES_API = {
    "claude_code": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
    "codex": ["OPENAI_API_KEY", "CODEX_API_KEY"],
    "antigravity": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
}

DELAIS = {"aucun": 8 * 60, "lecture": 15 * 60, "ecriture": 40 * 60}
SHELL_CLAUDE = "PowerShell" if WINDOWS else "Bash"   # sous Windows, Claude Code n'a pas d'outil Bash
OUTILS_CLAUDE = {"aucun": "", "lecture": "Read,Glob,Grep", "ecriture": f"Read,Glob,Grep,Write,Edit,{SHELL_CLAUDE}"}
ETAPES_MAX_CLAUDE = {"aucun": 1, "lecture": 25, "ecriture": 60}

CONSIGNE_UTF8 = (
    "# Écriture des fichiers\n"
    "Écris toujours les fichiers texte en UTF-8 (sinon les accents deviennent des « ? »). Préfère ton outil "
    "d'édition (apply_patch). Sous PowerShell, n'utilise jamais Set-Content, Out-File ou > sans préciser "
    "l'encodage : utilise [IO.File]::WriteAllText($chemin, $texte, [Text.UTF8Encoding]::new($false)).\n\n")


class ErreurIA(Exception):
    pass


class Annule(Exception):
    pass


class Appel:
    """Un appel en cours, que l'on peut interrompre (bouton Stop)."""

    def __init__(self):
        self.proc = None
        self.annule = False

    def arreter(self):
        self.annule = True
        proc = self.proc  # executer() peut le remettre à None en même temps
        if proc and proc.poll() is None:
            _tuer(proc)


# --- Trouver les exécutables -------------------------------------------------------

def trouver_executable(fournisseur):
    nom = FOURNISSEURS[fournisseur]["executable"]
    if not nom:
        return None
    force = os.environ.get(f"ATELIER_{nom.upper()}")
    if force:
        return force
    chemin = shutil.which(nom)
    if chemin:
        return chemin
    # Installation native de Claude Code quand le PATH n'est pas à jour.
    candidat = Path.home() / ".local" / "bin" / (nom + (".exe" if WINDOWS else ""))
    return str(candidat) if candidat.exists() else None


def etat_fournisseurs():
    etat = {}
    for cle, infos in FOURNISSEURS.items():
        etat[cle] = {k: v for k, v in infos.items() if k != "executable"}
        etat[cle]["executable"] = trouver_executable(cle) if infos["executable"] else "intégré"
    return etat


def _exiger(fournisseur):
    exe = trouver_executable(fournisseur)
    if not exe:
        nom = FOURNISSEURS[fournisseur]["executable"]
        raise ErreurIA(
            f"« {nom} » est introuvable. Installe-le ({FOURNISSEURS[fournisseur]['installation']}) "
            f"puis relance l'Atelier depuis un nouveau terminal."
        )
    return exe


# --- Environnement : abonnement uniquement, un dossier de connexion par compte ----

def environnement(compte):
    env = os.environ.copy()
    fournisseur = compte["fournisseur"]
    for variable in VARIABLES_API.get(fournisseur, []):
        env.pop(variable, None)
    dossier = stockage.dossier_compte(compte) if FOURNISSEURS[fournisseur]["multi_comptes"] else None
    if fournisseur == "claude_code" and dossier:
        env["CLAUDE_CONFIG_DIR"] = str(dossier)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)  # sinon il passerait avant le compte choisi
    if fournisseur == "codex" and dossier:
        env["CODEX_HOME"] = str(dossier)
    return env


# --- Exécution d'un processus ---------------------------------------------------------

def _tuer(proc):
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        proc.kill()


CARACTERES_CMD = re.compile(r'[\s&|<>^()%!,;=]')


def ligne_cmd(cmd):
    """Ligne de commande sûre pour un lanceur .cmd/.bat (codex.cmd de npm…) : cmd.exe relit la ligne,
    et un « & » ou un « | » non protégé dans un chemin (ex. « R&D ») couperait la commande en deux."""
    morceaux = []
    for arg in cmd:
        if '"' in arg:
            morceaux.append(subprocess.list2cmdline([arg]))
        elif not arg or CARACTERES_CMD.search(arg):
            fin = len(arg) - len(arg.rstrip("\\"))
            morceaux.append('"' + arg + "\\" * fin + '"')  # les \ finaux doublés pour ne pas échapper le "
        else:
            morceaux.append(arg)
    return " ".join(morceaux)


def executer(cmd, entree, env, cwd, delai, appel, sur_ligne=None):
    """Lance l'outil et attend sa fin. sur_ligne(ligne) reçoit la sortie au fil de l'eau (rapports en direct)."""
    if appel.annule:
        raise Annule()
    if WINDOWS and str(cmd[0]).lower().endswith((".cmd", ".bat")):
        cmd = ligne_cmd([str(a) for a in cmd])
    options = dict(
        cwd=str(cwd), env=env, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.PIPE if entree is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if WINDOWS:
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **options)
    except OSError as e:
        raise ErreurIA(f"Impossible de lancer {cmd if isinstance(cmd, str) else cmd[0]} : {e}")
    appel.proc = proc
    if appel.annule:  # Stop pressé pendant le démarrage du processus
        _tuer(proc)
    sortie, erreurs = [], []

    # Chaque lecteur ferme son flux quand il a fini : fermer un flux qu'un lecteur lit encore bloquerait.
    def lire_sortie():
        with proc.stdout:
            for ligne in proc.stdout:
                sortie.append(ligne)
                if sur_ligne:
                    try:
                        sur_ligne(ligne)
                    except Exception:  # un rapport mal lu ne doit pas faire échouer l'appel
                        pass

    def lire_erreurs():
        with proc.stderr:
            for ligne in proc.stderr:
                erreurs.append(ligne)

    lecteurs = [threading.Thread(target=lire_sortie, daemon=True), threading.Thread(target=lire_erreurs, daemon=True)]
    for lecteur in lecteurs:
        lecteur.start()
    trop_long = False
    try:
        if entree is not None:
            try:
                proc.stdin.write(entree)
                proc.stdin.close()
            except OSError:  # l'outil s'est arrêté avant de tout lire : son erreur dira pourquoi
                pass
        proc.wait(timeout=delai)
    except subprocess.TimeoutExpired:
        trop_long = True
        _tuer(proc)
        proc.wait()
    finally:
        # Un programme lancé en arrière-plan par l'IA (serveur de dev…) peut garder la sortie ouverte après
        # la fin de l'outil : on ne l'attend pas, son lecteur finira seul.
        for lecteur in lecteurs:
            lecteur.join(5)
        appel.proc = None
    if appel.annule:
        raise Annule()
    if trop_long:
        raise ErreurIA(f"Pas de réponse après {delai // 60} min : appel interrompu.")
    return proc.returncode, "".join(sortie), "".join(erreurs)


class Rapporteur:
    """Transforme les messages intermédiaires d'une IA en mini-rapports.
    Un texte n'est transmis que quand l'IA passe à une action : le dernier texte est la réponse finale."""

    def __init__(self, progression):
        self.progression = progression
        self.en_attente = ""

    def texte(self, texte):
        texte = (texte or "").strip()
        if not texte:
            return
        self.action()
        self.en_attente = texte

    def action(self):
        if self.en_attente and self.progression:
            self.progression(self.en_attente)
        self.en_attente = ""


def _fin(texte, n=1500):
    texte = texte.strip()
    return texte if len(texte) <= n else "…" + texte[-n:]


def _json(ligne):
    ligne = ligne.strip().lstrip("﻿")
    if not ligne.startswith("{"):
        return None
    try:
        objet = json.loads(ligne)
    except json.JSONDecodeError:
        return None
    return objet if isinstance(objet, dict) else None


def _effort(profil, fournisseur):
    effort = profil.get("effort") or ""
    return effort if effort in FOURNISSEURS[fournisseur]["efforts"] else ""


# --- Claude Code ------------------------------------------------------------------------

def _resultat_claude(texte):
    """Ligne finale « result » du flux stream-json (d'autres lignes peuvent la suivre)."""
    dernier = None
    for ligne in texte.splitlines():
        objet = _json(ligne)
        if objet is None:
            continue
        if objet.get("type") == "result":
            return objet
        dernier = objet
    return dernier if dernier and "result" in dernier else None


def _lecteur_claude(rapporteur):
    def lire(ligne):
        objet = _json(ligne)
        if not objet or objet.get("type") != "assistant":
            return
        for bloc in (objet.get("message") or {}).get("content") or []:
            if bloc.get("type") == "text":
                rapporteur.texte(bloc.get("text"))
            elif bloc.get("type") == "tool_use":
                rapporteur.action()
    return lire


def commande_claude(exe, profil, fichier_systeme, lisibles=()):
    acces = profil.get("acces", "aucun")
    outils = OUTILS_CLAUDE[acces]
    if profil.get("web"):
        outils = ",".join(filter(None, [outils, "WebSearch,WebFetch"]))
    etapes = ETAPES_MAX_CLAUDE[acces] if acces != "aucun" or not outils else 15
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
           "--strict-mcp-config", "--max-turns", str(etapes)]
    if profil.get("modele"):
        cmd += ["--model", profil["modele"]]
    effort = _effort(profil, "claude_code")
    if effort:
        cmd += ["--effort", effort]
    if not outils:
        # Pure discussion : aucun outil, prompt système remplacé (plus léger).
        cmd += ["--tools", "", "--system-prompt-file", fichier_systeme]
    else:
        # On garde le prompt de Claude Code (consignes d'usage des outils) et on ajoute le rôle.
        cmd += ["--tools", outils, "--allowedTools", outils, "--append-system-prompt-file", fichier_systeme]
        if acces == "ecriture":
            cmd += ["--permission-mode", "acceptEdits"]
        if acces != "aucun":
            for dossier in lisibles:
                cmd += ["--add-dir", str(dossier)]
    return cmd


def _appeler_claude(compte, profil, systeme, prompt, espace, appel, lisibles, progression=None):
    exe = _exiger("claude_code")
    acces = profil.get("acces", "aucun")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as f:
        f.write(systeme)
        fichier_systeme = f.name
    try:
        cmd = commande_claude(exe, profil, fichier_systeme, lisibles)
        code, sortie, erreurs = executer(cmd, prompt, environnement(compte), espace, DELAIS[acces], appel,
                                         _lecteur_claude(Rapporteur(progression)))
    finally:
        os.unlink(fichier_systeme)

    donnees = _resultat_claude(sortie)
    if donnees is None:
        raise ErreurIA(_fin(erreurs or sortie) or f"Claude Code s'est arrêté (code {code}) sans réponse.")
    texte = (donnees.get("result") or "").strip()
    sous_type = donnees.get("subtype", "success")
    if sous_type == "error_max_turns":
        raise ErreurIA("Limite d'étapes atteinte avant la fin de la réponse : découpe la demande en tâches plus petites.")
    if donnees.get("is_error") or sous_type != "success":
        raise ErreurIA(texte or _fin(json.dumps(donnees, ensure_ascii=False)))
    if not texte:
        raise ErreurIA("Claude Code a répondu sans texte.")
    return texte, {"session": donnees.get("session_id"), "duree_api_ms": donnees.get("duration_api_ms")}


# --- Codex (ChatGPT) --------------------------------------------------------------------

def commande_codex(exe, profil, espace, fichier_reponse):
    acces = profil.get("acces", "aucun")
    cmd = [exe, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--color", "never",
           "-C", str(espace), "-s", "workspace-write" if acces == "ecriture" else "read-only",
           "-o", str(fichier_reponse)]
    if profil.get("modele"):
        cmd += ["-m", profil["modele"]]
    # valeurs sans guillemets : Codex les lit comme du texte (plus sûr à travers codex.cmd sous Windows)
    effort = _effort(profil, "codex")
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    if WINDOWS:
        # Sans bac à sable Windows, Codex retombe en lecture seule (ni écriture ni commande).
        # « unelevated » ne demande pas de droits administrateur ; il limite les écritures au dossier du projet.
        cmd += ["-c", "windows.sandbox=unelevated"]
    if profil.get("web"):
        cmd += ["-c", "web_search=live"]
        if acces == "ecriture":
            cmd += ["-c", "sandbox_workspace_write.network_access=true"]  # réseau pour les commandes
    cmd.append("-")  # le prompt arrive par l'entrée standard
    return cmd


def _lecteur_codex(rapporteur, evenements):
    def lire(ligne):
        objet = _json(ligne)
        if not objet:
            return
        if objet.get("type") in ("error", "turn.failed"):
            evenements.append(objet)
        item = objet.get("item") or {}
        if objet.get("type") == "item.completed" and item.get("type") == "agent_message":
            rapporteur.texte(item.get("text"))
        elif objet.get("type") in ("item.started", "item.completed") and item.get("type") not in (None, "agent_message", "reasoning"):
            rapporteur.action()
    return lire


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
HORODATAGE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z?\s+(?:ERROR|WARN|INFO|DEBUG|TRACE)\s+[\w:]+:\s*")


def _texte_erreur(message):
    """Les erreurs de l'API arrivent souvent sous forme de JSON : on garde la phrase utile."""
    message = (message or "").strip()
    try:
        donnees = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return message
    if isinstance(donnees, dict):
        erreur = donnees.get("error")
        for valeur in (donnees.get("detail"), donnees.get("message"), erreur.get("message") if isinstance(erreur, dict) else erreur):
            if isinstance(valeur, str) and valeur.strip():
                return valeur.strip()
    return message


def erreur_codex(evenements, erreurs, code):
    """Message d'erreur lisible : événements JSON d'abord, sinon les lignes d'erreur du journal."""
    messages = []
    for e in evenements:
        brut = e.get("message") if e.get("type") == "error" else (e.get("error") or {}).get("message")
        texte = _texte_erreur(brut)
        if texte and texte not in messages:
            messages.append(texte)
    if messages:
        return " ".join(messages[-2:])
    lignes = [ANSI.sub("", l).strip() for l in (erreurs or "").splitlines()]
    utiles = [HORODATAGE.sub("", l) for l in lignes if re.search(r"\berror\b|erreur", l, re.I)]
    if utiles:
        return _fin("\n".join(dict.fromkeys(utiles[-3:])), 600)
    return _fin("\n".join(lignes), 600) or f"Codex s'est arrêté (code {code}) sans réponse."


def _appeler_codex(compte, profil, systeme, prompt, espace, appel, lisibles, progression=None):
    exe = _exiger("codex")
    acces = profil.get("acces", "aucun")
    evenements = []
    with tempfile.TemporaryDirectory() as tmp:
        derniere = Path(tmp) / "reponse.md"
        cmd = commande_codex(exe, profil, espace, derniere)
        # Codex n'a pas d'option « prompt système » : le rôle est placé en tête.
        utf8 = CONSIGNE_UTF8 if acces == "ecriture" else ""
        entree = f"# Tes instructions permanentes\n\n{systeme}\n\n{utf8}---\n\n{prompt}"
        code, sortie, erreurs = executer(cmd, entree, environnement(compte), espace, DELAIS[acces], appel,
                                         _lecteur_codex(Rapporteur(progression), evenements))
        texte = derniere.read_text(encoding="utf-8").strip() if derniere.exists() else ""
    if not texte and code == 0 and not evenements:
        texte = "\n".join(l for l in sortie.splitlines() if _json(l) is None).strip()
    if not texte:
        raise ErreurIA(erreur_codex(evenements, erreurs, code))
    return texte, {}


# --- Antigravity (Gemini) -------------------------------------------------------------------

def reglages_antigravity(profil, espace, lisibles=()):
    """Permissions d'agy pour ce profil. En mode non interactif, agy refuse d'office tout outil non autorisé
    et s'arrête alors sans réponse : on autorise donc explicitement ce que l'accès du profil permet.
    Ses commandes tournent dans son bac à sable (--sandbox), dossier du projet uniquement."""
    acces = profil.get("acces", "aucun")
    dossiers = [Path(espace) / ".atelier"] if acces == "aucun" else [Path(espace), *map(Path, lisibles)]
    regles = []
    for dossier in dossiers:
        for forme in dict.fromkeys([str(dossier), dossier.as_posix()]):
            regles.append(f"read_file({forme})")
            if acces == "ecriture" and dossier == Path(espace):
                regles.append(f"write_file({forme})")
    regles.append("command(*)")  # même pour lire un fichier, l'agent passe souvent par une commande
    if profil.get("web"):
        regles.append("read_url(*)")
    return {"permissions": {"allow": regles}}


def commande_antigravity(exe, profil, consigne, espace, lisibles=()):
    acces = profil.get("acces", "aucun")
    cmd = [exe, "-p", consigne, "--sandbox",
           "--settings", json.dumps(reglages_antigravity(profil, espace, lisibles), ensure_ascii=False)]
    if acces == "ecriture":
        cmd += ["--mode", "accept-edits"]   # sinon chaque modification attend une validation impossible
    if acces != "aucun":
        for dossier in lisibles:
            cmd += ["--add-dir", str(dossier)]  # documents du profil et du projet (normes, chartes…)
    if profil.get("modele"):
        cmd += ["--model", profil["modele"]]
    effort = _effort(profil, "antigravity")
    if effort:
        cmd += ["--effort", effort]
    return cmd


def _appeler_antigravity(compte, profil, systeme, prompt, espace, appel, lisibles, progression=None):
    exe = _exiger("antigravity")
    utf8 = CONSIGNE_UTF8 if profil.get("acces") == "ecriture" else ""
    contenu = f"# Tes instructions permanentes\n\n{systeme}\n\n{utf8}---\n\n{prompt}"
    # La ligne de commande Windows est limitée (~32 000 caractères) et un .cmd casse
    # les retours à la ligne : au-delà, on passe par un fichier que l'agent lit lui-même.
    if len(contenu) < 12000 and not exe.lower().endswith((".cmd", ".bat")):
        consigne = contenu
    else:
        fichier = espace / ".atelier" / f"consigne_{profil['id']}.md"
        fichier.parent.mkdir(exist_ok=True)
        fichier.write_text(contenu, encoding="utf-8")
        consigne = (f"Lis le fichier {fichier} : il contient tes instructions et la discussion. "
                    "Suis-les. Ta réponse finale doit être uniquement ton message pour la discussion.")
    cmd = commande_antigravity(exe, profil, consigne, espace, lisibles)
    # stdin fermé : sinon certaines versions d'agy attendent indéfiniment.
    code, sortie, erreurs = executer(cmd, None, environnement(compte), espace,
                                     DELAIS[profil.get("acces", "aucun")], appel)
    texte = sortie.strip()
    if code != 0 or not texte:
        message = ANSI.sub("", erreurs).strip().removeprefix("jetski:").strip()
        raise ErreurIA(_fin(message, 600) or "Antigravity n'a rien renvoyé (quota épuisé ? connexion à refaire ?).")
    return texte, {}


def _modeles_codex(compte):
    exe = _exiger("codex")
    code, sortie, erreurs = executer([exe, "debug", "models"], None, environnement(compte), stockage.DONNEES, 60, Appel())
    try:
        donnees = json.loads(sortie.strip().lstrip("﻿"))
    except json.JSONDecodeError:
        donnees = None
    if code != 0 or not isinstance(donnees, dict):
        raise ErreurIA(erreur_codex([], erreurs, code) if code != 0 else
                       "Liste des modèles illisible : mets Codex à jour (npm install -g @openai/codex).")
    modeles = sorted((m for m in donnees.get("models") or [] if m.get("slug") and m.get("visibility") != "hide"),
                     key=lambda m: m.get("priority", 999))
    resume = "\n".join(f"{m['slug']}  {m.get('display_name', '')} : {m.get('description', '')}" for m in modeles)
    return [m["slug"] for m in modeles], resume


def lister_modeles(compte):
    """Modèles réellement disponibles sur ce compte, lus avec l'outil (codex debug models, agy models)."""
    if compte["fournisseur"] == "codex":
        return _modeles_codex(compte)
    if compte["fournisseur"] != "antigravity":
        return [m[0] for m in FOURNISSEURS[compte["fournisseur"]]["modeles"]], ""
    exe = _exiger("antigravity")
    code, sortie, erreurs = executer([exe, "models"], None, environnement(compte), stockage.DONNEES, 60, Appel())
    if code != 0:
        raise ErreurIA(_fin(erreurs or sortie) or "agy models a échoué : connecte-toi d'abord avec « agy ».")
    modeles = []
    for ligne in sortie.splitlines():
        trouve = re.match(r"\s*[-*•]?\s*([A-Za-z][\w.\-]*[\d-][\w.\-]*)", ligne)
        if trouve and trouve.group(1) not in modeles:
            modeles.append(trouve.group(1))
    return modeles, sortie.strip()


# --- Simulation (pour tester l'interface sans consommer de quota) ---------------------------

def _appeler_simulation(compte, profil, systeme, prompt, espace, appel, lisibles, progression=None):
    for i in range(10):
        if appel.annule:
            raise Annule()
        if i == 5 and progression and "## Point d'étape" not in prompt:
            progression(f"*(simulation)* {profil['nom']} : étape intermédiaire en cours.")
        time.sleep(0.1)
    role = (profil.get("role") or "sans rôle").strip().splitlines()[0][:90]
    if "question" in role.lower() and "n'écris jamais @Humain" not in systeme:
        return ("J'ai besoin d'un arbitrage avant de continuer. @Humain, quelle option préfères-tu ?\n"
                "1. Une version courte, livrée vite\n2. Une version complète, plus longue à produire"), {}
    if "## Mémoire du projet à rédiger" in prompt:
        return "- Simulation : décisions prises jusqu'ici résumées ici.\n- Prochaine étape : à définir.", {}
    docs = re.findall(r"^### (.+?) \((?:joint à ton profil|document du projet)\)$", prompt, re.M)
    lignes = [f"*(simulation)* **{profil['nom']}** : {role}."]
    if docs:
        lignes.append(f"Documents reçus : {', '.join(docs)}.")
    if "## Consignes pour toi dans ce projet" in prompt:
        lignes.append("J'ai bien reçu mes consignes pour ce projet.")
    if profil.get("modele") or profil.get("effort"):
        lignes.append(f"Modèle : {profil.get('modele') or 'défaut'}, effort : {profil.get('effort') or 'défaut'}.")
    return "\n\n".join(lignes), {}


APPELS = {
    "claude_code": _appeler_claude,
    "codex": _appeler_codex,
    "antigravity": _appeler_antigravity,
    "simulation": _appeler_simulation,
}


def appeler(compte, profil, systeme, prompt, espace, appel, lisibles=(), progression=None):
    """Renvoie (texte, details). Lève ErreurIA ou Annule.
    progression(texte) reçoit les messages intermédiaires de l'IA pendant son travail (mini-rapports)."""
    return APPELS[compte["fournisseur"]](compte, profil, systeme, prompt, Path(espace), appel, list(lisibles),
                                         progression)


# --- Connexion et vérification des comptes ------------------------------------------------

def _commande_connexion(compte):
    exe = trouver_executable(compte["fournisseur"])
    nom = FOURNISSEURS[compte["fournisseur"]]["executable"]
    args = {"claude_code": ["auth", "login"], "codex": ["login"], "antigravity": []}[compte["fournisseur"]]
    return exe, nom, args


def instructions_connexion(compte):
    """Commandes à copier-coller si la fenêtre ne s'ouvre pas toute seule."""
    if compte["fournisseur"] == "simulation":
        return {}
    _, nom, args = _commande_connexion(compte)
    commande = " ".join([nom, *args])
    variable = {"claude_code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}.get(compte["fournisseur"])
    dossier = stockage.dossier_compte(compte) if FOURNISSEURS[compte["fournisseur"]]["multi_comptes"] else None
    if variable and dossier:
        return {
            "powershell": f'$env:{variable} = "{dossier}"; {commande}',
            "bash": f"{variable}={shlex.quote(str(dossier))} {commande}",
        }
    return {"powershell": commande, "bash": commande}


def ouvrir_fenetre_connexion(compte):
    """Ouvre un terminal où l'outil officiel fait lui-même la connexion (navigateur)."""
    exe, nom, args = _commande_connexion(compte)
    if not exe:
        raise ErreurIA(f"« {nom} » est introuvable sur ce PC.")
    env = environnement(compte)
    if WINDOWS:
        subprocess.Popen(["cmd", "/k", exe, *args], env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return True
    if MACOS:
        ligne = instructions_connexion(compte)["bash"].replace(nom, shlex.quote(exe), 1)
        script = f'tell application "Terminal" to do script {json.dumps(ligne)}'
        subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])
        return True
    return False  # Linux : l'interface affiche la commande à copier


def verifier_compte(compte):
    """Renvoie (connecte: bool|None, texte)."""
    fournisseur = compte["fournisseur"]
    if fournisseur == "simulation":
        return True, "Le mode simulation ne nécessite aucune connexion."
    exe = trouver_executable(fournisseur)
    if not exe:
        return False, f"« {FOURNISSEURS[fournisseur]['executable']} » est introuvable sur ce PC."
    if fournisseur == "antigravity":
        try:
            modeles, _ = lister_modeles(compte)
            return True, f"Connecté. {len(modeles)} modèle(s) disponible(s)."
        except ErreurIA as e:
            return False, str(e)
    cmd = [exe, "auth", "status", "--text"] if fournisseur == "claude_code" else [exe, "login", "status"]
    try:
        code, sortie, erreurs = executer(cmd, None, environnement(compte), stockage.DONNEES, 60, Appel())
    except ErreurIA as e:
        return False, str(e)
    texte = (sortie + "\n" + erreurs).strip() or f"code de sortie {code}"
    return code == 0, texte
