"""Documents importés : enregistrement, extraction du texte et place dans le contexte des IA.

Le texte est extrait une fois pour toutes à l'import (Word, PowerPoint, Excel, OpenDocument,
PDF si « pypdf » est installé, et tous les fichiers texte). Les IA reçoivent ce texte
directement dans leur contexte : ça marche avec toutes les IA, même sans accès aux fichiers.
Les images et les PDF non lisibles restent consultables par les profils qui ont l'accès lecture.
"""

import io
import mimetypes
import re
import zipfile
import xml.etree.ElementTree as ET

import stockage

TAILLE_MAX = 25 * 1024 * 1024        # 25 Mo par document
TEXTE_MAX_PAR_DOC = 30_000           # caractères recopiés par document dans le contexte
TEXTE_MAX_TOTAL = 60_000             # caractères de documents par intervention, au total
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

try:
    from pypdf import PdfReader
    PDF_DISPONIBLE = True
except ImportError:  # extraction PDF facultative : pip install pypdf
    PDF_DISPONIBLE = False

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def nom_sur(nom):
    nom = re.split(r"[\\/]", nom or "")[-1]
    nom = re.sub(r'[<>:"|?*\x00-\x1f]', "_", nom).strip(" .")
    return nom[:120] or "document"


# --- Extraction du texte --------------------------------------------------------------

def _docx(z):
    racine = ET.fromstring(z.read("word/document.xml"))
    lignes = []
    for p in racine.iter(W + "p"):
        morceaux = []
        for noeud in p.iter():
            if noeud.tag == W + "t":
                morceaux.append(noeud.text or "")
            elif noeud.tag == W + "tab":
                morceaux.append("\t")
            elif noeud.tag in (W + "br", W + "cr"):
                morceaux.append("\n")
        lignes.append("".join(morceaux))
    return "\n".join(lignes)


def _pptx(z):
    diapos = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    parties = []
    for i, nom in enumerate(diapos, 1):
        racine = ET.fromstring(z.read(nom))
        paragraphes = ["".join(t.text or "" for t in p.iter(A + "t")) for p in racine.iter(A + "p")]
        parties.append(f"--- Diapositive {i} ---\n" + "\n".join(x for x in paragraphes if x.strip()))
    return "\n\n".join(parties)


def _xlsx(z):
    partages = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(S + "si"):
            partages.append("".join(t.text or "" for t in si.iter(S + "t")))
    feuilles = sorted((n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                      key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    parties = []
    for i, nom in enumerate(feuilles, 1):
        lignes = []
        for ligne in ET.fromstring(z.read(nom)).iter(S + "row"):
            cellules = []
            for c in ligne.iter(S + "c"):
                v = c.find(S + "v")
                if c.get("t") == "s" and v is not None:
                    cellules.append(partages[int(v.text)])
                elif c.get("t") == "inlineStr":
                    cellules.append("".join(t.text or "" for t in c.iter(S + "t")))
                else:
                    cellules.append(v.text if v is not None and v.text else "")
            lignes.append("\t".join(cellules))
            if len(lignes) >= 2000:
                lignes.append("[… feuille tronquée]")
                break
        parties.append(f"--- Feuille {i} ---\n" + "\n".join(lignes))
    return "\n\n".join(parties)


def _odf(z):
    racine = ET.fromstring(z.read("content.xml"))
    return "\n".join("".join(e.itertext()) for e in racine.iter() if e.tag in (TEXT + "p", TEXT + "h"))


def _pdf(contenu):
    if not PDF_DISPONIBLE:
        return ""
    lecteur = PdfReader(io.BytesIO(contenu))
    pages = []
    for i, page in enumerate(lecteur.pages[:400], 1):
        pages.append(f"--- Page {i} ---\n{page.extract_text() or ''}")
    texte = "\n\n".join(pages)
    return texte if re.search(r"\w", re.sub(r"--- Page \d+ ---", "", texte)) else ""


def _texte_brut(contenu):
    if b"\x00" in contenu[:8000]:
        return ""
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            return contenu.decode(encodage)
        except UnicodeDecodeError:
            continue
    return contenu.decode("latin-1")


def extraire_texte(nom, contenu):
    extension = ("." + nom.rsplit(".", 1)[-1].lower()) if "." in nom else ""
    try:
        if extension in IMAGES:
            return ""
        if extension == ".pdf":
            texte = _pdf(contenu)
        elif extension in (".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods"):
            with zipfile.ZipFile(io.BytesIO(contenu)) as z:
                texte = {".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx}.get(extension, _odf)(z)
        else:
            texte = _texte_brut(contenu)
    except Exception:  # fichier abîmé ou format inattendu : on garde le fichier sans texte
        return ""
    texte = re.sub(r"[ \t]+\n", "\n", texte.replace("\r\n", "\n"))
    return re.sub(r"\n{3,}", "\n\n", texte).strip()


# --- Import et lecture ------------------------------------------------------------------

def importer(cible, cible_id, nom, contenu, membres=None):
    if len(contenu) > TAILLE_MAX:
        raise ValueError(f"Document trop gros ({len(contenu) // 1_000_000} Mo, maximum {TAILLE_MAX // 1_000_000} Mo).")
    nom = nom_sur(nom)
    texte = extraire_texte(nom, contenu)
    doc = stockage.documents.creer({
        "nom": nom, "type": mimetypes.guess_type(nom)[0] or "", "taille": len(contenu),
        "caracteres": len(texte), "cible": cible, "cible_id": cible_id, "membres": membres or [],
    })
    dossier = stockage.dossier_document(doc)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / nom).write_bytes(contenu)
    if texte:
        (dossier / "texte.txt").write_text(texte, encoding="utf-8")
    return doc


def chemin_original(doc):
    return stockage.dossier_document(doc) / doc["nom"]


def texte(doc):
    try:
        return (stockage.dossier_document(doc) / "texte.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def pour(projet, profil):
    """Documents qu'un profil reçoit dans un projet : les siens, puis ceux du projet qui lui sont destinés."""
    siens = stockage.documents_de("profil", profil["id"])
    du_projet = [d for d in stockage.documents_de("projet", projet["id"]) if not d["membres"] or profil["id"] in d["membres"]]
    return siens + du_projet


def dossiers_lisibles(projet, profil):
    """Dossiers de documents à ouvrir en lecture aux profils qui ont accès aux fichiers."""
    dossiers = [stockage.DONNEES / "profils" / profil["id"] / "documents",
                stockage.DONNEES / "projets" / projet["id"] / "documents"]
    return [d for d in dossiers if d.exists()]


def section(docs, acces):
    """Partie du contexte qui présente les documents à une IA."""
    if not docs:
        return ""
    peut_lire = acces in ("lecture", "ecriture")
    lignes = ["## Documents fournis",
              "Appuie-toi sur ces documents quand ils sont utiles et cite-les par leur nom.", ""]
    budget = TEXTE_MAX_TOTAL
    for doc in docs:
        origine = "joint à ton profil" if doc["cible"] == "profil" else "document du projet"
        lignes.append(f"### {doc['nom']} ({origine})")
        contenu = texte(doc)
        chemin = chemin_original(doc)
        if contenu:
            garde = contenu[:max(0, min(TEXTE_MAX_PAR_DOC, budget))]
            budget -= len(garde)
            lignes.append(garde)
            if len(garde) < len(contenu):
                suite = (f"le texte complet est dans {stockage.dossier_document(doc) / 'texte.txt'}"
                         if peut_lire else "demande à l'humain le passage dont tu as besoin")
                lignes.append(f"[… document tronqué ({len(garde)} caractères sur {len(contenu)}) : {suite}]")
        elif peut_lire:
            lignes.append(f"(pas de texte extractible : ouvre le fichier avec ton outil de lecture : {chemin})")
        else:
            lignes.append("(image ou fichier sans texte extractible : tu ne peux pas l'ouvrir ; "
                          "un membre qui a accès aux fichiers peut le décrire)")
        lignes.append("")
    return "\n".join(lignes)
