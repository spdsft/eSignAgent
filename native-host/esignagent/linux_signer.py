"""
Linux PKCS#11 bridge (stub — Phase 2).

Will use:
  - python-pkcs11 to load TSP-specific .so modules (cryptoCertumPKCS11.so for
    CertSIGN, libetpkcs11.so for DigiSign / AlfaSign, etc.)
  - GTK dialog for cert picker (pygobject)
  - asn1crypto to assemble CMS PKCS#7

Currently raises NotImplementedError. Linux is the lowest-priority target
since most typical workflows are on Windows desktops.
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
        "Linux signing nu este inca implementat. Disponibil in Phase 2."
    )


def sign_pdf_hash(thumbprint_hex: str, hash_b64: str) -> dict:
    raise NotImplementedError(
        "Linux signing nu este inca implementat. Disponibil in Phase 2."
    )
