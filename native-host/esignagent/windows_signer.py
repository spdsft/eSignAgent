"""
Windows CryptoAPI / CNG bridge.

Uses ctypes to call:
  - crypt32.dll  — cert store enumeration, cert chain building, CMS encoding
  - cryptui.dll  — CryptUIDlgSelectCertificate (the same picker Adobe shows)
  - ncrypt.dll   — modern signing API (NCryptSignHash, supports RSA-PKCS1v15 + RSA-PSS)

The signing flow is "raw signature": we ask Windows to sign a SHA-256 digest with
the private key tied to the chosen certificate. We then build the CMS PKCS#7
SignedData object ourselves with asn1crypto so we can include the full cert
chain and the SignedAttrs the way PAdES requires.

Why not let Windows build the CMS via CryptSignAndEncodeCertificate? Because
Windows' built-in CMS builder embeds different SignedAttrs depending on the
certificate's KeyUsage flags and is fiddly to customise — building it ourselves
is more predictable for PAdES B-LT downstream.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

if sys.platform == "win32":
    from asn1crypto import cms as asn1_cms
    from asn1crypto import core as asn1_core
    from asn1crypto import x509 as asn1_x509


logger = logging.getLogger("esignagent.windows")


# ============================================================================
# ctypes bindings
# ============================================================================

if sys.platform == "win32":
    crypt32 = ctypes.WinDLL("crypt32.dll")
    cryptui = ctypes.WinDLL("cryptui.dll")
    ncrypt = ctypes.WinDLL("ncrypt.dll")
    advapi32 = ctypes.WinDLL("advapi32.dll")

# crypt32 constants
CERT_STORE_PROV_SYSTEM_W = 10
CERT_STORE_PROV_MEMORY = 2
CERT_SYSTEM_STORE_CURRENT_USER = 0x00010000
CERT_STORE_READONLY_FLAG = 0x00008000
CERT_STORE_OPEN_EXISTING_FLAG = 0x00004000
CERT_FIND_ANY = 0
CERT_FIND_HASH = 0x00010000
X509_ASN_ENCODING = 0x00000001
PKCS_7_ASN_ENCODING = 0x00010000
ENCODING_TYPE = X509_ASN_ENCODING | PKCS_7_ASN_ENCODING

# CertGetCertificateContextProperty IDs
CERT_KEY_PROV_INFO_PROP_ID = 2
CERT_NCRYPT_KEY_HANDLE_PROP_ID = 78  # CNG modern handle

# CertAddCertificateContextToStore disposition
CERT_STORE_ADD_USE_EXISTING = 2

# CryptAcquireCertificatePrivateKey flags
CRYPT_ACQUIRE_CACHE_FLAG = 0x00000001
CRYPT_ACQUIRE_SILENT_FLAG = 0x00000040
CRYPT_ACQUIRE_PREFER_NCRYPT_KEY_FLAG = 0x00040000

# NCrypt
BCRYPT_PAD_PKCS1 = 2
BCRYPT_SHA256_ALGORITHM = "SHA256"

# CryptUIDlgSelectCertificate flags
CRYPTUI_SELECTCERT_MULTISELECT = 0x00000001  # we don't use this
CRYPTUI_DLG_SELCERT_SHOW_DETAILS_BUTTON = 0x00000200


# ----- Structures -----

class CRYPTOAPI_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class CERT_CONTEXT(ctypes.Structure):
    pass  # opaque pointer


class CRYPTUI_SELECTCERTIFICATE_STRUCT(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("hwndParent", wt.HWND),
        ("dwFlags", wt.DWORD),
        ("szTitle", wt.LPCWSTR),
        ("dwDontUseColumn", wt.DWORD),
        ("szDisplayString", wt.LPCWSTR),
        ("pFilterCallback", ctypes.c_void_p),
        ("pDisplayCallback", ctypes.c_void_p),
        ("pvCallbackData", ctypes.c_void_p),
        ("cDisplayStores", wt.DWORD),
        ("rghDisplayStores", ctypes.POINTER(ctypes.c_void_p)),
        ("cStores", wt.DWORD),
        ("rghStores", ctypes.POINTER(ctypes.c_void_p)),
        ("cPropSheetPages", wt.DWORD),
        ("rgPropSheetPages", ctypes.c_void_p),
        ("hSelectedCertStore", ctypes.c_void_p),
    ]


# ----- Function prototypes -----

if sys.platform == "win32":
    crypt32.CertOpenStore.restype = ctypes.c_void_p
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wt.DWORD]
    crypt32.CertCloseStore.restype = wt.BOOL

    crypt32.CertFindCertificateInStore.argtypes = [
        ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    crypt32.CertFindCertificateInStore.restype = ctypes.c_void_p

    crypt32.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    crypt32.CertFreeCertificateContext.restype = wt.BOOL

    crypt32.CertGetCertificateContextProperty.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.DWORD),
    ]
    crypt32.CertGetCertificateContextProperty.restype = wt.BOOL

    crypt32.CertGetEncodedHashData = None  # (we hash the cert ourselves)

    crypt32.CryptAcquireCertificatePrivateKey.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.DWORD),
        ctypes.POINTER(wt.BOOL),
    ]
    crypt32.CryptAcquireCertificatePrivateKey.restype = wt.BOOL

    cryptui.CryptUIDlgSelectCertificateW.argtypes = [
        ctypes.POINTER(CRYPTUI_SELECTCERTIFICATE_STRUCT)
    ]
    cryptui.CryptUIDlgSelectCertificateW.restype = ctypes.c_void_p

    ncrypt.NCryptSignHash.argtypes = [
        ctypes.c_void_p,                                 # hKey
        ctypes.c_void_p,                                 # pPaddingInfo
        ctypes.POINTER(ctypes.c_ubyte), wt.DWORD,        # pbHashValue, cbHashValue
        ctypes.POINTER(ctypes.c_ubyte), wt.DWORD,        # pbSignature, cbSignature
        ctypes.POINTER(wt.DWORD), wt.DWORD,              # pcbResult, dwFlags
    ]
    ncrypt.NCryptSignHash.restype = ctypes.c_long  # NTSTATUS

    ncrypt.NCryptFreeObject.argtypes = [ctypes.c_void_p]
    ncrypt.NCryptFreeObject.restype = ctypes.c_long


class BCRYPT_PKCS1_PADDING_INFO(ctypes.Structure):
    _fields_ = [("pszAlgId", wt.LPCWSTR)]


# ============================================================================
# High-level operations
# ============================================================================

def open_my_store():
    """Open the current user's MY (Personal) certificate store."""
    handle = crypt32.CertOpenStore(
        CERT_STORE_PROV_SYSTEM_W, 0, None,
        CERT_SYSTEM_STORE_CURRENT_USER
        | CERT_STORE_READONLY_FLAG
        | CERT_STORE_OPEN_EXISTING_FLAG,
        ctypes.c_wchar_p("MY"),
    )
    if not handle:
        raise OSError(
            f"CertOpenStore(MY) failed (LastError={ctypes.get_last_error()})"
        )
    return handle


def select_certificate_via_native_picker() -> dict:
    """
    Show the Windows native CryptUIDlgSelectCertificate dialog. Pre-filters
    Windows-MY to a memory store containing only certs with a private key
    AND a Key Usage extension permitting digital signature / non-repudiation.

    This excludes:
      - Trusted-site / Fiddler intermediates (no private key)
      - TLS handshake-cached certs (no private key, often no usable Key Usage)
      - Encryption-only certs (Key Usage = keyEncipherment only)

    Leaves: eIDAS-qualified signing certs (CertSIGN, DigiSign, Trans Sped,
    AlfaSign), non-qualified advanced electronic certs, smartcards, etc.
    """
    my_store = open_my_store()
    mem_store = ctypes.c_void_p(crypt32.CertOpenStore(
        CERT_STORE_PROV_MEMORY, 0, None, 0, None,
    ))
    if not mem_store.value:
        crypt32.CertCloseStore(my_store, 0)
        raise OSError(
            f"CertOpenStore(MEMORY) failed (LastError={ctypes.get_last_error()})"
        )

    try:
        added = _filter_signing_certs_into(my_store, mem_store)
        if added == 0:
            raise CertNotFoundError(
                "Niciun certificat de semnătură nu a fost găsit. "
                "Verificați că tokenul e conectat și driverul instalat."
            )

        params = CRYPTUI_SELECTCERTIFICATE_STRUCT()
        params.dwSize = ctypes.sizeof(CRYPTUI_SELECTCERTIFICATE_STRUCT)
        params.hwndParent = None
        params.dwFlags = CRYPTUI_DLG_SELCERT_SHOW_DETAILS_BUTTON
        params.szTitle = "eSignAgent — Selectați certificatul"
        params.szDisplayString = (
            "Alegeți certificatul cu care semnați documentul (USB sau cloud)."
        )
        store_array = (ctypes.c_void_p * 1)(mem_store)
        params.rghDisplayStores = store_array
        params.cDisplayStores = 1

        cert_ctx = cryptui.CryptUIDlgSelectCertificateW(ctypes.byref(params))
        if not cert_ctx:
            raise UserCancelled("Selecția certificatului a fost anulată.")

        return _cert_context_to_dict(cert_ctx)
    finally:
        crypt32.CertCloseStore(mem_store, 0)
        crypt32.CertCloseStore(my_store, 0)


def _filter_signing_certs_into(source_store, dest_store) -> int:
    """
    Enumerate `source_store` and copy into `dest_store` the certificates that
    are usable for digital signing. Returns count of certs added.
    """
    crypt32.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    crypt32.CertEnumCertificatesInStore.restype = ctypes.c_void_p
    crypt32.CertAddCertificateContextToStore.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
    ]
    crypt32.CertAddCertificateContextToStore.restype = wt.BOOL

    count = 0
    ctx = None
    now_naive = datetime.utcnow()
    while True:
        ctx = crypt32.CertEnumCertificatesInStore(source_store, ctx)
        if not ctx:
            break

        # 1) Try to acquire the private key SILENTLY. Many Windows-MY entries
        #    (Fiddler roots, TLS-cached client certs, etc.) declare a key info
        #    property but no actual usable private key — those fail here and
        #    are excluded. USB tokens / cloud certs whose driver is loaded
        #    succeed without prompting for PIN.
        h_key = ctypes.c_void_p(0)
        key_spec = wt.DWORD(0)
        must_free = wt.BOOL(0)
        ok = crypt32.CryptAcquireCertificatePrivateKey(
            ctx,
            CRYPT_ACQUIRE_CACHE_FLAG
            | CRYPT_ACQUIRE_SILENT_FLAG
            | CRYPT_ACQUIRE_PREFER_NCRYPT_KEY_FLAG,
            None,
            ctypes.byref(h_key),
            ctypes.byref(key_spec),
            ctypes.byref(must_free),
        )
        if not ok or not h_key.value:
            continue
        if must_free.value:
            try:
                ncrypt.NCryptFreeObject(h_key)
            except Exception:
                pass

        # 2) Parse cert; check Key Usage + validity period
        try:
            der = _cert_context_to_der(ctx)
            cert = asn1_x509.Certificate.load(der)
        except Exception:
            continue

        # Skip expired or not-yet-valid certs — irrelevant for new signatures
        try:
            valid_from = cert["tbs_certificate"]["validity"]["not_before"].native
            valid_until = cert["tbs_certificate"]["validity"]["not_after"].native
            if valid_from.tzinfo:
                valid_from = valid_from.replace(tzinfo=None)
                valid_until = valid_until.replace(tzinfo=None)
            if now_naive < valid_from or now_naive > valid_until:
                continue
        except Exception:
            pass  # if validity unparseable, allow

        if not _cert_allows_signing(cert):
            continue

        # Skip self-signed certs (subject == issuer). Real eIDAS signing certs
        # are issued by a CA, not self-signed. Filters out Windows SID-format
        # internal certs and dev-test self-signed entries.
        if cert.subject == cert.issuer:
            continue

        if crypt32.CertAddCertificateContextToStore(
            dest_store, ctx, CERT_STORE_ADD_USE_EXISTING, None,
        ):
            count += 1

    return count


def _cert_allows_signing(cert) -> bool:
    """Return True if the cert's Key Usage permits digital signature or non-repudiation, or has no Key Usage extension at all."""
    extensions = cert["tbs_certificate"]["extensions"]
    if not extensions:
        return True  # No extensions → permissive by default
    for ext in extensions:
        if ext["extn_id"].native != "key_usage":
            continue
        ku = ext["extn_value"].parsed
        try:
            usages = set(ku.native)
        except Exception:
            return True
        if "digital_signature" in usages or "non_repudiation" in usages:
            return True
        return False
    return True  # Key Usage absent → permissive


def sign_pdf_hash(thumbprint_hex: str, hash_b64: str) -> dict:
    """
    Sign the given SHA-256 hash with the private key of the certificate
    identified by `thumbprint_hex`, then build a CMS PKCS#7 detached
    SignedData object and return it base64-encoded.

    Returns: { cms_b64, certificate_chain_b64, signer_subject, ... }
    """
    import base64

    document_digest = base64.b64decode(hash_b64)
    if len(document_digest) != 32:
        raise ValueError(f"hash_b64 must decode to 32 bytes (SHA-256), got {len(document_digest)}")

    cert_ctx = _find_cert_by_thumbprint(thumbprint_hex)
    if not cert_ctx:
        raise CertNotFoundError(
            "Certificatul ales nu mai e disponibil. "
            "Selectați din nou certificatul."
        )
    try:
        cert_der = _cert_context_to_der(cert_ctx)
        signer_cert_asn1 = asn1_x509.Certificate.load(cert_der)

        # ----- Build SignedAttrs -----
        signing_time = datetime.now(timezone.utc).replace(microsecond=0)
        signed_attrs = asn1_cms.CMSAttributes([
            asn1_cms.CMSAttribute({
                "type": "content_type",
                "values": [asn1_cms.ContentType("data")],
            }),
            asn1_cms.CMSAttribute({
                "type": "signing_time",
                "values": [asn1_cms.Time({"utc_time": signing_time})],
            }),
            asn1_cms.CMSAttribute({
                "type": "message_digest",
                "values": [asn1_core.OctetString(document_digest)],
            }),
        ])
        signed_attrs_der = signed_attrs.dump()

        # ----- Hash SignedAttrs and ask Windows to sign it -----
        attrs_hash = hashlib.sha256(signed_attrs_der).digest()
        signature = _ncrypt_sign_hash(cert_ctx, attrs_hash)

        # ----- Collect cert chain (signer + issuers up to root) -----
        chain = _build_cert_chain(cert_der)

        # ----- Build CMS SignedData -----
        signer_info = asn1_cms.SignerInfo({
            "version": "v1",
            "sid": asn1_cms.SignerIdentifier({
                "issuer_and_serial_number": asn1_cms.IssuerAndSerialNumber({
                    "issuer": signer_cert_asn1.issuer,
                    "serial_number": signer_cert_asn1.serial_number,
                }),
            }),
            "digest_algorithm": asn1_cms.DigestAlgorithm({"algorithm": "sha256"}),
            "signed_attrs": signed_attrs,
            "signature_algorithm": asn1_cms.SignedDigestAlgorithm(
                {"algorithm": "rsassa_pkcs1v15"}
            ),
            "signature": signature,
        })

        signed_data = asn1_cms.SignedData({
            "version": "v1",
            "digest_algorithms": [
                asn1_cms.DigestAlgorithm({"algorithm": "sha256"})
            ],
            "encap_content_info": {"content_type": "data"},
            "certificates": [
                asn1_cms.CertificateChoices({"certificate": c}) for c in chain
            ],
            "signer_infos": [signer_info],
        })

        content_info = asn1_cms.ContentInfo({
            "content_type": "signed_data",
            "content": signed_data,
        })
        cms_der = content_info.dump()

        return {
            "cms_b64": base64.b64encode(cms_der).decode("ascii"),
            "signer_subject": signer_cert_asn1.subject.human_friendly,
            "signer_issuer": signer_cert_asn1.issuer.human_friendly,
            "signer_thumbprint": thumbprint_hex,
        }
    finally:
        crypt32.CertFreeCertificateContext(cert_ctx)


# ============================================================================
# Internal helpers
# ============================================================================

def _cert_context_to_der(cert_ctx) -> bytes:
    """Extract the DER-encoded certificate bytes from a CERT_CONTEXT pointer."""
    # CERT_CONTEXT layout (fixed): dwCertEncodingType (DWORD), pbCertEncoded (PBYTE),
    # cbCertEncoded (DWORD), pCertInfo (ptr), hCertStore (HCERTSTORE)
    class _CC(ctypes.Structure):
        _fields_ = [
            ("dwCertEncodingType", wt.DWORD),
            ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbCertEncoded", wt.DWORD),
            ("pCertInfo", ctypes.c_void_p),
            ("hCertStore", ctypes.c_void_p),
        ]

    cc = ctypes.cast(cert_ctx, ctypes.POINTER(_CC)).contents
    return bytes(ctypes.string_at(cc.pbCertEncoded, cc.cbCertEncoded))


def _cert_context_to_dict(cert_ctx) -> dict:
    """Parse CERT_CONTEXT into a JSON-friendly dict for the extension."""
    der = _cert_context_to_der(cert_ctx)
    cert = asn1_x509.Certificate.load(der)
    thumbprint = hashlib.sha1(der).hexdigest().upper()
    return {
        "thumbprint": thumbprint,
        "subject": cert.subject.human_friendly,
        "issuer": cert.issuer.human_friendly,
        "serial": format(cert.serial_number, "X"),
        "valid_from": cert["tbs_certificate"]["validity"]["not_before"].native.isoformat(),
        "valid_until": cert["tbs_certificate"]["validity"]["not_after"].native.isoformat(),
    }


def _find_cert_by_thumbprint(thumbprint_hex: str):
    """Locate a CERT_CONTEXT in MY store matching the SHA-1 thumbprint."""
    thumbprint_bytes = bytes.fromhex(thumbprint_hex)
    blob = CRYPTOAPI_BLOB()
    blob.cbData = len(thumbprint_bytes)
    arr = (ctypes.c_ubyte * len(thumbprint_bytes))(*thumbprint_bytes)
    blob.pbData = arr

    store = open_my_store()
    try:
        cert_ctx = crypt32.CertFindCertificateInStore(
            store, ENCODING_TYPE, 0, CERT_FIND_HASH,
            ctypes.byref(blob), None,
        )
        if not cert_ctx:
            return None

        # CertFindCertificateInStore returns a context owned by the store; it
        # becomes invalid once we close the store. Duplicate it so caller can
        # use it after we close the store.
        if not hasattr(crypt32, "_DupSet"):
            crypt32.CertDuplicateCertificateContext.argtypes = [ctypes.c_void_p]
            crypt32.CertDuplicateCertificateContext.restype = ctypes.c_void_p
            crypt32._DupSet = True
        return crypt32.CertDuplicateCertificateContext(cert_ctx)
    finally:
        crypt32.CertCloseStore(store, 0)


def _ncrypt_sign_hash(cert_ctx, hash_bytes: bytes) -> bytes:
    """Acquire the cert's private key handle (CNG) and sign `hash_bytes`."""
    h_key = ctypes.c_void_p(0)
    key_spec = wt.DWORD(0)
    must_free = wt.BOOL(0)

    ok = crypt32.CryptAcquireCertificatePrivateKey(
        cert_ctx,
        CRYPT_ACQUIRE_CACHE_FLAG | CRYPT_ACQUIRE_PREFER_NCRYPT_KEY_FLAG,
        None,
        ctypes.byref(h_key),
        ctypes.byref(key_spec),
        ctypes.byref(must_free),
    )
    if not ok or not h_key.value:
        raise SigningError(
            "Nu pot accesa cheia privată a certificatului. "
            "Verificați că tokenul e conectat și deblocat."
        )

    try:
        # Set up PKCS#1 v1.5 padding with SHA-256 algorithm identifier
        padding = BCRYPT_PKCS1_PADDING_INFO()
        padding.pszAlgId = "SHA256"

        # First call: query needed buffer size
        cb_result = wt.DWORD(0)
        hash_arr = (ctypes.c_ubyte * len(hash_bytes))(*hash_bytes)
        status = ncrypt.NCryptSignHash(
            h_key, ctypes.byref(padding),
            hash_arr, len(hash_bytes),
            None, 0,
            ctypes.byref(cb_result),
            BCRYPT_PAD_PKCS1,
        )
        if status != 0:
            raise SigningError(
                "Inițializarea semnării a eșuat. "
                "Verificați că tokenul e conectat și deblocat."
            )

        sig_buf = (ctypes.c_ubyte * cb_result.value)()
        status = ncrypt.NCryptSignHash(
            h_key, ctypes.byref(padding),
            hash_arr, len(hash_bytes),
            sig_buf, cb_result.value,
            ctypes.byref(cb_result),
            BCRYPT_PAD_PKCS1,
        )
        if status != 0:
            # Common errors: 0x80090026 NTE_NO_MEMORY, 0x80090009 NTE_BAD_FLAGS,
            # 0xC000A000 STATUS_INVALID_SIGNATURE, 0x80090020 NTE_FAIL.
            # 0x8009002D = NTE_INVALID_HANDLE → key handle bad (often: user cancelled PIN)
            if status & 0xFFFFFFFF == 0x8009002D:
                raise UserCancelled("Operațiunea a fost anulată sau PIN-ul a fost greșit.")
            # Common Windows signing failures we map to friendly text
            ntstatus = status & 0xFFFFFFFF
            if ntstatus == 0x80090016:
                msg = "Tokenul nu a fost găsit. Verificați conexiunea USB sau accesul la cloud."
            elif ntstatus == 0x80090014:
                msg = "PIN-ul a fost introdus greșit prea multe ori. Tokenul poate fi blocat."
            else:
                msg = (
                    "Semnarea a eșuat. Verificați tokenul, PIN-ul și driverul, "
                    "apoi reîncercați."
                )
            logger.error("NCryptSignHash NTSTATUS=0x%08X", ntstatus)
            raise SigningError(msg)

        return bytes(sig_buf[: cb_result.value])
    finally:
        if must_free.value:
            ncrypt.NCryptFreeObject(h_key)


def _build_cert_chain(signer_cert_der: bytes) -> list:
    """
    Build a cert chain by following AIA / issuer DN matching against the local
    cert stores. Returns a list of asn1crypto.Certificate, signer first.

    For PAdES B-LT this list is what the validator uses to anchor the chain;
    we include intermediates if we find them. Roots are typically NOT embedded
    (validator pulls from its trust store) but we include them too if present.
    """
    chain = [asn1_x509.Certificate.load(signer_cert_der)]

    seen = {chain[0].sha256_fingerprint}
    current = chain[0]
    # Stop conditions: (a) self-signed (root), (b) issuer not findable.
    for _depth in range(8):
        if current.subject == current.issuer:
            break  # self-signed, end of chain
        issuer_der = _find_issuer_in_local_stores(current)
        if issuer_der is None:
            break
        issuer = asn1_x509.Certificate.load(issuer_der)
        if issuer.sha256_fingerprint in seen:
            break
        chain.append(issuer)
        seen.add(issuer.sha256_fingerprint)
        current = issuer

    return chain


def _find_issuer_in_local_stores(cert: "asn1_x509.Certificate") -> Optional[bytes]:
    """Search common cert stores (CA, Root, MY) for a cert whose subject matches `cert.issuer`."""
    for store_name in ("CA", "Root", "MY"):
        result = _enum_store_for_subject(store_name, cert.issuer)
        if result is not None:
            return result
    return None


def _enum_store_for_subject(store_name: str, target_subject) -> Optional[bytes]:
    """Open the named system store and return the DER of the first cert matching subject."""
    handle = crypt32.CertOpenStore(
        CERT_STORE_PROV_SYSTEM_W, 0, None,
        CERT_SYSTEM_STORE_CURRENT_USER
        | CERT_STORE_READONLY_FLAG
        | CERT_STORE_OPEN_EXISTING_FLAG,
        ctypes.c_wchar_p(store_name),
    )
    if not handle:
        return None
    try:
        crypt32.CertEnumCertificatesInStore.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p,
        ]
        crypt32.CertEnumCertificatesInStore.restype = ctypes.c_void_p
        ctx = None
        while True:
            ctx = crypt32.CertEnumCertificatesInStore(handle, ctx)
            if not ctx:
                return None
            der = _cert_context_to_der(ctx)
            cert = asn1_x509.Certificate.load(der)
            if cert.subject == target_subject:
                # Duplicate before iteration moves on (Windows gives us a context
                # that's freed when CertEnumCertificatesInStore advances).
                return der
    finally:
        crypt32.CertCloseStore(handle, 0)


# ============================================================================
# Exceptions
# ============================================================================

class UserCancelled(Exception):
    """The user cancelled the cert picker or PIN dialog."""


class CertNotFoundError(Exception):
    """The thumbprint did not match any cert in the user's store."""


class SigningError(Exception):
    """Generic Windows signing failure."""
