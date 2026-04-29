/**
 * Content script (ISOLATED world).
 *
 * Relays window.postMessage from the page (where eSignAgentBridge lives in
 * the MAIN world) to chrome.runtime.sendMessage targeting the background
 * service worker, and back.
 *
 * Why an ISOLATED world content script + a separate MAIN world page-bridge?
 * Because chrome.runtime is only available in extension contexts (ISOLATED
 * + background), but the page's React code lives in MAIN. We use
 * window.postMessage as the pipe between MAIN and ISOLATED — that's the only
 * channel both can speak natively.
 */

(function () {
  // From page → background
  window.addEventListener('message', (ev) => {
    if (ev.source !== window) return;
    const msg = ev.data;
    if (!msg || typeof msg !== 'object') return;
    if (typeof msg.type !== 'string' || !msg.type.startsWith('ESIGN_AGENT_')) return;
    if (msg.__esignagent_relay === 'response') return;  // ignore our own echo

    chrome.runtime.sendMessage(msg, (response) => {
      if (chrome.runtime.lastError) {
        window.postMessage({
          type: 'ESIGN_AGENT_RESPONSE',
          requestId: msg.requestId,
          ok: false,
          error: 'Extension error: ' + chrome.runtime.lastError.message,
          code: 'EXTENSION_ERROR',
          __esignagent_relay: 'response',
        }, '*');
        return;
      }
      // Synchronous response (only used for HOST_NOT_AVAILABLE etc.)
      if (response && response.type === 'ESIGN_AGENT_RESPONSE') {
        window.postMessage({ ...response, __esignagent_relay: 'response' }, '*');
      }
    });
  });

  // Announce extension presence to the page (so detection is instant)
  window.postMessage({
    type: 'ESIGN_AGENT_HELLO',
    version: chrome.runtime.getManifest().version,
    __esignagent_relay: 'hello',
  }, '*');
})();
