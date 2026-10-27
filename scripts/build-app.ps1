# Builds the phone app (android/) and, with -Install, installs it on a USB-connected phone
# (USB debugging on). Uses the Java bundled with Android Studio; works offline once Gradle and
# the Android plugin are cached.
param([switch]$Install)

$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
$android = Join-Path $PSScriptRoot "..\android"
if (-not (Test-Path (Join-Path $android "local.properties"))) {
    Set-Content -Path (Join-Path $android "local.properties") -Value "sdk.dir=$($env:LOCALAPPDATA -replace '\','/')/Android/Sdk"
}

Push-Location $android
try {
    $task = if ($Install) { "installDebug" } else { "assembleDebug" }
    & .\gradlew.bat --offline $task
    if ($LASTEXITCODE -ne 0) { Write-Error "Gradle failed ($LASTEXITCODE). If it's a missing download, retry without --offline."; exit 1 }
    Write-Output "APK: $(Resolve-Path app\build\outputs\apk\debug\app-debug.apk)"
} finally {
    Pop-Location
}
