# Installation de Vox pour l'utilisateur courant.
# Aucune elevation de privileges n'est necessaire : tout va dans %LOCALAPPDATA%.
#
# Ce fichier est execute par l'archive auto-extractible (7-Zip SFX), qui l'a
# extrait dans un dossier temporaire : $PSScriptRoot pointe donc dessus.
# Le marqueur @VERSION@ est remplace a la construction par tools/build_installer.py.
# Volontairement en ASCII : PowerShell 5.1 lit mal l'UTF-8 sans BOM.

$ErrorActionPreference = 'Stop'
$version = '@VERSION@'
$src = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA 'Programs\Vox'
$data = Join-Path $env:LOCALAPPDATA 'Vox'

Write-Host ''
Write-Host "  Installation de Vox $version" -ForegroundColor Cyan
Write-Host "  Dossier : $dest"
Write-Host ''

New-Item -ItemType Directory -Force -Path $dest | Out-Null

# Vox tourne peut-etre deja : on l'arrete pour pouvoir remplacer l'executable.
Get-Process Vox -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

foreach ($name in @('Vox.exe', 'LISEZ-MOI.txt', '.env.example', 'uninstall.ps1')) {
    $from = Join-Path $src $name
    if (Test-Path $from) {
        Copy-Item $from $dest -Force
    }
}

$exe = Join-Path $dest 'Vox.exe'
if (-not (Test-Path $exe)) {
    Write-Host "  Echec : Vox.exe est introuvable dans l'archive." -ForegroundColor Red
    exit 1
}

# --- raccourcis Bureau + Menu Demarrer ---
$shell = New-Object -ComObject WScript.Shell
$links = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Vox.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Vox.lnk')
)
foreach ($link in $links) {
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = $dest
    $shortcut.Description = 'Vox - dictee vocale (Ctrl + Maj)'
    $shortcut.IconLocation = "$exe,0"
    $shortcut.Save()
}

# --- entree dans Applications installees ---
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Vox'
New-Item -Path $key -Force | Out-Null
$uninstaller = Join-Path $dest 'uninstall.ps1'
Set-ItemProperty -Path $key -Name DisplayName     -Value "Vox $version"
Set-ItemProperty -Path $key -Name DisplayVersion  -Value $version
Set-ItemProperty -Path $key -Name Publisher       -Value 'Quentin VLED'
Set-ItemProperty -Path $key -Name InstallLocation -Value $dest
Set-ItemProperty -Path $key -Name DisplayIcon     -Value $exe
Set-ItemProperty -Path $key -Name UninstallString -Value "powershell -NoProfile -ExecutionPolicy Bypass -File `"$uninstaller`""
Set-ItemProperty -Path $key -Name NoModify -Value 1 -Type DWord
Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord

Write-Host '  Raccourcis crees (Bureau et Menu Demarrer).' -ForegroundColor Green
Write-Host '  Desinstallable depuis Parametres > Applications.' -ForegroundColor DarkGray
Write-Host ''

if (-not (Test-Path (Join-Path $dest '.env'))) {
    Write-Host '  Au premier lancement, la fenetre de reglages s''ouvre :' -ForegroundColor Yellow
    Write-Host '  colle ta cle OpenRouter (voir LISEZ-MOI.txt).' -ForegroundColor Yellow
    Write-Host ''
}

Start-Process -FilePath $exe -WorkingDirectory $dest
Start-Sleep -Milliseconds 900
exit 0
