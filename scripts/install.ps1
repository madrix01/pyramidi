# Copies the Remote Script into Live's User Library.
# A junction would avoid re-running this, but OneDrive does not sync through junctions reliably.
# After running: in Live, set the Control Surface slot to None and back to DualSenseArturia to reload.
param(
    [string]$UserLibrary = "$env:USERPROFILE\OneDrive\Documents\Ableton\User Library"
)

$src = Join-Path $PSScriptRoot "..\remote_script\DualSenseArturia"
$dst = Join-Path $UserLibrary "Remote Scripts\DualSenseArturia"

if (-not (Test-Path $UserLibrary)) {
    Write-Error "User Library not found: $UserLibrary (pass -UserLibrary <path>)"
    exit 1
}

robocopy $src $dst /MIR /XD __pycache__ /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy failed ($LASTEXITCODE)"; exit 1 }
Write-Output "Installed to $dst"
