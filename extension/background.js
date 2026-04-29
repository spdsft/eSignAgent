/**
 * eSignAgent — extension background (service worker).
 *
 * Bridges:
 *   page (MAIN-world) <-> content_script (ISOLATED) <-> this background <->
 *   Native Messaging Host (esignagent-host.exe)
 *
 * Each page request is correlated by `requestId`. We keep one native port for
 * the service-worker lifetime; reconnects on disconnect.
 *
 * Page → content → background:
 *   { type: 'ESIGN_AGENT_PING', requestId }
 *   { type: 'ESIGN_AGENT_SELECT_CERT', requestId }
 *   { type: 'ESIGN_AGENT_SIGN_HASH', requestId, thumbprint, hash_b64 }
 *
 * Response is delivered via the same sendResponse callback (channel kept
 * open with `return true`). content.js relays it back to the page.
 */

const NATIVE_HOST_NAME = 'com.esignagent.host';

// hostId -> sendResponse callback (waiting for native host reply)
const _pending = new Map();

let _port = null;
let _portConnectError = null;


function _ensurePort() {
  if (_port) return _port;
  try {
    _port = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    _portConnectError = null;
  } catch (err) {
    _portConnectError = err && err.message ? err.message : String(err);
    console.error('[eSignAgent] connectNative failed:', _portConnectError);
    return null;
  }

  _port.onMessage.addListener((nativeMsg) => {
    const hostId = nativeMsg && nativeMsg.id;
    const cb = _pending.get(hostId);
    if (!cb) {
      console.warn('[eSignAgent] Native response with unknown id:', hostId);
      return;
    }
    _pending.delete(hostId);
    try {
      cb({
        ok: !!nativeMsg.ok,
        data: nativeMsg.data,
        error: nativeMsg.error,
        code: nativeMsg.code,
      });
    } catch (err) {
      // sendResponse can throw if the channel was already closed (tab gone)
      console.warn('[eSignAgent] sendResponse failed:', err && err.message);
    }
  });

  _port.onDisconnect.addListener(() => {
    const lastErr = chrome.runtime.lastError && chrome.runtime.lastError.message;
    console.warn('[eSignAgent] Native port disconnected:', lastErr);
    for (const cb of _pending.values()) {
      try {
        cb({
          ok: false,
          error: 'Native host disconnected: ' + (lastErr || 'unknown'),
          code: 'HOST_DISCONNECTED',
        });
      } catch (_e) { /* channel closed */ }
    }
    _pending.clear();
    _port = null;
  });

  return _port;
}


chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type || !message.type.startsWith('ESIGN_AGENT_')) {
    return false;
  }

  // Wrap sendResponse so the response always carries the page's requestId
  const reply = (payload) => {
    try {
      sendResponse({
        type: 'ESIGN_AGENT_RESPONSE',
        requestId: message.requestId,
        ...payload,
      });
    } catch (err) {
      console.warn('[eSignAgent] reply failed:', err && err.message);
    }
  };

  const port = _ensurePort();
  if (!port) {
    reply({
      ok: false,
      error: 'Native host not installed or could not be started: '
        + (_portConnectError || 'unknown'),
      code: 'HOST_NOT_AVAILABLE',
    });
    return false; // synchronous reply already sent
  }

  // Build host-level request
  const hostId = String(Date.now()) + '-' + Math.random().toString(36).slice(2, 10);
  let req;
  if (message.type === 'ESIGN_AGENT_PING') {
    req = { id: hostId, action: 'ping' };
  } else if (message.type === 'ESIGN_AGENT_SELECT_CERT') {
    req = { id: hostId, action: 'selectCertificate' };
  } else if (message.type === 'ESIGN_AGENT_SIGN_HASH') {
    req = {
      id: hostId, action: 'signPdfHash',
      thumbprint: message.thumbprint,
      hash_b64: message.hash_b64,
    };
  } else {
    reply({ ok: false, error: 'Unknown request type: ' + message.type, code: 'BAD_REQUEST' });
    return false;
  }

  _pending.set(hostId, reply);

  try {
    port.postMessage(req);
  } catch (err) {
    _pending.delete(hostId);
    reply({
      ok: false,
      error: 'Failed to forward to native host: ' + (err && err.message),
      code: 'HOST_WRITE_FAILED',
    });
    return false;
  }

  // Keep the channel open until the native host replies
  return true;
});
