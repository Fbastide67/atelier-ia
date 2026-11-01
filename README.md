# Atelier

Une interface locale pour faire **travailler plusieurs IA ensemble sur n'importe quel projet** :
un site, un logiciel, mais aussi un événement, un dossier, une étude, une campagne de
communication, des révisions… Tu crées des profils (rôle, consignes, documents, compte, modèle,
effort), tu les réunis dans un projet, et ils discutent entre eux, produisent les livrables et te
consultent aussi souvent que tu le souhaites.

**Pas de clé API, pas de paiement au token** : l'Atelier utilise tes abonnements en lançant les
outils officiels installés sur ton PC.

| Service | Outil officiel lancé | Abonnement | Plusieurs comptes | Modèles et effort |
|---|---|---|---|---|
| Claude | Claude Code (`claude`) | Pro / Max | oui | opus, sonnet, haiku… ; effort faible → maximum |
| ChatGPT | Codex CLI (`codex`) | Plus / Pro | oui | liste lue sur ton compte ; effort faible → maximum |
| Gemini | Antigravity CLI (`agy`), *expérimental* | Google AI Pro / Ultra | non | liste lue sur ton compte ; effort faible → maximum |
| — | Simulation | aucun | — | pour tout essayer sans rien consommer |

## Le principe (et ce qu'il ne faut pas faire)

- L'Atelier **ne lit, ne copie et ne stocke jamais** tes jetons de connexion. Il lance l'outil
  officiel *non modifié*, qui utilise la connexion que tu as faite toi-même dans la fenêtre de
  l'outil. C'est ce qu'autorisent les conditions d'Anthropic (se connecter avec son propre
  abonnement au Claude Code non modifié). Extraire le jeton pour l'utiliser ailleurs est
  interdit et bloqué.
- Les variables `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`… sont retirées avant chaque
  appel, pour que ce soit bien **ton abonnement** qui soit utilisé.
- Les quotas des abonnements supposent un usage « individuel et ordinaire ». Utilise des comptes
  **à toi** et relis les conditions de chaque service.

## Partager l'Atelier avec un lien (GitHub Pages)

L'Atelier peut être publié comme un site : tu partages le lien, chaque visiteur utilise **ses propres
abonnements** et garde **ses données sur son PC**. Une page web seule ne peut pas lancer Claude Code ou
Codex : le site est donc l'interface, et chaque visiteur installe une fois un petit **moteur local**
(ce dossier) en double-cliquant, sans taper de commande.

**Pour le visiteur** : il ouvre le lien, clique sur « Télécharger l'installateur », double-clique sur
`Installer-Atelier.bat`. L'installateur ajoute ce qui manque (Python, Claude Code, ChatGPT/Codex au choix,
pypdf), installe le moteur dans `%LOCALAPPDATA%\AtelierIA`, le lance au démarrage de Windows et crée les
raccourcis « Atelier IA ». La page se connecte ensuite toute seule au moteur (`http://127.0.0.1:8765`).
Pour mettre à jour : relancer l'installateur (les données sont gardées). Pour désinstaller :
`Desinstaller.bat` dans le dossier d'installation.

**Pour publier** (une fois) :
1. Crée un dépôt **public** sur GitHub et envoie-y ce dossier (le dossier `donnees/` est exclu par
   `.gitignore` : ne l'envoie jamais, il contient tes connexions).
2. Dans le dépôt : **Settings → Pages → Source : GitHub Actions**.
3. À chaque envoi sur `main`, `.github/workflows/site.yml` publie le site : l'interface (`web/`),
   l'installateur (`installation/installer.ps1`) et `moteur.zip`. Le lien est
   `https://<ton-nom>.github.io/<dépôt>/`.

**Sécurité** : le moteur n'obéit qu'à ce PC et au site d'où il a été installé (adresse écrite dans
`site.txt`, uniquement en https). Un autre site ne peut ni lire ni lancer quoi que ce soit.

## Installation (Windows)

Dans **Windows PowerShell** (pas PowerShell ISE) :

1. **Python 3.10 ou plus** : `python --version` (sinon https://www.python.org/downloads/, coche
   « Add python.exe to PATH »). Aucune autre bibliothèque n'est obligatoire.
2. **Claude Code** : `claude --version` (https://code.claude.com/docs/en/setup).
3. **Codex** (ChatGPT, facultatif) : Node.js (https://nodejs.org) puis `npm install -g @openai/codex`.
4. **Antigravity** (Gemini, facultatif, expérimental) : voir la documentation de Google, puis lance
   une fois `agy` dans un terminal pour te connecter.
5. **Lecture des PDF** (facultatif mais conseillé) : double-clique sur `installer-pdf.bat`.

Après une installation, ferme et rouvre PowerShell.

## Lancer

Double-clique sur **`lancer.bat`** (ou `python app.py`). Le navigateur s'ouvre sur
http://127.0.0.1:8765. **Garde la fenêtre noire ouverte** : c'est le serveur (`Ctrl+C` pour arrêter).
L'Atelier n'est accessible que depuis ton PC.

Tes données (projets, discussions, documents, mémoires) sont dans le dossier `donnees/`. Pour
mettre à jour l'Atelier, remplace tous les fichiers **sauf** ce dossier.

## Démarrer en 5 minutes

1. **Comptes → Ajouter un compte → Simulation** pour essayer sans consommer de quota.
2. **Réglages** : ton prénom, la langue des réponses, et ta **mémoire générale** (ce que toutes
   les IA doivent savoir sur toi, dans tous tes projets).
3. **Projets → Nouveau** : choisis le type de projet ; l'Atelier crée une **équipe adaptée**
   (ex. pour un événement : chef de projet, organisateur, communication, budget, critique).
4. **Réglages du projet** : écris le brief, les consignes pour toute l'équipe, la fréquence des
   questions, et des consignes propres à chaque membre (bouton « Consignes »).
5. **Documents** : glisse tes fichiers et choisis qui les reçoit.
6. **Discussion** : écris une consigne et clique sur **Envoyer et lancer**.
7. Quand tout marche, ajoute tes vrais comptes et change le compte des profils.

## Les fonctions

**Profils** : nom, couleur, compte, **modèle** (liste ou nom exact ; pour ChatGPT et Gemini, bouton
« Lister les modèles de ce compte »), **effort de réflexion**, accès aux fichiers, internet (Claude
et ChatGPT), **rôle**, **consignes permanentes** et
**documents joints** (charte, exemples… reçus dans tous ses projets). Le bouton « Tester » fait un
petit appel réel avec ce modèle et cet effort. Treize rôles sont prêts à l'emploi.

**Accès d'un profil** :
- *Discussion seule* : ne voit pas les fichiers (le moins gourmand).
- *Lecture* : lit le dossier du projet et les documents.
- *Modification* : crée et modifie les fichiers du dossier du projet **et y lance des commandes**
  (shell : PowerShell sous Windows) pour installer, compiler, tester ou lancer un logiciel du projet.
  Chaque outil garde son bac à sable : les écritures restent dans le dossier du projet.
- *Internet* (case à cocher) : recherche web, et réseau pour les commandes des profils « Modification »
  (Claude : WebSearch/WebFetch ; ChatGPT : recherche web et réseau du bac à sable).

**Dossier du projet** (Réglages du projet) : n'importe quel dossier de ton PC (bouton « Choisir… »).
L'équipe y travaille directement : c'est ainsi qu'elle modifie un dossier existant sur ton PC.

**Façon de travailler** (Réglages du projet) :
- *Chacun son tour* : une IA à la fois.
- *En équipe, en même temps* : toutes les IA de l'équipe travaillent **simultanément** pendant une
  séance. Pendant leur travail, leurs messages intermédiaires deviennent des **mini-rapports** : ils
  s'affichent en direct (bandeau et panneau « Équipe »), apparaissent dans la discussion et sont écrits
  dans `.atelier/journal-equipe.md`, que les autres membres relisent pour se coordonner (pas deux sur
  le même fichier, pas de travail en double). À la fin de chaque séance, le **coordinateur** fait un
  **point d'étape** (« CAP : BON » ou « CAP : À CORRIGER », une consigne par membre) pour vérifier que
  le travail va dans le bon sens. Les « interventions par clic » sont réparties entre les membres
  (6 interventions pour 3 membres = 2 séances) ; « Un tour » = une séance. « Faire parler » et les
  messages privés restent des interventions seules.

**Documents** : Word, PowerPoint, Excel, OpenDocument, PDF, texte, CSV, images (25 Mo maximum).
Le texte est extrait à l'import et donné aux IA destinataires à chaque intervention ; tu choisis
pour chaque document « toute l'équipe » ou certains membres. Les images et les PDF sans texte ne
sont lisibles que par les profils qui ont l'accès « Lecture » ou « Modification ».

**Questions** (réglage du projet) :
- *Jamais* : les IA décident seules, notent leurs hypothèses, et la discussion ne s'arrête jamais.
- *Parfois* : seulement pour ce qui bloque vraiment.
- *Normal* : pour les décisions importantes et les informations manquantes.
- *Beaucoup* : elles te font valider chaque étape.

Une question met la discussion en pause, apparaît encadrée, et ses options numérotées deviennent
des boutons : un clic prépare ta réponse.

**Parole** : le **dernier** `@Nom` d'un message donne la parole à ce membre (les IA terminent par
`@Nom` ; une mention plus haut dans le message ne compte pas) ; sinon c'est le tour de rôle. Un
message ne met en pause que s'il s'adresse à `@Humain` (dernière mention) : citer `@Humain` au milieu
d'un message puis passer la parole à un membre ne met pas en pause.
Dans le panneau « Équipe », « Faire parler » fait intervenir un membre précis et « Message privé »
envoie un message que lui seul verra.

**Mémoire** :
- *Mémoire générale* (Réglages) : qui tu es, tes préférences, valable pour tous les projets.
- *Mémoire du projet* (onglet Mémoire) : décisions, avancement, questions ouvertes. Tu peux
  l'écrire toi-même, la faire rédiger par une IA, ou la mettre à jour automatiquement toutes les
  N interventions. Chaque version est gardée et peut être restaurée.

**Livrables** : les profils avec l'accès « Modification » écrivent leurs fichiers (textes, plannings,
code…) dans le dossier du projet, visible dans l'onglet « Livrables ». La discussion s'exporte en
Markdown (Réglages du projet).

## Connecter un vrai compte

1. **Comptes → Ajouter un compte** (Claude ou ChatGPT). « Compte séparé » (par défaut) donne au
   compte son propre dossier de connexion : c'est ce qui permet **plusieurs comptes du même
   service**. Décoché, il réutilise la connexion déjà faite dans ton terminal.
2. **Se connecter** ouvre une fenêtre qui lance `claude auth login` (ou `codex login`) ; la
   connexion se termine dans ton navigateur, sur le site officiel. Si la fenêtre ne s'ouvre pas,
   copie la « commande manuelle » dans PowerShell.
3. **Vérifier**, puis **Tester** sur un profil.

## Économiser ton quota

- Moins de « messages relus par chaque IA » (8 à 12 suffit) et une mémoire du projet à jour.
- « Discussion seule » pour les profils qui n'ont pas besoin des fichiers.
- Modèles légers et effort faible pour les rôles simples ; réserve opus / effort élevé aux rôles
  qui réfléchissent (architecte, chef de projet).
- Des documents courts et ciblés (chaque document est relu à chaque intervention).
- Quelques tours à la fois, puis relis : des IA laissées seules peuvent tourner en rond.

## Limites connues

- **Antigravity (Gemini) est expérimental** : une seule connexion par PC, mode non interactif
  récent. Teste-le avec « Tester » avant de l'utiliser dans un projet. En mode non interactif, agy
  refuse d'office tout outil non autorisé : l'Atelier lui donne des permissions limitées au dossier
  du projet (lecture ; écriture pour « Modification ») et à ses commandes dans son bac à sable. Il lit
  souvent les fichiers par une commande : même en « Lecture », ses commandes sont autorisées
  (bac à sable, sans droit d'écriture sur les fichiers). Pas d'option internet pour Gemini.
- **Codex sous Windows** utilise son bac à sable « unelevated » (sans droits administrateur) : il
  écrit seulement dans le dossier du projet. Un dossier aux droits très restreints peut lui être refusé.
- **Mode équipe** : plusieurs IA modifient le même dossier en même temps. Elles se coordonnent par
  le journal et les points d'étape, mais rien n'empêche techniquement deux écritures sur un même
  fichier : garde des rôles bien séparés. Le mode équipe consomme plus de quota en même temps.
- Les noms de modèles évoluent : si un modèle disparaît, choisis « Autre » et tape le nom exact,
  ou laisse « Par défaut ». Tous les modèles n'acceptent pas tous les niveaux d'effort.
- Quand le quota d'un abonnement est épuisé, l'erreur s'affiche et la discussion se met en pause.
- Internet est proposé pour Claude et ChatGPT.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `app.py` | Serveur HTTP local (bibliothèque standard) et API JSON de l'interface |
| `orchestrateur.py` | Le salon : choix de l'orateur, contexte envoyé aux IA, questions, mémoire automatique |
| `fournisseurs.py` | Lancement de `claude -p`, `codex exec`, `agy -p` ; modèles et efforts ; simulation |
| `documents.py` | Import des documents et extraction du texte (Word, Excel, PowerPoint, OpenDocument, PDF) |
| `catalogue.py` | Types de projet, rôles et équipes prêts à l'emploi, fréquences de questions |
| `stockage.py` | Données en fichiers JSON dans `donnees/` |
| `web/` | Interface (HTML, CSS, JavaScript sans framework), servie par le moteur ou par le site public |
| `installation/installer.ps1` | Installateur du moteur pour les visiteurs du site public |
| `.github/workflows/site.yml` | Publication du site sur GitHub Pages |
| `tests/` | Tests sans IA réelle (faux `claude` / `codex` / `agy`) |

Tests : `python -m unittest discover -s tests -v`. Le test de l'interface (`tests/test_interface.py`)
pilote un vrai navigateur ; il demande `pip install playwright` puis `python -m playwright install chromium`,
sinon il est ignoré.
