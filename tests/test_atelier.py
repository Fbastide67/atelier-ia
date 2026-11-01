"""Tests sans IA réelle :  python -m unittest discover -s tests -v"""

import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock
import urllib.request
import zipfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="atelier_test_")
os.environ["ATELIER_DONNEES"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402
import catalogue  # noqa: E402
import documents  # noqa: E402
import fournisseurs  # noqa: E402
import orchestrateur  # noqa: E402
import stockage  # noqa: E402

FAUX = Path(__file__).resolve().parent / "faux_cli.py"


def faux_executable(dossier):
    """Lanceur du faux CLI, exécutable comme un vrai programme."""
    if fournisseurs.WINDOWS:
        chemin = Path(dossier) / "faux.cmd"
        chemin.write_text(f'@"{sys.executable}" "{FAUX}" %*\r\n', encoding="utf-8")
    else:
        chemin = Path(dossier) / "faux"
        chemin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAUX}" "$@"\n', encoding="utf-8")
        chemin.chmod(0o755)
    return str(chemin)


def compte_simu():
    return stockage.comptes.creer({"nom": "Simu", "fournisseur": "simulation"})


def attendre(salon, delai=10):
    salon.attendre(delai)
    if salon.statut()["en_cours"]:
        raise AssertionError("le salon ne s'arrête pas")


class AvecFauxOutils(unittest.TestCase):
    outil = "claude"

    def setUp(self):
        self.dossier = tempfile.mkdtemp(dir=TMP)
        self.journal = Path(self.dossier) / "journal.jsonl"
        exe = faux_executable(self.dossier)
        self.env_avant = dict(os.environ)
        os.environ.update({
            "ATELIER_CLAUDE": exe, "ATELIER_CODEX": exe, "ATELIER_AGY": exe,
            "FAUX_JOURNAL": str(self.journal), "FAUX_OUTIL": self.outil,
            "ANTHROPIC_API_KEY": "cle-qui-ne-doit-pas-passer", "OPENAI_API_KEY": "idem", "GEMINI_API_KEY": "idem",
        })
        self.espace = Path(self.dossier) / "espace"
        self.espace.mkdir()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_avant)

    def appels(self):
        return [json.loads(l) for l in self.journal.read_text(encoding="utf-8").splitlines()]

    def appeler(self, profil, systeme="S", prompt="P", lisibles=(), progression=None):
        base = {"id": "p1", "nom": "X", "acces": "aucun", "modele": "", "effort": "", "web": False, "role": ""}
        return fournisseurs.appeler(self.compte, dict(base, **profil), systeme, prompt, self.espace,
                                    fournisseurs.Appel(), lisibles, progression)


def valeur(args, option):
    return args[args.index(option) + 1]


class TestClaudeCode(AvecFauxOutils):
    def setUp(self):
        super().setUp()
        self.compte = stockage.comptes.creer({"nom": "Claude A", "fournisseur": "claude_code"})

    def test_discussion_sans_outils_et_sans_cle_api(self):
        texte, _ = self.appeler({"modele": "sonnet"}, "SYSTEME", "PROMPT é")
        self.assertEqual(texte, "Réponse de Claude é à ç")
        appel = self.appels()[0]
        args = appel["args"]
        self.assertEqual(args[:4], ["-p", "--output-format", "stream-json", "--verbose"])
        self.assertEqual(valeur(args, "--tools"), "")
        self.assertIn("--system-prompt-file", args)
        self.assertEqual(valeur(args, "--model"), "sonnet")
        self.assertEqual(valeur(args, "--max-turns"), "1")
        self.assertNotIn("--effort", args)
        self.assertEqual(appel["entree"], "PROMPT é")
        self.assertIsNone(appel["env"]["ANTHROPIC_API_KEY"])
        self.assertEqual(appel["env"]["CLAUDE_CONFIG_DIR"], str(stockage.dossier_compte(self.compte)))
        self.assertEqual(Path(appel["cwd"]).resolve(), self.espace.resolve())

    def test_effort(self):
        self.appeler({"effort": "xhigh"})
        self.appeler({"effort": "minimal"})  # niveau propre à Codex : ignoré pour Claude
        a, b = self.appels()
        self.assertEqual(valeur(a["args"], "--effort"), "xhigh")
        self.assertNotIn("--effort", b["args"])

    def test_ecriture_autorise_les_modifications_et_les_documents(self):
        docs = Path(self.dossier) / "docs"
        docs.mkdir()
        self.appeler({"acces": "ecriture"}, lisibles=[docs])
        args = self.appels()[0]["args"]
        self.assertEqual(valeur(args, "--permission-mode"), "acceptEdits")
        shell = "PowerShell" if fournisseurs.WINDOWS else "Bash"  # Claude Code n'a pas de Bash sous Windows
        self.assertEqual(valeur(args, "--tools"), f"Read,Glob,Grep,Write,Edit,{shell}")
        self.assertEqual(valeur(args, "--allowedTools"), f"Read,Glob,Grep,Write,Edit,{shell}")
        self.assertEqual(valeur(args, "--add-dir"), str(docs))
        self.assertIn("--append-system-prompt-file", args)
        self.assertNotIn("--model", args)
        self.assertTrue((self.espace / "cree_par_claude.txt").exists())

    def test_recherche_web_sans_acces_fichiers(self):
        self.appeler({"web": True}, lisibles=[Path(self.dossier)])
        args = self.appels()[0]["args"]
        self.assertEqual(valeur(args, "--tools"), "WebSearch,WebFetch")
        self.assertEqual(valeur(args, "--allowedTools"), "WebSearch,WebFetch")
        self.assertEqual(valeur(args, "--max-turns"), "15")
        self.assertNotIn("--add-dir", args)  # pas d'accès fichiers : pas de dossier de documents
        self.assertNotIn("--permission-mode", args)

    def test_mini_rapports_pendant_le_travail(self):
        os.environ["FAUX_FLUX"] = "1"
        rapports = []
        texte, _ = self.appeler({"acces": "lecture"}, progression=rapports.append)
        self.assertEqual(texte, "Réponse de Claude é à ç")
        self.assertEqual(rapports, ["Je lis le brief."])  # la réponse finale n'est pas un rapport

    def test_compte_par_defaut_ne_force_pas_de_dossier(self):
        self.compte = stockage.comptes.creer({"nom": "défaut", "fournisseur": "claude_code", "compte_separe": False})
        self.appeler({})
        self.assertIsNone(self.appels()[0]["env"]["CLAUDE_CONFIG_DIR"])

    def test_erreur_de_quota_remontee(self):
        os.environ["FAUX_ERREUR"] = "1"
        with self.assertRaisesRegex(fournisseurs.ErreurIA, "usage limit"):
            self.appeler({})

    def test_verification_du_compte(self):
        connecte, _ = fournisseurs.verifier_compte(self.compte)
        self.assertTrue(connecte)
        self.assertEqual(self.appels()[0]["args"], ["auth", "status", "--text"])


class TestCodex(AvecFauxOutils):
    outil = "codex"

    def setUp(self):
        super().setUp()
        self.compte = stockage.comptes.creer({"nom": "GPT", "fournisseur": "codex"})

    def test_exec_lit_la_derniere_reponse(self):
        texte, _ = self.appeler({"acces": "lecture", "modele": "gpt-6-luna", "effort": "high"}, "ROLE", "PROMPT")
        self.assertEqual(texte, "Réponse de Codex")
        appel = self.appels()[0]
        args = appel["args"]
        self.assertEqual(args[:2], ["exec", "--json"])
        self.assertEqual(valeur(args, "-s"), "read-only")
        self.assertEqual(valeur(args, "-m"), "gpt-6-luna")
        self.assertEqual(valeur(args, "-c"), "model_reasoning_effort=high")
        self.assertEqual(args[-1], "-")
        self.assertIn("ROLE", appel["entree"])
        self.assertNotIn("UTF-8", appel["entree"])  # consigne d'écriture réservée aux profils qui modifient
        self.assertNotIn("web_search=live", args)
        self.assertIsNone(appel["env"]["OPENAI_API_KEY"])
        self.assertEqual(appel["env"]["CODEX_HOME"], str(stockage.dossier_compte(self.compte)))

    def test_ecriture_bac_a_sable_windows_utf8_et_effort_inconnu(self):
        self.appeler({"acces": "ecriture", "effort": "minimal"})  # « minimal » n'est plus proposé pour Codex
        appel = self.appels()[0]
        args = appel["args"]
        self.assertEqual(valeur(args, "-s"), "workspace-write")
        self.assertFalse(any(a.startswith("model_reasoning_effort") for a in args))
        self.assertEqual("windows.sandbox=unelevated" in args, fournisseurs.WINDOWS)
        self.assertNotIn("sandbox_workspace_write.network_access=true", args)
        self.assertIn("UTF-8", appel["entree"])

    def test_internet_et_reseau_des_commandes(self):
        self.appeler({"acces": "ecriture", "web": True})
        self.appeler({"acces": "lecture", "web": True})
        ecriture, lecture = (a["args"] for a in self.appels())
        self.assertIn("web_search=live", ecriture)
        self.assertIn("sandbox_workspace_write.network_access=true", ecriture)
        self.assertIn("web_search=live", lecture)
        self.assertNotIn("sandbox_workspace_write.network_access=true", lecture)

    def test_mini_rapports(self):
        os.environ["FAUX_FLUX"] = "1"
        rapports = []
        texte, _ = self.appeler({"acces": "ecriture"}, progression=rapports.append)
        self.assertEqual(texte, "Réponse de Codex")
        self.assertEqual(rapports, ["Je commence par le plan."])

    def test_erreur_lisible(self):
        os.environ["FAUX_ERREUR"] = "1"
        with self.assertRaises(fournisseurs.ErreurIA) as erreur:
            self.appeler({"modele": "gpt-5.5"})
        self.assertEqual(str(erreur.exception),
                         "The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account.")
        texte = fournisseurs.erreur_codex([], "\x1b[31m2026-11-01T08:29:15.000000Z ERROR codex_core::exec: "
                                              "stream disconnected\x1b[0m\nautre ligne", 1)
        self.assertEqual(texte, "stream disconnected")

    def test_chemin_avec_esperluette_sous_windows(self):
        # codex.cmd (npm) passe par cmd.exe : « R&D » ne doit pas couper la commande en deux.
        self.espace = Path(self.dossier) / "R&D (été)"
        self.espace.mkdir()
        texte, _ = self.appeler({"acces": "ecriture", "web": True})
        self.assertEqual(texte, "Réponse de Codex")
        args = self.appels()[0]["args"]
        self.assertEqual(Path(valeur(args, "-C")), self.espace)
        self.assertEqual(valeur(args, "-s"), "workspace-write")
        self.assertEqual(args[-1], "-")

    def test_lister_les_modeles_du_compte(self):
        modeles, brut = fournisseurs.lister_modeles(self.compte)
        self.assertEqual(modeles, ["gpt-6-luna", "gpt-5.6-terra"])  # modèles cachés écartés, ordre du catalogue
        self.assertIn("GPT-6-Luna", brut)
        self.assertEqual(self.appels()[0]["args"], ["debug", "models"])
        self.assertEqual(self.appels()[0]["env"]["CODEX_HOME"], str(stockage.dossier_compte(self.compte)))


class TestAntigravity(AvecFauxOutils):
    outil = "agy"

    def setUp(self):
        super().setUp()
        self.compte = stockage.comptes.creer({"nom": "Gemini", "fournisseur": "antigravity"})

    def consigne(self, appel):
        """Texte reçu par agy : en argument, ou dans le fichier qu'on lui demande de lire
        (toujours le cas avec le faux .cmd de Windows, qui ne transmet pas les retours à la ligne)."""
        consigne = appel["args"][1]
        fichier = self.espace / ".atelier" / "consigne_p1.md"
        return fichier.read_text(encoding="utf-8") if str(fichier) in consigne else consigne

    def regles(self, appel):
        return json.loads(valeur(appel["args"], "--settings"))["permissions"]["allow"]

    def test_prompt_modele_effort_et_permissions_lecture(self):
        docs = Path(self.dossier) / "normes"
        docs.mkdir()
        texte, _ = self.appeler({"acces": "lecture", "modele": "gemini-3.1-pro", "effort": "high"}, "ROLE", "PROMPT",
                                lisibles=[docs])
        self.assertEqual(texte, "Réponse de Gemini")
        appel = self.appels()[0]
        self.assertEqual(appel["args"][0], "-p")
        self.assertIn("ROLE", self.consigne(appel))
        self.assertEqual(valeur(appel["args"], "--model"), "gemini-3.1-pro")
        self.assertEqual(valeur(appel["args"], "--effort"), "high")
        self.assertIn("--sandbox", appel["args"])
        self.assertNotIn("--mode", appel["args"])  # le mode « plan » rend un plan au lieu de répondre
        self.assertEqual(valeur(appel["args"], "--add-dir"), str(docs))  # documents des normes
        regles = self.regles(appel)
        self.assertIn(f"read_file({self.espace})", regles)
        self.assertIn(f"read_file({docs})", regles)
        self.assertFalse(any(r.startswith("write_file") for r in regles))
        self.assertIn(appel["entree"], ("", None))  # entrée standard fermée (sous Windows, NUL passe pour un terminal)
        self.assertIsNone(appel["env"]["GEMINI_API_KEY"])

    def test_modification(self):
        self.appeler({"acces": "ecriture", "web": True})
        appel = self.appels()[0]
        self.assertEqual(valeur(appel["args"], "--mode"), "accept-edits")
        regles = self.regles(appel)
        self.assertIn(f"write_file({self.espace})", regles)
        self.assertIn("read_url(*)", regles)
        self.assertIn("UTF-8", self.consigne(appel))

    def test_discussion_seule_lit_seulement_sa_consigne(self):
        self.appeler({})
        appel = self.appels()[0]
        self.assertEqual([r for r in self.regles(appel) if r.startswith("read_file")][0],
                         f"read_file({self.espace / '.atelier'})")
        self.assertNotIn("--add-dir", appel["args"])

    def test_refus_de_permission_explique(self):
        os.environ["FAUX_ERREUR"] = "1"
        with self.assertRaisesRegex(fournisseurs.ErreurIA, "auto-denied"):
            self.appeler({})

    def test_long_prompt_passe_par_un_fichier(self):
        self.appeler({}, "ROLE", "x" * 20000)
        consigne = self.appels()[0]["args"][1]
        self.assertIn("consigne_p1.md", consigne)
        self.assertTrue((self.espace / ".atelier" / "consigne_p1.md").exists())

    def test_lister_les_modeles(self):
        modeles, brut = fournisseurs.lister_modeles(self.compte)
        self.assertEqual(modeles, ["gemini-3.1-pro", "gemini-3.8-flash"])
        self.assertIn("Gemini 3.1 Pro", brut)


class TestExecuter(unittest.TestCase):
    def script(self, code):
        chemin = Path(tempfile.mkdtemp(dir=TMP)) / "outil.py"
        chemin.write_text(code, encoding="utf-8")
        return [sys.executable, str(chemin)]

    def test_programme_en_arriere_plan_ne_bloque_pas(self):
        # L'IA lance un serveur qui hérite de la sortie, puis l'outil se termine : on n'attend pas le serveur.
        cmd = self.script("import subprocess, sys\n"
                          "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(25)'])\n"
                          "print('fini', flush=True)\n")
        debut = time.time()
        code, sortie, _ = fournisseurs.executer(cmd, None, os.environ.copy(), TMP, 60, fournisseurs.Appel())
        self.assertEqual((code, sortie.strip()), (0, "fini"))
        self.assertLess(time.time() - debut, 15)

    def test_stop_pendant_le_demarrage(self):
        appel = fournisseurs.Appel()
        popen = fournisseurs.subprocess.Popen

        class PopenLent(popen):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                fournisseurs.subprocess.Popen = popen  # seulement le premier lancement (pas taskkill)
                appel.arreter()  # Stop pressé avant que l'Atelier connaisse le processus

        fournisseurs.subprocess.Popen = PopenLent
        try:
            debut = time.time()
            with self.assertRaises(fournisseurs.Annule):
                fournisseurs.executer(self.script("import time; time.sleep(20)"), None, os.environ.copy(), TMP, 60, appel)
        finally:
            fournisseurs.subprocess.Popen = popen
        self.assertLess(time.time() - debut, 10)

    def test_sortie_lue_au_fil_de_l_eau(self):
        vues = []
        cmd = self.script("import time\nprint('un', flush=True)\ntime.sleep(0.5)\nprint('deux', flush=True)\n")
        code, sortie, _ = fournisseurs.executer(cmd, None, os.environ.copy(), TMP, 60, fournisseurs.Appel(),
                                                lambda l: vues.append((l.strip(), time.time())))
        self.assertEqual([v[0] for v in vues], ["un", "deux"])
        self.assertGreater(vues[1][1] - vues[0][1], 0.3)  # « un » est arrivé avant la fin


def zip_en_memoire(fichiers):
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        for nom, contenu in fichiers.items():
            z.writestr(nom, contenu)
    return tampon.getvalue()


def pdf_minimal(texte):
    objets = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    flux = f"BT /F1 18 Tf 20 100 Td ({texte}) Tj ET"
    objets[3] = f"<< /Length {len(flux)} >>\nstream\n{flux}\nendstream"
    sortie, positions = "%PDF-1.4\n", []
    for i, obj in enumerate(objets, 1):
        positions.append(len(sortie))
        sortie += f"{i} 0 obj\n{obj}\nendobj\n"
    debut_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n"
    sortie += "".join(f"{p:010d} 00000 n \n" for p in positions)
    sortie += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{debut_xref}\n%%EOF\n"
    return sortie.encode("latin-1")


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
T = 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'


class TestDocuments(unittest.TestCase):
    def test_extraction_formats_courants(self):
        x = documents.extraire_texte
        self.assertEqual(x("a.txt", "Été à Marseille".encode("utf-8")), "Été à Marseille")
        self.assertEqual(x("a.csv", "prix;été".encode("cp1252")), "prix;été")
        self.assertEqual(x("image.png", b"\x89PNG\r\n\x00\x00"), "")
        self.assertEqual(x("inconnu.bin", b"\x00\x01\x02"), "")
        docx = zip_en_memoire({"word/document.xml":
            f'<w:document {W}><w:body><w:p><w:r><w:t>Bonjour</w:t></w:r><w:r><w:tab/><w:t>Word</w:t></w:r></w:p>'
            f'<w:p><w:r><w:t>Ligne 2</w:t></w:r></w:p></w:body></w:document>'})
        self.assertEqual(x("a.docx", docx), "Bonjour\tWord\nLigne 2")
        diapo = lambda t: f'<p:sld xmlns:p="p" {A}><a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:sld>'
        pptx = zip_en_memoire({"ppt/slides/slide1.xml": diapo("Titre"), "ppt/slides/slide10.xml": diapo("Fin"),
                               "ppt/slides/slide2.xml": diapo("Milieu")})
        texte = x("a.pptx", pptx)
        self.assertLess(texte.index("Titre"), texte.index("Milieu"))
        self.assertLess(texte.index("Milieu"), texte.index("Fin"))
        xlsx = zip_en_memoire({
            "xl/sharedStrings.xml": f'<sst {S}><si><t>Nom</t></si><si><t>Prix</t></si></sst>',
            "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
                                        f'<row><c t="inlineStr"><is><t>Entrée</t></is></c><c><v>12</v></c></row></sheetData></worksheet>'})
        self.assertIn("Nom\tPrix\nEntrée\t12", x("a.xlsx", xlsx))
        odt = zip_en_memoire({"content.xml": f'<doc {T}><text:h>Titre</text:h><text:p>Corps <text:span>ici</text:span></text:p></doc>'})
        self.assertEqual(x("a.odt", odt), "Titre\nCorps ici")
        self.assertEqual(x("abime.docx", b"pas un zip"), "")

    @unittest.skipUnless(documents.PDF_DISPONIBLE, "pypdf non installé")
    def test_extraction_pdf(self):
        self.assertIn("Bonjour PDF", documents.extraire_texte("a.pdf", pdf_minimal("Bonjour PDF")))

    def test_pdf_sans_pypdf_reste_lisible_par_fichier(self):
        ancien = documents.PDF_DISPONIBLE
        documents.PDF_DISPONIBLE = False
        try:
            self.assertEqual(documents.extraire_texte("a.pdf", pdf_minimal("x")), "")
        finally:
            documents.PDF_DISPONIBLE = ancien

    def test_import_nom_sur_et_distribution(self):
        projet = stockage.projets.creer({"nom": "Docs"})
        a = stockage.profils.creer({"nom": "A"})
        b = stockage.profils.creer({"nom": "B"})
        d1 = documents.importer("profil", a["id"], "../../charte.md", "Ton : direct".encode())
        d2 = documents.importer("projet", projet["id"], "brief.txt", b"Pour tous")
        d3 = documents.importer("projet", projet["id"], "budget.txt", b"Pour B", membres=[b["id"]])
        d4 = documents.importer("projet", projet["id"], "photo.png", b"\x89PNG\x00")
        self.assertEqual(d1["nom"], "charte.md")
        self.assertTrue(documents.chemin_original(d1).exists())
        self.assertEqual(d1["caracteres"], len("Ton : direct"))
        self.assertEqual([d["id"] for d in documents.pour(projet, a)], [d1["id"], d2["id"], d4["id"]])
        self.assertEqual([d["id"] for d in documents.pour(projet, b)], [d2["id"], d3["id"], d4["id"]])
        section = documents.section(documents.pour(projet, a), "aucun")
        self.assertIn("### charte.md (joint à ton profil)", section)
        self.assertIn("Ton : direct", section)
        self.assertIn("tu ne peux pas l'ouvrir", section)
        self.assertIn(str(documents.chemin_original(d4)), documents.section([d4], "lecture"))
        self.assertTrue(stockage.supprimer_document(d1["id"]))
        self.assertFalse(stockage.dossier_document(d1).exists())

    def test_troncature_et_taille_max(self):
        projet = stockage.projets.creer({"nom": "Gros"})
        doc = documents.importer("projet", projet["id"], "long.txt", ("a" * 50_000).encode())
        section = documents.section([doc], "aucun")
        self.assertIn("document tronqué (30000 caractères sur 50000)", section)
        self.assertIn("demande à l'humain", section)
        self.assertIn("texte.txt", documents.section([doc], "lecture"))
        ancien = documents.TAILLE_MAX
        documents.TAILLE_MAX = 10
        try:
            with self.assertRaises(ValueError):
                documents.importer("projet", projet["id"], "x.txt", b"x" * 11)
        finally:
            documents.TAILLE_MAX = ancien


class TestPrompt(unittest.TestCase):
    def test_prompt_systeme(self):
        profil = {"nom": "Dev", "role": "Code", "instructions": "Toujours commenter", "acces": "ecriture", "web": True}
        params = {"nom_utilisateur": "Félix", "langue": "anglais"}
        s = orchestrateur.prompt_systeme(profil, {"questions": "normal"}, params)
        self.assertIn("avec Félix", s)
        self.assertIn("# Tes consignes permanentes\nToujours commenter", s)
        self.assertIn("Réponds en anglais", s)
        self.assertIn(catalogue.CONSIGNE_CHOIX, s)
        self.assertIn("chercher sur internet", s)
        self.assertIn("Mets les livrables", s)
        jamais = orchestrateur.prompt_systeme(profil, {"questions": "jamais"}, params)
        self.assertIn("n'écris jamais @Humain", jamais)
        self.assertNotIn(catalogue.CONSIGNE_CHOIX, jamais)
        self.assertIn("valider chaque étape", orchestrateur.prompt_systeme(profil, {"questions": "beaucoup"}, params))

    def test_prompt_tour_complet(self):
        espace = Path(tempfile.mkdtemp(dir=TMP))
        (espace / "index.html").write_text("<html>", encoding="utf-8")
        (espace / ".atelier").mkdir()
        (espace / ".atelier" / "cache").write_text("x", encoding="utf-8")
        a = {"id": "a", "nom": "A", "role": "Architecte du site", "acces": "lecture"}
        b = {"id": "b", "nom": "B", "role": "Autre", "acces": "aucun"}
        projet = {"nom": "P", "type": "evenement", "brief": "BRIEF", "memoire": "MEMO", "historique_max": 3,
                  "consignes": "Budget serré", "consignes_membres": {"a": "Concentre-toi sur la logistique"}}
        msgs = [{"n": i, "nom": "X", "texte": f"message {i}", "auteur": "humain"} for i in range(1, 6)]
        msgs.append({"n": 6, "nom": "Humain", "texte": "secret pour B", "auteur": "humain", "prive_pour": "b"})
        msgs.append({"n": 7, "nom": "Humain", "texte": "privé pour A", "auteur": "humain", "prive_pour": "a"})
        params = {"nom_utilisateur": "félix", "memoire_generale": "Étudiant ingénieur"}
        p = orchestrateur.prompt_tour(projet, a, [a, b], msgs, espace, params, section_documents="## Documents fournis\nDOCS")
        self.assertIn("(événement", p)
        self.assertIn("## Brief du projet (écrit par félix)\nBRIEF", p)
        self.assertIn("Budget serré", p)
        self.assertIn("## Consignes pour toi dans ce projet\nConcentre-toi sur la logistique", p)
        self.assertIn("Étudiant ingénieur", p)
        self.assertIn("MEMO", p)
        self.assertIn("@A : Architecte du site [lecture seule]  ← c'est toi", p)
        self.assertIn("- index.html", p)
        self.assertNotIn("cache", p)
        self.assertIn("DOCS", p)
        self.assertNotIn("secret pour B", p)
        self.assertIn("### #7 — Félix (message privé pour toi)", p)
        self.assertIn("message 5", p)
        self.assertNotIn("message 3", p)
        pb = orchestrateur.prompt_tour(projet, b, [a, b], msgs, espace, params)
        self.assertNotIn("Consignes pour toi", pb)
        self.assertIn("secret pour B", pb)
        self.assertNotIn("privé pour A", pb)


class TestParole(unittest.TestCase):
    equipe = [{"id": "a", "nom": "Architecte"}, {"id": "d", "nom": "Dev"}, {"id": "df", "nom": "Dev front"}]

    def msg(self, auteur, texte, **extra):
        return {"n": 1, "auteur": auteur, "nom": auteur, "texte": texte, **extra}

    def orateur(self, messages, force=None):
        return orchestrateur.choisir_orateur(self.equipe, messages, force)["id"]

    def test_tour_de_role(self):
        self.assertEqual(self.orateur([]), "a")
        self.assertEqual(self.orateur([self.msg("a", "plan")]), "d")
        self.assertEqual(self.orateur([self.msg("df", "fini")]), "a")

    def test_mention_prioritaire_et_nom_le_plus_long(self):
        self.assertEqual(self.orateur([self.msg("a", "À toi @dev front !")]), "df")
        self.assertEqual(self.orateur([self.msg("a", "Ok @Dev, vas-y")]), "d")
        self.assertEqual(self.orateur([self.msg("humain", "@Architecte refais le plan")]), "a")

    def test_on_ne_se_donne_pas_la_parole_a_soi_meme(self):
        self.assertEqual(self.orateur([self.msg("a", "moi @Architecte")]), "d")

    def test_force_et_message_prive(self):
        self.assertEqual(self.orateur([self.msg("a", "@Dev")], force="df"), "df")
        self.assertEqual(self.orateur([self.msg("humain", "@Dev regarde", prive_pour="a")]), "a")
        self.assertEqual(self.orateur([self.msg("a", "x")], force="inconnu"), "d")

    def test_la_derniere_mention_donne_la_parole(self):
        # Les IA citent les autres en cours de message et terminent par @Nom : c'est ce dernier qui parle.
        self.assertEqual(self.orateur([self.msg("a", "Comme l'a dit @Dev front, le plan tient. À toi @Dev")]), "d")
        self.assertEqual(self.orateur([self.msg("a", "@Dev a fini. @Dev front, relis.")]), "df")
        self.assertEqual(self.orateur([self.msg("a", "@Dev, puis moi @Architecte")]), "d")  # soi-même ne compte pas

    def test_humain_au_milieu_ne_donne_pas_la_parole_a_l_humain(self):
        texte = "Comme demandé par @Humain, j'ai fini la page. @Dev, à toi."
        self.assertEqual(self.orateur([self.msg("a", texte)]), "d")
        self.assertFalse(orchestrateur.s_adresse_a_humain(texte, self.equipe, "a"))
        self.assertTrue(orchestrateur.s_adresse_a_humain("@Dev a fini. @Humain, on valide ?", self.equipe, "a"))
        self.assertEqual(self.orateur([self.msg("a", "@Dev a fini. @Humain, on valide ?")]), "d")  # tour de rôle

    def test_humain_et_mention_partielle(self):
        self.assertTrue(orchestrateur.s_adresse_a_humain("Question pour @humain : la date ?"))
        self.assertFalse(orchestrateur.s_adresse_a_humain("@Humanité"))
        self.assertIsNone(orchestrateur.derniere_mention("@Developpeur", [{"nom": "Dev"}]))
        self.assertEqual(orchestrateur.derniere_mention("@Dev @Developpeur", [{"nom": "Dev"}]), {"nom": "Dev"})

    def test_adresse_e_mail_n_est_pas_une_mention(self):
        self.assertEqual(self.orateur([self.msg("a", "@Dev, à toi. Le devis vient de devis@dev.fr")]), "d")
        self.assertFalse(orchestrateur.s_adresse_a_humain("@Dev regarde. Écris à support@humain.fr", self.equipe, "a"))

    def test_les_mini_rapports_ne_comptent_pas(self):
        messages = [self.msg("a", "plan"), self.msg("d", "je lis le plan @Architecte", rapport=True)]
        self.assertEqual(self.orateur(messages), "d")


class TestSalon(unittest.TestCase):
    def projet(self, *roles, **reglages):
        compte = compte_simu()
        equipe = [stockage.profils.creer({"nom": f"M{i}", "compte_id": compte["id"], "role": r})
                  for i, r in enumerate(roles)]
        projet = stockage.projets.creer({"nom": "Test", "brief": "Un projet", "equipe": [p["id"] for p in equipe], **reglages})
        return projet, equipe, orchestrateur.salon(projet["id"])

    def test_tour_de_role_et_question(self):
        projet, (a, b), salon = self.projet("Rôle A", "Rôle B")
        self.assertTrue(salon.lancer(3))
        attendre(salon)
        self.assertEqual([m["auteur"] for m in stockage.messages(projet["id"])], [a["id"], b["id"], a["id"]])

        stockage.profils.modifier(b["id"], {"role": "Pose une question"})
        salon.lancer(5)
        attendre(salon)
        dernier = stockage.messages(projet["id"])[-1]
        self.assertEqual(dernier["auteur"], b["id"])
        self.assertTrue(dernier["question"])
        self.assertIn("attend ta réponse", salon.statut()["info"])

    def test_questions_jamais_ne_met_pas_en_pause(self):
        projet, (a,), salon = self.projet("Rôle", questions="jamais")
        original = fournisseurs.APPELS["simulation"]
        fournisseurs.APPELS["simulation"] = lambda *args: ("Je demande quand même à @Humain", {})
        try:
            salon.lancer(3)
            attendre(salon)
        finally:
            fournisseurs.APPELS["simulation"] = original
        messages = stockage.messages(projet["id"])
        self.assertEqual(len(messages), 3)
        self.assertFalse(any(m.get("question") for m in messages))

    def test_orateur_force(self):
        projet, (a, b, c), salon = self.projet("A", "B", "C")
        salon.lancer(2, premier=c["id"])
        attendre(salon)
        self.assertEqual([m["auteur"] for m in stockage.messages(projet["id"])], [c["id"], a["id"]])

    def test_memoire_automatique_et_historique(self):
        projet, (a, b), salon = self.projet("A", "B", memoire_auto=2)
        salon.lancer(4)
        attendre(salon)
        messages = stockage.messages(projet["id"])
        systeme = [m for m in messages if m["auteur"] == "systeme"]
        self.assertEqual(len(systeme), 2)
        apres = stockage.projets.get(projet["id"])
        self.assertIn("Simulation : décisions", apres["memoire"])
        self.assertEqual(apres["memoire_maj_n"], messages[-2]["n"])
        self.assertEqual(len(stockage.historique_memoire(projet["id"])), 1)  # même texte : une seule version

    def test_memoire_automatique_en_echec_ne_boucle_pas(self):
        projet, (a,), salon = self.projet("A", memoire_auto=2)
        sans_compte = stockage.profils.creer({"nom": "Sans compte"})
        stockage.projets.modifier(projet["id"], {"memoire_profil": sans_compte["id"]})
        salon.lancer(6)
        attendre(salon)
        echecs = [m for m in stockage.messages(projet["id"]) if "mémoire impossible" in m["texte"]]
        self.assertEqual(len(echecs), 3)  # une tentative toutes les 2 interventions, pas après chacune

    def test_memoire_manuelle(self):
        projet, (a,), salon = self.projet("A")
        stockage.enregistrer_memoire(projet["id"], "écrit à la main", "Toi")
        salon.resumer_memoire(a["id"])
        attendre(salon)
        historique = stockage.historique_memoire(projet["id"])
        self.assertEqual([h["auteur"] for h in historique], ["Toi", "M0"])

    def test_stop_interrompt(self):
        projet, (a,), salon = self.projet("A")
        salon.lancer(10)
        time.sleep(0.3)
        salon.arreter()
        attendre(salon)
        self.assertLess(len(stockage.messages(projet["id"])), 10)

    def test_erreur_met_en_pause(self):
        projet, (a,), salon = self.projet("A")

        def panne(*args):
            raise fournisseurs.ErreurIA("quota épuisé")
        original = fournisseurs.APPELS["simulation"]
        fournisseurs.APPELS["simulation"] = panne
        try:
            salon.lancer(3)
            attendre(salon)
        finally:
            fournisseurs.APPELS["simulation"] = original
        messages = stockage.messages(projet["id"])
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]["erreur"])
        self.assertIn("quota épuisé", messages[0]["texte"])

    def test_mention_de_l_humain_au_milieu_ne_met_pas_en_pause(self):
        projet, (a, b), salon = self.projet("A", "B")
        original = fournisseurs.APPELS["simulation"]
        fournisseurs.APPELS["simulation"] = lambda compte, profil, *reste: (
            f"Vu avec @Humain. Suite pour @{(b if profil['id'] == a['id'] else a)['nom']}.", {})
        try:
            salon.lancer(3)
            attendre(salon)
        finally:
            fournisseurs.APPELS["simulation"] = original
        messages = stockage.messages(projet["id"])
        self.assertEqual(len(messages), 3)
        self.assertFalse(any(m.get("question") for m in messages))

    def test_equipe_en_simultane_rapports_et_point_d_etape(self):
        projet, equipe, salon = self.projet("A", "B", "C", mode="equipe", coordinateur="")
        stockage.projets.modifier(projet["id"], {"coordinateur": equipe[1]["id"]})
        maximum = 0
        self.assertTrue(salon.lancer(3))  # 3 interventions pour 3 membres : une séance
        while salon.statut()["en_cours"]:
            maximum = max(maximum, len(salon.statut()["actifs"]))
            time.sleep(0.02)
        attendre(salon)
        self.assertEqual(maximum, 3)  # les trois membres travaillaient en même temps
        messages = stockage.messages(projet["id"])
        ids = {p["id"] for p in equipe}
        rapports = [m for m in messages if m.get("rapport")]
        finaux = [m for m in messages if m["auteur"] in ids and not m.get("rapport") and not m.get("point_etape")]
        points = [m for m in messages if m.get("point_etape")]
        self.assertEqual({m["auteur"] for m in rapports}, ids)
        self.assertEqual({m["auteur"] for m in finaux}, ids)
        self.assertTrue(all(m["seance"] == 1 for m in finaux))
        self.assertEqual([m["auteur"] for m in points], [equipe[1]["id"]])  # le coordinateur choisi
        self.assertEqual(messages[-1], points[0])
        self.assertIn("Séances terminées", salon.statut()["info"])
        journal = (stockage.espace_projet(projet) / orchestrateur.JOURNAL).read_text(encoding="utf-8")
        self.assertIn("étape intermédiaire", journal)
        self.assertIn("a terminé sa séance", journal)
        self.assertNotIn(orchestrateur.JOURNAL, [f["chemin"] for f in orchestrateur.lister_fichiers(stockage.espace_projet(projet))])

    def test_equipe_consigne_et_contexte(self):
        projet, (a, b), salon = self.projet("A", "B", mode="equipe", points_etape=False)
        vus = []
        original = fournisseurs.APPELS["simulation"]

        def espion(compte, profil, systeme, prompt, *reste):
            vus.append((profil["nom"], prompt))
            return f"Fait : ma part. @{b['nom']}", {}
        fournisseurs.APPELS["simulation"] = espion
        try:
            salon.lancer(4)  # 4 interventions pour 2 membres : deux séances
            attendre(salon)
        finally:
            fournisseurs.APPELS["simulation"] = original
        self.assertEqual(len(vus), 4)
        self.assertIn("EN MÊME TEMPS", vus[0][1])
        self.assertIn("séance 2", vus[-1][1])
        self.assertFalse(any(m.get("point_etape") for m in stockage.messages(projet["id"])))

    def test_equipe_question_met_en_pause_apres_la_seance(self):
        projet, (a, b), salon = self.projet("Rôle A", "Pose une question", mode="equipe")
        salon.lancer(10)
        attendre(salon)
        messages = stockage.messages(projet["id"])
        self.assertTrue(any(m.get("question") and m["auteur"] == b["id"] for m in messages))
        self.assertFalse(any(m.get("point_etape") for m in messages))
        self.assertEqual(sum(1 for m in messages if m["auteur"] == "systeme" and "Séance" in m["texte"]), 1)
        self.assertIn("attend ta réponse", salon.statut()["info"])

    def test_equipe_une_panne_n_arrete_pas_les_autres(self):
        projet, (a, b), salon = self.projet("A", "B", mode="equipe", points_etape=False)
        original = fournisseurs.APPELS["simulation"]

        def appel(compte, profil, *reste):
            if profil["id"] == a["id"]:
                raise fournisseurs.ErreurIA("quota épuisé")
            return "Fait.", {}
        fournisseurs.APPELS["simulation"] = appel
        try:
            salon.lancer(2)
            attendre(salon)
        finally:
            fournisseurs.APPELS["simulation"] = original
        messages = stockage.messages(projet["id"])
        self.assertTrue(any(m.get("erreur") and "quota épuisé" in m["texte"] for m in messages))
        self.assertTrue(any(m["auteur"] == b["id"] and m["texte"] == "Fait." for m in messages))

    def test_equipe_faire_parler_reste_une_intervention_seule(self):
        projet, (a, b), salon = self.projet("A", "B", mode="equipe")
        salon.lancer(1, premier=b["id"])
        attendre(salon)
        self.assertEqual([m["auteur"] for m in stockage.messages(projet["id"])], [b["id"]])

    def test_stop_en_mode_equipe(self):
        projet, equipe, salon = self.projet("A", "B", "C", mode="equipe")
        salon.lancer(30)
        time.sleep(0.3)
        salon.arreter()
        attendre(salon)
        self.assertEqual(salon.statut()["info"], "Arrêté.")
        self.assertEqual(salon.statut()["actifs"], [])

    def test_documents_consignes_modele_dans_la_reponse_simulee(self):
        projet, (a,), salon = self.projet("A", consignes_membres={})
        stockage.projets.modifier(projet["id"], {"consignes_membres": {a["id"]: "Sois bref"}})
        stockage.profils.modifier(a["id"], {"modele": "opus", "effort": "high"})
        documents.importer("projet", projet["id"], "charte.txt", b"Couleurs : noir")
        salon.lancer(1)
        attendre(salon)
        texte = stockage.messages(projet["id"])[0]["texte"]
        self.assertIn("charte.txt", texte)
        self.assertIn("consignes pour ce projet", texte)
        self.assertIn("Modèle : opus, effort : high", texte)


class TestServeur(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serveur = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Gestionnaire)
        cls.port = cls.serveur.server_address[1]
        threading.Thread(target=cls.serveur.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.serveur.shutdown()

    def requete(self, methode, chemin, corps=None, entetes=None, brut=False):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{chemin}", method=methode,
            data=json.dumps(corps).encode() if corps is not None else None,
            headers={"Content-Type": "application/json", **(entetes or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return (r.status, r.read(), r.headers) if brut else (r.status, json.loads(r.read() or b"{}"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def ok(self, methode, chemin, corps=None):
        code, r = self.requete(methode, chemin, corps)
        self.assertEqual(code, 200, r)
        return r

    def attendre_fin(self, projet_id):
        orchestrateur.salon(projet_id).attendre(10)

    def test_parcours_complet(self):
        compte = self.ok("POST", "/api/comptes", {"nom": "Simu", "fournisseur": "simulation"})
        projet = self.ok("POST", "/api/projets", {"nom": "Soirée", "type": "evenement", "equipe_auto": compte["id"]})
        noms = [stockage.profils.get(i)["nom"] for i in projet["equipe"]]
        self.assertEqual(noms, catalogue.MODELES_EQUIPES["evenement"])
        second = self.ok("POST", "/api/projets", {"nom": "Autre", "type": "evenement", "equipe_auto": compte["id"]})
        self.assertEqual(second["equipe"], projet["equipe"])  # profils réutilisés, pas dupliqués

        chef, orga = projet["equipe"][:2]
        self.ok("PUT", f"/api/projets/{projet['id']}", {"brief": "Brief", "questions": "parfois",
                                                        "consignes_membres": {orga: "Budget 500 €", chef: "  "}})
        self.assertEqual(stockage.projets.get(projet["id"])["consignes_membres"], {orga: "Budget 500 €"})

        doc = self.ok("POST", "/api/documents", {"cible": "projet", "cible_id": projet["id"], "nom": "lieu.txt",
                                                 "contenu": base64.b64encode("Salle : Le Cabanon".encode()).decode(),
                                                 "membres": [orga, "intrus"]})
        self.assertEqual(doc["membres"], [orga])
        self.assertEqual(self.ok("GET", f"/api/documents/{doc['id']}/texte")["texte"], "Salle : Le Cabanon")
        self.ok("PUT", f"/api/documents/{doc['id']}", {"membres": []})

        message = self.ok("POST", f"/api/projets/{projet['id']}/messages", {"texte": "Juste pour toi", "prive_pour": orga})
        self.assertEqual(message["prive_nom"], "Organisateur")
        self.ok("POST", f"/api/projets/{projet['id']}/lancer", {"tours": 2})
        self.attendre_fin(projet["id"])
        r = self.ok("GET", f"/api/projets/{projet['id']}/messages?depuis=0")
        self.assertEqual([m["auteur"] for m in r["messages"]], ["humain", orga, projet["equipe"][2]])
        self.assertIn("lieu.txt", r["messages"][1]["texte"])

        self.ok("POST", f"/api/projets/{projet['id']}/lancer", {"tours": 1, "orateur": chef})
        self.attendre_fin(projet["id"])
        self.assertEqual(stockage.messages(projet["id"])[-1]["auteur"], chef)

        code, contenu, entetes = self.requete("GET", f"/api/projets/{projet['id']}/export", brut=True)
        self.assertEqual(code, 200)
        self.assertIn("attachment", entetes["Content-Disposition"])
        self.assertIn("(privé pour Organisateur)", contenu.decode("utf-8"))

        self.ok("PUT", f"/api/projets/{projet['id']}/memoire", {"texte": "Décision : 25 octobre"})
        memoire = self.ok("GET", f"/api/projets/{projet['id']}/memoire")
        self.assertEqual(memoire["memoire"], "Décision : 25 octobre")
        self.assertEqual(memoire["historique"][0]["auteur"], "Toi")
        self.ok("POST", f"/api/projets/{projet['id']}/memoire", {})
        self.attendre_fin(projet["id"])
        self.assertEqual(len(self.ok("GET", f"/api/projets/{projet['id']}/memoire")["historique"]), 2)

        self.ok("DELETE", f"/api/profils/{orga}")
        apres = stockage.projets.get(projet["id"])
        self.assertNotIn(orga, apres["equipe"])
        self.assertEqual(apres["consignes_membres"], {})

        self.ok("DELETE", f"/api/projets/{second['id']}")
        self.assertIsNone(stockage.projets.get(second["id"]))

    def test_validations(self):
        compte = self.ok("POST", "/api/comptes", {"nom": "Simu", "fournisseur": "simulation"})
        self.assertEqual(self.requete("POST", "/api/comptes", {"nom": "x", "fournisseur": "inconnu"})[0], 400)
        self.assertEqual(self.requete("POST", "/api/profils", {"nom": "@humain"})[0], 400)
        profil = self.ok("POST", "/api/profils", {"nom": "  @Rédacteur  web ", "compte_id": compte["id"], "web": 1})
        self.assertEqual(profil["nom"], "Rédacteur web")
        self.assertIs(profil["web"], True)
        self.assertEqual(self.requete("POST", "/api/projets", {"nom": "x", "dossier": "relatif/pas/bon"})[0], 400)
        self.assertEqual(self.requete("POST", "/api/projets", {"nom": "x", "type": "inconnu"})[0], 400)
        self.assertEqual(self.requete("POST", "/api/projets", {"nom": "x", "mode": "inconnu"})[0], 400)
        equipe = self.ok("POST", "/api/projets", {"nom": "Équipe", "mode": "equipe", "points_etape": 0})
        self.assertEqual((equipe["mode"], equipe["points_etape"], equipe["statut"]["actifs"]), ("equipe", False, []))
        self.assertEqual(self.requete("POST", "/api/projets", {"nom": ""})[0], 400)
        projet = self.ok("POST", "/api/projets", {"nom": "Vide", "memoire_auto": "999"})
        self.assertEqual(projet["memoire_auto"], 100)
        self.assertEqual(self.requete("POST", f"/api/projets/{projet['id']}/lancer", {})[0], 400)  # équipe vide
        self.assertEqual(self.requete("POST", f"/api/projets/{projet['id']}/messages",
                                      {"texte": "x", "prive_pour": profil["id"]})[0], 400)
        self.assertEqual(self.requete("POST", "/api/documents", {"cible": "projet", "cible_id": projet["id"],
                                                                 "nom": "a.txt", "contenu": "%%%"})[0], 400)
        self.assertEqual(self.requete("GET", f"/api/projets/{projet['id']}/fichier?chemin=../../projets.json")[0], 404)
        params = self.ok("PUT", "/api/parametres", {"nom_utilisateur": "Félix", "inconnu": "x"})
        self.assertEqual(params["nom_utilisateur"], "Félix")
        self.assertNotIn("inconnu", params)
        etat = self.ok("GET", "/api/etat")
        self.assertIn("simulation", etat["fournisseurs"])
        self.assertIn("xhigh", etat["fournisseurs"]["claude_code"]["efforts"])

    def test_repondre_efface_l_attente(self):
        compte = self.ok("POST", "/api/comptes", {"nom": "Simu", "fournisseur": "simulation"})
        profil = self.ok("POST", "/api/profils", {"nom": "Curieux", "compte_id": compte["id"], "role": "Pose une question"})
        projet = self.ok("POST", "/api/projets", {"nom": "Q", "equipe": [profil["id"]]})
        self.ok("POST", f"/api/projets/{projet['id']}/lancer", {"tours": 3})
        self.attendre_fin(projet["id"])
        r = self.ok("GET", f"/api/projets/{projet['id']}/messages")
        self.assertEqual(len(r["messages"]), 1)
        self.assertIn("attend ta réponse", r["statut"]["info"])
        self.ok("POST", f"/api/projets/{projet['id']}/messages", {"texte": "Option 1"})
        self.assertEqual(self.ok("GET", f"/api/projets/{projet['id']}/messages")["statut"]["info"], "")

    def test_document_de_profil(self):
        profil = self.ok("POST", "/api/profils", {"nom": "Charte"})
        doc = self.ok("POST", "/api/documents", {"cible": "profil", "cible_id": profil["id"], "nom": "style.md",
                                                 "contenu": base64.b64encode(b"Phrases courtes").decode()})
        self.assertEqual(doc["caracteres"], 15)
        self.ok("DELETE", f"/api/profils/{profil['id']}")
        self.assertIsNone(stockage.documents.get(doc["id"]))

    def test_protections(self):
        self.assertEqual(self.requete("GET", "/api/etat", entetes={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.requete("POST", "/api/comptes", {"nom": "x", "fournisseur": "simulation"},
                                      entetes={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.requete("GET", "/api/etat", entetes={"Origin": "https://evil.example"})[0], 403)

    def test_site_public(self):
        site = "https://moi.github.io"
        with mock.patch.dict(os.environ, {"ATELIER_SITES": f"{site}/atelier-ia/ http://exemple.com https://"}):
            self.assertEqual(app.sites_autorises(), {site})  # http seulement pour un site de test sur ce PC
            code, _, entetes = self.requete("GET", "/api/etat", entetes={"Origin": site}, brut=True)
            self.assertEqual((code, entetes["Access-Control-Allow-Origin"]), (200, site))
            code, r = self.requete("POST", "/api/comptes", {"nom": "Site", "fournisseur": "simulation"}, entetes={"Origin": site})
            self.assertEqual(code, 200, r)

            preflight = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/comptes", method="OPTIONS", headers={
                "Origin": site, "Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"})
            with urllib.request.urlopen(preflight) as r:
                self.assertEqual(r.status, 204)
                self.assertEqual(r.headers["Access-Control-Allow-Private-Network"], "true")
            preflight.headers["Origin"] = "https://evil.github.io"
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(preflight)

            # le site cherche le moteur : ping lisible partout, mais il dit si ce site est autorisé
            self.assertTrue(self.requete("GET", "/api/ping", entetes={"Origin": site})[1]["autorise"])
            code, _, entetes = self.requete("GET", "/api/ping", entetes={"Origin": "https://evil.github.io"}, brut=True)
            self.assertEqual((code, entetes["Access-Control-Allow-Origin"]), (200, "*"))
            self.assertFalse(self.requete("GET", "/api/ping", entetes={"Origin": "https://evil.github.io"})[1]["autorise"])
            self.assertEqual(self.requete("POST", "/api/comptes", {"nom": "x", "fournisseur": "simulation"},
                                          entetes={"Origin": "https://evil.github.io"})[0], 403)

    def test_port_occupe(self):
        # Sous Windows, sans exclusivité, un second Atelier écoutait le même port sans jamais rien recevoir.
        with self.assertRaises(OSError):
            app.Serveur(("127.0.0.1", self.port), app.Gestionnaire)
        self.assertTrue(app.deja_lance(self.port))

    def test_page_principale(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn("Atelier", r.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
