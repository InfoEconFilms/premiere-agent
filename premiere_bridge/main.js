/* global CSInterface */
(function () {
  'use strict';

  var cs = typeof CSInterface !== 'undefined' ? new CSInterface() : null;
  var logEl = document.getElementById('log');
  var statusEl = document.getElementById('status');

  function log(value) {
    var text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    logEl.textContent = text;
  }

  function evalScript(functionName, args) {
    return new Promise(function (resolve, reject) {
      if (!cs) {
        reject(new Error('CSInterface unavailable. Open this as a Premiere CEP panel.'));
        return;
      }
      var script = functionName + '(' + JSON.stringify(JSON.stringify(args || {})) + ')';
      cs.evalScript(script, function (raw) {
        try {
          resolve(JSON.parse(raw));
        } catch (err) {
          reject(new Error('ExtendScript returned non-JSON: ' + raw));
        }
      });
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
}());
