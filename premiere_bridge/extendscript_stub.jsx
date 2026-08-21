/*
 * ExtendScript function stubs for Premiere Agent CEP bridge.
 *
 * Each function accepts a JSON string and returns a JSON string. The CEP panel
 * wrapper is responsible for turning JSON-RPC requests into these calls.
 * Fill these functions with real Premiere Pro ExtendScript calls when packaging
 * the panel.
 */

function paJson(result) {
  return JSON.stringify(result);
}

function paParse(raw) {
  try { return JSON.parse(raw || '{}'); }
  catch (err) { return {}; }
}

function paStatus(raw) {
  return paJson({
    ok: true,
    backend: 'cep_stub',
    premiere_connected: !!app,
    note: 'Stub only: replace with real project/sequence probes.'
  });
}

function paGetActiveProject(raw) {
  return paJson({
    ok: true,
    project: {
      id: app && app.project ? String(app.project.documentID || 'premiere_project') : null,
      name: app && app.project ? String(app.project.name || '') : null,
      path: app && app.project ? String(app.project.path || '') : null
    }
  });
}

function paGetActiveSequence(raw) {
  var seq = app && app.project ? app.project.activeSequence : null;
  return paJson({
    ok: !!seq,
    sequence: seq ? {
      id: String(seq.sequenceID || seq.name),
      name: String(seq.name || ''),
      // ExtendScript exposes many details inconsistently by Premiere version;
      // real implementation should include duration, fps, dimensions, in/out.
      duration_s: null,
      marker_count: null
    } : null
  });
}

function paSnapshotSequence(raw) {
  var args = paParse(raw);
  return paJson({
    ok: true,
    snapshot: {
      requested_sequence_id: args.sequence_id || null,
      note: 'Stub only: return tracks, clips, markers, selected range, and optional still/contact sheet path.'
    }
  });
}

function paDuplicateSequence(raw) {
  var args = paParse(raw);
  // Real implementation: find sequence by id/name, clone it, return new id.
  return paJson({
    ok: false,
    unsupported_stub: true,
    sequence_id: args.sequence_id || null,
    backup_name: args.backup_name || null,
    message: 'Implement using Premiere sequence clone/duplicate API for your target version.'
  });
}

function paAddMarker(raw) {
  var args = paParse(raw);
  if (!args.backup_sequence_id) {
    return paJson({ ok: false, error: 'backup_sequence_id required' });
  }
  // Real implementation: use sequence.markers.createMarker(seconds) and set name/comment/color if available.
  return paJson({ ok: false, unsupported_stub: true, requested_marker: args });
}

function paImportMedia(raw) {
  var args = paParse(raw);
  if (!args.backup_sequence_id) {
    return paJson({ ok: false, error: 'backup_sequence_id required' });
  }
  // Real implementation: app.project.importFiles([...]) then insert/overwrite clip at requested time/track.
  return paJson({ ok: false, unsupported_stub: true, requested_import: args });
}

function paQueueExport(raw) {
  var args = paParse(raw);
  if (!args.backup_sequence_id) {
    return paJson({ ok: false, error: 'backup_sequence_id required' });
  }
  // Real implementation: app.encoder.launchEncoder(); sequence.exportAsMediaDirect or AME queue APIs.
  return paJson({ ok: false, unsupported_stub: true, requested_export: args });
}

function paApplyBasicLumetri(raw) {
  var args = paParse(raw);
  return paJson({ ok: false, unsupported_stub: true, requested_lumetri: args });
}

function paSetClipTransform(raw) {
  var args = paParse(raw);
  return paJson({ ok: false, unsupported_stub: true, requested_transform: args });
}
