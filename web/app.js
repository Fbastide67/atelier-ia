"use strict";

const $ = (sel, racine = document) => racine.querySelector(sel);
const $$ = (sel, racine = document) => [...racine.querySelectorAll(sel)];

const etat = {
  d: null,               // réponse de /api/etat
  projetId: null,
  dernierN: 0,
  generation: 0,         // change à chaque changement de projet : ignore les réponses périmées
  minuteur: null,
  panneau: "discussion",
  resultats: {},         // résultats de test / vérification affichés dans les listes
  memoireModifiee: false,
  actifs: [],            // membres qui travaillent en ce moment (mode équipe : plusieurs)
  modelesComptes: {},    // modèles lus sur chaque compte (bouton « Lister les modèles »)
};

const ACCES = {
  aucun: ["Discussion seule", "ne voit pas les fichiers ; le moins gourmand"],
  lecture: ["Lecture", "lit les fichiers et les documents du projet"],
  ecriture: ["Modification", "crée et modifie les fichiers du dossier du projet et y lance des commandes (shell)"],
};
const TACHES = { memoire: "met à jour la mémoire", travaille: "travaille", point: "fait le point d'étape", parle: "écrit" };
const EFFORT_COURT = { minimal: "effort minimal", low: "effort faible", medium: "effort moyen", high: "effort élevé", xhigh: "effort très élevé", max: "effort maximum" };

// --- Utilitaires ------------------------------------------------------------------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// --- Moteur local ---------------------------------------------------------------------
// L'interface est servie soit par le moteur lui-même (http://127.0.0.1:8765), soit par le site public
// (GitHub Pages) : elle cherche alors le moteur installé sur le PC du visiteur, qui lance ses IA.

const PORTS_MOTEUR = [8765, 8766, 8767, 8768, 8769];
let MOTEUR = "";         // adresse du moteur (vide : même adresse que la page)

async function sonderMoteur(base) {
  try {
    const r = await fetch(`${base}/api/ping`, { cache: "no-store" });
    const d = r.ok ? await r.json() : null;
    return d?.atelier === true ? d : null;
  } catch {
    return null;
  }
}

async function accesReseauLocal() {
  // Chrome demande au visiteur l'accès au réseau local (dont son propre PC) avant qu'un site joigne le moteur.
  try {
    return (await navigator.permissions.query({ name: "local-network-access" })).state;
  } catch {
    return null;  // navigateur sans cette autorisation : rien à demander
  }
}

async function trouverMoteur() {
  // Tous les ports en même temps : sous Windows, un port fermé met ~2 s à répondre.
  const bases = PORTS_MOTEUR.map((p) => `http://127.0.0.1:${p}`);
  const reponses = await Promise.all(bases.map(sonderMoteur));
  const i = reponses.findIndex(Boolean);
  return i < 0 ? null : { base: bases[i], autorise: reponses[i].autorise };
}

const adresseSite = () => new URL(".", location.href).href;

function telechargerInstallateur() {
  // Petit lanceur propre à ce site : il récupère installer.ps1 publié à côté de cette page.
  const lignes = [
    "@echo off",
    "chcp 65001 >nul",
    "title Installation de l'Atelier IA",
    `set "ATELIER_SITE=${adresseSite()}"`,
    "powershell -NoProfile -ExecutionPolicy Bypass -Command \"[Net.ServicePointManager]::SecurityProtocol='Tls12'; " +
      "$w=New-Object Net.WebClient; $w.Encoding=[Text.Encoding]::UTF8; iex $w.DownloadString($env:ATELIER_SITE+'installer.ps1')\"",
    "if errorlevel 1 pause",
    "",
  ];
  const lien = document.createElement("a");
  lien.href = URL.createObjectURL(new Blob([lignes.join("\r\n")], { type: "application/octet-stream" }));
  lien.download = "Installer-Atelier.bat";
  lien.click();
  setTimeout(() => URL.revokeObjectURL(lien.href), 10000);
}

let minuteurMoteur;
let rechercheEnCours = false;

function afficherMoteur(cas) {
  const ecran = $("#moteur");
  $$("[data-cas]", ecran).forEach((b) => (b.hidden = b.dataset.cas !== cas));
  $("#moteur-site").textContent = location.origin;
  $("#moteur-etat").textContent = "Recherche du moteur sur ce PC…";
  ecran.hidden = false;
  clearTimeout(minuteurMoteur);
  minuteurMoteur = setTimeout(connecterMoteur, 3000);  // se connecte tout seul dès que le moteur répond
}

// demande : le visiteur a cliqué. Sans clic, on ne déclenche pas la demande d'accès de Chrome
// (sinon un nouveau visiteur la verrait avant même d'avoir installé le moteur).
async function connecterMoteur(demande = false) {
  clearTimeout(minuteurMoteur);
  if (rechercheEnCours) return;
  rechercheEnCours = true;
  try {
    let moteur = location.protocol.startsWith("http") && (await sonderMoteur("")) ? { base: "", autorise: true } : null;
    if (!moteur) {
      const acces = await accesReseauLocal();
      if (acces === "denied") return afficherMoteur("bloque");
      if (acces === "prompt" && !demande) return afficherMoteur(MOTEUR || etat.d ? "perdu" : "absent");
      if (acces === "prompt") $("#moteur-etat").textContent = "Chrome te demande l'accès au réseau local : clique sur « Autoriser ».";
      moteur = await trouverMoteur();
    }
    if (!moteur) return afficherMoteur(MOTEUR || etat.d ? "perdu" : "absent");
    if (!moteur.autorise) return afficherMoteur("refuse");
    const premiere = !etat.d;
    MOTEUR = moteur.base;
    $("#moteur").hidden = true;
    try {
      await charger();
    } catch (e) {
      return toast(`Impossible de joindre l'Atelier : ${e.message}`, true);
    }
    if (premiere && etat.d.projets.length) selectionnerProjet(etat.d.projets[0].id);
    else if (etat.projetId) sonder();
  } finally {
    rechercheEnCours = false;
  }
}

async function api(methode, chemin, corps) {
  let r;
  try {
    r = await fetch(MOTEUR + chemin, {
      method: methode,
      headers: { "Content-Type": "application/json" },
      body: corps === undefined ? undefined : JSON.stringify(corps),
    });
  } catch {
    if ($("#moteur").hidden) connecterMoteur();
    throw new Error("Le moteur de l'Atelier ne répond pas sur ce PC.");
  }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.erreur || `Erreur ${r.status}`);
  return d;
}

let minuteurToast;
function toast(message, erreur = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (erreur ? " erreur" : "");
  t.hidden = false;
  clearTimeout(minuteurToast);
  minuteurToast = setTimeout(() => (t.hidden = true), erreur ? 7000 : 3500);
}

async function essayer(action, succes) {
  try {
    const r = await action();
    if (succes) toast(succes);
    return r;
  } catch (e) {
    toast(e.message, true);
    return null;
  }
}

function enBase64(fichier) {
  return new Promise((ok, ko) => {
    const lecteur = new FileReader();
    lecteur.onload = () => ok(String(lecteur.result).split(",")[1] || "");
    lecteur.onerror = () => ko(new Error(`Lecture impossible : ${fichier.name}`));
    lecteur.readAsDataURL(fichier);
  });
}

const PETITS_MOTS = new Set(["de", "du", "des", "la", "le", "les", "et", "d", "l"]);
function initiales(nom) {
  const mots = String(nom).split(/[\s'’-]+/).filter((m) => m && !PETITS_MOTS.has(m.toLowerCase()));
  return (mots.length > 1 ? mots[0][0] + mots[1][0] : (mots[0] || "?").slice(0, 2)).toUpperCase();
}

const taille = (o) => (o < 1024 ? `${o} o` : o < 1048576 ? `${Math.round(o / 1024)} Ko` : `${(o / 1048576).toFixed(1)} Mo`);
const heure = (iso) => (iso || "").slice(11, 16);
const date = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)} à ${iso.slice(11, 16)}` : "");
const profil = (id) => etat.d.profils.find((p) => p.id === id);
const compte = (id) => etat.d.comptes.find((c) => c.id === id);
const projetCourant = () => etat.d.projets.find((p) => p.id === etat.projetId);
const fournisseurDe = (p) => { const c = p && compte(p.compte_id); return c ? etat.d.fournisseurs[c.fournisseur] : null; };
const equipeDe = (projet) => projet.equipe.map(profil).filter(Boolean);
const docsDe = (cible, id) => etat.d.documents.filter((d) => d.cible === cible && d.cible_id === id);
const prenom = () => etat.d.parametres.nom_utilisateur || "";

function modeleEffort(p) {
  return [p.modele || "modèle par défaut", p.effort && EFFORT_COURT[p.effort]].filter(Boolean).join(", ");
}

function descriptionProfil(p) {
  const c = compte(p.compte_id);
  const f = fournisseurDe(p);
  return [c ? `${c.nom} (${f.court})` : "aucun compte", modeleEffort(p), ACCES[p.acces]?.[0], p.web && f?.web ? "internet" : ""]
    .filter(Boolean).join(" · ");
}

function avatar(p, classe = "avatar") {
  return `<span class="${classe}" style="--couleur:${esc(p.couleur)}" aria-hidden="true">${esc(initiales(p.nom))}</span>`;
}

// --- Rendu du texte des messages (Markdown simplifié) ------------------------------

function regexMentions() {
  const projet = projetCourant();
  const noms = ["Humain", ...(projet ? equipeDe(projet).map((p) => p.nom) : [])]
    .sort((a, b) => b.length - a.length)
    .map((n) => esc(n).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(`@(${noms.join("|")})(?![\\p{L}\\p{N}_])`, "giu");
}

function inline(s, mentions) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/\[À COMPLÉTER[^\]]*\]/g, (m) => `<span class="marqueur">${m}</span>`)
    .replace(mentions, (m) => `<span class="mention">${m}</span>`);
}

function blocs(texte, mentions) {
  let html = "", liste = null, para = [], table = [];
  const fermerPara = () => { if (para.length) { html += `<p>${para.map((l) => inline(l, mentions)).join("<br>")}</p>`; para = []; } };
  const fermerListe = () => { if (liste) { html += `</${liste}>`; liste = null; } };
  const fermerTable = () => {
    if (!table.length) return;
    const lignes = table.filter((l) => !/^\s*\|?\s*:?-{2,}/.test(l)).map((l) => l.trim().replace(/^\||\|$/g, "").split("|"));
    html += "<table>" + lignes.map((cellules, i) => `<tr>${cellules.map((c) => `<${i ? "td" : "th"}>${inline(c.trim(), mentions)}</${i ? "td" : "th"}>`).join("")}</tr>`).join("") + "</table>";
    table = [];
  };
  for (const ligne of texte.split("\n")) {
    const puce = ligne.match(/^\s*[-*•]\s+(.*)/);
    const num = ligne.match(/^\s*\d+[.)]\s+(.*)/);
    const titre = ligne.match(/^#{1,6}\s+(.*)/);
    if (/^\s*\|.*\|\s*$/.test(ligne)) { fermerPara(); fermerListe(); table.push(ligne); continue; }
    fermerTable();
    if (puce || num) {
      fermerPara();
      const type = puce ? "ul" : "ol";
      if (liste !== type) { fermerListe(); html += `<${type}>`; liste = type; }
      html += `<li>${inline((puce || num)[1], mentions)}</li>`;
    } else if (titre) {
      fermerPara(); fermerListe();
      html += `<h4>${inline(titre[1], mentions)}</h4>`;
    } else if (!ligne.trim()) {
      fermerPara(); fermerListe();
    } else {
      fermerListe();
      para.push(ligne);
    }
  }
  fermerTable(); fermerPara(); fermerListe();
  return html;
}

function rendreTexte(texte) {
  const mentions = regexMentions();
  return texte.split("```").map((partie, i) => {
    if (i % 2 === 0) return blocs(partie, mentions);
    const code = partie.includes("\n") ? partie.slice(partie.indexOf("\n") + 1) : partie;
    return `<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`;
  }).join("");
}

// --- Navigation ----------------------------------------------------------------------

function changerVue(vue) {
  $$(".nav-lien").forEach((b) => b.classList.toggle("actif", b.dataset.vue === vue));
  $$(".vue").forEach((v) => (v.hidden = v.id !== `vue-${vue}`));
  ({ profils: rendreProfils, comptes: rendreComptes, reglages: remplirParametres }[vue] || (() => {}))();
}

function changerPanneau(panneau) {
  etat.panneau = panneau;
  $$(".onglet").forEach((b) => b.classList.toggle("actif", b.dataset.panneau === panneau));
  $$(".panneau").forEach((p) => (p.hidden = p.id !== `panneau-${panneau}`));
  ({ documents: rendreDocuments, fichiers: chargerFichiers, memoire: chargerMemoire, "reglages-projet": remplirReglages }[panneau] || (() => {}))();
}

async function charger() {
  etat.d = await api("GET", "/api/etat");
  rendreListeProjets();
  if (!$("#vue-profils").hidden) rendreProfils();
  if (!$("#vue-comptes").hidden) rendreComptes();
  if (projetCourant()) rendreProjetFixe();
}

// --- Projets --------------------------------------------------------------------------

function rendreListeProjets() {
  const ul = $("#liste-projets");
  ul.innerHTML = etat.d.projets.map((p) => `
    <li><button data-id="${p.id}" class="${p.id === etat.projetId ? "actif" : ""}">
      <span class="nom">${p.statut.en_cours ? '<span class="point" title="Discussion en cours"></span>' : ""}${esc(p.nom)}</span>
      <span class="type">${esc(etat.d.types_projet[p.type] || "")}</span>
    </button></li>`).join("") || `<li class="aide" style="padding:0 16px">Aucun projet pour l'instant.</li>`;
  $$("button", ul).forEach((b) => (b.onclick = () => selectionnerProjet(b.dataset.id)));
}

function selectionnerProjet(id, panneau) {
  etat.projetId = id;
  etat.dernierN = 0;
  etat.generation++;
  etat.memoireModifiee = false;
  etat.actifs = [];
  clearTimeout(etat.minuteur);
  $("#fil").innerHTML = "";
  $("#projet-vide").hidden = !!id;
  $("#projet-contenu").hidden = !id;
  rendreListeProjets();
  if (!id) return;
  rendreProjetFixe();
  changerPanneau(panneau || (projetCourant().equipe.length ? etat.panneau : "reglages-projet"));
  sonder();
}

function rendreProjetFixe() {
  const p = projetCourant();
  $("#projet-nom").textContent = p.nom;
  $("#projet-type").textContent = etat.d.types_projet[p.type] || "";
  $("#nb-documents").textContent = docsDe("projet", p.id).length || "";
  $("#exporter").href = `${MOTEUR}/api/projets/${p.id}/export`;
  rendreEquipeLaterale();
  rendreDestinataires();
}

function rendreEquipeLaterale(force = true) {
  const p = projetCourant();
  const equipe = equipeDe(p);
  const actifs = Object.fromEntries(etat.actifs.map((a) => [a.id, a]));
  // Le sondage repasse ici toutes les 1,5 s : on ne reconstruit que si quelque chose a changé
  // (sinon le focus clavier et les clics en cours sur ces boutons seraient perdus).
  const signature = JSON.stringify([p.id, p.equipe, p.questions, p.mode, etat.actifs.map((a) => [a.id, a.tache, a.rapport])]);
  if (!force && signature === etat.signatureEquipe) return;
  etat.signatureEquipe = signature;
  $("#equipe-laterale").innerHTML = equipe.map((m) => `
    <li class="membre ${actifs[m.id] ? (actifs[m.id].tache === "parle" ? "parle" : "travaille") : ""}">
      ${avatar(m)}<span class="nom">${esc(m.nom)}</span>
      <span class="detail">${esc((fournisseurDe(m)?.court || "sans compte") + " · " + modeleEffort(m))}</span>
      ${actifs[m.id]?.rapport ? `<span class="rapport-direct">${esc(actifs[m.id].rapport.slice(0, 160))}</span>` : ""}
      <span class="actions">
        <button class="bouton texte" data-parler="${m.id}">Faire parler</button>
        <button class="bouton texte" data-prive="${m.id}">Message privé</button>
      </span>
    </li>`).join("") || `<li class="aide">Aucun membre : choisis l'équipe dans Réglages.</li>`;
  $$("[data-parler]").forEach((b) => (b.onclick = () => lancer(1, b.dataset.parler)));
  $$("[data-prive]").forEach((b) => (b.onclick = () => {
    $("#destinataire").value = b.dataset.prive;
    majDestinataire();
    $("#saisie").focus();
  }));
  const q = etat.d.questions[p.questions];
  $("#questions-resume").innerHTML = `Travail : <b>${esc(etat.d.modes[p.mode] || "")}</b>.<br>` +
    `Questions : <b>${esc(q?.nom || "")}</b>. ${esc(q?.aide || "")}`;
  $("#mentions").innerHTML = equipe.map((m) => `<button type="button" data-nom="${esc(m.nom)}">@${esc(m.nom)}</button>`).join("");
  $$("#mentions button").forEach((b) => (b.onclick = () => {
    const zone = $("#saisie");
    zone.value = `${zone.value}${zone.value && !/\s$/.test(zone.value) ? " " : ""}@${b.dataset.nom} `;
    zone.focus();
  }));
}

function rendreDestinataires() {
  const select = $("#destinataire");
  const actuel = select.value;
  select.innerHTML = `<option value="">Toute l'équipe</option>` +
    equipeDe(projetCourant()).map((m) => `<option value="${m.id}">${esc(m.nom)} (privé)</option>`).join("");
  select.value = [...select.options].some((o) => o.value === actuel) ? actuel : "";
  majDestinataire();
}

function majDestinataire() {
  const id = $("#destinataire").value;
  const equipe = projetCourant()?.mode === "equipe";
  $("#aide-destinataire").textContent = id
    ? `Seul ${profil(id).nom} verra ce message, et c'est lui qui répondra.`
    : equipe
      ? "Mode équipe : « Envoyer et lancer » fait travailler toute l'équipe en même temps. Pour un seul membre : message privé ou « Faire parler »."
      : "Écris @Nom pour donner la parole à quelqu'un.";
}

// Discussion ------------------------------------------------------------------------------

function nomAuteur(m) {
  if (m.auteur === "humain") return prenom() || "Toi";
  return m.nom;
}

function rendreMessage(m) {
  const div = document.createElement("div");
  if (m.auteur === "systeme") {
    div.className = `message systeme ${m.erreur ? "erreur" : ""}`;
    div.innerHTML = `<span class="texte-systeme">${esc(m.texte)}</span>`;
    return div;
  }
  const p = profil(m.auteur);
  div.className = `message ${m.auteur === "humain" ? "humain" : ""} ${m.question ? "question" : ""} ${m.rapport ? "rapport" : ""} ${m.point_etape ? "point-etape" : ""}`;
  const meta = p && !m.rapport ? [fournisseurDe(p)?.court, p.modele, p.effort && EFFORT_COURT[p.effort], m.duree && `${Math.round(m.duree)} s`].filter(Boolean).join(" · ") : "";
  const etiquettes = [
    m.prive_pour ? `<span class="etiquette">privé → ${esc(m.prive_nom || profil(m.prive_pour)?.nom || "?")}</span>` : "",
    m.rapport ? `<span class="etiquette">mini-rapport en cours de travail</span>` : "",
    m.point_etape ? `<span class="etiquette accent">point d'étape</span>` : "",
    m.seance && !m.point_etape ? `<span class="etiquette">séance ${m.seance}</span>` : "",
    m.question ? `<span class="etiquette accent">question pour toi</span>` : "",
  ].join("");
  const options = m.question ? [...m.texte.matchAll(/^\s*(\d+)[.)]\s+(.+)$/gm)].slice(0, 8) : [];
  div.innerHTML = `
    ${p ? avatar(p) : `<span class="avatar">${esc(initiales(nomAuteur(m)))}</span>`}
    <div>
      <div class="tete"><span class="auteur">${esc(nomAuteur(m))}</span>${etiquettes}
        <span class="meta">${heure(m.date)}${meta ? " · " + esc(meta) : ""}</span><span class="numero">#${m.n}</span></div>
      <div class="corps">${rendreTexte(m.texte)}</div>
      ${options.length ? `<div class="reponses">${options.map((o) => `<button class="bouton petit" data-option="${esc(o[1] + ". " + o[2])}">${esc(o[1])}. ${esc(o[2].slice(0, 60))}</button>`).join("")}</div>` : ""}
    </div>`;
  $$("[data-option]", div).forEach((b) => (b.onclick = () => {
    $("#saisie").value = `Je choisis l'option ${b.dataset.option}`;
    $("#saisie").focus();
  }));
  return div;
}

function afficherStatut(statut) {
  const enCours = statut.en_cours;
  $("#lancer").disabled = enCours;
  $("#tour-suivant").disabled = enCours;
  $("#stop").hidden = !enCours;
  const bandeau = $("#en-cours");
  etat.actifs = (statut.actifs || []).filter((a) => profil(a.id));
  bandeau.hidden = !enCours;
  if (enCours) {
    bandeau.innerHTML = etat.actifs.length
      ? etat.actifs.map((a) => `<span class="actif-ligne"><span class="pulsation"></span><b>${esc(profil(a.id).nom)}</b>
          ${TACHES[a.tache] || "écrit"}… <span>${a.depuis} s</span>${a.rapport ? ` <span class="dernier-rapport">— ${esc(a.rapport.slice(0, 120))}</span>` : ""}</span>`).join("")
      : `<span class="actif-ligne"><span class="pulsation"></span>Préparation du tour suivant…</span>`;
  }
  const s = $("#projet-statut");
  const attente = !enCours && /attend(ent)? ta réponse/.test(statut.info || "");
  s.className = "statut" + (enCours ? " actif" : attente ? " attente" : "");
  s.textContent = enCours
    ? (etat.actifs.length > 1 ? `L'équipe travaille en même temps (${etat.actifs.length} membres)` : "Discussion en cours")
    : statut.info || "En attente : écris un message ou lance la discussion.";
  rendreEquipeLaterale(false);
  const p = projetCourant();
  if (p && p.statut.en_cours !== enCours) {
    p.statut = statut;
    rendreListeProjets();
    if (!enCours) charger();  // les IA ont pu créer des fichiers ou changer la mémoire
  }
}

async function sonder() {
  clearTimeout(etat.minuteur);
  const generation = etat.generation;
  const id = etat.projetId;
  if (!id) return;
  try {
    const r = await api("GET", `/api/projets/${id}/messages?depuis=${etat.dernierN}`);
    if (generation !== etat.generation) return;
    const fil = $("#fil");
    const enBas = fil.scrollHeight - fil.scrollTop - fil.clientHeight < 120;
    const nouveaux = r.messages.filter((m) => m.n > etat.dernierN);
    if (nouveaux.length) $(".vide-fil", fil)?.remove();
    for (const m of nouveaux) {
      fil.appendChild(rendreMessage(m));
      etat.dernierN = m.n;
    }
    if (!fil.children.length) {
      fil.innerHTML = `<div class="vide-fil"><p><b>La discussion n'a pas commencé.</b></p>
        <p>Écris une première consigne à l'équipe, ou clique sur « Lancer » : le premier membre ouvrira la discussion à partir du brief.</p></div>`;
    } else if (nouveaux.length && enBas) {
      fil.scrollTop = fil.scrollHeight;
    }
    afficherStatut(r.statut);
    majMemoire(r.memoire);
  } catch (e) {
    if (generation !== etat.generation) return;
    $("#projet-statut").textContent = "Connexion à l'Atelier perdue : le moteur est-il lancé sur ce PC ?";
    if (!$("#moteur").hidden) return;  // l'écran du moteur relancera le sondage une fois reconnecté
  }
  if (generation === etat.generation) etat.minuteur = setTimeout(sonder, 1500);
}

function majMemoire(memoire) {
  const p = projetCourant();
  if (!p || memoire === p.memoire) return;
  p.memoire = memoire;
  if (!etat.memoireModifiee) $("#memoire-texte").value = memoire;
  if (etat.panneau === "memoire") chargerHistorique();
}

async function envoyer(e) {
  e.preventDefault();
  const lancerApres = (e.submitter?.value || "lancer") === "lancer";
  const zone = $("#saisie");
  const texte = zone.value.trim();
  const prive = $("#destinataire").value;
  if (texte) {
    const ok = await essayer(() => api("POST", `/api/projets/${etat.projetId}/messages`, { texte, prive_pour: prive || null }));
    if (!ok) return;
    zone.value = "";
    $("#destinataire").value = "";
    majDestinataire();
  } else if (!lancerApres) {
    return;
  }
  if (lancerApres) await lancer(prive ? 1 : undefined, prive || undefined);
  sonder();
}

async function lancer(tours, orateur) {
  const p = projetCourant();
  if (!p.equipe.length) {
    changerPanneau("reglages-projet");
    return toast("Choisis d'abord l'équipe du projet.", true);
  }
  await essayer(() => api("POST", `/api/projets/${p.id}/lancer`, { tours, orateur }));
  sonder();
}

// Documents du projet -------------------------------------------------------------------

function rendreDocuments() {
  const p = projetCourant();
  $("#alerte-pdf").hidden = etat.d.pdf_disponible;
  const equipe = equipeDe(p);
  const docs = docsDe("projet", p.id);
  $("#nb-documents").textContent = docs.length || "";
  $("#table-documents tbody").innerHTML = docs.map((d) => `
    <tr data-doc="${d.id}">
      <td><div class="nom-doc">${esc(d.nom)}</div><div class="aide">${taille(d.taille)} · ajouté le ${date(d.cree_le)}</div></td>
      <td class="aide">${d.caracteres ? `${d.caracteres.toLocaleString("fr-FR")} caractères` : "aucun texte (image ou PDF)"}</td>
      <td><div class="destinataires">
        <label><input type="checkbox" data-tous ${d.membres.length ? "" : "checked"}> Toute l'équipe</label>
        ${equipe.map((m) => `<label><input type="checkbox" data-membre="${m.id}" ${d.membres.includes(m.id) ? "checked" : ""}> ${esc(m.nom)}</label>`).join("")}
      </div></td>
      <td>
        ${d.caracteres ? `<button class="bouton texte" data-apercu>Aperçu</button>` : ""}
        <button class="bouton texte" data-ouvrir>Ouvrir</button>
        <button class="bouton texte" data-supprimer>Retirer</button>
      </td>
    </tr>`).join("") || `<tr><td colspan="4" class="aide">Aucun document. Les documents donnent du contexte aux IA : cahier des charges, notes, exemples, chartes, données…</td></tr>`;

  $$("#table-documents tr[data-doc]").forEach((tr) => {
    const id = tr.dataset.doc;
    const tous = $("[data-tous]", tr);
    const membres = $$("[data-membre]", tr);
    const enregistrer = async () => {
      const choisis = membres.filter((c) => c.checked).map((c) => c.dataset.membre);
      tous.checked = !choisis.length;
      await essayer(() => api("PUT", `/api/documents/${id}`, { membres: choisis }), "Destinataires mis à jour.");
      await charger();
    };
    tous.onchange = () => { membres.forEach((c) => (c.checked = false)); enregistrer(); };
    membres.forEach((c) => (c.onchange = enregistrer));
    const apercu = $("[data-apercu]", tr);
    if (apercu) apercu.onclick = async () => {
      const r = await essayer(() => api("GET", `/api/documents/${id}/texte`));
      if (!r) return;
      const pre = $("#apercu-document");
      pre.hidden = false;
      pre.textContent = r.texte + (r.tronque ? "\n\n[… aperçu tronqué]" : "");
      pre.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    $("[data-ouvrir]", tr).onclick = () => essayer(() => api("POST", `/api/documents/${id}/ouvrir`));
    $("[data-supprimer]", tr).onclick = async () => {
      if (!confirm("Retirer ce document du projet ?")) return;
      await essayer(() => api("DELETE", `/api/documents/${id}`), "Document retiré.");
      $("#apercu-document").hidden = true;
      await charger();
      rendreDocuments();
    };
  });
}

async function importerFichiers(cible, cibleId, fichiers) {
  let n = 0;
  for (const f of fichiers) {
    toast(`Import de ${f.name}…`);
    const contenu = await essayer(() => enBase64(f));
    if (contenu === null) continue;
    const doc = await essayer(() => api("POST", "/api/documents", { cible, cible_id: cibleId, nom: f.name, contenu }));
    if (doc) {
      n++;
      if (!doc.caracteres && /\.pdf$/i.test(doc.nom) && !etat.d.pdf_disponible) {
        toast(`${doc.nom} : texte du PDF non extrait (voir l'encadré « Extraction des PDF »).`, true);
      }
    }
  }
  await charger();
  if (n) toast(n > 1 ? `${n} documents importés.` : "Document importé.");
  return n;
}

// Livrables ---------------------------------------------------------------------------------

async function chargerFichiers() {
  const p = projetCourant();
  $("#chemin-espace").textContent = p.espace;
  const r = await essayer(() => api("GET", `/api/projets/${p.id}/fichiers`));
  if (!r) return;
  const ul = $("#liste-fichiers");
  ul.innerHTML = r.fichiers.map((f) => `<li><button data-chemin="${esc(f.chemin)}">${esc(f.chemin)}<span class="taille">${taille(f.taille)}</span></button></li>`).join("")
    || `<li class="vide">Aucun livrable pour l'instant. Les profils qui ont l'accès « Modification » écrivent ici.</li>`;
  $$("button", ul).forEach((b) => (b.onclick = async () => {
    $$("button", ul).forEach((x) => x.classList.toggle("actif", x === b));
    const f = await essayer(() => api("GET", `/api/projets/${p.id}/fichier?chemin=${encodeURIComponent(b.dataset.chemin)}`));
    if (f) $("#apercu").textContent = f.contenu ?? f.info;
  }));
}

// Mémoire ------------------------------------------------------------------------------------

async function chargerMemoire() {
  const p = projetCourant();
  if (!etat.memoireModifiee) $("#memoire-texte").value = p.memoire;
  $("#memoire-auto").value = p.memoire_auto;
  $("#memoire-profil").innerHTML = equipeDe(p).map((m) => `<option value="${m.id}">${esc(m.nom)}</option>`).join("")
    || `<option value="">(aucun membre)</option>`;
  $("#memoire-profil").value = p.memoire_profil && p.equipe.includes(p.memoire_profil) ? p.memoire_profil : (p.equipe[0] || "");
  await chargerHistorique();
}

async function chargerHistorique() {
  const r = await essayer(() => api("GET", `/api/projets/${etat.projetId}/memoire`));
  if (!r) return;
  $("#memoire-historique").innerHTML = r.historique.map((h, i) => `
    <li><div class="tete"><b>${esc(h.auteur)}</b><span>${date(h.date)}</span><span class="espaceur"></span>
      ${i ? `<button class="bouton texte" data-restaurer="${i}">Restaurer</button>` : `<span class="aide">version actuelle</span>`}</div>
      <div class="extrait">${esc(h.texte)}</div></li>`).join("") || `<li class="aide">Aucune version enregistrée pour l'instant.</li>`;
  $$("[data-restaurer]").forEach((b) => (b.onclick = async () => {
    const texte = r.historique[+b.dataset.restaurer].texte;
    if (await essayer(() => api("PUT", `/api/projets/${etat.projetId}/memoire`, { texte }), "Version restaurée.")) {
      etat.memoireModifiee = false;
      projetCourant().memoire = texte;
      $("#memoire-texte").value = texte;
      chargerHistorique();
    }
  }));
}

async function enregistrerMemoire() {
  const texte = $("#memoire-texte").value;
  if (await essayer(() => api("PUT", `/api/projets/${etat.projetId}/memoire`, { texte }), "Mémoire enregistrée.")) {
    etat.memoireModifiee = false;
    projetCourant().memoire = texte;
    chargerHistorique();
  }
}

async function reglageMemoire() {
  const corps = { memoire_auto: +$("#memoire-auto").value || 0, memoire_profil: $("#memoire-profil").value };
  if (await essayer(() => api("PUT", `/api/projets/${etat.projetId}`, corps), "Réglage de la mémoire enregistré.")) await charger();
}

// Réglages du projet --------------------------------------------------------------------------

let ordreEquipe = [];

function remplirReglages() {
  const p = projetCourant();
  const form = $("#panneau-reglages-projet");
  $("#select-type").innerHTML = Object.entries(etat.d.types_projet).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  for (const champ of ["nom", "type", "brief", "consignes", "tours_par_lancement", "historique_max", "dossier"]) {
    form.elements[champ].value = p[champ] ?? "";
  }
  form.elements.dossier.placeholder = p.espace;
  $("#choix-questions").innerHTML = Object.entries(etat.d.questions).map(([k, q]) => `
    <label><span class="titre"><input type="radio" name="questions" value="${k}" ${p.questions === k ? "checked" : ""}>${esc(q.nom)}</span>
      <span class="aide">${esc(q.aide)}</span></label>`).join("");
  const AIDE_MODES = {
    tour: "Une IA à la fois, dans l'ordre de l'équipe ou selon les @mentions.",
    equipe: "Toutes les IA travaillent en même temps, envoient des mini-rapports pendant leur travail, puis le coordinateur fait le point.",
  };
  $("#choix-mode").innerHTML = Object.entries(etat.d.modes).map(([k, nom]) => `
    <label><span class="titre"><input type="radio" name="mode" value="${k}" ${(p.mode || "tour") === k ? "checked" : ""}>${esc(nom)}</span>
      <span class="aide">${esc(AIDE_MODES[k] || "")}</span></label>`).join("");
  form.elements.points_etape.checked = p.points_etape !== false;
  ordreEquipe = [...p.equipe.filter(profil), ...etat.d.profils.map((x) => x.id).filter((id) => !p.equipe.includes(id))];
  rendreEquipeReglages(new Set(p.equipe), { ...p.consignes_membres });
  rendreCoordinateur(p.coordinateur);
  majMode();
}

function rendreCoordinateur(actuel) {
  const select = $("#select-coordinateur");
  const choisis = new Set($$("#equipe-reglages input[type=checkbox]:checked").map((c) => c.value));
  const membres = (choisis.size ? ordreEquipe.filter((id) => choisis.has(id)) : projetCourant().equipe).map(profil).filter(Boolean);
  const valeur = actuel ?? select.value;
  select.innerHTML = `<option value="">Le premier de l'équipe</option>` +
    membres.map((m) => `<option value="${m.id}">${esc(m.nom)}</option>`).join("");
  select.value = membres.some((m) => m.id === valeur) ? valeur : "";
}

function majMode() {
  const equipe = $("#panneau-reglages-projet").elements.mode.value === "equipe";
  $("#reglages-equipe").hidden = !equipe;
  $("#libelle-tours").firstChild.textContent = equipe ? "Interventions par clic sur « Lancer » (réparties entre les membres) " : "Interventions par clic sur « Lancer » ";
}

function lireEquipeReglages() {
  const coches = new Set($$("#equipe-reglages input[type=checkbox]:checked").map((c) => c.value));
  const consignes = {};
  $$("#equipe-reglages textarea").forEach((t) => { if (t.value.trim()) consignes[t.dataset.consignes] = t.value; });
  return { coches, consignes };
}

function rendreEquipeReglages(coches, consignes) {
  const div = $("#equipe-reglages");
  if (!etat.d.profils.length) {
    div.innerHTML = `<p class="aide">Aucun profil. Crée-en dans « Profils ».</p>`;
    return;
  }
  div.innerHTML = ordreEquipe.map((id, i) => {
    const p = profil(id);
    const texte = consignes[id] || "";
    return `<div class="ligne-equipe">
      <div class="haut">
        <input type="checkbox" id="eq-${id}" value="${id}" ${coches.has(id) ? "checked" : ""}>
        <label class="nom" for="eq-${id}"><span class="mini-avatar" style="--couleur:${esc(p.couleur)}"></span><b>${esc(p.nom)}</b>
          <span class="detail">${esc(descriptionProfil(p))}</span></label>
        <button type="button" class="bouton texte" data-voir-consignes="${id}">Consignes${texte ? " (1)" : ""}</button>
        <button type="button" class="bouton petit" data-bouger="${i}" data-sens="-1" ${i === 0 ? "disabled" : ""} aria-label="Monter">↑</button>
        <button type="button" class="bouton petit" data-bouger="${i}" data-sens="1" ${i === ordreEquipe.length - 1 ? "disabled" : ""} aria-label="Descendre">↓</button>
      </div>
      <textarea rows="2" data-consignes="${id}" placeholder="Consignes pour ${esc(p.nom)} dans ce projet uniquement" ${texte ? "" : "hidden"}>${esc(texte)}</textarea>
    </div>`;
  }).join("");
  $$("[data-bouger]", div).forEach((b) => (b.onclick = () => {
    const { coches, consignes } = lireEquipeReglages();
    const i = +b.dataset.bouger, j = i + +b.dataset.sens;
    [ordreEquipe[i], ordreEquipe[j]] = [ordreEquipe[j], ordreEquipe[i]];
    rendreEquipeReglages(coches, consignes);
    rendreCoordinateur();
  }));
  $$("input[type=checkbox]", div).forEach((c) => (c.onchange = () => rendreCoordinateur()));
  $$("[data-voir-consignes]", div).forEach((b) => (b.onclick = () => {
    const zone = $(`textarea[data-consignes="${b.dataset.voirConsignes}"]`, div);
    zone.hidden = !zone.hidden;
    if (!zone.hidden) zone.focus();
  }));
}

async function enregistrerReglages(e) {
  e.preventDefault();
  const form = e.target;
  const { coches, consignes } = lireEquipeReglages();
  const corps = {
    nom: form.elements.nom.value.trim(),
    type: form.elements.type.value,
    brief: form.elements.brief.value,
    consignes: form.elements.consignes.value,
    questions: form.elements.questions.value || "normal",
    mode: form.elements.mode.value || "tour",
    points_etape: form.elements.points_etape.checked,
    coordinateur: form.elements.coordinateur.value,
    tours_par_lancement: +form.elements.tours_par_lancement.value || 6,
    historique_max: +form.elements.historique_max.value || 12,
    dossier: form.elements.dossier.value.trim(),
    equipe: ordreEquipe.filter((id) => coches.has(id)),
    consignes_membres: consignes,
  };
  if (await essayer(() => api("PUT", `/api/projets/${etat.projetId}`, corps), "Projet enregistré.")) {
    await charger();
    remplirReglages();
  }
}

function nouveauProjet() {
  const comptes = etat.d.comptes;
  const types = Object.entries(etat.d.types_projet).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  ouvrirDialogue("Nouveau projet", `
    <label class="large">Nom du projet <input name="nom" required placeholder="Ex. Soirée du 25 octobre, Mémoire de fin d'études, Appli de révisions…"></label>
    <label class="large">Type de projet <select name="type">${types}</select></label>
    <fieldset class="large"><legend>Équipe</legend>
      <label class="case"><input type="radio" name="equipe" value="auto" ${comptes.length ? "checked" : "disabled"}>
        <span>Créer une équipe adaptée avec le compte
          <select name="compte" ${comptes.length ? "" : "disabled"}>${comptes.map((c) => `<option value="${c.id}">${esc(c.nom)}</option>`).join("")}</select>
          <span class="aide" id="apercu-equipe"></span></span></label>
      <label class="case"><input type="radio" name="equipe" value="manuel" ${comptes.length ? "" : "checked"}>
        <span>Je choisirai l'équipe moi-même${comptes.length ? "" : " <span class=\"aide\">(ajoute d'abord un compte pour l'équipe automatique)</span>"}</span></label>
    </fieldset>`, async (fd) => {
    const corps = { nom: fd.get("nom"), type: fd.get("type") };
    if (fd.get("equipe") === "auto") corps.equipe_auto = fd.get("compte");
    const p = await api("POST", "/api/projets", corps);
    await charger();
    selectionnerProjet(p.id, "reglages-projet");
    toast("Projet créé : écris le brief, puis lance la discussion.");
  }, "Créer");
  const select = $("#dialogue [name=type]");
  const apercu = () => { $("#apercu-equipe").textContent = (etat.d.modeles_equipes[select.value] || []).join(", "); };
  select.onchange = apercu;
  apercu();
}

// --- Profils ----------------------------------------------------------------------------------

function rendreProfils() {
  $("#modele-role").innerHTML = `<option value="">Partir d'un rôle prêt à l'emploi…</option>` +
    etat.d.modeles_roles.map((m, i) => `<option value="${i}">${esc(m.nom)}</option>`).join("");
  const liste = $("#liste-profils");
  liste.innerHTML = etat.d.profils.map((p) => {
    const r = etat.resultats[p.id];
    const docs = docsDe("profil", p.id).length;
    return `<div class="ligne">
      ${avatar(p)}
      <div>
        <div class="titre">${esc(p.nom)}${docs ? `<span class="etiquette">${docs} document${docs > 1 ? "s" : ""}</span>` : ""}</div>
        <div class="sous">${esc(descriptionProfil(p))}</div>
        <div class="role">${esc(p.role || "(pas de rôle défini)")}</div>
      </div>
      <div class="boutons">
        <button class="bouton petit" data-action="modifier" data-id="${p.id}">Modifier</button>
        <button class="bouton petit" data-action="tester" data-id="${p.id}">Tester</button>
        <button class="bouton petit" data-action="dupliquer" data-id="${p.id}">Dupliquer</button>
        <button class="bouton petit danger" data-action="supprimer" data-id="${p.id}">Supprimer</button>
      </div>
      ${r ? `<div class="resultat ${r.ok ? "ok" : "ko"}">${esc(r.texte)}</div>` : ""}
    </div>`;
  }).join("") || `<p class="aide" style="padding:16px 0">Aucun profil. Pars d'un rôle prêt à l'emploi (menu ci-dessus) ou crée un nouveau projet : l'Atelier propose une équipe adaptée.</p>`;
  $$("[data-action]", liste).forEach((b) => (b.onclick = () => actionProfil(b.dataset.action, b.dataset.id, b)));
}

async function actionProfil(action, id, bouton) {
  const p = profil(id);
  if (action === "modifier") return editerProfil(p);
  if (action === "dupliquer") return editerProfil({ ...p, id: undefined, nom: `${p.nom} 2` });
  if (action === "supprimer") {
    if (!confirm(`Supprimer le profil « ${p.nom} » ? Il sera retiré des équipes, et ses documents supprimés.`)) return;
    await essayer(() => api("DELETE", `/api/profils/${id}`), "Profil supprimé.");
    return charger();
  }
  if (action === "tester") {
    bouton.disabled = true;
    bouton.textContent = "Test en cours…";
    const r = await essayer(() => api("POST", `/api/profils/${id}/tester`));
    etat.resultats[id] = r || { ok: false, texte: "échec de la requête" };
    rendreProfils();
  }
}

function optionsModeles(f, actuel, supplementaires = []) {
  const modeles = [...f.modeles, ...supplementaires.filter((m) => !f.modeles.some((x) => x[0] === m)).map((m) => [m, m])];
  const connu = !actuel || modeles.some((m) => m[0] === actuel);
  return `<option value="">Par défaut (réglage de l'outil)</option>` +
    modeles.map(([id, nom]) => `<option value="${esc(id)}" ${id === actuel ? "selected" : ""}>${esc(nom)}</option>`).join("") +
    `<option value="__autre" ${connu ? "" : "selected"}>Autre (saisir le nom exact)…</option>`;
}

function editerProfil(p) {
  const comptes = etat.d.comptes;
  if (!comptes.length) {
    toast("Ajoute d'abord un compte.", true);
    return changerVue("comptes");
  }
  const docs = p.id ? docsDe("profil", p.id) : [];
  const acces = Object.entries(ACCES).map(([cle, [titre, aide]]) => `
    <label class="case"><input type="radio" name="acces" value="${cle}" ${cle === (p.acces || "aucun") ? "checked" : ""}>
      <span><b>${titre}</b> <span class="aide">: ${aide}</span></span></label>`).join("");
  ouvrirDialogue(p.id ? `Profil : ${p.nom}` : "Nouveau profil", `
    <label>Nom (sert pour les @mentions) <input name="nom" required value="${esc(p.nom || "")}" placeholder="Ex. Organisateur"></label>
    <label>Couleur <input name="couleur" type="color" value="${esc(p.couleur || "#b4532a")}"></label>
    <label>Compte <select name="compte_id">${comptes.map((c) => `<option value="${c.id}" ${c.id === p.compte_id ? "selected" : ""}>${esc(c.nom)} (${esc(etat.d.fournisseurs[c.fournisseur].court)})</option>`).join("")}</select></label>
    <label>Modèle
      <select name="modele_choix"></select>
      <input name="modele_libre" placeholder="Nom exact du modèle" hidden>
      <button type="button" class="bouton texte" id="lister-modeles" hidden>Lister les modèles de ce compte</button>
    </label>
    <label>Effort de réflexion <select name="effort"></select>
      <span class="aide">Plus d'effort = réponses plus réfléchies, mais plus lentes et plus de quota.</span></label>
    <label class="case" style="align-self:end"><input type="checkbox" name="web" ${p.web ? "checked" : ""}>
      <span>Recherche sur internet <span class="aide" id="aide-web"></span></span></label>
    <fieldset class="large"><legend>Accès aux fichiers du projet</legend>${acces}</fieldset>
    <label class="large">Rôle : qui il est et ce qu'il fait dans l'équipe
      <textarea name="role" rows="6" placeholder="Ex. Tu t'occupes de la logistique : planning, prestataires, matériel…">${esc(p.role || "")}</textarea></label>
    <label class="large">Consignes permanentes : comment il doit travailler, dans tous les projets
      <textarea name="instructions" rows="4" placeholder="Ex. Réponds toujours avec un tableau récapitulatif. Vouvoie les partenaires dans les courriers. Ne dépasse jamais 300 mots.">${esc(p.instructions || "")}</textarea></label>
    <div class="large">
      <b>Documents joints au profil</b> <span class="aide">: il les reçoit dans tous ses projets (charte, exemples, notes de référence…).</span>
      ${p.id ? `<ul class="liste-docs" id="docs-profil"></ul>
        <label class="lien">Joindre des documents<input type="file" id="fichiers-profil" multiple hidden></label>`
        : `<p class="aide">Enregistre d'abord le profil, puis rouvre-le pour joindre des documents.</p>`}
    </div>`, async (fd) => {
    const choix = fd.get("modele_choix");
    const corps = {
      nom: fd.get("nom"), couleur: fd.get("couleur"), compte_id: fd.get("compte_id"),
      modele: choix === "__autre" ? (fd.get("modele_libre") || "").trim() : choix,
      effort: fd.get("effort"), acces: fd.get("acces"), web: fd.get("web") === "on",
      role: fd.get("role"), instructions: fd.get("instructions"),
    };
    await (p.id ? api("PUT", `/api/profils/${p.id}`, corps) : api("POST", "/api/profils", corps));
    delete etat.resultats[p.id];
    await charger();
    toast("Profil enregistré.");
  });

  const form = $("#dialogue-form");
  let choix = { modele: p.modele || "", effort: p.effort || "" };  // garde ce qui est choisi dans le dialogue
  const lireChoix = () => {
    if (!form.elements.modele_choix.options.length) return;
    const m = form.elements.modele_choix.value;
    choix = { modele: m === "__autre" ? (form.elements.modele_libre.value || "").trim() : m, effort: form.elements.effort.value };
  };
  const majCompte = () => {
    lireChoix();
    const c = compte(form.elements.compte_id.value);
    const f = etat.d.fournisseurs[c.fournisseur];
    form.elements.modele_choix.innerHTML = optionsModeles(f, choix.modele, etat.modelesComptes[c.id] || []);
    const efforts = f.efforts.length ? f.efforts : Object.keys(etat.d.efforts);
    form.elements.effort.innerHTML = `<option value="">Par défaut</option>` +
      efforts.map((e) => `<option value="${e}" ${e === choix.effort ? "selected" : ""}>${esc(etat.d.efforts[e])}</option>`).join("");
    form.elements.web.disabled = !f.web;
    if (!f.web) form.elements.web.checked = false;
    $("#aide-web").textContent = f.web ? ": il pourra chercher et citer des pages web (et ses commandes auront le réseau)."
      : ": disponible avec Claude et ChatGPT.";
    $("#lister-modeles").hidden = !["antigravity", "codex"].includes(c.fournisseur);
    majLibre();
  };
  const majLibre = () => {
    const libre = form.elements.modele_choix.value === "__autre";
    form.elements.modele_libre.hidden = !libre;
    if (libre && !form.elements.modele_libre.value) form.elements.modele_libre.value = choix.modele;
  };
  form.elements.compte_id.onchange = majCompte;
  form.elements.modele_choix.onchange = majLibre;
  $("#lister-modeles").onclick = async () => {
    const id = form.elements.compte_id.value;
    const r = await essayer(() => api("GET", `/api/comptes/${id}/modeles`));
    if (!r) return;
    etat.modelesComptes[id] = r.modeles;
    majCompte();
    toast(r.modeles.length ? `${r.modeles.length} modèle(s) trouvé(s).` : "Aucun modèle reconnu dans la réponse de l'outil.");
  };
  majCompte();

  if (p.id) {
    const rendreDocsProfil = () => {
      $("#docs-profil").innerHTML = docsDe("profil", p.id).map((d) => `<li><span class="nom-doc">${esc(d.nom)}</span>
        <span class="aide">${d.caracteres ? `${d.caracteres.toLocaleString("fr-FR")} car.` : "sans texte"}</span>
        <button type="button" class="bouton texte" data-retirer="${d.id}">Retirer</button></li>`).join("") || `<li class="aide">Aucun document.</li>`;
      $$("[data-retirer]", $("#docs-profil")).forEach((b) => (b.onclick = async () => {
        await essayer(() => api("DELETE", `/api/documents/${b.dataset.retirer}`));
        await charger();
        rendreDocsProfil();
      }));
    };
    $("#fichiers-profil").onchange = async (e) => {
      await importerFichiers("profil", p.id, [...e.target.files]);
      e.target.value = "";
      rendreDocsProfil();
    };
    rendreDocsProfil();
  }
}

// --- Comptes ----------------------------------------------------------------------------------

function rendreComptes() {
  const f = etat.d.fournisseurs;
  $("#etat-outils").innerHTML = `<thead><tr><th>Outil officiel</th><th>Sur ce PC</th></tr></thead><tbody>` +
    Object.entries(f).filter(([cle]) => cle !== "simulation").map(([, o]) => `
    <tr><td><b>${esc(o.nom)}</b></td>
      <td>${o.executable ? `<span class="statut-compte ok">installé</span> <code>${esc(o.executable)}</code>`
        : `<span class="statut-compte ko">absent</span> <span class="aide">installation : <code>${esc(o.installation)}</code></span>`}</td></tr>`).join("") + "</tbody>";

  $("#liste-comptes").innerHTML = etat.d.comptes.map((c) => {
    const r = etat.resultats[c.id];
    const commande = c.instructions?.[etat.d.windows ? "powershell" : "bash"];
    const utilise = etat.d.profils.filter((p) => p.compte_id === c.id).map((p) => p.nom);
    const statut = r ? `<span class="statut-compte ${r.connecte ? "ok" : r.connecte === false ? "ko" : ""}">${r.connecte ? "connecté" : r.connecte === false ? "non connecté" : "état inconnu"}</span>` : "";
    return `<div class="ligne">
      <span class="avatar" style="--couleur:var(--encre-2)">${esc(initiales(f[c.fournisseur].court))}</span>
      <div>
        <div class="titre">${esc(c.nom)} ${statut}</div>
        <div class="sous">${esc(f[c.fournisseur].nom)}${utilise.length ? ` · utilisé par ${esc(utilise.join(", "))}` : ""}</div>
        <div class="sous">${c.dossier ? `Connexion rangée dans <code>${esc(c.dossier)}</code>` : c.fournisseur === "simulation" ? "Aucune connexion nécessaire." : "Utilise la connexion par défaut de l'outil."}</div>
        ${commande ? `<details><summary>Commande manuelle, si la fenêtre de connexion ne s'ouvre pas</summary>
          <div class="commande"><code>${esc(commande)}</code><button class="bouton petit" data-copier="${esc(commande)}">Copier</button></div></details>` : ""}
      </div>
      <div class="boutons">
        ${c.fournisseur !== "simulation" ? `<button class="bouton petit" data-action="connexion" data-id="${c.id}">Se connecter</button>
        <button class="bouton petit" data-action="verifier" data-id="${c.id}">Vérifier</button>` : ""}
        <button class="bouton petit" data-action="renommer" data-id="${c.id}">Renommer</button>
        <button class="bouton petit danger" data-action="supprimer" data-id="${c.id}">Supprimer</button>
      </div>
      ${r ? `<div class="resultat ${r.connecte ? "ok" : "ko"}">${esc(r.texte)}</div>` : ""}
    </div>`;
  }).join("") || `<p class="aide" style="padding:16px 0">Aucun compte. Commence par un compte « Simulation » pour essayer l'Atelier sans rien consommer.</p>`;
  $$("#liste-comptes [data-action]").forEach((b) => (b.onclick = () => actionCompte(b.dataset.action, b.dataset.id, b)));
  $$("#liste-comptes [data-copier]").forEach((b) => (b.onclick = () =>
    navigator.clipboard.writeText(b.dataset.copier).then(() => toast("Commande copiée : colle-la dans PowerShell."))));
}

async function actionCompte(action, id, bouton) {
  const c = compte(id);
  if (action === "connexion") {
    const r = await essayer(() => api("POST", `/api/comptes/${id}/connexion`));
    if (r) toast(r.fenetre
      ? "Une fenêtre s'est ouverte : termine la connexion dans le navigateur, puis clique sur « Vérifier »."
      : "Ouvre un terminal et lance la commande manuelle affichée sous le compte.");
  }
  if (action === "verifier") {
    bouton.disabled = true;
    bouton.textContent = "Vérification…";
    etat.resultats[id] = (await essayer(() => api("POST", `/api/comptes/${id}/verifier`))) || { connecte: false, texte: "échec de la requête" };
    rendreComptes();
  }
  if (action === "renommer") {
    ouvrirDialogue("Renommer le compte", `<label class="large">Nom <input name="nom" required value="${esc(c.nom)}"></label>`, async (fd) => {
      await api("PUT", `/api/comptes/${id}`, { nom: fd.get("nom") });
      await charger();
    });
  }
  if (action === "supprimer") {
    if (!confirm(`Retirer le compte « ${c.nom} » de l'Atelier ? Ton abonnement n'est pas touché.`)) return;
    await essayer(() => api("DELETE", `/api/comptes/${id}`), "Compte retiré.");
    charger();
  }
}

function nouveauCompte() {
  const f = etat.d.fournisseurs;
  ouvrirDialogue("Ajouter un compte", `
    <label class="large">Service <select name="fournisseur">${Object.entries(f).map(([k, o]) => `<option value="${k}">${esc(o.nom)}</option>`).join("")}</select></label>
    <label class="large">Nom du compte <input name="nom" required placeholder="Ex. Claude perso, ChatGPT asso…"></label>
    <label class="large case" id="ligne-separe"><input type="checkbox" name="compte_separe" checked>
      <span>Compte séparé <span class="aide">(recommandé) : permet d'avoir plusieurs comptes du même service. Décoche pour réutiliser la connexion déjà faite dans ton terminal.</span></span></label>
    <p class="large aide" id="aide-fournisseur"></p>`, async (fd) => {
    const fournisseur = fd.get("fournisseur");
    const c = await api("POST", "/api/comptes", {
      nom: fd.get("nom"), fournisseur, compte_separe: f[fournisseur].multi_comptes && fd.get("compte_separe") === "on",
    });
    await charger();
    changerVue("comptes");
    if (c.fournisseur !== "simulation" && c.compte_separe) toast("Compte ajouté. Clique sur « Se connecter » pour le relier à ton abonnement.");
  }, "Ajouter");
  const select = $("#dialogue [name=fournisseur]");
  const maj = () => {
    $("#ligne-separe").hidden = !f[select.value].multi_comptes;
    $("#aide-fournisseur").textContent = {
      antigravity: "Expérimental : une seule connexion Antigravity par PC. Connecte-toi d'abord en lançant « agy » dans un terminal.",
      simulation: "Réponses factices instantanées, pour essayer l'Atelier sans consommer ton abonnement.",
    }[select.value] || "";
  };
  select.onchange = maj;
  maj();
}

// --- Réglages généraux ---------------------------------------------------------------------

function remplirParametres() {
  const form = $("#form-parametres");
  for (const [k, v] of Object.entries(etat.d.parametres)) if (form.elements[k]) form.elements[k].value = v;
  const outils = Object.values(etat.d.fournisseurs).filter((o) => o.executable !== "intégré");
  $("#infos-systeme").innerHTML = `<dl>
    <dt>Données de l'Atelier</dt><dd><code>${esc(etat.d.dossier_donnees)}</code></dd>
    <dt>Extraction des PDF</dt><dd>${etat.d.pdf_disponible ? "activée" : "désactivée : lance <code>installer-pdf.bat</code> puis redémarre l'Atelier"}</dd>
    ${outils.map((o) => `<dt>${esc(o.court)}</dt><dd>${o.executable ? esc(o.executable) : "outil non installé"}</dd>`).join("")}
  </dl>`;
}

async function enregistrerParametres(e) {
  e.preventDefault();
  const corps = Object.fromEntries(new FormData(e.target).entries());
  if (await essayer(() => api("PUT", "/api/parametres", corps), "Réglages enregistrés.")) await charger();
}

// --- Dialogue générique -----------------------------------------------------------------------

function ouvrirDialogue(titre, champs, surValider, libelleOk = "Enregistrer") {
  const dlg = $("#dialogue");
  $("#dialogue-titre").textContent = titre;
  $("#dialogue-champs").innerHTML = champs;
  $("#dialogue-ok").textContent = libelleOk;
  $("#dialogue-form").onsubmit = async (e) => {
    if (e.submitter?.value !== "ok") return;
    e.preventDefault();
    const bouton = e.submitter;
    bouton.disabled = true;
    try {
      await surValider(new FormData(e.target));
      dlg.close();
    } catch (err) {
      toast(err.message, true);
    } finally {
      bouton.disabled = false;
    }
  };
  dlg.showModal();
  $("input:not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea, select", dlg)?.focus();
}

// --- Démarrage ----------------------------------------------------------------------------------

$$(".nav-lien").forEach((b) => (b.onclick = () => changerVue(b.dataset.vue)));
$$(".onglet").forEach((b) => (b.onclick = () => changerPanneau(b.dataset.panneau)));
$("#accueil").onclick = (e) => { e.preventDefault(); changerVue("projets"); };
$("#nouveau-projet").onclick = nouveauProjet;
$("#lancer").onclick = () => lancer();
$("#tour-suivant").onclick = () => lancer(1);
$("#stop").onclick = () => essayer(() => api("POST", `/api/projets/${etat.projetId}/stop`), "Arrêt demandé.");
$("#composer").onsubmit = envoyer;
$("#saisie").onkeydown = (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#composer").requestSubmit($("#envoyer-lancer"));
};
$("#destinataire").onchange = majDestinataire;
$("#panneau-reglages-projet").onsubmit = enregistrerReglages;
$("#choix-mode").onchange = majMode;
$("#choisir-dossier").onclick = async (e) => {
  const bouton = e.currentTarget;
  const champ = $("#panneau-reglages-projet").elements.dossier;
  const generation = etat.generation;
  bouton.disabled = true;
  toast("Choisis le dossier dans la fenêtre qui vient de s'ouvrir.");
  const r = await essayer(() => api("POST", "/api/choisir-dossier", { depart: champ.value || projetCourant().espace }));
  bouton.disabled = false;
  if (generation !== etat.generation) return toast("Projet changé entre-temps : dossier choisi ignoré.", true);
  if (r?.dossier) {
    champ.value = r.dossier;
    toast("Dossier choisi : clique sur « Enregistrer » pour l'adopter.");
  }
};
$("#archiver-discussion").onclick = async () => {
  if (!confirm("Archiver la discussion ? Elle disparaît de l'écran mais reste sauvegardée dans le dossier des données. La mémoire du projet est conservée.")) return;
  if (await essayer(() => api("DELETE", `/api/projets/${etat.projetId}/messages`), "Discussion archivée.")) selectionnerProjet(etat.projetId, "discussion");
};
$("#supprimer-projet").onclick = async () => {
  const p = projetCourant();
  if (!confirm(`Supprimer le projet « ${p.nom} » ? Sa discussion et ses documents sont déplacés dans la corbeille de l'Atelier.`)) return;
  if (await essayer(() => api("DELETE", `/api/projets/${p.id}`), "Projet supprimé.")) {
    await charger();
    selectionnerProjet(null);
  }
};
$("#rafraichir-fichiers").onclick = chargerFichiers;
$("#ouvrir-dossier").onclick = () => essayer(() => api("POST", `/api/projets/${etat.projetId}/ouvrir-dossier`));
$("#memoire-texte").oninput = () => (etat.memoireModifiee = true);
$("#memoire-enregistrer").onclick = enregistrerMemoire;
$("#memoire-auto").onchange = reglageMemoire;
$("#memoire-profil").onchange = reglageMemoire;
$("#memoire-resumer").onclick = async () => {
  if (await essayer(() => api("POST", `/api/projets/${etat.projetId}/memoire`, { profil_id: $("#memoire-profil").value }))) {
    toast("Mise à jour de la mémoire lancée : le résultat apparaîtra ici.");
    sonder();
  }
};
$("#fichiers-projet").onchange = async (e) => {
  await importerFichiers("projet", etat.projetId, [...e.target.files]);
  e.target.value = "";
  rendreDocuments();
};
const depot = $("#depot");
depot.ondragover = (e) => { e.preventDefault(); depot.classList.add("survol"); };
depot.ondragleave = () => depot.classList.remove("survol");
depot.ondrop = async (e) => {
  e.preventDefault();
  depot.classList.remove("survol");
  await importerFichiers("projet", etat.projetId, [...e.dataTransfer.files]);
  rendreDocuments();
};
$("#nouveau-profil").onclick = () => editerProfil({});
$("#modele-role").onchange = (e) => {
  const m = etat.d.modeles_roles[e.target.value];
  e.target.value = "";
  if (m) editerProfil({ ...m, compte_id: etat.d.comptes[0]?.id });
};
$("#nouveau-compte").onclick = nouveauCompte;
$("#form-parametres").onsubmit = enregistrerParametres;

$$(".moteur-telecharger").forEach((b) => (b.onclick = telechargerInstallateur));
$$(".moteur-reessayer").forEach((b) => (b.onclick = () => connecterMoteur(true)));

connecterMoteur();
