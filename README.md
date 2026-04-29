# eSignAgent

Open-source PDF digital signing client for web applications.

A free, vendor-neutral alternative to commercial signing extensions.
Lets a web app obtain PAdES-compatible CMS PKCS#7 detached signatures
from certificates already installed on the user's OS — USB tokens or
cloud certs from any eIDAS-qualified TSP — without server-side key
custody.

## Components

- **Native host** (Python, ~500 LOC) — talks to the OS Cert Store via
  `ctypes`. Windows CryptoAPI / CNG is implemented; macOS Keychain and
  Linux PKCS#11 adapters are stubbed for later phases.
- **Browser extension** (Manifest v3, ~250 LOC) — bridges page JS to
  the native host via Chrome Native Messaging. Compatible with Chrome,
  Edge, Brave, and Firefox.
- **Windows installer** (Inno Setup) — registers the host manifest in
  HKLM and force-installs the extension via Chrome / Edge enterprise
  policy. One double-click does everything.

## Architecture

```
┌─────────────┐  postMessage  ┌─────────────────┐  chrome.runtime  ┌────────────────┐  stdio JSON  ┌──────────────────┐
│   Web app   │ ←──────────→  │ content_script  │ ←──────────────→ │ background.js  │ ←──────────→ │ esignagent-host  │
│  (your JS)  │  MAIN ↔ ISO   │     bridge      │     messaging    │ (service worker)│              │       (.exe)     │
└─────────────┘               └─────────────────┘                  └────────────────┘              └──────────────────┘
                                                                                                            │
                                                                                                            ▼
                                                                                                  ┌──────────────────┐
                                                                                                  │  OS Cert Store   │
                                                                                                  │ (Windows CSP /   │
                                                                                                  │  macOS Keychain) │
                                                                                                  └──────────────────┘
```

The web app calls
`await window.eSignAgent.signPdfHash(thumbprint, hashB64)`
and gets back a Base64-encoded CMS PKCS#7 detached signature. PDF
placeholder construction, TSA stamping (B-T) and OCSP/CRL embedding
(B-LT) happen on the consuming app's backend with a library like
[pyhanko](https://pyhanko.readthedocs.io); they are out of scope here.

## Repository layout

```
eSignAgent/
├── extension/                 browser extension (Manifest v3)
│   ├── manifest.json
│   ├── background.js          service worker, native messaging port mgmt
│   ├── content.js             ISOLATED-world relay
│   ├── page-bridge.js         MAIN-world API → window.eSignAgent
│   └── icons/
├── native-host/               Python signing daemon
│   ├── esignagent/
│   │   ├── main.py            entry point, request dispatcher
│   │   ├── messaging.py       Chrome Native Messaging wire format
│   │   ├── windows_signer.py  CryptoAPI/CNG via ctypes + CMS construction
│   │   ├── macos_signer.py    Keychain stub
│   │   └── linux_signer.py    PKCS#11 stub
│   ├── run_host.py            PyInstaller entry shim
│   ├── build_windows.py       produces dist\esignagent-host.exe
│   ├── pyproject.toml
│   └── requirements.txt       only asn1crypto
├── installer/
│   └── windows/
│       ├── installer.iss      Inno Setup script (Windows .exe installer)
│       └── dev_install.ps1    dev-time manifest registration
└── docs/
    └── update.xml.template    Chrome extension auto-update manifest
```

## Public JS API

Available on every page that matches the extension's `content_scripts.matches`:

```ts
window.eSignAgent.ping()
  : Promise<{ host: string, version: string, platform: 'win32'|'darwin'|'linux' }>

window.eSignAgent.selectCertificate()
  : Promise<{
      thumbprint: string,
      subject: string,
      issuer: string,
      serial: string,
      valid_from: string,
      valid_until: string,
    }>

window.eSignAgent.signPdfHash(thumbprint: string, hashB64: string)
  : Promise<{
      cms_b64: string,           // Base64 PKCS#7 detached signature
      signer_subject: string,
      signer_issuer: string,
      signer_thumbprint: string,
    }>
```

`window.eSignAgent` is `undefined` if the extension is not installed.

## End-user install (Windows)

Download `eSignAgent-Setup-x.y.z.exe` from the latest release and run it
(admin elevation required). Restart the browser. That's it — the host is
registered, the extension is force-installed via enterprise policy, and
the web app gets `window.eSignAgent` injected on matching origins.

Releases: https://github.com/spdsft/eSignAgent/releases

## Development setup

### 1. Build the host

```powershell
cd native-host
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt pyinstaller
python build_windows.py
# Output: dist\esignagent-host.exe
```

### 2. Match patterns

By default the extension matches `localhost` and `127.0.0.1`. Forks that
deploy on a production domain should add their origin under
`content_scripts.matches` and `host_permissions` in `extension/manifest.json`,
then rebuild the `.crx`.

### 3. Load the unpacked extension

1. `chrome://extensions` → enable Developer mode → Load unpacked → pick
   `eSignAgent\extension`
2. Note the assigned ID. With `manifest.key` set, this is always
   `aoonbefkefmhoicoceilifnngkenmfah`.

### 4. Register the native host manifest

```powershell
cd installer\windows
.\dev_install.ps1 -ExtensionId aoonbefkefmhoicoceilifnngkenmfah
```

Restart the browser.

### 5. Smoke-test

```js
await window.eSignAgent.ping()
await window.eSignAgent.selectCertificate()
```

## Roadmap

- **Phase 1 (current)** — Windows + Chromium browsers; Firefox supported
  but extension distribution requires a signed XPI from Mozilla AMO.
- **Phase 2** — macOS Keychain support.
- **Phase 3** — Linux PKCS#11 support.

## License

[Apache 2.0](LICENSE).

Dependencies:
- [asn1crypto](https://github.com/wbond/asn1crypto) (MIT)
