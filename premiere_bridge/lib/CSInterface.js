/* Minimal CSInterface shim for the Premiere Agent Bridge CEP panel.
 * Real CEP hosts expose window.__adobe_cep__.evalScript. This file avoids
 * depending on Adobe's full sample library for the first bridge scaffold.
 */
(function (global) {
  'use strict';
  if (!global.SystemPath) {
    global.SystemPath = { EXTENSION: 'extension' };
  }
  if (global.CSInterface) return;
  function CSInterface() {}
  CSInterface.prototype.evalScript = function (script, callback) {
    if (!global.__adobe_cep__ || typeof global.__adobe_cep__.evalScript !== 'function') {
      if (callback) callback(JSON.stringify({ ok: false, error: 'Adobe CEP host unavailable' }));
      return;
    }
    global.__adobe_cep__.evalScript(script, callback || function () {});
  };
  CSInterface.prototype.getSystemPath = function (pathType) {
    if (!global.__adobe_cep__ || typeof global.__adobe_cep__.getSystemPath !== 'function') {
      return '';
    }
    return global.__adobe_cep__.getSystemPath(pathType);
  };
  global.CSInterface = CSInterface;
}(this));
