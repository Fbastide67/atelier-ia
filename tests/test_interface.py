"""Test de l'interface de bout en bout, comme un utilisateur (nécessite Playwright).

    pip install playwright && python -m playwright install chromium
    python -m unittest tests.test_interface -v

Variable facultative : ATELIER_CHROMIUM = chemin d'un Chromium déjà installé.
Le test lance son propre Atelier sur un port libre, avec des données temporaires et
des comptes « Simulation » : il ne consomme aucun abonnement.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

RACINE = Path(__file__).resolve().parent.parent
CAPTURES = os.environ.get("ATELIER_CAPTURES")  # dossier où enregistrer des captures d'écran (facultatif)


def port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(sync_playwright is None, "Playwright n'est pas installé")
class TestInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.donnees = tempfile.mkdtemp(prefix="atelier_ui_")
        cls.port = port_libre()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.serveur = subprocess.Popen(
            [sys.executable, str(RACINE / "app.py"), "--port", str(cls.port), "--pas-de-navigateur"],
            env={**os.environ, "ATELIER_DONNEES": cls.donnees}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.url + "/api/etat")
                break
            except OSError:
                time.sleep(0.1)
        cls.pw = sync_playwright().start()
        cls.navigateur = cls.pw.chromium.launch(executable_path=os.environ.get("ATELIER_CHROMIUM") or None)

    @classmethod
    def tearDownClass(cls):
        cls.navigateur.close()
        cls.pw.stop()
        cls.serveur.terminate()
        cls.serveur.wait(5)

    def api(self, chemin):
        with urllib.request.urlopen(self.url + chemin) as r:
            return json.loads(r.read())

    def capture(self, page, nom, complete=False):
        if CAPTURES:
            page.screenshot(path=str(Path(CAPTURES) / f"{nom}.png"), full_page=complete)

    def nouvelle_page(self, **options):
        page = self.navigateur.new_page(**options)
        page.on("pageerror", lambda e: self.erreurs.append(str(e)))
        page.on("console", lambda m: m.type == "error" and self.erreurs.append(m.text))
        page.on("dialog", lambda d: d.accept())  # confirmations (archiver, supprimer…)
        return page

    def test_parcours_complet(self):
        self.erreurs = []
        page = self.nouvelle_page(viewport={"width": 1440, "height": 900})
        page.goto(self.url)
        expect(page.locator("#projet-vide")).to_be_visible()
        self.capture(page, "01-accueil")

        # Comptes
        page.click(".nav-lien[data-vue=comptes]")
        page.click("#nouveau-compte")
        page.select_option("#dialogue [name=fournisseur]", "simulation")
        expect(page.locator("#ligne-separe")).to_be_hidden()
        page.fill("#dialogue [name=nom]", "Simu")
        page.click("#dialogue-ok")
        expect(page.locator("#liste-comptes")).to_contain_text("Simu")
        page.click("#nouveau-compte")
        page.select_option("#dialogue [name=fournisseur]", "claude_code")
        expect(page.locator("#ligne-separe")).to_be_visible()
        page.fill("#dialogue [name=nom]", "Claude perso")
        page.click("#dialogue-ok")
        expect(page.locator("#liste-comptes")).to_contain_text("Connexion rangée dans")
        self.capture(page, "02-comptes")

        # Réglages généraux
        page.click(".nav-lien[data-vue=reglages]")
        page.fill("#form-parametres [name=nom_utilisateur]", "Félix")
        page.fill("#form-parametres [name=memoire_generale]", "Étudiant ingénieur à Marseille.")
        page.click("#form-parametres button[type=submit]")
        expect(page.locator("#toast")).to_contain_text("Réglages enregistrés")
        self.assertEqual(self.api("/api/etat")["parametres"]["nom_utilisateur"], "Félix")

        # Nouveau projet avec équipe automatique
        page.click(".nav-lien[data-vue=projets]")
        page.click("#nouveau-projet")
        page.fill("#dialogue [name=nom]", "Soirée techno")
        page.select_option("#dialogue [name=type]", "evenement")
        expect(page.locator("#apercu-equipe")).to_contain_text("Organisateur")
        page.select_option("#dialogue [name=compte]", label="Simu")
        page.click("#dialogue-ok")
        expect(page.locator("#panneau-reglages-projet")).to_be_visible()
        expect(page.locator("#equipe-reglages input[type=checkbox]:checked")).to_have_count(5)

        # Réglages du projet : brief, consignes, questions, consignes d'un membre, ordre
        page.fill("#panneau-reglages-projet [name=brief]", "Organiser la soirée du 25 octobre.")
        page.fill("#panneau-reglages-projet [name=consignes]", "Budget serré.")
        page.check("#choix-questions input[value=parfois]")
        ligne = page.locator(".ligne-equipe", has_text="Organisateur")
        ligne.locator("[data-voir-consignes]").click()
        ligne.locator("textarea").fill("Concentre-toi sur la logistique.")
        page.locator(".ligne-equipe").nth(1).locator("[data-sens='-1']").click()  # Organisateur passe en premier
        expect(page.locator(".ligne-equipe").first).to_contain_text("Organisateur")
        expect(page.locator(".ligne-equipe").first.locator("textarea")).to_have_value("Concentre-toi sur la logistique.")
        page.click("#panneau-reglages-projet button[type=submit]")
        expect(page.locator("#toast")).to_contain_text("Projet enregistré")
        projet = self.api("/api/etat")["projets"][0]
        profils = {p["nom"]: p for p in self.api("/api/etat")["profils"]}
        orga, critique = profils["Organisateur"], profils["Critique"]
        self.assertEqual(projet["questions"], "parfois")
        self.assertEqual(projet["equipe"][0], orga["id"])
        self.assertEqual(projet["consignes_membres"], {orga["id"]: "Concentre-toi sur la logistique."})
        self.capture(page, "03-reglages-projet", complete=True)

        # Documents : import, destinataires, aperçu
        page.click(".onglet[data-panneau=documents]")
        page.set_input_files("#fichiers-projet", files=[
            {"name": "lieu.txt", "mimeType": "text/plain", "buffer": "Salle : Le Cabanon, 300 places".encode()}])
        expect(page.locator("#table-documents")).to_contain_text("lieu.txt")
        expect(page.locator("#nb-documents")).to_have_text("1")
        page.locator("#table-documents tr[data-doc]").locator(f"[data-membre='{orga['id']}']").check()
        expect(page.locator("#toast")).to_contain_text("Destinataires mis à jour")
        expect(page.locator("#table-documents [data-tous]")).not_to_be_checked()
        self.assertEqual(self.api("/api/etat")["documents"][0]["membres"], [orga["id"]])
        page.click("#table-documents [data-apercu]")
        expect(page.locator("#apercu-document")).to_contain_text("Le Cabanon")
        self.capture(page, "04-documents")

        # Discussion : message privé puis réponse du destinataire
        page.click(".onglet[data-panneau=discussion]")
        expect(page.locator("#equipe-laterale .membre")).to_have_count(5)
        page.select_option("#destinataire", orga["id"])
        expect(page.locator("#aide-destinataire")).to_contain_text("Seul Organisateur")
        page.fill("#saisie", "Prépare le planning.")
        page.click("#envoyer-lancer")
        expect(page.locator(".message.humain")).to_contain_text("privé → Organisateur")
        reponse = page.locator(".message", has_text="Documents reçus")
        expect(reponse).to_contain_text("lieu.txt", timeout=10000)
        expect(reponse).to_contain_text("consignes pour ce projet")
        expect(page.locator("#lancer")).to_be_enabled(timeout=10000)

        # « Faire parler » un membre précis
        page.locator(".membre", has_text="Critique").locator("[data-parler]").click()
        expect(page.locator(".message .auteur", has_text="Critique")).to_have_count(1, timeout=10000)
        expect(page.locator("#lancer")).to_be_enabled(timeout=10000)

        # Question à l'humain avec options cliquables
        page.click(".nav-lien[data-vue=profils]")
        page.locator(".ligne", has_text="Critique").locator("[data-action=modifier]").click()
        page.fill("#dialogue [name=role]", "Tu poses une question avant de critiquer.")
        page.click("#dialogue-ok")
        expect(page.locator("#toast")).to_contain_text("Profil enregistré")
        page.click(".nav-lien[data-vue=projets]")
        page.locator(".membre", has_text="Critique").locator("[data-parler]").click()
        question = page.locator(".message.question")
        expect(question).to_contain_text("question pour toi", timeout=10000)
        expect(page.locator("#projet-statut")).to_contain_text("attend ta réponse", timeout=10000)
        question.locator("[data-option]").nth(1).click()
        expect(page.locator("#saisie")).to_have_value("Je choisis l'option 2. Une version complète, plus longue à produire")
        page.click("#composer button[value=envoyer]")
        expect(page.locator(".message.humain").last).to_contain_text("option 2")
        expect(page.locator("#projet-statut")).not_to_contain_text("attend ta réponse")
        self.capture(page, "05-discussion")

        # Mémoire : manuelle, par une IA, restauration, automatique
        page.click(".onglet[data-panneau=memoire]")
        page.fill("#memoire-texte", "Date : 25 octobre.")
        page.click("#memoire-enregistrer")
        expect(page.locator("#memoire-historique li")).to_have_count(1)
        page.select_option("#memoire-profil", label="Chef de projet")
        expect(page.locator("#toast")).to_contain_text("Réglage de la mémoire enregistré")
        page.click("#memoire-resumer")
        expect(page.locator("#memoire-texte")).to_have_value(
            "- Simulation : décisions prises jusqu'ici résumées ici.\n- Prochaine étape : à définir.", timeout=10000)
        expect(page.locator("#memoire-historique li")).to_have_count(2)
        page.click("[data-restaurer='1']")
        expect(page.locator("#memoire-texte")).to_have_value("Date : 25 octobre.")
        page.fill("#memoire-auto", "2")
        page.dispatch_event("#memoire-auto", "change")
        expect(page.locator("#toast")).to_contain_text("Réglage de la mémoire enregistré")
        self.assertEqual(self.api("/api/etat")["projets"][0]["memoire_auto"], 2)
        self.capture(page, "06-memoire")
        page.click(".onglet[data-panneau=discussion]")
        page.fill("#saisie", "Continuez.")
        page.click("#envoyer-lancer")
        # 1 mise à jour manuelle + 2 automatiques (toutes les 2 interventions, avant la question du Critique)
        expect(page.locator(".message.systeme", has_text="Mémoire du projet mise à jour")).to_have_count(3, timeout=20000)
        expect(page.locator("#lancer")).to_be_enabled(timeout=20000)

        # Profil : modèle, effort, modèle libre, documents, test
        page.click(".nav-lien[data-vue=profils]")
        page.locator(".ligne", has_text="Chef de projet").locator("[data-action=modifier]").click()
        page.select_option("#dialogue [name=compte_id]", label="Claude perso (Claude)")
        expect(page.locator("#dialogue [name=modele_choix] option")).to_have_count(9)  # défaut + 7 + autre
        page.select_option("#dialogue [name=modele_choix]", "opus")
        page.select_option("#dialogue [name=effort]", "xhigh")
        expect(page.locator("#dialogue [name=web]")).to_be_enabled()
        page.check("#dialogue [name=web]")
        page.fill("#dialogue [name=instructions]", "Toujours un tableau récapitulatif.")
        page.set_input_files("#fichiers-profil", files=[
            {"name": "charte.md", "mimeType": "text/markdown", "buffer": b"Ton direct."}])
        expect(page.locator("#docs-profil")).to_contain_text("charte.md")
        page.click("#dialogue-ok")
        ligne_chef = page.locator(".ligne", has_text="Chef de projet")
        expect(ligne_chef).to_contain_text("opus, effort très élevé")
        expect(ligne_chef).to_contain_text("internet")
        expect(ligne_chef).to_contain_text("1 document")
        chef = next(p for p in self.api("/api/etat")["profils"] if p["nom"] == "Chef de projet")
        self.assertEqual((chef["modele"], chef["effort"], chef["web"]), ("opus", "xhigh", True))
        self.assertEqual(chef["instructions"], "Toujours un tableau récapitulatif.")
        ligne_chef.locator("[data-action=modifier]").click()
        page.select_option("#dialogue [name=compte_id]", label="Simu (Simulation)")
        expect(page.locator("#dialogue [name=web]")).to_be_disabled()
        page.select_option("#dialogue [name=modele_choix]", "__autre")
        page.fill("#dialogue [name=modele_libre]", "mon-modele-perso")
        page.select_option("#dialogue [name=effort]", "high")
        page.click("#dialogue-ok")
        expect(ligne_chef).to_contain_text("mon-modele-perso, effort élevé")
        ligne_chef.locator("[data-action=tester]").click()
        expect(ligne_chef.locator(".resultat.ok")).to_contain_text("Modèle : mon-modele-perso, effort : high", timeout=10000)
        page.select_option("#modele-role", label="Pédagogue")
        expect(page.locator("#dialogue [name=nom]")).to_have_value("Pédagogue")
        page.click("#dialogue-ok")
        expect(page.locator("#liste-profils")).to_contain_text("Pédagogue")
        self.capture(page, "07-profils", complete=True)

        # Livrables
        page.click(".nav-lien[data-vue=projets]")
        page.click(".onglet[data-panneau=fichiers]")
        espace = Path(self.api("/api/etat")["projets"][0]["espace"])
        (espace / "planning.md").write_text("# Planning\n18h : installation", encoding="utf-8")
        page.click("#rafraichir-fichiers")
        page.click("#liste-fichiers button", timeout=5000)
        expect(page.locator("#apercu")).to_contain_text("18h : installation")

        # Export, archivage
        page.click(".onglet[data-panneau=reglages-projet]")
        export = page.request.get(self.url + page.locator("#exporter").get_attribute("href"))
        self.assertIn("Prépare le planning.", export.text())
        self.assertIn("(privé pour Organisateur)", export.text())
        page.click("#archiver-discussion")
        expect(page.locator(".vide-fil")).to_be_visible()

        # Affichage sombre et mobile, sans débordement horizontal
        for nom, options in (("08-sombre", {"viewport": {"width": 1440, "height": 900}, "color_scheme": "dark"}),
                             ("09-mobile", {"viewport": {"width": 390, "height": 844}})):
            autre = self.nouvelle_page(**options)
            autre.goto(self.url)
            expect(autre.locator("#projet-nom")).to_have_text("Soirée techno")
            self.capture(autre, nom)
            largeur = autre.evaluate("document.documentElement.scrollWidth")
            self.assertLessEqual(largeur, options["viewport"]["width"], nom)
            autre.close()

        # Suppression du projet
        page.click(".onglet[data-panneau=reglages-projet]")
        page.click("#supprimer-projet")
        expect(page.locator("#projet-vide")).to_be_visible()
        self.assertEqual(self.api("/api/etat")["projets"], [])
        self.assertEqual(self.erreurs, [])


if __name__ == "__main__":
    unittest.main()
