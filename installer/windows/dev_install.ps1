<#
.SYNOPSIS
  Dev-install script — registers the eSignAgent native host with Chrome,
  Edge, Brave, and Firefox on the current Windows user.

.DESCRIPTION
  On Windows, Chromium browsers locate Native Messaging hosts via the
  registry, NOT via files in the User Data folder (the file-based discovery
  applies only on Linux/macOS). This script writes:

    1. The manifest JSON to %LOCALAPPDATA%\eSignAgent\<host>.json
    2. A registry pointer per browser:
         HKCU\Software\<vendor>\<browser>\NativeMessagingHosts\<host>
       (default value) = path to the manifest JSON

  After running, restart the browser so it re-reads the registry on startup.

.PARAMETER HostExe
  Absolute path to the built host executable. Default points at the local
  build output of native-host\dist\esignagent-host.exe.

.PARAMETER ExtensionId
  The browser extension ID. With manifest.key set in the extension, this is
  always aoonbefkefmhoicoceilifnngkenmfah regardless of how the extension
  was installed (Load unpacked or .crx).

.EXAMPLE
  .\dev_install.ps1 -ExtensionId aoonbefkefmhoicoceilifnngkenmfah
#>
[CmdletBinding()]
param(
  [string]$HostExe = (Join-Path $PSScriptRoot "..\..\native-host\dist\esignagent-host.exe"),
  [Parameter(Mandatory=$true)]
  [string]$ExtensionId
)

$ErrorActionPreference = "Stop"

# Resolve to absolute path so the registry/manifest entries don't depend on cwd
$HostExe = [System.IO.Path]::GetFullPath($HostExe)

if (-not (Test-Path $HostExe)) {
  Write-Host "Host exe not found at: $HostExe" -ForegroundColor Yellow
  Write-Host "Build it first: cd ..\..\native-host && python build_windows.py" -ForegroundColor Yellow
  Write-Host ""
}

$hostName = "com.esignagent.host"

# Manifest JSON lives in a stable per-user location, separate from any one
# browser's profile. Both registry entries point to this single file.
$manifestDir  = Join-Path $env:LOCALAPPDATA "eSignAgent"
$manifestPath = Join-Path $manifestDir "$hostName.json"
if (-not (Test-Path $manifestDir)) {
  New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
}

# Chromium-format manifest (allowed_origins) — used by Chrome, Edge, Brave
$chromiumManifest = @{
  name = $hostName
  description = "eSignAgent native host"
  path = $HostExe
  type = "stdio"
  allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json -Depth 5

# Firefox-format manifest (allowed_extensions) — different field name
$firefoxManifestPath = Join-Path $manifestDir "$hostName.firefox.json"
$firefoxManifest = @{
  name = $hostName
  description = "eSignAgent native host"
  path = $HostExe
  type = "stdio"
  allowed_extensions = @($ExtensionId)
} | ConvertTo-Json -Depth 5

$chromiumManifest | Out-File -FilePath $manifestPath -Encoding ascii -NoNewline
$firefoxManifest  | Out-File -FilePath $firefoxManifestPath -Encoding ascii -NoNewline
Write-Host "Manifest (Chromium): $manifestPath" -ForegroundColor DarkGray
Write-Host "Manifest (Firefox):  $firefoxManifestPath" -ForegroundColor DarkGray

# Per-browser registry pointers (HKCU). On Windows, Chromium browsers IGNORE
# files placed in their User Data\NativeMessagingHosts folder — they read
# only from the registry.
$registryEntries = @(
  @{ Browser = "Chrome";  Key = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName";       Manifest = $manifestPath; ParentCheck = "$env:LOCALAPPDATA\Google\Chrome\User Data" },
  @{ Browser = "Edge";    Key = "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$hostName";       Manifest = $manifestPath; ParentCheck = "$env:LOCALAPPDATA\Microsoft\Edge\User Data" },
  @{ Browser = "Brave";   Key = "HKCU:\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\$hostName"; Manifest = $manifestPath; ParentCheck = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data" },
  @{ Browser = "Firefox"; Key = "HKCU:\Software\Mozilla\NativeMessagingHosts\$hostName";              Manifest = $firefoxManifestPath; ParentCheck = "$env:APPDATA\Mozilla" }
)

foreach ($entry in $registryEntries) {
  if (-not (Test-Path $entry.ParentCheck)) {
    Write-Host "Skipping $($entry.Browser) - browser not installed (parent missing: $($entry.ParentCheck))" -ForegroundColor DarkGray
    continue
  }
  New-Item -Path $entry.Key -Force | Out-Null
  Set-ItemProperty -Path $entry.Key -Name "(default)" -Value $entry.Manifest
  Write-Host "[$($entry.Browser)] $($entry.Key) -> $($entry.Manifest)" -ForegroundColor Green
}

Write-Host ""
Write-Host "DONE. To test:" -ForegroundColor Cyan
Write-Host "  1. Fully close Chrome (all windows + chrome.exe processes), then reopen" -ForegroundColor Cyan
Write-Host "  2. Open chrome://extensions, enable Developer mode, Load unpacked the extension folder" -ForegroundColor Cyan
Write-Host "  3. Verify the loaded extension ID is exactly: $ExtensionId" -ForegroundColor Cyan
Write-Host "  4. Hard-reload your web app (Ctrl+Shift+R) so the new content_script is injected" -ForegroundColor Cyan
Write-Host "  5. Open DevTools console and run: await window.eSignAgent.ping()" -ForegroundColor Cyan
