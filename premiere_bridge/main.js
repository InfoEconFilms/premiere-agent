/* global CSInterface, PremiereAgentRpcTransport */
(function () {
  'use strict';

  var cs = typeof CSInterface !== 'undefined' ? new CSInterface() : null;
  var logEl = document.getElementById('log');
  var statusEl = document.getElementById('status');
  var bridgeEl = document.getElementById('bridge');
  var server = null;
  var jsxLoaded = false;

  function log(value) {
    var text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    logEl.textContent = text;
  }

  function setBridgeStatus(message, ok) {
    bridgeEl.textContent = message;
    bridgeEl.className = ok ? 'ok' : 'warn';
  }

  function evalScriptRaw(script) {
    return new Promise(function (resolve, reject) {
      if (!cs) {
        reject(new Error('CSInterface unavailable. Open this as a Premiere CEP panel.'));
        return;
      }
      cs.evalScript(script, function (raw) {
        resolve(raw);
      });
    });
  }

  function quoteForExtendScript(value) {
    return '"' + String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
  }

  async function ensureExtendScriptBridgeLoaded() {
    if (jsxLoaded) return;
    if (!cs || typeof cs.getSystemPath !== 'function' || typeof SystemPath === 'undefined') {
      // Fall back to the manifest <ScriptPath>. If that failed, the function call
      // below will return the original EvalScript error for easier diagnosis.
      jsxLoaded = true;
      return;
    }
    var extensionPath = cs.getSystemPath(SystemPath.EXTENSION);
    if (!extensionPath) {
      jsxLoaded = true;
      return;
    }
    var jsxPath = extensionPath.replace(/\\/g, '/') + '/extendscript_bridge.jsx';
    var script = 'try { $.evalFile(' + quoteForExtendScript(jsxPath) + '); "__PA_BRIDGE_LOADED__"; } catch (e) { "__PA_BRIDGE_LOAD_ERROR__" + e.toString(); }';
    var raw = await evalScriptRaw(script);
    if (String(raw).indexOf('__PA_BRIDGE_LOAD_ERROR__') === 0) {
      throw new Error('Could not load extendscript_bridge.jsx: ' + raw);
    }
    jsxLoaded = true;
  }

  function evalScript(functionName, args) {
    return new Promise(function (resolve, reject) {
      if (!cs) {
        reject(new Error('CSInterface unavailable. Open this as a Premiere CEP panel.'));
        return;
      }
      ensureExtendScriptBridgeLoaded().then(function () {
        var script = functionName + '(' + JSON.stringify(JSON.stringify(args || {})) + ')';
        cs.evalScript(script, function (raw) {
          try {
            resolve(JSON.parse(raw));
          } catch (err) {
            reject(new Error('ExtendScript returned non-JSON: ' + raw));
          }
        });
      }).catch(reject);
    });
  }

  async function call(functionName, args) {
    try {
      var result = await evalScript(functionName, args);
      log(result);
      statusEl.textContent = result && result.ok ? 'Premiere script responding' : 'Premiere script returned warning';
      statusEl.className = result && result.ok ? 'ok' : 'warn';
      return result;
    } catch (err) {
      statusEl.textContent = 'error';
      statusEl.className = 'warn';
      log(String(err && err.message ? err.message : err));
      return null;
    }
  }

  function startTransport() {
    if (server) return;
    if (!PremiereAgentRpcTransport || !PremiereAgentRpcTransport.startBridgeServer) {
      setBridgeStatus('transport unavailable', false);
      return;
    }
    try {
      server = PremiereAgentRpcTransport.startBridgeServer(evalScript, {
        host: '127.0.0.1',
        port: 48791,
        log: log,
        onStatus: function (state) {
          if (state && state.ok) setBridgeStatus(state.url, true);
          else setBridgeStatus(state && state.message ? state.message : 'bridge unavailable', false);
        }
      });
    } catch (err) {
      setBridgeStatus(String(err && err.message ? err.message : err), false);
    }
  }

  document.getElementById('btnStatus').addEventListener('click', function () {
    call('paStatus', {});
  });

  document.getElementById('btnSequence').addEventListener('click', function () {
    call('paGetActiveSequence', {});
  });

  document.getElementById('btnBackup').addEventListener('click', async function () {
    var seq = await call('paGetActiveSequence', {});
    var sequenceId = seq && seq.sequence && seq.sequence.id;
    if (sequenceId) {
      call('paDuplicateSequence', { sequence_id: sequenceId, backup_name: seq.sequence.name + '_AI_BACKUP' });
    }
  });

  startTransport();
}());
