# Installe (ou met à jour) le moteur local de l'Atelier IA, sans aucune commande à taper.
#
# Lancé par « Installer-Atelier.bat », que le site public génère (bouton « Télécharger l'installateur ») :
# ce fichier définit ATELIER_SITE (adresse du site) puis télécharge et exécute ce script, publié à côté du site.
#
# Ce que fait l'installation :
#   1. installe Python, Claude Code et (au choix) ChatGPT/Codex s'ils manquent ;
#   2. copie le moteur (moteur.zip, publié avec le site) dans %LOCALAPPDATA%\AtelierIA, données conservées ;
#   3. autorise ce site à piloter le moteur (site.txt) ;
#   4. lance le moteur au démarrage de Windows, crée les raccourcis, ouvre le site.
#
# ATELIER_DOSSIER : autre dossier d'installation. ATELIER_TEST=1 : ni question, ni raccourci, ni navigateur.

$ErrorActionPreference = "Continue"   # Windows PowerShell 5.1 transforme sinon le moindre message d'un outil en erreur
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Site = "$env:ATELIER_SITE".Trim()
$Dossier = if ($env:ATELIER_DOSSIER) { $env:ATELIER_DOSSIER } else { Join-Path $env:LOCALAPPDATA "AtelierIA" }
$Test = $env:ATELIER_TEST -eq "1"
$Port = 8765

function Etape($texte) { Write-Host ""; Write-Host "==> $texte" -ForegroundColor Cyan }
function Info($texte) { Write-Host "    $texte" }
function Echec($texte) { throw $texte }

function Rafraichir-Path {
    # Les outils installés à l'instant ne sont pas encore dans le PATH de cette fenêtre.
    $env:Path = @([Environment]::GetEnvironmentVariable("Path", "Machine"),
                  [Environment]::GetEnvironmentVariable("Path", "User"),
                  (Join-Path $HOME ".local\bin")) -join ";"
}

function Installer-Winget($id, $nom, [switch]$Machine) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Echec "winget (App Installer de Microsoft) est absent : installe $nom à la main, puis relance l'installateur."
    }
    Info "Installation de $nom (quelques minutes, Windows peut demander une autorisation)..."
    $options = @("install", "--id", $id, "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements")
    if (-not $Machine) { $options += @("--scope", "user") }
    & winget @options | Out-Host
    Rafraichir-Path
}

function Trouver-Python {
    # Un vrai Python 3.10+ (pas le raccourci du Microsoft Store), avec pythonw.exe pour tourner sans fenêtre.
    $candidats = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidats += (& py -3 -c "import sys; print(sys.executable)" | Select-Object -First 1)
    }
    $candidats += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" `
        -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    foreach ($python in $candidats) {
        if (-not $python -or -not (Test-Path $python)) { continue }
        if (-not (Test-Path (Join-Path (Split-Path $python) "pythonw.exe"))) { continue }
        if ((& $python -c "import sys; print(sys.version_info >= (3, 10))") -eq "True") { return $python }
    }
    return $null
}

function Trouver-Outil($nom) {
    $commande = Get-Command $nom -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($commande) { return $commande.Source }
    $natif = Join-Path $HOME ".local\bin\$nom.exe"
    if (Test-Path $natif) { return $natif }
    return $null
}

function Arreter-Moteur {
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains((Join-Path $Dossier "app.py")) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Moteur-Repond {
    # Le moteur prend le port suivant si 8765 est occupé ; la page cherche aussi sur ces ports.
    foreach ($p in $Port..($Port + 4)) {
        try { if ((Invoke-RestMethod "http://127.0.0.1:$p/api/ping" -TimeoutSec 2).atelier -eq $true) { return $true } } catch {}
    }
    return $false
}

function Raccourci($chemin, $cible, $arguments, $description) {
    $raccourci = (New-Object -ComObject WScript.Shell).CreateShortcut($chemin)
    $raccourci.TargetPath = $cible
    $raccourci.Arguments = $arguments
    $raccourci.WorkingDirectory = $Dossier
    $raccourci.Description = $description
    $raccourci.Save()
}

try {
    Write-Host ""
    Write-Host "  Installation de l'Atelier IA" -ForegroundColor White
    Write-Host "  Site : $Site"
    if (-not $Site -or $Site -notmatch '^https?://') {
        Echec "Adresse du site inconnue : télécharge l'installateur depuis la page de l'Atelier."
    }
    if (-not $Site.EndsWith("/")) { $Site += "/" }

    # --- 1. Outils ------------------------------------------------------------------------------------
    Etape "Python"
    $Python = Trouver-Python
    if (-not $Python) {
        Installer-Winget "Python.Python.3.12" "Python 3.12"
        $Python = Trouver-Python
        if (-not $Python) { Echec "Python n'a pas pu être installé. Installe-le depuis https://www.python.org puis relance l'installateur." }
    }
    $Pythonw = Join-Path (Split-Path $Python) "pythonw.exe"
    Info "OK : $Python"

    Etape "Claude Code (pour utiliser ton abonnement Claude)"
    if (-not (Trouver-Outil "claude")) {
        Info "Installation avec l'installateur officiel d'Anthropic..."
        # Dans un PowerShell séparé : le script officiel peut fermer sa fenêtre en finissant.
        & powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex" | Out-Host
        Rafraichir-Path
        if (-not (Trouver-Outil "claude")) { Installer-Winget "Anthropic.ClaudeCode" "Claude Code" }
    }
    $Claude = Trouver-Outil "claude"
    if ($Claude) { Info "OK : $Claude" } else { Info "Claude Code n'a pas pu être installé : l'Atelier fonctionnera sans (Simulation, ChatGPT)." }

    Etape "ChatGPT (Codex), facultatif"
    if (Trouver-Outil "codex") {
        Info "OK : déjà installé."
    } elseif (-not $Test -and (Read-Host "    Installer aussi ChatGPT pour utiliser ton abonnement ChatGPT ? (o/n)") -match '^[oOyY]') {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Installer-Winget "OpenJS.NodeJS.LTS" "Node.js" -Machine }
        $npm = Get-Command npm -ErrorAction SilentlyContinue
        if (-not $npm -and (Test-Path "$env:ProgramFiles\nodejs\npm.cmd")) { $npm = "$env:ProgramFiles\nodejs\npm.cmd" }
        if ($npm) {
            & $npm install -g @openai/codex | Out-Host
            Rafraichir-Path
            $env:Path += ";$env:APPDATA\npm"
        }
        if (Trouver-Outil "codex") { Info "OK." } else { Info "Codex n'a pas pu être installé : tu pourras réessayer en relançant l'installateur." }
    } else {
        Info "Ignoré (relance l'installateur plus tard pour l'ajouter)."
    }

    Etape "Lecture des PDF"
    & $Python -m pip install --user --quiet --disable-pip-version-check pypdf | Out-Host
    Info "OK."

    # --- 2. Moteur --------------------------------------------------------------------------------------
    Etape "Moteur de l'Atelier"
    $zip = Join-Path $env:TEMP "atelier-moteur.zip"
    $extrait = Join-Path $env:TEMP "atelier-moteur"
    Info "Téléchargement depuis $Site"
    try {
        Invoke-WebRequest "${Site}moteur.zip" -OutFile $zip -UseBasicParsing
    } catch {
        Echec "Téléchargement du moteur impossible ($($_.Exception.Message))."
    }
    Remove-Item $extrait -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $extrait -Force
    if (-not (Test-Path (Join-Path $extrait "app.py"))) { Echec "moteur.zip ne contient pas l'Atelier." }

    Arreter-Moteur   # mise à jour : on remplace les fichiers du moteur, jamais le dossier « donnees »
    New-Item -ItemType Directory -Force $Dossier | Out-Null
    Copy-Item (Join-Path $extrait "*") $Dossier -Recurse -Force
    Remove-Item $zip, $extrait -Recurse -Force -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText((Join-Path $Dossier "site.txt"), "$Site`r`n")
    Info "OK : $Dossier"

    # --- 3. Démarrage automatique et raccourcis -------------------------------------------------------
    $app = "`"$(Join-Path $Dossier 'app.py')`""
    if (-not $Test) {
        Etape "Raccourcis"
        Raccourci (Join-Path ([Environment]::GetFolderPath("Startup")) "Atelier IA (moteur).lnk") $Pythonw "$app --pas-de-navigateur" `
            "Moteur de l'Atelier IA, lancé avec Windows"
        Raccourci (Join-Path ([Environment]::GetFolderPath("Programs")) "Atelier IA.lnk") $Pythonw $app "Ouvre l'Atelier IA"
        Raccourci (Join-Path ([Environment]::GetFolderPath("Desktop")) "Atelier IA.lnk") $Pythonw $app "Ouvre l'Atelier IA"
        @(
            "@echo off",
            "rem Retire l'Atelier IA de ce PC. Tes donnees (dossier donnees) sont gardees.",
            "powershell -NoProfile -Command `"Get-CimInstance Win32_Process | Where-Object { `$_.CommandLine -like '*AtelierIA*app.py*' } | ForEach-Object { Stop-Process -Id `$_.ProcessId -Force }`"",
            "del `"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Atelier IA (moteur).lnk`" 2>nul",
            "del `"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Atelier IA.lnk`" 2>nul",
            "powershell -NoProfile -Command `"Remove-Item ([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'Atelier IA.lnk')) -ErrorAction SilentlyContinue`"",
            "echo L'Atelier IA est desinstalle. Tes donnees sont dans : %~dp0donnees",
            "echo Supprime ce dossier a la main si tu n'en as plus besoin.",
            "pause"
        ) -join "`r`n" | ForEach-Object { [IO.File]::WriteAllText((Join-Path $Dossier "Desinstaller.bat"), $_) }
        Info "OK : démarrage avec Windows, menu Démarrer et Bureau."
    }

    # --- 4. Lancement -------------------------------------------------------------------------------------
    Etape "Démarrage du moteur"
    Start-Process -FilePath $Pythonw -ArgumentList @($app, "--pas-de-navigateur") -WorkingDirectory $Dossier
    $pret = $false
    for ($i = 0; $i -lt 30 -and -not $pret; $i++) { Start-Sleep -Milliseconds 500; $pret = Moteur-Repond }
    if (-not $pret) { Echec "Le moteur ne répond pas : redémarre le PC, puis rouvre la page de l'Atelier." }
    Info "OK : le moteur tourne sur ce PC."

    Write-Host ""
    Write-Host "  C'est prêt ! Sur la page de l'Atelier, clique sur « Me connecter au moteur », puis sur « Autoriser »." -ForegroundColor Green
    Write-Host "  Ensuite : Comptes > Ajouter un compte > Claude > Se connecter (connexion sur le site officiel)."
    if (-not $Test) {
        Start-Process $Site
        Start-Sleep -Seconds 6
    }
    exit 0
} catch {
    Write-Host ""
    Write-Host "  L'installation a échoué : $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Corrige le problème puis double-clique à nouveau sur Installer-Atelier.bat."
    exit 1
}
