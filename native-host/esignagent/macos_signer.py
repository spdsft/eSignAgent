"""
macOS Keychain bridge (stub — Phase 2).

Will use:
  - SecKeychain APIs via pyobjc-framework-Security to enumerate identities
  - SecChooseIdentityDialog (or custom NSOpenPanel) to pick a cert
  - SecKeyRawSign / SecKeyCreateSignature for signing
  - asn1crypto to assemble CMS PKCS#7 (same as Windows)

Currently raises NotImplementedError so the build doesn't fail on macOS,
but the actual signing flow will be added in Phase 2 once Windows is stable.
"""
from __future__ import annotations


class UserCancelled(Exception):
    pass


class CertNotFoundError(Exception):
    pass


class SigningError(Exception):
    pass


def select_certificate_via_native_picker() -> dict:
    raise NotImplementedError(
        "macOS signing nu este inca implementat. Disponibil in Phase 2."
    )


def sign_pdf_hash(thumbprint_hex: str, hash_b64: str) -> dict:
    raise NotImplementedError(
        "macOS signing nu este inca implementat. Disponibil in Phase 2."
    )
