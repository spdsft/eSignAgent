"""
eSignAgent native host — entry point.

Started by the browser (Chrome/Edge/Firefox) via the Native Messaging
manifest registered at install time. Reads JSON requests from stdin,
performs the signing operation against the OS Cert Store, writes JSON
responses to stdout.

Each browser tab gets its own host process; the process lives as long
as the connection is open. We loop reading requests until EOF.

Request schema:
    { "id": "<correlation>", "action": "ping" }
    { "id": "<correlation>", "action": "selectCertificate" }
    { "id": "<correlation>", "action": "signPdfHash",
      "thumbprint": "HEX", "hash_b64": "BASE64" }

Response schema:
    { "id": "<correlation>", "ok": true,  "data": <action-specific> }
    { "id": "<correlation>", "ok": false, "error": "<message>",
      "code": "USER_CANCELLED" | "CERT_NOT_FOUND" | "SIGNING_ERROR" | "INTERNAL" }
"""
import logging
import os
import sys
import traceback
from pathlib import Path

from . import __version__
from .messaging import read_message, send_message


# ----- Logging to a file in user's local app data (NEVER stdout — would corrupt the wire) -----
def _setup_logging() -> None:
    log_dir = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "eSignAgent" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "host.log"
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


_setup_logging()
logger = logging.getLogger("esignagent")


def _platform_signer():
    if sys.platform == "win32":
        from . import windows_signer
        return windows_signer
    if sys.platform == "darwin":
        from . import macos_signer
        return macos_signer
    from . import linux_signer
    return linux_signer


def handle_request(req: dict) -> dict:
    req_id = req.get("id")
    action = req.get("action")

    if not action:
        return {"id": req_id, "ok": False, "error": "Missing 'action' field", "code": "BAD_REQUEST"}

    try:
        if action == "ping":
            return {"id": req_id, "ok": True, "data": {
                "host": "esignagent-host",
                "version": __version__,
                "platform": sys.platform,
            }}

        signer = _platform_signer()

        if action == "selectCertificate":
            cert = signer.select_certificate_via_native_picker()
            return {"id": req_id, "ok": True, "data": cert}

        if action == "signPdfHash":
            thumbprint = req.get("thumbprint")
            hash_b64 = req.get("hash_b64")
            if not thumbprint or not hash_b64:
                return {"id": req_id, "ok": False, "error": "Missing thumbprint or hash_b64", "code": "BAD_REQUEST"}
            result = signer.sign_pdf_hash(thumbprint, hash_b64)
            return {"id": req_id, "ok": True, "data": result}

        return {"id": req_id, "ok": False, "error": f"Unknown action: {action}", "code": "BAD_REQUEST"}

    except Exception as exc:
        logger.exception("handle_request failed")
        cls = type(exc).__name__
        code = "INTERNAL"
        if cls == "UserCancelled":
            code = "USER_CANCELLED"
        elif cls == "CertNotFoundError":
            code = "CERT_NOT_FOUND"
        elif cls == "SigningError":
            code = "SIGNING_ERROR"
        return {
            "id": req_id, "ok": False,
            "error": str(exc) or cls,
            "code": code,
        }


def main() -> int:
    logger.info("Host started, version=%s, platform=%s", __version__, sys.platform)
    try:
        while True:
            req = read_message()
            if req is None:
                logger.info("Browser closed connection, exiting")
                return 0
            response = handle_request(req)
            send_message(response)
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("Fatal error in main loop")
        return 1


if __name__ == "__main__":
    sys.exit(main())
