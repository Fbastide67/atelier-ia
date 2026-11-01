@echo off
rem Active l'extraction du texte des PDF (installe la bibliotheque pypdf).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 -m pip install --user pypdf) else (python -m pip install --user pypdf)
echo.
echo Termine. Relance l'Atelier pour que le changement soit pris en compte.
pause
