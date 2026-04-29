/**
 * Page-bridge (MAIN world): exposes `window.eSignAgent` to the application's
 * React code so it can call signing operations without dealing with
 * postMessage plumbing.
 *
 * API (Promise-based):
 *   await window.eSignAgent.ping()
 *      -> { host, version, platform }
 *
 *   await window.eSignAgent.selectCertificate()
 *      -> { thumbprint, subject, issuer, serial, valid_from, valid_until }
 *
 *   await window.eSignAgent.signPdfHash(thumbprint, hashB64)
 *      -> { cms_b64, signer_subject, signer_issuer, signer_thumbprint }
 *
 * If the extension is not installed, window.eSignAgent will be `undefined`.
 */

(function () {
  if (window.eSignAgent) return;

  const _pending = new Map();

  window.addEventListener('message', (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.__esignagent_relay !== 'response') return;
    if (msg.type !== 'ESIGN_AGENT_RESPONSE') return;

    const handlers = _pending.get(msg.requestId);
    if (!handlers) return;
    _pending.delete(msg.requestId);
    if (msg.ok) handlers.resolve(msg.data);
    else {
      const err = new Error(msg.error || 'Unknown signing error');
      err.code = msg.code;
      handlers.reject(err);
    }
  });

  function _send(type, payload) {
    const requestId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    return new Promise((resolve, reject) => {
      _pending.set(requestId, { resolve, reject });
      window.postMessage({ type, requestId, ...payload }, '*');
      // Safety timeout (30s for ping/select, 120s for sign — PIN entry can be slow)
      const timeoutMs = type === 'ESIGN_AGENT_SIGN_HASH' ? 120_000 : 30_000;
      setTimeout(() => {
        if (_pending.has(requestId)) {
          _pending.delete(requestId);
          const err = new Error('eSignAgent: timeout');
          err.code = 'TIMEOUT';
          reject(err);
        }
      }, timeoutMs);
    });
  }

  window.eSignAgent = Object.freeze({
    ping: () => _send('ESIGN_AGENT_PING', {}),
    selectCertificate: () => _send('ESIGN_AGENT_SELECT_CERT', {}),
    signPdfHash: (thumbprint, hash_b64) =>
      _send('ESIGN_AGENT_SIGN_HASH', { thumbprint, hash_b64 }),
  });
})();
