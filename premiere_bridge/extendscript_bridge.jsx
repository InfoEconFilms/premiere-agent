/*
 * Premiere Agent Bridge — ExtendScript adapter for Adobe Premiere Pro.
 *
 * CEP loads this file via CSXS manifest. Every public pa* function accepts a
 * JSON string and returns a JSON string so the JavaScript panel can expose the
 * same JSON-RPC contract as the Python mock bridge.
 *
 * Safety: mutating methods require backup_sequence_id in the request. The
 * orchestrator is responsible for calling duplicate_sequence first.
 */

function paJson(result) {
  try { return JSON.stringify(result); }
  catch (err) { return '{"ok":false,"error":"JSON stringify failed"}'; }
}

function paParse(raw) {
  try { return JSON.parse(raw || '{}'); }
  catch (err) { return {}; }
}

function paHasApp() {
  return typeof app !== 'undefined' && app && app.project;
}

function paActiveSequence() {
  if (!paHasApp()) return null;
  return app.project.activeSequence || null;
}

function paSequenceId(seq) {
  if (!seq) return null;
  return String(seq.sequenceID || seq.id || seq.name || 'active_sequence');
}

function paSequenceDurationSeconds(seq) {
  try {
    if (seq && seq.end && seq.end.seconds !== undefined) return Number(seq.end.seconds);
    if (seq && seq.getInPointAsTime && seq.getOutPointAsTime) {
      var outT = seq.getOutPointAsTime();
      if (outT && outT.seconds !== undefined) return Number(outT.seconds);
    }
  } catch (err) {}
  return null;
}

function paMarkerCount(markers) {
  try {
    var count = 0;
    if (!markers) return 0;
    var marker = markers.getFirstMarker ? markers.getFirstMarker() : null;
    while (marker) {
      count += 1;
      marker = markers.getNextMarker ? markers.getNextMarker(marker) : null;
    }
    return count;
  } catch (err) { return null; }
}

function paSequenceSummary(seq) {
  if (!seq) return null;
  return {
    id: paSequenceId(seq),
    name: String(seq.name || ''),
    duration_s: paSequenceDurationSeconds(seq),
    marker_count: paMarkerCount(seq.markers),
    video_track_count: seq.videoTracks ? Number(seq.videoTracks.numTracks || 0) : null,
    audio_track_count: seq.audioTracks ? Number(seq.audioTracks.numTracks || 0) : null
  };
}

function paFindSequence(sequenceId) {
  if (!paHasApp()) return null;
  var wanted = sequenceId ? String(sequenceId) : null;
  if (!wanted) return paActiveSequence();
  var seqs = app.project.sequences;
  if (!seqs) return paActiveSequence();
  try {
    for (var i = 0; i < seqs.numSequences; i += 1) {
      var seq = seqs[i];
      if (!seq) continue;
      if (String(seq.sequenceID || '') === wanted || String(seq.id || '') === wanted || String(seq.name || '') === wanted) return seq;
    }
  } catch (err) {}
  var active = paActiveSequence();
  if (active && (String(paSequenceId(active)) === wanted || String(active.name || '') === wanted)) return active;
  return null;
}

function paRequireBackup(args) {
  if (!args || !args.backup_sequence_id) {
    return { ok: false, error: 'backup_sequence_id required' };
  }
  return null;
}

function paStatus(raw) {
  var seq = paActiveSequence();
  return paJson({
    ok: paHasApp(),
    backend: 'cep_extendscript',
    premiere_connected: paHasApp(),
    project_id: paHasApp() ? String(app.project.documentID || 'premiere_project') : null,
    project_name: paHasApp() ? String(app.project.name || '') : null,
    active_sequence_id: seq ? paSequenceId(seq) : null,
    active_sequence_name: seq ? String(seq.name || '') : null
  });
}

function paGetActiveProject(raw) {
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var seqCount = null;
  try { seqCount = app.project.sequences ? Number(app.project.sequences.numSequences || 0) : 0; } catch (err) {}
  return paJson({
    ok: true,
    project: {
      id: String(app.project.documentID || 'premiere_project'),
      name: String(app.project.name || ''),
      path: String(app.project.path || ''),
      sequence_count: seqCount
    }
  });
}

function paGetActiveSequence(raw) {
  var seq = paActiveSequence();
  if (!seq) return paJson({ ok: false, error: 'No active sequence' });
  return paJson({ ok: true, sequence: paSequenceSummary(seq) });
}

function paSnapshotSequence(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', requested_sequence_id: args.sequence_id || null });
  var clips = [];
  try {
    for (var vt = 0; vt < seq.videoTracks.numTracks; vt += 1) {
      var track = seq.videoTracks[vt];
      for (var c = 0; c < track.clips.numItems; c += 1) {
        var clip = track.clips[c];
        clips.push({
          track_type: 'video',
          track_index: vt,
          id: String(clip.nodeId || clip.name || ('v' + vt + '_' + c)),
          name: String(clip.name || ''),
          start_s: clip.start && clip.start.seconds !== undefined ? Number(clip.start.seconds) : null,
          end_s: clip.end && clip.end.seconds !== undefined ? Number(clip.end.seconds) : null
        });
      }
    }
  } catch (err) {}
  return paJson({
    ok: true,
    snapshot: paSequenceSummary(seq),
    clips: clips,
    verification: {
      kind: 'premiere_sequence_snapshot',
      note: 'CEP ExtendScript snapshot; export a still/contact sheet separately when visual proof is needed.'
    }
  });
}

function paDuplicateSequence(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var backupName = String(args.backup_name || (String(seq.name || 'Sequence') + '_AI_BACKUP'));
  try {
    // Premiere ExtendScript sequence duplication support varies by version.
    // Try clone/duplicate if exposed, otherwise return a clear unsupported result.
    var backup = null;
    if (typeof seq.clone === 'function') backup = seq.clone();
    else if (typeof seq.duplicate === 'function') backup = seq.duplicate();
    if (backup) {
      try { backup.name = backupName; } catch (renameErr) {}
      return paJson({ ok: true, sequence_id: paSequenceId(seq), backup_sequence_id: paSequenceId(backup), backup_name: String(backup.name || backupName) });
    }
  } catch (err) {
    return paJson({ ok: false, error: String(err), sequence_id: paSequenceId(seq), backup_name: backupName });
  }
  return paJson({
    ok: false,
    unsupported: true,
    sequence_id: paSequenceId(seq),
    backup_name: backupName,
    message: 'This Premiere version did not expose sequence clone/duplicate to ExtendScript. Duplicate the sequence manually, then pass its id/name as backup_sequence_id.'
  });
}

function paAddMarker(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq || !seq.markers || !seq.markers.createMarker) return paJson({ ok: false, error: 'Sequence marker API unavailable' });
  try {
    var marker = seq.markers.createMarker(Number(args.time_s || 0));
    marker.name = String(args.label || 'AI Marker');
    marker.comments = String(args.comment || '');
    // Marker color APIs vary by version; ignore if unavailable.
    try { if (args.color && marker.setColorByIndex) marker.setColorByIndex(Number(args.color_index || 0)); } catch (colorErr) {}
    return paJson({
      ok: true,
      sequence_id: paSequenceId(seq),
      marker: {
        id: String(marker.guid || marker.name || ('marker_' + String(args.time_s || 0))),
        time_s: Number(args.time_s || 0),
        label: String(marker.name || args.label || 'AI Marker'),
        comment: String(marker.comments || args.comment || ''),
        backup_sequence_id: args.backup_sequence_id
      }
    });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paImportMedia(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  try {
    var path = String(args.media_path || '');
    if (!path) return paJson({ ok: false, error: 'media_path required' });
    var ok = app.project.importFiles([path], true, app.project.rootItem, false);
    return paJson({ ok: !!ok, sequence_id: args.sequence_id || null, imported: { media_path: path, backup_sequence_id: args.backup_sequence_id }, note: 'Imported to project bin; sequence insertion is version-specific and remains a follow-up operation.' });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paQueueExport(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found' });
  try {
    var outputPath = String(args.output_path || '');
    if (!outputPath) return paJson({ ok: false, error: 'output_path required' });
    if (app.encoder && app.encoder.launchEncoder) app.encoder.launchEncoder();
    // Actual preset paths are site-specific. Return a structured handoff until a
    // production preset file is configured.
    return paJson({
      ok: true,
      queued: false,
      sequence_id: paSequenceId(seq),
      export: {
        output_path: outputPath,
        range_start_s: args.range_start_s || null,
        range_end_s: args.range_end_s || null,
        preset: args.preset || 'match_source_h264',
        backup_sequence_id: args.backup_sequence_id
      },
      next_step: 'Configure a Premiere/AME .epr preset path, then call sequence.exportAsMediaDirect or encoder.encodeSequence.'
    });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paApplyBasicLumetri(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  return paJson({ ok: false, unsupported: true, requested_lumetri: args, message: 'Lumetri control requires clip/component traversal for the target Premiere version.' });
}

function paSetClipTransform(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  return paJson({ ok: false, unsupported: true, requested_transform: args, message: 'Transform control requires stable clip ids from snapshot_sequence and component traversal.' });
}
