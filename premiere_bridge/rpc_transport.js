/* Premiere Agent CEP JSON-RPC transport.
 *
 * Runs inside a CEP panel with Node enabled. It opens a localhost HTTP server
 * and forwards JSON-RPC requests to ExtendScript functions through a supplied
 * `callPremiere(functionName, params)` callback.
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  root.PremiereAgentRpcTransport = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  var DEFAULT_HOST = '127.0.0.1';
  var DEFAULT_PORT = 48791;
  var METHOD_MAP = {
    status: 'paStatus',
    get_active_project: 'paGetActiveProject',
    get_active_sequence: 'paGetActiveSequence',
    snapshot_sequence: 'paSnapshotSequence',
    get_sequence_structure: 'paGetSequenceStructure',
    verify_premiere_connection: 'paVerifyPremiereConnection',
    duplicate_sequence: 'paDuplicateSequence',
    add_marker: 'paAddMarker',
    add_editorial_markers: 'paAddEditorialMarkers',
    list_markers: 'paListMarkers',
    export_sequence_review_frames: 'paExportSequenceReviewFrames',
    import_captions: 'paImportCaptions',
    import_media: 'paImportMedia',
    queue_export: 'paQueueExport',
    apply_basic_lumetri: 'paApplyBasicLumetri',
    set_clip_transform: 'paSetClipTransform',
    move_clip: 'paMoveClip',
    remove_clip: 'paRemoveClip',
    execute_extendscript: 'paExecuteExtendScript',
    evaluate_expression: 'paEvaluateExpression',
    inspect_dom_object: 'paInspectDomObject',
    list_clip_effects: 'paListClipEffects',
    get_effect_properties: 'paGetEffectProperties',
    set_effect_property: 'paSetEffectProperty',
    get_keyframes: 'paGetKeyframes',
    add_keyframe: 'paAddKeyframe',
    remove_keyframe: 'paRemoveKeyframe',
    select_clips_by_name: 'paSelectClipsByName',
    select_all_clips: 'paSelectAllClips',
    deselect_all_clips: 'paDeselectAllClips',
    select_clips_in_range: 'paSelectClipsInRange',
    select_clips_by_color: 'paSelectClipsByColor',
    invert_selection: 'paInvertSelection',
    select_disabled_clips: 'paSelectDisabledClips',
    copy_effect_values: 'paCopyEffectValues',
    remove_effect_by_name: 'paRemoveEffectByName',
    set_blend_mode: 'paSetBlendMode',
    save_project: 'paSaveProject',
    undo: 'paUndo',
    set_active_sequence: 'paSetActiveSequence',
    create_bin: 'paCreateBin',
    delete_bin: 'paDeleteBin',
    rename_bin: 'paRenameBin',
    move_item_to_bin: 'paMoveItemToBin',
    get_item_info: 'paGetItemInfo',
    select_item: 'paSelectItem',
    check_offline_media: 'paCheckOfflineMedia'
  };

  function jsonRpcError(id, code, message) {
    return { jsonrpc: '2.0', id: id === undefined ? null : id, error: { code: code, message: message } };
  }

  function jsonRpcResult(id, result) {
    return { jsonrpc: '2.0', id: id === undefined ? null : id, result: result };
  }

  function createJsonRpcHandler(callPremiere) {
    return async function handle(payload) {
      if (!payload || payload.jsonrpc !== '2.0' || !payload.method) {
        return jsonRpcError(payload && payload.id, -32600, 'Invalid JSON-RPC request');
      }
      if (String(payload.method).indexOf('notifications/') === 0) {
        return null;
      }
      var scriptName = METHOD_MAP[payload.method];
      if (!scriptName) {
        return jsonRpcError(payload.id, -32601, 'Method not found: ' + payload.method);
      }
      try {
        var result = await callPremiere(scriptName, payload.params || {});
        if (result && result.ok === false && result.error) {
          return jsonRpcError(payload.id, -32000, String(result.error));
        }
        return jsonRpcResult(payload.id, result);
      } catch (err) {
        return jsonRpcError(payload.id, -32603, String(err && err.message ? err.message : err));
      }
    };
  }

  function sendJson(res, statusCode, data) {
    var body = data ? JSON.stringify(data, null, 2) : '';
    res.writeHead(statusCode, {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body),
      'Access-Control-Allow-Origin': 'http://127.0.0.1',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    });
    res.end(body);
  }

  function startBridgeServer(callPremiere, options) {
    options = options || {};
    var host = options.host || DEFAULT_HOST;
    var port = Number(options.port || DEFAULT_PORT);
    var log = options.log || function () {};
    var onStatus = options.onStatus || function () {};
    var requireFn = options.require || (typeof require !== 'undefined' ? require : null);
    if (!requireFn) {
      throw new Error('Node require unavailable; CEP manifest must enable Node.js');
    }
    var http = requireFn('http');
    var handler = createJsonRpcHandler(callPremiere);
    var server = http.createServer(function (req, res) {
      if (req.method === 'OPTIONS') {
        sendJson(res, 204, null);
        return;
      }
      if (req.method === 'GET' && (req.url === '/health' || req.url === '/status')) {
        sendJson(res, 200, { ok: true, service: 'premiere-agent-cep-bridge', url: 'http://' + host + ':' + port + '/jsonrpc' });
        return;
      }
      if (req.method !== 'POST' || req.url !== '/jsonrpc') {
        sendJson(res, 404, { ok: false, error: 'not_found' });
        return;
      }
      var raw = '';
      req.on('data', function (chunk) { raw += chunk; });
      req.on('end', async function () {
        var payload;
        try {
          payload = JSON.parse(raw || '{}');
        } catch (err) {
          sendJson(res, 400, jsonRpcError(null, -32700, 'Parse error: ' + err.message));
          return;
        }
        var response = await handler(payload);
        if (response === null) {
          sendJson(res, 204, null);
        } else {
          sendJson(res, 200, response);
        }
      });
    });
    server.on('error', function (err) {
      var message = 'Bridge server error: ' + (err && err.message ? err.message : err);
      log(message);
      onStatus({ ok: false, message: message });
    });
    server.listen(port, host, function () {
      var url = 'http://' + host + ':' + port + '/jsonrpc';
      log('Premiere Agent bridge listening at ' + url);
      onStatus({ ok: true, url: url });
    });
    return server;
  }

  return {
    DEFAULT_HOST: DEFAULT_HOST,
    DEFAULT_PORT: DEFAULT_PORT,
    METHOD_MAP: METHOD_MAP,
    createJsonRpcHandler: createJsonRpcHandler,
    startBridgeServer: startBridgeServer
  };
}));
