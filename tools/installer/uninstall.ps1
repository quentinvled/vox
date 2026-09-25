# Desinstallation de Vox (utilisateur courant).
#
# Ce fichier est copie dans le dossier d'installation et appele par l'entree
# "Applications installees" de Windows.
# Volontairement en ASCII : PowerShell 5.1 lit mal l'UTF-8 sans BOM.

$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms

$dest = Join-Path $env:LOCALAPPDATA 'Programs\Vox'
$data = Join-Path $env:LOCALAPPDATA 'Vox'

Get-Process Vox -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

# Raccourcis
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Vox.lnk') -Force
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'Vox.lnk') -Force

# Entree "Applications installees"
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Vox' -Recurse -Force

# Demarrage automatique, si l'utilisateur l'avait active
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'Vox' -Force

# Programme
Remove-Item $dest -Recurse -Force

# Donnees personnelles : on demande avant de supprimer
if (Test-Path $data) {
    $question = "Vox est desinstalle.`n`nSupprimer aussi ton historique de dictees, " +
                "tes reglages et ta cle API ?`n`nDossier : $data"
    $answer = [System.Windows.Forms.MessageBox]::Show(
        $question,
        'Desinstallation de Vox',
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question
    )
    if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
        Remove-Item $data -Recurse -Force
        [System.Windows.Forms.MessageBox]::Show(
            'Historique, reglages et cle API supprimes.',
            'Vox',
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
    }
}

[System.Windows.Forms.MessageBox]::Show(
    'Vox a ete desinstalle.',
    'Vox',
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
) | Out-Null
