/*
 * CEP panel stub for a real Premiere Agent live bridge.
 *
 * This is intentionally a contract skeleton, not a packaged Adobe extension.
 * A production panel should expose these methods via a local HTTP/WebSocket
 * JSON-RPC bridge or forward requests to the Python bridge process.
 */

/* global CSInterface */

function jsonRpcResult(id, result) {
  return { jsonrpc: '2.0', id: id, result: result };
}

function jsonRpcError(id, code, message) {
  return { jsonrpc: '2.0', id: id, error: { code: code, message: message } };
}

function evalPremiereScript(functionName, args) {
  return new Promise(function(resolve, reject) {
    if (typeof CSInterface === 'undefined') {
      reject(new Error('CSInterface unavailable; this stub must run inside a CEP panel'));
      return;
    }
    var cs = new CSInterface();
    var script = functionName + '(' + JSON.stringify(JSON.stringify(args || {})) + ')';
    cs.evalScript(script, function(raw) {
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(new Error('ExtendScript returned non-JSON: ' + raw));
      }
    });
  });
}

async function handlePremiereAgentRpc(request) {
  var id = request && request.id;
  try {
    if (!request || request.jsonrpc !== '2.0' || !request.method) {
      return jsonRpcError(id || null, -32600, 'Invalid JSON-RPC request');
    }
    var params = request.params || {};
    switch (request.method) {
      case 'status':
        return jsonRpcResult(id, await evalPremiereScript('paStatus', params));
      case 'get_active_project':
        return jsonRpcResult(id, await evalPremiereScript('paGetActiveProject', params));
      case 'get_active_sequence':
        return jsonRpcResult(id, await evalPremiereScript('paGetActiveSequence', params));
      case 'snapshot_sequence':
        return jsonRpcResult(id, await evalPremiereScript('paSnapshotSequence', params));
      case 'duplicate_sequence':
        return jsonRpcResult(id, await evalPremiereScript('paDuplicateSequence', params));
      case 'add_marker':
        return jsonRpcResult(id, await evalPremiereScript('paAddMarker', params));
      case 'import_media':
        return jsonRpcResult(id, await evalPremiereScript('paImportMedia', params));
      case 'queue_export':
        return jsonRpcResult(id, await evalPremiereScript('paQueueExport', params));
      case 'apply_basic_lumetri':
        return jsonRpcResult(id, await evalPremiereScript('paApplyBasicLumetri', params));
      case 'set_clip_transform':
        return jsonRpcResult(id, await evalPremiereScript('paSetClipTransform', params));
      default:
        return jsonRpcError(id, -32601, 'Method not found: ' + request.method);
    }
  } catch (err) {
    return jsonRpcError(id || null, -32603, String(err && err.message ? err.message : err));
  }
}

// Export for bundlers/tests; ignored by plain CEP if module is unavailable.
if (typeof module !== 'undefined') {
  module.exports = { handlePremiereAgentRpc: handlePremiereAgentRpc };
}
