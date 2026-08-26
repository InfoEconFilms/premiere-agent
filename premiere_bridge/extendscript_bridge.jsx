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

function paEscapeJsonString(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r/g, '\\r').replace(/\n/g, '\\n').replace(/\t/g, '\\t');
}

function paJsonValue(value) {
  var t = typeof value;
  if (value === null || value === undefined) return 'null';
  if (t === 'string') return '"' + paEscapeJsonString(value) + '"';
  if (t === 'number') return isFinite(value) ? String(value) : 'null';
  if (t === 'boolean') return value ? 'true' : 'false';
  if (value instanceof Array) {
    var arr = [];
    for (var i = 0; i < value.length; i += 1) arr.push(paJsonValue(value[i]));
    return '[' + arr.join(',') + ']';
  }
  if (t === 'object') {
    var parts = [];
    for (var key in value) {
      if (value.hasOwnProperty(key) && typeof value[key] !== 'function') {
        parts.push('"' + paEscapeJsonString(key) + '":' + paJsonValue(value[key]));
      }
    }
    return '{' + parts.join(',') + '}';
  }
  return 'null';
}

function paJson(result) {
  try { return paJsonValue(result); }
  catch (err) { return '{"ok":false,"error":"paJson failed: ' + paEscapeJsonString(String(err)) + '"}'; }
}

function paParse(raw) {
  try {
    if (typeof JSON !== 'undefined' && JSON.parse) return JSON.parse(raw || '{}');
  } catch (jsonErr) {}
  try { return eval('(' + (raw || '{}') + ')'); }
  catch (err) { return {}; }
}

function paHasApp() {
  return !!(typeof app !== 'undefined' && app && app.project);
}

function paActiveSequence() {
  if (!paHasApp()) return null;
  return app.project.activeSequence || null;
}

function paSequenceId(seq) {
  if (!seq) return null;
  return String(seq.sequenceID || seq.id || seq.name || 'active_sequence');
}

function paClipEndSeconds(clip) {
  try {
    if (clip && clip.end && clip.end.seconds !== undefined) {
      var endS = Number(clip.end.seconds);
      if (isFinite(endS) && endS >= 0) return endS;
    }
  } catch (err) {}
  return null;
}

function paMaxClipEndSeconds(seq) {
  var maxEnd = null;
  try {
    if (seq && seq.videoTracks) {
      for (var vt = 0; vt < seq.videoTracks.numTracks; vt += 1) {
        var vTrack = seq.videoTracks[vt];
        for (var vc = 0; vc < vTrack.clips.numItems; vc += 1) {
          var vEnd = paClipEndSeconds(vTrack.clips[vc]);
          if (vEnd !== null && (maxEnd === null || vEnd > maxEnd)) maxEnd = vEnd;
        }
      }
    }
  } catch (vErr) {}
  try {
    if (seq && seq.audioTracks) {
      for (var at = 0; at < seq.audioTracks.numTracks; at += 1) {
        var aTrack = seq.audioTracks[at];
        for (var ac = 0; ac < aTrack.clips.numItems; ac += 1) {
          var aEnd = paClipEndSeconds(aTrack.clips[ac]);
          if (aEnd !== null && (maxEnd === null || aEnd > maxEnd)) maxEnd = aEnd;
        }
      }
    }
  } catch (aErr) {}
  return maxEnd;
}

function paSequenceDurationSeconds(seq) {
  var duration = null;
  try {
    if (seq && seq.end && seq.end.seconds !== undefined) {
      duration = Number(seq.end.seconds);
      if (isFinite(duration) && duration >= 0) return duration;
    }
    if (seq && seq.getOutPointAsTime) {
      var outT = seq.getOutPointAsTime();
      if (outT && outT.seconds !== undefined) {
        duration = Number(outT.seconds);
        if (isFinite(duration) && duration >= 0) return duration;
      }
    }
  } catch (err) {}
  return paMaxClipEndSeconds(seq);
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
  if (!wanted || wanted === 'active_sequence' || wanted === 'active') return paActiveSequence();
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

function paFindProjectItem(nodeIdOrName, rootItem) {
  if (!paHasApp()) return null;
  if (!rootItem) rootItem = app.project.rootItem;
  if (!rootItem || !rootItem.children) return null;
  for (var i = 0; i < rootItem.children.numItems; i += 1) {
    var item = rootItem.children[i];
    if (!item) continue;
    if (String(item.nodeId || '') === nodeIdOrName || String(item.name || '') === nodeIdOrName) return item;
    if (item.type === 2) { // Bin
      var found = paFindProjectItem(nodeIdOrName, item);
      if (found) return found;
    }
  }
  return null;
}

// Import puts the new item's ProjectItem name at the file's display name (usually
// the filename with extension). Some Premiere builds strip the extension instead,
// so try both before giving up.
function paFindImportedProjectItem(filePath) {
  var raw = String(filePath || '');
  var slashAt = raw.lastIndexOf('/');
  var backslashAt = raw.lastIndexOf('\\');
  var cut = slashAt > backslashAt ? slashAt : backslashAt;
  var fullName = decodeURI(cut >= 0 ? raw.substring(cut + 1) : raw);
  var dot = fullName.lastIndexOf('.');
  var baseName = dot > 0 ? fullName.substring(0, dot) : fullName;
  return paFindProjectItem(fullName) || paFindProjectItem(baseName);
}

// Sequence.CAPTION_FORMAT_* are static constants on the Sequence class exposed by
// Premiere's ExtendScript engine (added alongside scriptable caption-track
// creation). Default to subtitle (SRT/VTT) since that is what import_captions is
// documented to accept.
function paCaptionFormatConstant(name) {
  var key = String(name || 'subtitle').toLowerCase();
  try {
    if (typeof Sequence === 'undefined') return null;
    if (key === '608') return Sequence.CAPTION_FORMAT_608;
    if (key === '708') return Sequence.CAPTION_FORMAT_708;
    if (key === 'teletext') return Sequence.CAPTION_FORMAT_TELETEXT;
    if (key === 'ebu' || key === 'open_ebu') return Sequence.CAPTION_FORMAT_OPEN_EBU;
    if (key === 'op42') return Sequence.CAPTION_FORMAT_OP42;
    if (key === 'op47') return Sequence.CAPTION_FORMAT_OP47;
    return Sequence.CAPTION_FORMAT_SUBTITLE;
  } catch (err) {
    return null;
  }
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

function paVerifyPremiereConnection(raw) {
  var projectOpen = !!paHasApp();
  var seq = projectOpen ? paActiveSequence() : null;
  var sequenceOpen = !!seq;
  var snapshotReadable = false;
  var durationValid = false;
  var trackReadable = false;
  try {
    if (seq) {
      var duration = paSequenceDurationSeconds(seq);
      durationValid = duration === null || (isFinite(duration) && duration >= 0);
      trackReadable = !!(seq.videoTracks && seq.audioTracks);
      snapshotReadable = trackReadable;
    }
  } catch (err) {}
  var overall = 'unreachable';
  if (projectOpen && sequenceOpen && snapshotReadable && durationValid) overall = 'ready';
  else if (projectOpen && !sequenceOpen) overall = 'needs_active_sequence';
  else if (projectOpen) overall = 'needs_readable_sequence';
  return paJson({
    ok: true,
    backend: 'cep_extendscript',
    overall: overall,
    checks: {
      bridge_reachable: true,
      premiere_project_open: projectOpen,
      active_sequence_open: sequenceOpen,
      read_only_snapshot_ok: snapshotReadable,
      duration_non_negative_or_null: durationValid,
      track_collections_readable: trackReadable
    },
    privacy: 'This read-only check intentionally omits project names, paths, media paths, and clip names.',
    mutates_project: false,
    next_step: overall === 'ready' ? 'Safe to call get_sequence_structure or make an explicitly confirmed backup.' : 'Open a Premiere project and active sequence, then retry.'
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

function paClipSummary(clip, trackType, trackIndex, clipIndex) {
  var startS = null;
  var endS = null;
  var durationS = null;
  var inS = null;
  var outS = null;
  try { if (clip.start && clip.start.seconds !== undefined) startS = Number(clip.start.seconds); } catch (err1) {}
  try { if (clip.end && clip.end.seconds !== undefined) endS = Number(clip.end.seconds); } catch (err2) {}
  try { if (clip.duration && clip.duration.seconds !== undefined) durationS = Number(clip.duration.seconds); } catch (err3) {}
  try { if (clip.inPoint && clip.inPoint.seconds !== undefined) inS = Number(clip.inPoint.seconds); } catch (err4) {}
  try { if (clip.outPoint && clip.outPoint.seconds !== undefined) outS = Number(clip.outPoint.seconds); } catch (err5) {}
  var enabled = null;
  try { if (typeof clip.isDisabled === 'function') enabled = !clip.isDisabled(); } catch (err6) {}
  return {
    track_type: trackType,
    track_index: trackIndex,
    clip_index: clipIndex,
    id: String(clip.nodeId || clip.guid || clip.name || (trackType + trackIndex + '_' + clipIndex)),
    name: String(clip.name || ''),
    start_s: startS,
    end_s: endS,
    duration_s: durationS,
    in_s: inS,
    out_s: outS,
    enabled: enabled
  };
}

function paTrackStructure(track, trackType, trackIndex) {
  var clips = [];
  var gaps = [];
  var previousEnd = 0;
  try {
    for (var c = 0; c < track.clips.numItems; c += 1) {
      var clipInfo = paClipSummary(track.clips[c], trackType, trackIndex, c);
      if (clipInfo.start_s !== null && previousEnd !== null && clipInfo.start_s > previousEnd) {
        gaps.push({ start_s: previousEnd, end_s: clipInfo.start_s, duration_s: clipInfo.start_s - previousEnd });
      }
      if (clipInfo.end_s !== null && (previousEnd === null || clipInfo.end_s > previousEnd)) previousEnd = clipInfo.end_s;
      clips.push(clipInfo);
    }
  } catch (err) {}
  var muted = null;
  var locked = null;
  try { if (typeof track.isMuted === 'function') muted = track.isMuted(); } catch (muteErr) {}
  try { if (typeof track.isLocked === 'function') locked = track.isLocked(); } catch (lockErr) {}
  return {
    type: trackType,
    index: trackIndex,
    name: String(track.name || (trackType.toUpperCase() + String(trackIndex + 1))),
    clip_count: clips.length,
    muted: muted,
    locked: locked,
    clips: clips,
    gaps: gaps
  };
}

function paGetSequenceStructure(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', requested_sequence_id: args.sequence_id || null });
  var videoTracks = [];
  var audioTracks = [];
  try {
    for (var vt = 0; vt < seq.videoTracks.numTracks; vt += 1) {
      videoTracks.push(paTrackStructure(seq.videoTracks[vt], 'video', vt));
    }
  } catch (vErr) {}
  try {
    for (var at = 0; at < seq.audioTracks.numTracks; at += 1) {
      audioTracks.push(paTrackStructure(seq.audioTracks[at], 'audio', at));
    }
  } catch (aErr) {}
  var playheadS = null;
  try {
    var pos = seq.getPlayerPosition ? seq.getPlayerPosition() : null;
    if (pos && pos.seconds !== undefined) playheadS = Number(pos.seconds);
  } catch (posErr) {}
  var clipTotal = 0;
  for (var vi = 0; vi < videoTracks.length; vi += 1) clipTotal += videoTracks[vi].clip_count;
  for (var ai = 0; ai < audioTracks.length; ai += 1) clipTotal += audioTracks[ai].clip_count;
  return paJson({
    ok: true,
    sequence: paSequenceSummary(seq),
    playhead_s: playheadS,
    total_clip_count: clipTotal,
    video_tracks: videoTracks,
    audio_tracks: audioTracks,
    verification: {
      kind: 'premiere_sequence_structure',
      boundary: 'host_snapshot',
      mutates_project: false
    }
  });
}

function paDuplicateSequence(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var backupName = String(args.backup_name || (String(seq.name || 'Sequence') + '_AI_BACKUP'));
  if (typeof seq.clone !== 'function' && typeof seq.duplicate !== 'function') {
    return paJson({
      ok: false,
      unsupported: true,
      sequence_id: paSequenceId(seq),
      backup_name: backupName,
      message: 'This Premiere version did not expose sequence clone/duplicate to ExtendScript. Duplicate the sequence manually, then pass its id/name as backup_sequence_id.'
    });
  }
  try {
    // clone()/duplicate() returns a bare success flag rather than a reference to
    // the new Sequence object on some Premiere builds, so locate the new sequence
    // by diffing app.project.sequences before/after the call instead of trusting
    // the return value. Diff by paSequenceId() string, not object reference (===):
    // indexing into an ExtendScript collection can hand back a fresh JS wrapper
    // each time even for the same underlying native sequence, so reference
    // equality between two separate reads is unreliable and would otherwise make
    // every existing sequence look "new".
    var seqs = app.project.sequences;
    var beforeIds = {};
    if (seqs) {
      for (var i = 0; i < seqs.numSequences; i += 1) {
        var existing = seqs[i];
        if (existing) beforeIds[paSequenceId(existing)] = true;
      }
    }
    var cloneResult = (typeof seq.clone === 'function') ? seq.clone() : seq.duplicate();
    if (!cloneResult) {
      return paJson({
        ok: false,
        unsupported: true,
        sequence_id: paSequenceId(seq),
        backup_name: backupName,
        message: 'Premiere rejected the sequence clone/duplicate call.'
      });
    }
    var backup = (cloneResult && typeof cloneResult === 'object' && cloneResult.name !== undefined) ? cloneResult : null;
    if (!backup && seqs) {
      for (var j = 0; j < seqs.numSequences; j += 1) {
        var candidate = seqs[j];
        if (candidate && !beforeIds[paSequenceId(candidate)]) { backup = candidate; break; }
      }
    }
    if (!backup) {
      return paJson({ ok: false, error: 'Sequence duplicated but the new sequence could not be located', sequence_id: paSequenceId(seq), backup_name: backupName });
    }
    // Sequence.name (and ProjectItem.name) are effectively read-only via
    // ExtendScript: the assignment is accepted by the JS wrapper object without
    // throwing, so reading it back in this same script reports the fake value —
    // but it is never actually pushed down to Premiere's real sequence data, so
    // the Project panel keeps showing Premiere's own auto-generated "Copy" name.
    // This is a documented Adobe limitation (createSetNameAction() exists but has
    // no script-callable execute()), not something this bridge can work around.
    var actualName = String(backup.name || '');
    return paJson({
      ok: true,
      sequence_id: paSequenceId(seq),
      backup_sequence_id: paSequenceId(backup),
      backup_name: backupName,
      actual_backup_name: actualName,
      backup_name_applied: false,
      note: 'Sequence duplicated successfully, but Premiere\'s ExtendScript API does not support renaming a sequence, so it kept its auto-generated name (see actual_backup_name) instead of backup_name. Use backup_sequence_id to reference it reliably.'
    });
  } catch (err) {
    return paJson({ ok: false, error: String(err), sequence_id: paSequenceId(seq), backup_name: backupName });
  }
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

function paMarkerTimeSeconds(marker, propName) {
  try {
    var t = marker[propName || 'start'];
    if (t && t.seconds !== undefined) return Number(t.seconds);
    if (typeof t === 'number') return Number(t);
  } catch (err) {}
  return null;
}

function paMarkerSummary(marker, index) {
  var colorIndex = null;
  try { if (marker.getColorByIndex) colorIndex = Number(marker.getColorByIndex()); } catch (colorErr) {}
  return {
    index: index,
    id: String(marker.guid || marker.name || ('marker_' + String(index + 1))),
    time_s: paMarkerTimeSeconds(marker, 'start'),
    end_s: paMarkerTimeSeconds(marker, 'end'),
    label: String(marker.name || ''),
    comment: String(marker.comments || ''),
    color_index: colorIndex
  };
}

function paListMarkers(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq || !seq.markers) return paJson({ ok: false, error: 'Sequence marker API unavailable' });
  var markers = [];
  try {
    var marker = seq.markers.getFirstMarker ? seq.markers.getFirstMarker() : null;
    while (marker) {
      markers.push(paMarkerSummary(marker, markers.length));
      marker = seq.markers.getNextMarker ? seq.markers.getNextMarker(marker) : null;
    }
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({
    ok: true,
    sequence: paSequenceSummary(seq),
    marker_count: markers.length,
    markers: markers,
    verification: { kind: 'premiere_marker_list', mutates_project: false }
  });
}

function paAddEditorialMarkers(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq || !seq.markers || !seq.markers.createMarker) return paJson({ ok: false, error: 'Sequence marker API unavailable' });
  var notes = args.notes || [];
  if (!(notes instanceof Array) || notes.length < 1) return paJson({ ok: false, error: 'notes must be a non-empty array' });
  var added = [];
  var failures = [];
  for (var i = 0; i < notes.length; i += 1) {
    try {
      var note = notes[i] || {};
      var timeS = Number(note.time_s !== undefined ? note.time_s : note.start_s);
      if (!isFinite(timeS) || timeS < 0) throw new Error('note time_s/start_s must be non-negative');
      var kind = String(note.kind || 'editorial_note');
      var label = String(note.label || ('AI ' + kind));
      var comment = String(note.comment || note.reason || '');
      var marker = seq.markers.createMarker(timeS);
      marker.name = label;
      marker.comments = comment;
      try { if (marker.setColorByIndex) marker.setColorByIndex(Number(note.color_index || 1)); } catch (colorErr) {}
      var summary = paMarkerSummary(marker, added.length);
      summary.kind = kind;
      summary.backup_sequence_id = args.backup_sequence_id;
      added.push(summary);
    } catch (err) {
      failures.push({ index: i, error: String(err) });
    }
  }
  return paJson({
    ok: added.length > 0,
    sequence_id: paSequenceId(seq),
    added_count: added.length,
    failed_count: failures.length,
    markers: added,
    failures: failures,
    verification: { kind: 'premiere_editorial_marker_pass', mutates_project: true, backup_sequence_id: args.backup_sequence_id }
  });
}

function paSecondsToTicks(seconds) {
  return String(Math.round(Number(seconds || 0) * 254016000000));
}

function paTicksToSeconds(ticks) {
  return Number(ticks) / 254016000000;
}

function paIsMacOS() {
  return !!($.os && $.os.toLowerCase().indexOf('mac') !== -1);
}

function paAdobeAppFolders(appNamePrefix) {
  var base = new Folder(paIsMacOS() ? '/Applications' : 'C:\\\\Program Files\\\\Adobe');
  if (!base.exists) return [];
  var found = [];
  var subs = base.getFiles(function(f) { return f instanceof Folder; });
  for (var i = 0; i < subs.length; i += 1) {
    if (subs[i].displayName.indexOf(appNamePrefix) === 0) found.push(subs[i]);
  }
  found.sort(function(a, b) { return a.displayName < b.displayName ? 1 : -1; });
  return found;
}

function paAdobeResourceFolder(appFolder, relativePath) {
  var prefix = appFolder.fsName + (paIsMacOS() ? '/Contents/' : '/');
  return new Folder(prefix + relativePath);
}

function paCollectEprFiles(folder, out) {
  if (!folder || !folder.exists) return out;
  var entries = folder.getFiles();
  for (var i = 0; i < entries.length; i += 1) {
    var entry = entries[i];
    if (entry instanceof Folder) paCollectEprFiles(entry, out);
    else if (/\.epr$/i.test(entry.name)) out.push(entry);
  }
  return out;
}

function paCollectAllPresets() {
  var roots = [];
  var ame = paAdobeAppFolders('Adobe Media Encoder');
  for (var i = 0; i < ame.length; i += 1) roots.push(paAdobeResourceFolder(ame[i], 'MediaIO/systempresets'));
  var ppro = paAdobeAppFolders('Adobe Premiere Pro');
  for (var j = 0; j < ppro.length; j += 1) {
    roots.push(paAdobeResourceFolder(ppro[j], 'Settings/IngestPresets'));
    roots.push(paAdobeResourceFolder(ppro[j], 'MediaIO/systempresets'));
  }
  var userRoot = new Folder(Folder.myDocuments.fsName + '/Adobe/Adobe Media Encoder');
  if (userRoot.exists) {
    var versions = userRoot.getFiles(function(f) { return f instanceof Folder; });
    for (var v = 0; v < versions.length; v += 1) roots.push(new Folder(versions[v].fsName + '/Presets'));
  }
  var presets = [];
  for (var r = 0; r < roots.length; r += 1) {
    var eprs = paCollectEprFiles(roots[r], []);
    for (var e = 0; e < eprs.length; e += 1) {
      presets.push({ name: decodeURI(eprs[e].displayName).replace(/\.epr$/i, ''), path: eprs[e].fsName, format: eprs[e].parent ? decodeURI(eprs[e].parent.displayName) : '' });
    }
  }
  return presets;
}

function paFindStillPreset(outputPath) {
  var wantJpeg = /\.jpe?g$/i.test(outputPath);
  var needles = wantJpeg ? ['jpeg', 'jpg'] : ['png'];
  var presets = paCollectAllPresets();
  for (var n = 0; n < needles.length; n += 1) {
    for (var i = 0; i < presets.length; i += 1) {
      var haystack = (presets[i].name + ' ' + presets[i].format).toLowerCase();
      if (haystack.indexOf(needles[n]) !== -1) return presets[i].path;
    }
  }
  return '';
}

function paFindFallbackStillPreset() {
  var fallbacks = [
    { needles: ['tiff', 'tif'], ext: '.tif' },
    { needles: ['bmp'], ext: '.bmp' },
    { needles: ['gif'], ext: '.gif' }
  ];
  var presets = paCollectAllPresets();
  for (var f = 0; f < fallbacks.length; f += 1) {
    for (var n = 0; n < fallbacks[f].needles.length; n += 1) {
      for (var i = 0; i < presets.length; i += 1) {
        var haystack = (presets[i].name + ' ' + presets[i].format).toLowerCase();
        if (haystack.indexOf(fallbacks[f].needles[n]) !== -1) return { path: presets[i].path, ext: fallbacks[f].ext, name: presets[i].name };
      }
    }
  }
  return null;
}

function paWithExtension(path, ext) {
  var s = String(path || '');
  var slash = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\\\'));
  var dot = s.lastIndexOf('.');
  if (dot > slash) return s.substring(0, dot) + ext;
  return s + ext;
}

function paFindPresetByHint(hint) {
  var needle = String(hint || '').toLowerCase().replace(/_/g, ' ');
  if (!needle) return '';
  var presets = paCollectAllPresets();
  for (var i = 0; i < presets.length; i += 1) {
    var haystack = (presets[i].name + ' ' + presets[i].format).toLowerCase();
    if (haystack.indexOf(needle) !== -1) return presets[i].path;
  }
  return '';
}

function paFindH264Preset() {
  var presets = paCollectAllPresets();
  var first = '';
  for (var i = 0; i < presets.length; i += 1) {
    var haystack = (presets[i].name + ' ' + presets[i].format).toLowerCase();
    if (haystack.indexOf('h264') !== -1 || haystack.indexOf('h.264') !== -1 || haystack.indexOf('48323634') !== -1) {
      if (!first) first = presets[i].path;
      if (haystack.indexOf('match source') !== -1) return presets[i].path;
    }
  }
  return first;
}

function paFirstWrittenFile(outputPath) {
  var exact = new File(outputPath);
  if (exact.exists && exact.length > 0) return exact.fsName;
  var dir = exact.parent;
  if (!dir || !dir.exists) return '';
  var fullName = decodeURI(exact.name);
  var dot = fullName.lastIndexOf('.');
  var base = dot === -1 ? fullName : fullName.substring(0, dot);
  var ext = dot === -1 ? '' : fullName.substring(dot).toLowerCase();
  var matches = dir.getFiles(function(candidate) {
    if (candidate instanceof Folder) return false;
    var nm = decodeURI(candidate.name);
    if (nm.indexOf(base) !== 0) return false;
    return ext === '' || nm.toLowerCase().substring(nm.length - ext.length) === ext;
  });
  if (!matches || !matches.length) return '';
  var produced = matches[0];
  if (produced.length <= 0) return '';
  try {
    if (produced.fsName !== exact.fsName) produced.rename(fullName);
    return exact.exists ? exact.fsName : produced.fsName;
  } catch (renameErr) {
    return produced.fsName;
  }
}

function paNormalizeFolderPath(path) {
  var raw = String(path || '');
  try {
    if (raw === '/tmp') return '/private/tmp';
    if (raw.indexOf('/tmp/') === 0) return '/private' + raw;
  } catch (err) {}
  return raw;
}

function paEnsureFolder(path) {
  var folder = new Folder(paNormalizeFolderPath(path));
  if (folder.exists) return folder;
  try {
    if (folder.parent && !folder.parent.exists) paEnsureFolder(folder.parent.fsName);
  } catch (parentErr) {}
  try { folder.create(); } catch (createErr) {}
  return folder;
}

function paExportOneReviewFrame(seq, outputPath, timeS) {
  var notes = [];
  var savedPos = null;
  var atTicks = paSecondsToTicks(timeS);
  try { if (seq.getPlayerPosition) savedPos = seq.getPlayerPosition().ticks; } catch (posErr) {}
  try { if (seq.setPlayerPosition) seq.setPlayerPosition(atTicks); } catch (setErr) { notes.push('setPlayerPosition: ' + String(setErr)); }
  var stale = new File(outputPath);
  try { if (stale.exists) stale.remove(); } catch (removeErr) {}
  var wantJpeg = /\.jpe?g$/i.test(outputPath);
  try {
    app.enableQE();
    var qeSeq = qe.project.getActiveSequence();
    if (qeSeq) {
      var fn = wantJpeg ? qeSeq.exportFrameJPEG : qeSeq.exportFramePNG;
      if (typeof fn === 'function') {
        var w = String(seq.frameSizeHorizontal || 1920);
        var h = String(seq.frameSizeVertical || 1080);
        try { notes.push('QE returned ' + fn.call(qeSeq, outputPath, w, h)); }
        catch (argErr) {
          try { notes.push('QE returned ' + fn.call(qeSeq, outputPath, w)); }
          catch (argErr2) { notes.push('QE exportFrame: ' + String(argErr2)); }
        }
      } else {
        notes.push('QE exportFrame unavailable');
      }
    } else {
      notes.push('QE no active sequence');
    }
  } catch (qeErr) { notes.push('QE: ' + String(qeErr)); }
  var written = paFirstWrittenFile(outputPath);
  if (written) {
    try { if (savedPos && seq.setPlayerPosition) seq.setPlayerPosition(savedPos); } catch (restoreErr) {}
    return { ok: true, path: written, time_s: timeS, method: 'qe_exportFrame', notes: notes };
  }
  notes.push('QE wrote no file; trying one-frame Media Encoder export');
  try {
    var preset = paFindStillPreset(outputPath);
    var exportPath = outputPath;
    var stillFallback = null;
    var proofIsVideo = false;
    if (!preset) {
      stillFallback = paFindFallbackStillPreset();
      if (stillFallback) {
        preset = stillFallback.path;
        exportPath = paWithExtension(outputPath, stillFallback.ext);
        notes.push('AME: no ' + (wantJpeg ? 'JPEG' : 'PNG') + ' still preset found; using ' + stillFallback.name + ' at ' + exportPath);
      }
    }
    if (!preset) {
      notes.push('AME: no still-image preset found; trying H.264 short proof clip');
      preset = paFindH264Preset();
      exportPath = paWithExtension(outputPath, '.mp4');
      proofIsVideo = true;
    }
    if (!preset) {
      notes.push('AME: no still or H.264 preset found');
    } else if (!seq.exportAsMediaDirect) {
      notes.push('AME: sequence.exportAsMediaDirect unavailable');
    } else {
      var savedIn = null;
      var savedOut = null;
      try { if (seq.getInPointAsTime) savedIn = seq.getInPointAsTime().ticks; } catch (inErr) {}
      try { if (seq.getOutPointAsTime) savedOut = seq.getOutPointAsTime().ticks; } catch (outErr) {}
      try {
        var frameTicks = Number(seq.timebase || 0);
        var startTicks = Number(atTicks);
        if (!isFinite(frameTicks) || frameTicks <= 0) frameTicks = 10160640000;
        var exportTicks = proofIsVideo ? Math.max(frameTicks * 6, 63504000000) : frameTicks;
        var staleExport = new File(exportPath);
        try { if (staleExport.exists) staleExport.remove(); } catch (staleExportErr) {}
        seq.setInPoint(paTicksToSeconds(startTicks));
        seq.setOutPoint(paTicksToSeconds(startTicks + exportTicks));
        seq.exportAsMediaDirect(exportPath, preset, app.encoder.ENCODE_IN_TO_OUT);
        notes.push('AME preset: ' + preset);
        if (proofIsVideo) notes.push('AME proof clip duration_s: ' + paTicksToSeconds(exportTicks));
      } finally {
        try { if (savedIn !== null) seq.setInPoint(paTicksToSeconds(savedIn)); } catch (restoreInErr) {}
        try { if (savedOut !== null) seq.setOutPoint(paTicksToSeconds(savedOut)); } catch (restoreOutErr) {}
      }
      var ameWritten = paFirstWrittenFile(exportPath);
      if (ameWritten) {
        try { if (savedPos && seq.setPlayerPosition) seq.setPlayerPosition(savedPos); } catch (restoreAmeErr) {}
        return { ok: true, path: ameWritten, requested_path: outputPath, time_s: timeS, method: stillFallback ? 'ame_fallback_still_export' : (/\.mp4$/i.test(exportPath) ? 'ame_h264_short_proof_export' : 'ame_still_export'), notes: notes };
      }
    }
  } catch (ameErr) { notes.push('AME: ' + String(ameErr)); }
  try { if (savedPos && seq.setPlayerPosition) seq.setPlayerPosition(savedPos); } catch (restoreErr2) {}
  written = paFirstWrittenFile(outputPath);
  if (written) return { ok: true, path: written, time_s: timeS, method: 'ame_still_export', notes: notes };
  return { ok: false, path: outputPath, time_s: timeS, method: null, notes: notes, error: 'No still frame was written by QE or Media Encoder' };
}

function paExportSequenceReviewFrames(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found' });
  var frameCount = Number(args.frame_count || 6);
  if (!isFinite(frameCount) || frameCount < 2) frameCount = 2;
  if (frameCount > 24) frameCount = 24;
  frameCount = Math.floor(frameCount);
  var duration = paSequenceDurationSeconds(seq);
  var startS = Number(args.range_start_s || 0);
  var endS = args.range_end_s !== null && args.range_end_s !== undefined ? Number(args.range_end_s) : duration;
  if (!isFinite(startS) || startS < 0) startS = 0;
  if (!isFinite(endS) || endS <= startS) return paJson({ ok: false, error: 'review frame range is empty or invalid' });
  var folder = paEnsureFolder(args.output_dir);
  if (!folder.exists) return paJson({ ok: false, error: 'could not create output directory: ' + String(args.output_dir || ''), resolved_output_dir: folder.fsName });
  var frames = [];
  var failures = [];
  var span = endS - startS;
  for (var i = 0; i < frameCount; i += 1) {
    var atS = startS + (span * i / (frameCount - 1));
    if (atS >= endS) atS = Math.max(startS, endS - 0.001);
    var num = String(i + 1);
    while (num.length < 3) num = '0' + num;
    var outPath = folder.fsName + '/review_' + num + '.png';
    var result = paExportOneReviewFrame(seq, outPath, atS);
    if (result.ok) frames.push(result);
    else failures.push(result);
  }
  return paJson({
    ok: frames.length > 0,
    sequence: paSequenceSummary(seq),
    output_dir: folder.fsName,
    range: { start_s: startS, end_s: endS },
    requested_count: frameCount,
    exported_count: frames.length,
    frames: frames,
    failures: failures,
    contact_sheet_pending: true,
    verification: { kind: 'premiere_review_frames', scope: 'each returned frame path was verified by CEP File.exists; make contact sheet locally from returned files', backup_sequence_id: args.backup_sequence_id }
  });
}

function paImportCaptions(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found' });
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var path = String(args.caption_path || '');
  if (!path) return paJson({ ok: false, error: 'caption_path required' });
  var startS = Number(args.start_s || 0);
  var formatName = String(args.caption_format || 'subtitle');

  var imported = false;
  try { imported = !!app.project.importFiles([path], true, app.project.rootItem, false); }
  catch (importErr) { return paJson({ ok: false, error: String(importErr) }); }
  if (!imported) {
    return paJson({
      ok: false,
      sequence_id: paSequenceId(seq),
      imported: false,
      caption_track_created: false,
      caption_path: path,
      error: 'Premiere rejected the caption file import',
      verification: { kind: 'premiere_caption_import', mutates_project: false, backup_sequence_id: args.backup_sequence_id }
    });
  }

  var captionTrackCreated = false;
  var note = '';
  var item = null;
  try { item = paFindImportedProjectItem(path); }
  catch (findErr) { note = 'Imported item lookup failed: ' + String(findErr); }

  if (!item) {
    note = note || 'Caption file imported into the project bin, but the new ProjectItem could not be located by name, so no caption track was created.';
  } else if (!seq.createCaptionTrack) {
    note = 'Caption file imported; this Premiere scripting build does not expose Sequence.createCaptionTrack, so no caption track was created.';
  } else {
    var formatConst = paCaptionFormatConstant(formatName);
    try {
      var trackResult = (formatConst === null || formatConst === undefined)
        ? seq.createCaptionTrack(item, startS)
        : seq.createCaptionTrack(item, startS, formatConst);
      captionTrackCreated = !!trackResult;
      note = captionTrackCreated
        ? 'Caption track created from ' + item.name + ' at ' + startS + 's.'
        : 'Premiere accepted the createCaptionTrack call but returned a falsy result; verify the sequence in the UI.';
    } catch (captionErr) {
      note = 'createCaptionTrack threw: ' + String(captionErr);
    }
  }

  return paJson({
    ok: imported,
    sequence_id: paSequenceId(seq),
    imported: imported,
    caption_track_created: captionTrackCreated,
    caption_item_name: item ? String(item.name || '') : null,
    caption_path: path,
    start_s: startS,
    caption_format: formatName,
    note: note,
    verification: { kind: 'premiere_caption_import', mutates_project: true, backup_sequence_id: args.backup_sequence_id, render_verified: false, verification_scope: 'Premiere accepted the caption-track creation call; verify playback or exported frames before delivery.' }
  });
}

function paParseTrackSpec(spec, defaultType) {
  var s = String(spec || '').replace(/^\s+|\s+$/g, '');
  if (!s) return { type: defaultType || 'video', index: 0 };
  var m = /^([va])(\d+)$/i.exec(s);
  if (m) {
    return { type: (m[1].toLowerCase() === 'a') ? 'audio' : 'video', index: Math.max(0, parseInt(m[2], 10) - 1) };
  }
  var n = Number(s);
  if (isFinite(n)) return { type: defaultType || 'video', index: Math.max(0, Math.floor(n)) };
  return { type: defaultType || 'video', index: 0 };
}

function paImportMedia(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var path = String(args.media_path || '');
  if (!path) return paJson({ ok: false, error: 'media_path required' });
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });

  // Diff the project root's children before/after import to find the newly
  // added item, using stable string nodeIds (not object references, which
  // ExtendScript collections can hand back as fresh wrapper instances on
  // each access -- see the duplicate_sequence fix for the same gotcha).
  var beforeIds = {};
  try {
    var existing = app.project.rootItem.children;
    for (var i = 0; i < existing.numItems; i += 1) {
      try { beforeIds[String(existing[i].nodeId)] = true; } catch (e0) {}
    }
  } catch (scanErr) {}

  var importOk = false;
  try { importOk = !!app.project.importFiles([path], true, app.project.rootItem, false); }
  catch (impErr) { return paJson({ ok: false, error: String(impErr) }); }
  if (!importOk) return paJson({ ok: false, error: 'Import failed', media_path: path });

  var newItem = null;
  try {
    var after = app.project.rootItem.children;
    for (var j = 0; j < after.numItems; j += 1) {
      var cand = after[j];
      if (cand && !beforeIds[String(cand.nodeId)]) { newItem = cand; break; }
    }
  } catch (scan2Err) {}
  if (!newItem) {
    try { newItem = paFindProjectItem(new File(path).name); } catch (fallbackErr) {}
  }
  if (!newItem) {
    return paJson({ ok: true, imported: true, inserted: false, sequence_id: paSequenceId(seq), media_path: path, note: 'File imported to project bin, but could not identify the new project item to insert into the sequence.' });
  }

  var spec = paParseTrackSpec(args.track, 'video');
  var tracks = (spec.type === 'audio') ? seq.audioTracks : seq.videoTracks;
  var track = null;
  try { track = tracks[spec.index]; } catch (trackErr) {}
  if (!track || typeof track.insertClip !== 'function') {
    return paJson({ ok: true, imported: true, inserted: false, sequence_id: paSequenceId(seq), media_path: path, item_node_id: String(newItem.nodeId || ''), note: 'File imported to project bin, but the requested track (' + spec.type + ' index ' + spec.index + ') is unavailable or does not support insertClip.' });
  }

  var timeS = (args.time_s !== undefined && args.time_s !== null) ? Number(args.time_s) : 0;
  try {
    // Track.insertClip's documented signature takes a ProjectItem. newItem
    // came straight from app.project.rootItem.children, so it already is
    // one -- never pass a TrackItem here (see the ExtendScript crash gotcha).
    track.insertClip(newItem, timeS);
  } catch (insertErr) {
    return paJson({ ok: true, imported: true, inserted: false, sequence_id: paSequenceId(seq), media_path: path, item_node_id: String(newItem.nodeId || ''), error: String(insertErr) });
  }

  return paJson({
    ok: true,
    imported: true,
    inserted: true,
    sequence_id: paSequenceId(seq),
    media_path: path,
    item_node_id: String(newItem.nodeId || ''),
    item_name: String(newItem.name || ''),
    track_type: spec.type,
    track_index: spec.index,
    time_s: timeS,
    backup_sequence_id: args.backup_sequence_id
  });
}

function paQueueExport(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var outputPath = String(args.output_path || '');
  if (!outputPath) return paJson({ ok: false, error: 'output_path required' });
  if (!seq.exportAsMediaDirect) return paJson({ ok: false, error: 'sequence.exportAsMediaDirect unavailable on this Premiere version' });
  var preset = args.preset ? paFindPresetByHint(args.preset) : '';
  if (!preset) preset = paFindH264Preset();
  if (!preset) return paJson({ ok: false, error: 'No matching export preset found on this system (checked Premiere/AME system presets)', requested_preset: args.preset || null });
  try {
    var outFile = new File(paNormalizeFolderPath(outputPath));
    if (outFile.parent) paEnsureFolder(outFile.parent.fsName);
  } catch (folderErr) {}
  var stale = new File(outputPath);
  try { if (stale.exists) stale.remove(); } catch (removeErr) {}
  var hasRange = args.range_start_s !== undefined && args.range_start_s !== null && args.range_end_s !== undefined && args.range_end_s !== null;
  var savedIn = null;
  var savedOut = null;
  try { if (seq.getInPointAsTime) savedIn = seq.getInPointAsTime().ticks; } catch (e1) {}
  try { if (seq.getOutPointAsTime) savedOut = seq.getOutPointAsTime().ticks; } catch (e2) {}
  var workArea = app.encoder.ENCODE_ENTIRE;
  var exportErr = null;
  try {
    if (hasRange) {
      seq.setInPoint(Number(args.range_start_s));
      seq.setOutPoint(Number(args.range_end_s));
      workArea = app.encoder.ENCODE_IN_TO_OUT;
    }
    seq.exportAsMediaDirect(outputPath, preset, workArea);
  } catch (err) {
    exportErr = String(err);
  }
  try { if (savedIn !== null) seq.setInPoint(paTicksToSeconds(savedIn)); } catch (r1) {}
  try { if (savedOut !== null) seq.setOutPoint(paTicksToSeconds(savedOut)); } catch (r2) {}
  if (exportErr) return paJson({ ok: false, error: exportErr, sequence_id: paSequenceId(seq) });
  var written = paFirstWrittenFile(outputPath);
  var result = {
    ok: !!written,
    queued: false,
    exported: !!written,
    sequence_id: paSequenceId(seq),
    "export": {
      output_path: written || outputPath,
      preset: preset,
      range_start_s: hasRange ? Number(args.range_start_s) : null,
      range_end_s: hasRange ? Number(args.range_end_s) : null,
      backup_sequence_id: args.backup_sequence_id
    },
    note: 'exportAsMediaDirect renders synchronously through Premiere itself (not queued to Adobe Media Encoder); this call blocks until the file is written or the render fails.'
  };
  if (!written) result.error = 'No file was written at the requested output path';
  return paJson(result);
}

// Basic-Correction-block deltas (from neutral) for each named look, applied
// scaled by `intensity` (0-1). Property collisions: Lumetri Color has ~130
// properties and reuses names like "Saturation"/"Contrast"/"Temperature"
// across sections (Basic Correction, Creative, Color Wheels & Match), so
// paFindProperty's by-name lookup is unreliable here -- these are the fixed
// property indices for the Basic Correction block's sliders, confirmed live
// against a real "AE.ADBE Lumetri" component on Premiere 26.3.2.
var PA_LUMETRI_BASIC_INDEX = { exposure: 19, contrast: 20, highlights: 21, shadows: 22, whites: 23, blacks: 24, saturation: 16, temperature: 14, tint: 15 };
var PA_LUMETRI_LOOKS = {
  'subtle_professional': { contrast: 8, highlights: -5, shadows: 5, saturation: 5 },
  'warm': { temperature: 15, exposure: 2, saturation: 8 },
  'cool': { temperature: -15, contrast: 5 },
  'high_contrast': { contrast: 25, highlights: -10, shadows: -10, blacks: -5, whites: 5 },
  'muted': { saturation: -40, contrast: -5 }
};

function paApplyBasicLumetri(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });

  var lookName = String(args.look || 'subtle_professional');
  if (!PA_LUMETRI_LOOKS.hasOwnProperty(lookName)) {
    return paJson({ ok: false, error: 'Unknown look: ' + lookName, available_looks: (function(){ var k=[]; for (var n in PA_LUMETRI_LOOKS) k.push(n); return k; })() });
  }
  var intensity = (args.intensity !== undefined && args.intensity !== null) ? Number(args.intensity) : 0.25;

  var comp = paFindComponent(clip, 'Lumetri Color');
  var added = false;
  if (!comp) {
    try {
      app.enableQE();
      var effect = qe.project.getVideoEffectByName('Lumetri Color', false);
      if (!effect) return paJson({ ok: false, error: 'Lumetri Color effect not found via QE DOM' });
      var qeSeq = qe.project.getActiveSequence();
      if (!qeSeq || paSequenceId(seq) !== paSequenceId(app.project.activeSequence)) {
        return paJson({ ok: false, error: 'Target sequence must be the active sequence to add a new effect via the QE DOM (adding effects to a non-active sequence is not supported).' });
      }
      var qeTrack = (String(args.track_type || 'video').toLowerCase() === 'audio') ? null : qeSeq.getVideoTrackAt(Number(args.track_index));
      if (!qeTrack) return paJson({ ok: false, error: 'Lumetri Color only applies to video tracks' });
      var qeItem = null;
      var seen = 0;
      for (var qi = 0; qi < qeTrack.numItems; qi += 1) {
        var cand = qeTrack.getItemAt(qi);
        var isEmpty = false;
        try { isEmpty = (String(cand.type) === 'Empty'); } catch (typeErr) {}
        if (isEmpty) continue;
        if (seen === Number(args.clip_index)) { qeItem = cand; break; }
        seen += 1;
      }
      if (!qeItem) return paJson({ ok: false, error: 'Could not resolve the target clip via the QE DOM' });
      var addOk = qeItem.addVideoEffect(effect);
      if (!addOk) return paJson({ ok: false, error: 'QE DOM declined to add Lumetri Color to this clip' });
      added = true;
      comp = paFindComponent(clip, 'Lumetri Color');
      if (!comp) return paJson({ ok: false, error: 'Lumetri Color was reported added but is not visible on the clip afterward' });
    } catch (addErr) {
      return paJson({ ok: false, error: 'Failed to add Lumetri Color: ' + String(addErr) });
    }
  }

  var deltas = PA_LUMETRI_LOOKS[lookName];
  var applied = {};
  var errors = [];
  for (var key in deltas) {
    if (!deltas.hasOwnProperty(key)) continue;
    var propIndex = PA_LUMETRI_BASIC_INDEX[key];
    var prop = null;
    try { prop = comp.properties[propIndex]; } catch (idxErr) {}
    if (!prop) { errors.push(key + ': property index unavailable'); continue; }
    var neutral = (key === 'saturation') ? 100 : 0;
    var newValue = neutral + (deltas[key] * intensity);
    try {
      prop.setValue(newValue, true);
      applied[key] = newValue;
    } catch (setErr) {
      errors.push(key + ': ' + String(setErr));
    }
  }

  return paJson({
    ok: errors.length === 0,
    clip_name: String(clip.name || ''),
    look: lookName,
    intensity: intensity,
    effect_added: added,
    applied: applied,
    errors: errors,
    verification: 'Premiere accepted the write(s); verify via get_effect_properties readback or exported frames before delivery.'
  });
}

function paSetClipTransform(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var comp = paFindComponent(clip, 'Motion');
  if (!comp) return paJson({ ok: false, error: 'Motion component not found on clip' });

  var hasRange = args.range_start_s !== undefined && args.range_start_s !== null;
  var applied = {};
  var errors = [];

  function applyProp(propName, value) {
    var prop = paFindProperty(comp, propName);
    if (!prop) { errors.push(propName + ': property not found'); return; }
    try {
      if (hasRange) {
        try { if (typeof prop.isTimeVarying === 'function' && !prop.isTimeVarying()) prop.setTimeVarying(true); } catch (tvErr) {}
        var startTime = new Time();
        startTime.ticks = String(paSecondsToTicks(Number(args.range_start_s)));
        prop.addKey(startTime);
        prop.setValueAtKey(startTime, value, true);
        if (args.range_end_s !== undefined && args.range_end_s !== null) {
          var endTime = new Time();
          endTime.ticks = String(paSecondsToTicks(Number(args.range_end_s)));
          prop.addKey(endTime);
          prop.setValueAtKey(endTime, value, true);
        }
      } else {
        prop.setValue(value, true);
      }
      applied[propName] = value;
    } catch (err) {
      errors.push(propName + ': ' + String(err));
    }
  }

  if (args.scale !== undefined && args.scale !== null) applyProp('Scale', Number(args.scale));
  if (args.position !== undefined && args.position !== null) applyProp('Position', args.position);
  if (args.rotation !== undefined && args.rotation !== null) applyProp('Rotation', Number(args.rotation));

  var appliedCount = 0;
  for (var k in applied) { if (applied.hasOwnProperty(k)) appliedCount += 1; }
  if (appliedCount === 0) {
    return paJson({ ok: false, error: 'No transform values provided (scale/position/rotation), or all failed', details: errors });
  }

  return paJson({
    ok: errors.length === 0,
    clip_name: String(clip.name || ''),
    applied: applied,
    errors: errors,
    keyframed: hasRange,
    verification: 'Premiere accepted the write(s); verify via get_effect_properties readback or exported frames before delivery.'
  });
}

function paRemoveClip(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var trackType = String(args.track_type || 'video').toLowerCase();
  var trackIndex = Number(args.track_index);
  var clipIndex = Number(args.clip_index);
  if (!isFinite(trackIndex) || !isFinite(clipIndex)) {
    return paJson({ ok: false, error: 'track_index and clip_index are required numbers' });
  }
  var tracks = (trackType === 'audio') ? seq.audioTracks : seq.videoTracks;
  if (!tracks) return paJson({ ok: false, error: 'Track collection unavailable for track_type ' + trackType });
  var track = null;
  try { track = tracks[trackIndex]; } catch (e1) {}
  if (!track) return paJson({ ok: false, error: 'track_index out of range', track_index: trackIndex });
  var clip = null;
  try { clip = track.clips[clipIndex]; } catch (e2) {}
  if (!clip) return paJson({ ok: false, error: 'clip_index out of range', clip_index: clipIndex });
  var clipName = String(clip.name || '');
  if (typeof clip.remove !== 'function') {
    return paJson({ ok: false, unsupported: true, message: 'TrackItem.remove is not exposed to ExtendScript on this Premiere build.', clip_name: clipName });
  }
  try {
    // TrackItem.remove(rippleEdit, alignToVideo): boolean. Pass rippleEdit=false
    // so this only lifts the clip (leaves a gap) rather than shifting every
    // later clip on the track — a plain removal, not a ripple delete.
    clip.remove(false, false);
    return paJson({
      ok: true,
      sequence_id: paSequenceId(seq),
      clip_name: clipName,
      track_type: trackType,
      track_index: trackIndex,
      clip_index: clipIndex,
      note: 'Clip removed from the track.'
    });
  } catch (err) {
    return paJson({ ok: false, error: String(err), clip_name: clipName });
  }
}

function paMoveClip(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var trackType = String(args.track_type || 'video').toLowerCase();
  var fromIndex = Number(args.from_track_index);
  var toIndex = Number(args.to_track_index);
  var clipIndex = Number(args.clip_index);
  if (!isFinite(fromIndex) || !isFinite(toIndex) || !isFinite(clipIndex)) {
    return paJson({ ok: false, error: 'from_track_index, to_track_index, and clip_index are required numbers' });
  }
  var tracks = (trackType === 'audio') ? seq.audioTracks : seq.videoTracks;
  if (!tracks) return paJson({ ok: false, error: 'Track collection unavailable for track_type ' + trackType });
  var fromTrack = null;
  var toTrack = null;
  try { fromTrack = tracks[fromIndex]; } catch (e1) {}
  try { toTrack = tracks[toIndex]; } catch (e2) {}
  if (!fromTrack) return paJson({ ok: false, error: 'from_track_index out of range', from_track_index: fromIndex });
  if (!toTrack) return paJson({ ok: false, error: 'to_track_index out of range', to_track_index: toIndex });
  if (typeof toTrack.insertClip !== 'function') {
    return paJson({ ok: false, unsupported: true, message: 'This Premiere version did not expose Track.insertClip to ExtendScript.' });
  }
  var clip = null;
  try { clip = fromTrack.clips[clipIndex]; } catch (e3) {}
  if (!clip) return paJson({ ok: false, error: 'clip_index out of range on from_track_index', clip_index: clipIndex });
  var clipName = String(clip.name || '');
  // Track.insertClip's documented signature takes a ProjectItem, not a
  // TrackItem. An earlier version of this function tried passing the
  // TrackItem directly (reasoning a try/catch would safely fall back if
  // rejected) and it crashed the entire Premiere process instead of throwing
  // a catchable error — a try/catch cannot protect against a native crash.
  // Never pass anything but clip.projectItem here.
  var projectItem = null;
  try { projectItem = clip.projectItem; } catch (piErr) {}
  if (!projectItem) {
    return paJson({ ok: false, error: 'Could not read clip.projectItem; refusing to call insertClip with an unproven argument type', clip_name: clipName });
  }
  var originalStartS = null;
  try { if (clip.start && clip.start.seconds !== undefined) originalStartS = Number(clip.start.seconds); } catch (e4) {}
  var targetStartS = (args.start_s !== undefined && args.start_s !== null) ? Number(args.start_s) : (originalStartS !== null ? originalStartS : 0);
  // insertClip is called with the ProjectItem's own current in/out marks, which
  // may not match this TrackItem's trim on the timeline (no in/out is set here
  // to avoid introducing another not-yet-proven-live native call in the same
  // change) — verify the inserted clip's duration in the Premiere UI.
  var inserted = null;
  try {
    inserted = toTrack.insertClip(projectItem, targetStartS);
  } catch (err) {
    return paJson({ ok: false, error: String(err), clip_name: clipName });
  }
  if (!inserted) {
    return paJson({ ok: false, error: 'Premiere rejected the insertClip call', clip_name: clipName });
  }
  var removed = false;
  var removeError = null;
  var removeAttempted = !!args.remove_original;
  if (removeAttempted) {
    // TrackItem.remove() is undocumented and unproven on this Premiere build —
    // attempt only when explicitly requested, as its own isolated step, so a
    // crash here doesn't also implicate (or hide behind) the insert above.
    try {
      if (typeof clip.remove === 'function') {
        clip.remove();
        removed = true;
      } else {
        removeError = 'clip.remove is not a function on this Premiere build';
      }
    } catch (removeErr) {
      removeError = String(removeErr);
    }
  }
  return paJson({
    ok: true,
    sequence_id: paSequenceId(seq),
    clip_name: clipName,
    track_type: trackType,
    from_track_index: fromIndex,
    to_track_index: toIndex,
    start_s: targetStartS,
    remove_attempted: removeAttempted,
    original_removed: removed,
    remove_error: removeError,
    note: removeAttempted
      ? (removed
          ? 'Clip inserted on the target track and the original was removed from the source track.'
          : 'Clip inserted on the target track, but removing the original failed — delete it manually in the Premiere UI.')
      : 'Clip copied to the target track. remove_original was not set, so the original clip is still on the source track — delete it manually, or call again with remove_original=true once the copy is confirmed correct.'
  });
}

// Escape hatch for anything not covered by a fixed tool above. Runs in THIS
// file's already-loaded engine (unlike a fresh-process bridge), so user code
// gets free access to every paXxx helper already defined here, and read-only
// calls need no Premiere relaunch — only editing this .jsx does.
//
// This carries the exact same risk as hand-editing this file: a try/catch
// protects against thrown JS exceptions, NOT against passing a wrong native
// object type into a method with a fixed native signature, which can crash the
// whole Premiere process outright (see paMoveClip's history above). Treat any
// mutating call through here with the same care as a new paXxx function: never
// guess at native argument types, prefer property reads when exploring, and
// isolate an unproven mutating call before combining it with others.
function paExecuteExtendScript(raw) {
  var args = paParse(raw);
  var code = String(args.code || '');
  if (!code) return paJson({ ok: false, error: 'code is required' });
  try {
    var __pa_exec_result = eval('(function(){\n' + code + '\n})()');
    return paJson({ ok: true, result: __pa_exec_result === undefined ? null : __pa_exec_result });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paEvaluateExpression(raw) {
  var args = paParse(raw);
  var expression = String(args.expression || '');
  if (!expression) return paJson({ ok: false, error: 'expression is required' });
  try {
    var __pa_eval_value = eval('(' + expression + ')');
    return paJson({ ok: true, value: __pa_eval_value === undefined ? null : __pa_eval_value, value_type: typeof __pa_eval_value });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paInspectDomObjectValue(obj, depth, maxDepth) {
  if (depth > maxDepth) return '<max depth>';
  if (obj === null) return null;
  if (obj === undefined) return undefined;
  var t = typeof obj;
  if (t === 'string' || t === 'number' || t === 'boolean') return obj;
  var result = {};
  var propCount = 0;
  try {
    for (var key in obj) {
      if (propCount > 50) { result.__truncated = true; break; }
      try {
        var val = obj[key];
        var vt = typeof val;
        if (vt === 'function') {
          result[key] = '[function]';
        } else if (vt === 'object' && val !== null) {
          result[key] = (depth < maxDepth) ? paInspectDomObjectValue(val, depth + 1, maxDepth) : '[object]';
        } else {
          result[key] = val;
        }
      } catch (propErr) {
        result[key] = '[error: ' + String(propErr) + ']';
      }
      propCount += 1;
    }
  } catch (enumErr) {
    return { __error: String(enumErr) };
  }
  // for-in on a native/COM-like object often misses its own collection/identity
  // properties entirely, so probe the common ones directly too.
  try { if (obj.numItems !== undefined) result.numItems = obj.numItems; } catch (e1) {}
  try { if (obj.numTracks !== undefined) result.numTracks = obj.numTracks; } catch (e2) {}
  try { if (obj.numSequences !== undefined) result.numSequences = obj.numSequences; } catch (e3) {}
  try { if (obj.length !== undefined) result.length = obj.length; } catch (e4) {}
  try { if (obj.name !== undefined) result.name = obj.name; } catch (e5) {}
  try { if (obj.displayName !== undefined) result.displayName = obj.displayName; } catch (e6) {}
  try { if (obj.matchName !== undefined) result.matchName = obj.matchName; } catch (e7) {}
  try { if (obj.nodeId !== undefined) result.nodeId = obj.nodeId; } catch (e8) {}
  return result;
}

// Effects/keyframes helpers: all read via the public, documented Component/
// Property DOM (clip.components, component.properties, property.getValue/
// setValue/getKeys/addKey/setValueAtKey/removeKey) — no QE DOM involved, unlike
// paMoveClip's insertClip/remove or move_clip_to_track's moveToTrack.
function paLookupClip(seq, trackType, trackIndex, clipIndex) {
  var tracks = (trackType === 'audio') ? seq.audioTracks : seq.videoTracks;
  if (!tracks) return null;
  var track = null;
  try { track = tracks[trackIndex]; } catch (e1) {}
  if (!track) return null;
  var clip = null;
  try { clip = track.clips[clipIndex]; } catch (e2) {}
  return clip;
}

function paFindComponent(clip, effectName) {
  if (!clip || !clip.components) return null;
  try {
    for (var i = 0; i < clip.components.numItems; i += 1) {
      var comp = clip.components[i];
      if (String(comp.displayName || '') === effectName || String(comp.matchName || '') === effectName) return comp;
    }
  } catch (err) {}
  return null;
}

function paFindProperty(comp, propertyName) {
  if (!comp || !comp.properties) return null;
  try {
    for (var p = 0; p < comp.properties.numItems; p += 1) {
      var prop = comp.properties[p];
      if (String(prop.displayName || '') === propertyName) return prop;
    }
  } catch (err) {}
  return null;
}

function paListClipEffects(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var effects = [];
  try {
    for (var i = 0; i < clip.components.numItems; i += 1) {
      var comp = clip.components[i];
      effects.push({ index: i, display_name: String(comp.displayName || ''), match_name: String(comp.matchName || '') });
    }
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, clip_name: String(clip.name || ''), effects: effects });
}

function paGetEffectProperties(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var comp = paFindComponent(clip, String(args.effect_name || ''));
  if (!comp) return paJson({ ok: false, error: 'Effect not found: ' + String(args.effect_name || '') });
  var props = [];
  try {
    for (var p = 0; p < comp.properties.numItems; p += 1) {
      var prop = comp.properties[p];
      var info = { index: p, display_name: String(prop.displayName || ''), is_time_varying: false, keyframes_supported: false, value: null };
      try { info.is_time_varying = !!prop.isTimeVarying(); } catch (e1) {}
      try { info.keyframes_supported = !!prop.areKeyframesSupported(); } catch (e2) {}
      try { info.value = prop.getValue(0, 0); } catch (e3) {}
      props.push(info);
    }
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, effect: String(comp.displayName || ''), match_name: String(comp.matchName || ''), properties: props });
}

function paSetEffectProperty(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var comp = paFindComponent(clip, String(args.effect_name || ''));
  if (!comp) return paJson({ ok: false, error: 'Effect not found: ' + String(args.effect_name || '') });
  var prop = paFindProperty(comp, String(args.property_name || ''));
  if (!prop) return paJson({ ok: false, error: 'Property not found: ' + String(args.property_name || '') });
  var value = args.value;
  try {
    prop.setValue(value, true);
  } catch (err) {
    return paJson({ ok: false, error: 'Premiere could not set the requested effect property: ' + String(err) });
  }
  var readback = null;
  var readbackAvailable = true;
  try { readback = prop.getValue(); } catch (rbErr) { readbackAvailable = false; }
  return paJson({
    ok: true,
    effect: String(args.effect_name || ''),
    property: String(args.property_name || ''),
    requested_value: value,
    value: readbackAvailable ? readback : value,
    readback_verified: readbackAvailable && readback === value,
    verification: readbackAvailable
      ? 'Premiere parameter readback only; verify playback or exported frames before delivery.'
      : 'Premiere accepted the write but this property exposed no readback value; verify playback or exported frames before delivery.'
  });
}

function paGetKeyframes(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var comp = paFindComponent(clip, String(args.effect_name || ''));
  if (!comp) return paJson({ ok: false, error: 'Effect not found' });
  var prop = paFindProperty(comp, String(args.property_name || ''));
  if (!prop) return paJson({ ok: false, error: 'Property not found' });
  var isTimeVarying = false;
  try { isTimeVarying = !!prop.isTimeVarying(); } catch (e1) {}
  if (!isTimeVarying) {
    return paJson({ ok: true, is_time_varying: false, keyframes: [], message: 'Property has no keyframes' });
  }
  var keyframes = [];
  try {
    var keys = prop.getKeys();
    if (keys) {
      for (var k = 0; k < keys.length; k += 1) {
        var time = keys[k];
        var val = null;
        try { val = prop.getValueAtKey(time); } catch (e2) {}
        keyframes.push({ time_s: paTicksToSeconds(time.ticks), value: val });
      }
    }
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, is_time_varying: true, keyframes: keyframes });
}

function paAddKeyframe(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var comp = paFindComponent(clip, String(args.effect_name || ''));
  if (!comp) return paJson({ ok: false, error: 'Effect not found' });
  var prop = paFindProperty(comp, String(args.property_name || ''));
  if (!prop) return paJson({ ok: false, error: 'Property not found' });
  try {
    if (typeof prop.areKeyframesSupported === 'function' && !prop.areKeyframesSupported()) {
      return paJson({ ok: false, error: 'Property does not support keyframes' });
    }
  } catch (e1) {}
  try {
    if (typeof prop.isTimeVarying === 'function' && !prop.isTimeVarying()) {
      prop.setTimeVarying(true);
    }
  } catch (e2) {}
  var timeS = Number(args.time_s || 0);
  var value = args.value;
  try {
    var time = new Time();
    time.ticks = String(paSecondsToTicks(timeS));
    prop.addKey(time);
    prop.setValueAtKey(time, value, true);
    var readBack = null;
    try { readBack = prop.getValueAtKey(time); } catch (e3) {}
    if (readBack === null || readBack === undefined) {
      return paJson({ ok: false, error: 'Premiere did not return the keyframe value after writing it; storage is not verified.' });
    }
    if (typeof readBack === 'number' && typeof value === 'number' && Math.abs(readBack - value) > 0.0001) {
      return paJson({ ok: false, error: 'Premiere returned ' + readBack + ' after writing keyframe value ' + value + '; storage is not verified.' });
    }
    return paJson({
      ok: true,
      added: true,
      effect: String(args.effect_name || ''),
      property: String(args.property_name || ''),
      time_s: timeS,
      value: value,
      readback_value: readBack,
      verification: 'Premiere parameter readback only; verify playback or exported frames before relying on visual output.'
    });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paRemoveKeyframe(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var comp = paFindComponent(clip, String(args.effect_name || ''));
  if (!comp) return paJson({ ok: false, error: 'Effect not found' });
  var prop = paFindProperty(comp, String(args.property_name || ''));
  if (!prop) return paJson({ ok: false, error: 'Property not found' });
  try {
    var time = new Time();
    time.ticks = String(paSecondsToTicks(Number(args.time_s || 0)));
    prop.removeKey(time);
    return paJson({ ok: true, removed: true, effect: String(args.effect_name || ''), property: String(args.property_name || ''), time_s: Number(args.time_s || 0) });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

// Selection tools: pure public DOM (clip.setSelected/isSelected/isDisabled,
// projectItem.getColorLabel). Selection state is trivially reversible UI state,
// not a structural/timing/effect mutation, so unlike every write tool above
// these do not require backup_sequence_id.
function paDeselectAllClipsInSeq(seq) {
  var count = 0;
  try {
    for (var t = 0; t < seq.videoTracks.numTracks; t += 1) {
      var vt = seq.videoTracks[t];
      for (var c = 0; c < vt.clips.numItems; c += 1) { vt.clips[c].setSelected(false, true); count += 1; }
    }
    for (var a = 0; a < seq.audioTracks.numTracks; a += 1) {
      var at = seq.audioTracks[a];
      for (var c2 = 0; c2 < at.clips.numItems; c2 += 1) { at.clips[c2].setSelected(false, true); count += 1; }
    }
  } catch (err) {}
  return count;
}

function paSelectClipsByName(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var query = String(args.name || '').toLowerCase();
  var trackType = String(args.track_type || 'both');
  var addToSelection = !!args.add_to_selection;
  var trackIndex = (args.track_index !== undefined && args.track_index !== null) ? Number(args.track_index) : null;
  if (!addToSelection) paDeselectAllClipsInSeq(seq);
  var count = 0;
  try {
    function scan(tracks) {
      for (var t = 0; t < tracks.numTracks; t += 1) {
        if (trackIndex !== null && t !== trackIndex) continue;
        var track = tracks[t];
        for (var c = 0; c < track.clips.numItems; c += 1) {
          var clip = track.clips[c];
          if (String(clip.name || '').toLowerCase().indexOf(query) !== -1) {
            clip.setSelected(true, true);
            count += 1;
          }
        }
      }
    }
    if (trackType !== 'audio') scan(seq.videoTracks);
    if (trackType !== 'video') scan(seq.audioTracks);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, selected: count, query: String(args.name || '') });
}

function paSelectAllClips(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var trackType = String(args.track_type || 'both');
  var trackIndex = (args.track_index !== undefined && args.track_index !== null) ? Number(args.track_index) : null;
  var count = 0;
  try {
    function selectAll(tracks) {
      for (var t = 0; t < tracks.numTracks; t += 1) {
        if (trackIndex !== null && t !== trackIndex) continue;
        var track = tracks[t];
        for (var c = 0; c < track.clips.numItems; c += 1) { track.clips[c].setSelected(true, true); count += 1; }
      }
    }
    if (trackType !== 'audio') selectAll(seq.videoTracks);
    if (trackType !== 'video') selectAll(seq.audioTracks);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, selected: count });
}

function paDeselectAllClips(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var count = paDeselectAllClipsInSeq(seq);
  return paJson({ ok: true, deselected: count });
}

function paSelectClipsInRange(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var trackType = String(args.track_type || 'both');
  var trackIndex = (args.track_index !== undefined && args.track_index !== null) ? Number(args.track_index) : null;
  var startTicks = paSecondsToTicks(Number(args.start_s || 0));
  var endTicks = paSecondsToTicks(Number(args.end_s || 0));
  paDeselectAllClipsInSeq(seq);
  var count = 0;
  try {
    function selectInRange(tracks) {
      for (var t = 0; t < tracks.numTracks; t += 1) {
        if (trackIndex !== null && t !== trackIndex) continue;
        var track = tracks[t];
        for (var c = 0; c < track.clips.numItems; c += 1) {
          var clip = track.clips[c];
          var cs = parseFloat(clip.start.ticks);
          var ce = parseFloat(clip.end.ticks);
          if (cs < endTicks && ce > startTicks) { clip.setSelected(true, true); count += 1; }
        }
      }
    }
    if (trackType !== 'audio') selectInRange(seq.videoTracks);
    if (trackType !== 'video') selectInRange(seq.audioTracks);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, selected: count, range_start_s: Number(args.start_s || 0), range_end_s: Number(args.end_s || 0) });
}

function paSelectClipsByColor(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var colorIndex = Number(args.color_index);
  paDeselectAllClipsInSeq(seq);
  var count = 0;
  try {
    function scan(tracks) {
      for (var t = 0; t < tracks.numTracks; t += 1) {
        var track = tracks[t];
        for (var c = 0; c < track.clips.numItems; c += 1) {
          var clip = track.clips[c];
          try {
            if (clip.projectItem && clip.projectItem.getColorLabel() === colorIndex) {
              clip.setSelected(true, true);
              count += 1;
            }
          } catch (innerErr) {}
        }
      }
    }
    scan(seq.videoTracks);
    scan(seq.audioTracks);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, selected: count, color_index: colorIndex });
}

function paInvertSelection(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var nowSelected = 0;
  var nowDeselected = 0;
  try {
    function invert(tracks) {
      for (var t = 0; t < tracks.numTracks; t += 1) {
        var track = tracks[t];
        for (var c = 0; c < track.clips.numItems; c += 1) {
          var clip = track.clips[c];
          if (clip.isSelected()) { clip.setSelected(false, true); nowDeselected += 1; }
          else { clip.setSelected(true, true); nowSelected += 1; }
        }
      }
    }
    invert(seq.videoTracks);
    invert(seq.audioTracks);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, now_selected: nowSelected, now_deselected: nowDeselected });
}

function paSelectDisabledClips(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  paDeselectAllClipsInSeq(seq);
  var count = 0;
  try {
    function scan(tracks) {
      for (var t = 0; t < tracks.numTracks; t += 1) {
        var track = tracks[t];
        for (var c = 0; c < track.clips.numItems; c += 1) {
          try {
            if (track.clips[c].isDisabled()) { track.clips[c].setSelected(true, true); count += 1; }
          } catch (innerErr) {}
        }
      }
    }
    scan(seq.videoTracks);
    scan(seq.audioTracks);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, selected: count });
}

// Effect-level copy/remove/blend-mode: pure public Component/Property DOM.
function paCopyEffectValues(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var srcClip = paLookupClip(seq, String(args.source_track_type || 'video').toLowerCase(), Number(args.source_track_index), Number(args.source_clip_index));
  if (!srcClip) return paJson({ ok: false, error: 'Source clip not found' });
  var tgtClip = paLookupClip(seq, String(args.target_track_type || 'video').toLowerCase(), Number(args.target_track_index), Number(args.target_clip_index));
  if (!tgtClip) return paJson({ ok: false, error: 'Target clip not found' });
  var effectName = String(args.effect_name || '');
  var srcComp = paFindComponent(srcClip, effectName);
  if (!srcComp) return paJson({ ok: false, error: 'Effect not found on source clip: ' + effectName });
  var tgtComp = paFindComponent(tgtClip, effectName);
  if (!tgtComp) return paJson({ ok: false, error: 'Effect not found on target clip: ' + effectName });
  var copied = 0;
  try {
    for (var p = 0; p < srcComp.properties.numItems; p += 1) {
      var srcProp = srcComp.properties[p];
      var tgtProp = paFindProperty(tgtComp, String(srcProp.displayName || ''));
      if (!tgtProp) continue;
      try {
        var val = srcProp.getValue(0, 0);
        tgtProp.setValue(val, true);
        copied += 1;
      } catch (setErr) {}
    }
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, copied_properties: copied, effect: effectName, source_clip: String(srcClip.name || ''), target_clip: String(tgtClip.name || '') });
}

function paRemoveEffectByName(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var effectName = String(args.effect_name || '');
  var matches = [];
  try {
    // Back-to-front so removal doesn't shift not-yet-processed indices, and
    // preflight every match before mutating any so an unsupported component
    // can't cause a partial removal.
    for (var i = clip.components.numItems - 1; i >= 0; i -= 1) {
      if (String(clip.components[i].displayName || '') === effectName) matches.push(i);
    }
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  if (!matches.length) return paJson({ ok: false, error: 'Effect not found: ' + effectName });
  for (var j = 0; j < matches.length; j += 1) {
    var component = clip.components[matches[j]];
    if (!component || typeof component.remove !== 'function') {
      return paJson({ ok: false, unsupported: true, error: 'Premiere does not expose Component.remove() for "' + effectName + '" on this build. No matching components were removed; no safe targeted QE fallback exists — remove it manually in Effect Controls.' });
    }
  }
  var removed = 0;
  for (var k = 0; k < matches.length; k += 1) {
    try {
      clip.components[matches[k]].remove();
      removed += 1;
    } catch (removeErr) {
      return paJson({ ok: false, error: 'Premiere could not remove "' + effectName + '" after removing ' + removed + ' matching component(s): ' + String(removeErr) });
    }
  }
  return paJson({ ok: true, removed: removed, effect: effectName, clip_name: String(clip.name || '') });
}

// Empirically re-derived 2026-08-26 against a real Premiere build (26.3.2) by
// compositing two known solid colors on overlapping tracks, sweeping every
// value 0-27 on the first "Blend Mode" property, and matching the rendered
// pixel color against each blend mode's expected formula (re-tested with the
// two colors swapped top/bottom to disambiguate Darker Color from Dissolve,
// since both are indistinguishable from Normal on a single solid-color test).
// This is NOT the same order as the visual dropdown menu or the upstream
// reference repo's guess (which this replaces) -- do not "fix" it back to a
// sequential 1-27 mapping without re-verifying live first.
var PA_BLEND_MODE_VALUES = {
  'Color': 0, 'Color Burn': 1, 'Color Dodge': 2, 'Darken': 3, 'Darker Color': 4,
  'Difference': 5, 'Dissolve': 6, 'Exclusion': 7, 'Hard Light': 8, 'Hard Mix': 9,
  'Hue': 10, 'Lighten': 11, 'Lighter Color': 12, 'Linear Burn': 13, 'Linear Dodge': 14,
  'Linear Light': 15, 'Luminosity': 16, 'Multiply': 17, 'Normal': 18, 'Overlay': 19,
  'Pin Light': 20, 'Saturation': 21, 'Screen': 22, 'Soft Light': 23, 'Vivid Light': 24,
  'Subtract': 25, 'Divide': 26
};

// The Opacity component exposes TWO distinct properties both displayName ===
// 'Blend Mode' on this Premiere build; paFindProperty returns only the first
// (index 1 in the properties collection), which live testing confirmed is the
// one actually driving compositing -- the second (index 2, default value 0)
// was tested and found inert. This is intentional, not a bug to "fix" by
// picking the other one.
function paSetBlendMode(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var clip = paLookupClip(seq, String(args.track_type || 'video').toLowerCase(), Number(args.track_index), Number(args.clip_index));
  if (!clip) return paJson({ ok: false, error: 'Clip not found' });
  var blendModeName = String(args.blend_mode || 'Normal');
  var modeValue = PA_BLEND_MODE_VALUES.hasOwnProperty(blendModeName) ? PA_BLEND_MODE_VALUES[blendModeName] : PA_BLEND_MODE_VALUES['Normal'];
  var comp = paFindComponent(clip, 'Opacity');
  if (!comp) return paJson({ ok: false, error: 'Opacity component not found on clip' });
  var prop = paFindProperty(comp, 'Blend Mode');
  if (!prop) return paJson({ ok: false, error: 'Blend Mode property not found on Opacity component' });
  try {
    prop.setValue(modeValue, true);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({
    ok: true,
    blend_mode_requested: blendModeName,
    blend_mode_value_written: modeValue,
    clip_name: String(clip.name || '')
  });
}

// Project/bin/item management: all public documented DOM.
function paSaveProject(raw) {
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  try {
    app.project.save();
    return paJson({ ok: true, saved: true, name: String(app.project.name || ''), path: String(app.project.path || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paUndo(raw) {
  var args = paParse(raw);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var count = Number(args.count || 1);
  try {
    for (var i = 0; i < count; i += 1) app.project.undo();
    return paJson({ ok: true, undone: count });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetActiveSequence(raw) {
  var args = paParse(raw);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  try {
    app.project.activeSequence = seq;
    return paJson({ ok: true, active: true, name: String(seq.name || ''), id: paSequenceId(seq) });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paCreateBin(raw) {
  var args = paParse(raw);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var name = String(args.name || '');
  if (!name) return paJson({ ok: false, error: 'name is required' });
  var parent = app.project.rootItem;
  if (args.parent_bin) {
    parent = paFindProjectItem(String(args.parent_bin));
    if (!parent) return paJson({ ok: false, error: 'Parent bin not found: ' + String(args.parent_bin) });
  }
  try {
    var newBin = parent.createBin(name);
    return paJson({ ok: true, created: true, name: name, node_id: String(newBin.nodeId || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paDeleteBin(raw) {
  var args = paParse(raw);
  var bin = paFindProjectItem(String(args.bin_id || ''));
  if (!bin) return paJson({ ok: false, error: 'Bin not found: ' + String(args.bin_id || '') });
  if (bin.type !== 2) return paJson({ ok: false, error: 'Item is not a bin' });
  var name = String(bin.name || '');
  try {
    bin.deleteBin();
    return paJson({ ok: true, deleted: true, name: name });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paRenameBin(raw) {
  var args = paParse(raw);
  var bin = paFindProjectItem(String(args.bin_id || ''));
  if (!bin) return paJson({ ok: false, error: 'Bin not found: ' + String(args.bin_id || '') });
  if (bin.type !== 2) return paJson({ ok: false, error: 'Item is not a bin' });
  var oldName = String(bin.name || '');
  var newName = String(args.new_name || '');
  try {
    bin.renameBin(newName);
    return paJson({ ok: true, renamed: true, old_name: oldName, new_name: newName });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paMoveItemToBin(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var targetBin = paFindProjectItem(String(args.target_bin || ''));
  if (!targetBin) return paJson({ ok: false, error: 'Target bin not found: ' + String(args.target_bin || '') });
  try {
    item.moveBin(targetBin);
    return paJson({ ok: true, moved: true, item: String(item.name || ''), to_bin: String(targetBin.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paGetItemInfo(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var info = {
    name: String(item.name || ''),
    node_id: String(item.nodeId || ''),
    type: item.type === 1 ? 'clip' : (item.type === 2 ? 'bin' : (item.type === 3 ? 'sequence' : 'unknown')),
    tree_path: String(item.treePath || '')
  };
  try { info.is_sequence = !!item.isSequence(); } catch (e1) {}
  try { info.is_multicam_clip = !!item.isMulticamClip(); } catch (e2) {}
  try { info.is_merged_clip = !!item.isMergedClip(); } catch (e3) {}
  try { info.is_offline = !!item.isOffline(); } catch (e4) {}
  try { info.media_path = String(item.getMediaPath() || ''); } catch (e5) {}
  try { info.has_proxy = !!item.hasProxy(); } catch (e6) {}
  try { info.can_proxy = !!item.canProxy(); } catch (e7) {}
  return paJson({ ok: true, item: info });
}

function paSelectItem(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    item.select();
    return paJson({ ok: true, selected: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paCheckOfflineMedia(raw) {
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var offlineItems = [];
  function checkItem(item) {
    if (item.type === 1) {
      try {
        if (item.isOffline && item.isOffline()) {
          var mediaPath = '';
          try { mediaPath = String(item.getMediaPath() || ''); } catch (mpErr) {}
          offlineItems.push({ node_id: String(item.nodeId || ''), name: String(item.name || ''), media_path: mediaPath });
        }
      } catch (err) {}
    }
    if (item.type === 2 && item.children) {
      for (var i = 0; i < item.children.numItems; i += 1) checkItem(item.children[i]);
    }
  }
  try {
    var root = app.project.rootItem;
    for (var i = 0; i < root.children.numItems; i += 1) checkItem(root.children[i]);
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
  return paJson({ ok: true, offline_count: offlineItems.length, items: offlineItems });
}

function paGetMetadata(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var metadata = { name: String(item.name || ''), node_id: String(item.nodeId || '') };
  try { metadata.project_metadata = String(item.getProjectMetadata() || ''); } catch (e1) {}
  try { metadata.xmp_metadata = String(item.getXMPMetadata() || ''); } catch (e2) {}
  try { metadata.media_path = String(item.getMediaPath() || ''); } catch (e3) {}
  return paJson({ ok: true, metadata: metadata });
}

function paSetMetadata(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var fieldName = String(args.field_name || '');
  var value = String(args.value || '');
  try {
    item.setProjectMetadata(value, [fieldName]);
    return paJson({ ok: true, updated: true, item: String(item.name || ''), field: fieldName });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetColorLabel(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var colorIndex = Number(args.color_index);
  try {
    item.setColorLabel(colorIndex);
    return paJson({ ok: true, updated: true, item: String(item.name || ''), color_index: colorIndex });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paGetColorLabel(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    var colorIndex = item.getColorLabel();
    return paJson({ ok: true, item: String(item.name || ''), color_index: colorIndex });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paGetFootageInterpretation(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    var interp = item.getFootageInterpretation();
    if (!interp) return paJson({ ok: false, error: 'No footage interpretation available' });
    return paJson({
      ok: true, item: String(item.name || ''),
      alpha_usage: interp.alphaUsage, field_type: interp.fieldType, frame_rate: interp.frameRate,
      ignore_alpha: !!interp.ignoreAlpha, invert_alpha: !!interp.invertAlpha, pixel_aspect_ratio: interp.pixelAspectRatio
    });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetFootageInterpretation(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    var interp = item.getFootageInterpretation();
    if (!interp) return paJson({ ok: false, error: 'No footage interpretation available' });
    if (args.frame_rate !== undefined && args.frame_rate !== null) interp.frameRate = Number(args.frame_rate);
    if (args.pixel_aspect_ratio !== undefined && args.pixel_aspect_ratio !== null) interp.pixelAspectRatio = Number(args.pixel_aspect_ratio);
    item.setFootageInterpretation(interp);
    return paJson({ ok: true, updated: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paGetXmpMetadata(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    var xmp = String(item.getXMPMetadata() || '');
    return paJson({ ok: true, item: String(item.name || ''), xmp_metadata: xmp });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetXmpMetadata(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    item.setXMPMetadata(String(args.xmp_xml || ''));
    return paJson({ ok: true, updated: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paGetColorSpace(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var info = { item: String(item.name || '') };
  try {
    var cs = item.getColorSpace();
    info.color_space = cs ? String(cs.name || cs) : '';
  } catch (e1) { info.color_space = 'unknown'; }
  try {
    var ocs = item.getOriginalColorSpace();
    info.original_color_space = ocs ? String(ocs.name || ocs) : '';
  } catch (e2) {}
  try { info.embedded_lut = String(item.getEmbeddedLUTID() || ''); } catch (e3) {}
  try { info.input_lut = String(item.getInputLUTID() || ''); } catch (e4) {}
  return paJson({ ok: true, info: info });
}

function paImportMediaFiles(raw) {
  var args = paParse(raw);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var filePaths = args.file_paths;
  if (!filePaths || !filePaths.length) return paJson({ ok: false, error: 'file_paths is required and must be non-empty' });
  var targetBin = app.project.rootItem;
  if (args.target_bin) {
    targetBin = paFindProjectItem(String(args.target_bin));
    if (!targetBin) return paJson({ ok: false, error: 'Bin not found: ' + String(args.target_bin) });
  }
  var suppressUi = args.suppress_ui !== false;
  var paths = [];
  for (var i = 0; i < filePaths.length; i += 1) paths.push(String(filePaths[i]));
  try {
    var success = app.project.importFiles(paths, suppressUi, targetBin, false);
    if (!success) return paJson({ ok: false, error: 'Import failed' });
    return paJson({ ok: true, imported: paths.length, files: paths });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paImportFolder(raw) {
  var args = paParse(raw);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var folderPath = String(args.folder_path || '');
  var folder = new Folder(folderPath);
  if (!folder.exists) return paJson({ ok: false, error: 'Folder not found: ' + folderPath });
  var files = folder.getFiles();
  var filePaths = [];
  for (var i = 0; i < files.length; i += 1) {
    if (files[i] instanceof File) filePaths.push(files[i].fsName);
  }
  if (!filePaths.length) return paJson({ ok: false, error: 'No files found in folder' });
  try {
    var success = app.project.importFiles(filePaths, true, app.project.rootItem, false);
    if (!success) return paJson({ ok: false, error: 'Import failed' });
    return paJson({ ok: true, imported: filePaths.length, folder: folderPath });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paRelinkMedia(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var newPath = String(args.new_path || '');
  try {
    var success = item.changeMediaPath(newPath, true);
    return paJson({ ok: true, relinked: !!success, item: String(item.name || ''), new_path: newPath });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paRefreshMedia(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    item.refreshMedia();
    return paJson({ ok: true, refreshed: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetOffline(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    item.setOffline();
    return paJson({ ok: true, offline: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paHasProxy(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var info = { item: String(item.name || '') };
  try { info.has_proxy = !!item.hasProxy(); } catch (e1) { info.has_proxy = false; }
  try { info.can_proxy = !!item.canProxy(); } catch (e2) { info.can_proxy = false; }
  try { info.proxy_path = String(item.getProxyPath() || ''); } catch (e3) {}
  return paJson({ ok: true, info: info });
}

function paDetachProxy(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    item.detachProxy();
    return paJson({ ok: true, detached: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetOverrideFrameRate(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var frameRate = Number(args.frame_rate);
  try {
    item.setOverrideFrameRate(frameRate);
    return paJson({ ok: true, set: true, item: String(item.name || ''), frame_rate: frameRate });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetOverridePixelAspectRatio(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var numerator = Number(args.numerator);
  var denominator = Number(args.denominator);
  try {
    item.setOverridePixelAspectRatio(numerator, denominator);
    return paJson({ ok: true, set: true, item: String(item.name || ''), par: numerator + ':' + denominator });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetScaleToFrameSize(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    item.setScaleToFrameSize();
    return paJson({ ok: true, set: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetStartTime(raw) {
  var args = paParse(raw);
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  var startSeconds = Number(args.start_seconds || 0);
  try {
    var ticks = paSecondsToTicks(startSeconds).toString();
    item.setStartTime(ticks);
    return paJson({ ok: true, set: true, item: String(item.name || ''), start_seconds: startSeconds });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paOpenInSource(raw) {
  var args = paParse(raw);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var item = paFindProjectItem(String(args.item_id || ''));
  if (!item) return paJson({ ok: false, error: 'Item not found: ' + String(args.item_id || '') });
  try {
    app.sourceMonitor.openProjectItem(item);
    return paJson({ ok: true, opened: true, item: String(item.name || '') });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paCloseSourceMonitor(raw) {
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  try {
    app.sourceMonitor.closeClip();
    return paJson({ ok: true, closed: true });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paCloseAllSourceClips(raw) {
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  try {
    app.sourceMonitor.closeAllClips();
    return paJson({ ok: true, closed: true });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paSetSourceInOut(raw) {
  var args = paParse(raw);
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var item = null;
  try { item = app.sourceMonitor.getProjectItem(); } catch (e0) {}
  if (!item) return paJson({ ok: false, error: 'No clip open in Source Monitor' });
  var inSet = false;
  var outSet = false;
  try {
    if (args.in_seconds !== undefined && args.in_seconds !== null) {
      var inTime = new Time();
      inTime.seconds = Number(args.in_seconds);
      item.setInPoint(inTime.ticks, 4);
      inSet = true;
    }
    if (args.out_seconds !== undefined && args.out_seconds !== null) {
      var outTime = new Time();
      outTime.seconds = Number(args.out_seconds);
      item.setOutPoint(outTime.ticks, 4);
      outSet = true;
    }
    return paJson({ ok: true, item: String(item.name || ''), in_set: inSet, out_set: outSet });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paInsertFromSource(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var item = null;
  try { item = app.sourceMonitor.getProjectItem(); } catch (e0) {}
  if (!item) return paJson({ ok: false, error: 'No clip open in Source Monitor' });
  var vTrack = args.video_track_index !== undefined && args.video_track_index !== null ? Number(args.video_track_index) : 0;
  var aTrack = args.audio_track_index !== undefined && args.audio_track_index !== null ? Number(args.audio_track_index) : 0;
  try {
    var pos = seq.getPlayerPosition().ticks;
    seq.insertClip(item, pos, vTrack, aTrack);
    return paJson({ ok: true, inserted: true, item: String(item.name || ''), at_seconds: paTicksToSeconds(pos) });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paOverwriteFromSource(raw) {
  var args = paParse(raw);
  var backupErr = paRequireBackup(args);
  if (backupErr) return paJson(backupErr);
  var seq = paFindSequence(args.sequence_id);
  if (!seq) return paJson({ ok: false, error: 'Sequence not found', sequence_id: args.sequence_id || null });
  var item = null;
  try { item = app.sourceMonitor.getProjectItem(); } catch (e0) {}
  if (!item) return paJson({ ok: false, error: 'No clip open in Source Monitor' });
  var vTrack = args.video_track_index !== undefined && args.video_track_index !== null ? Number(args.video_track_index) : 0;
  var aTrack = args.audio_track_index !== undefined && args.audio_track_index !== null ? Number(args.audio_track_index) : 0;
  try {
    var pos = seq.getPlayerPosition().ticks;
    seq.overwriteClip(item, pos, vTrack, aTrack);
    return paJson({ ok: true, overwritten: true, item: String(item.name || ''), at_seconds: paTicksToSeconds(pos) });
  } catch (err) {
    return paJson({ ok: false, error: String(err) });
  }
}

function paGetSourceMonitorInfo(raw) {
  if (!paHasApp()) return paJson({ ok: false, error: 'Premiere project unavailable' });
  var item = null;
  try { item = app.sourceMonitor.getProjectItem(); } catch (e0) {}
  if (!item) return paJson({ ok: true, loaded: false });
  var info = { loaded: true, node_id: String(item.nodeId || ''), name: String(item.name || '') };
  try { info.media_path = String(item.getMediaPath() || ''); } catch (e1) {}
  try { info.in_point = paTicksToSeconds(item.getInPoint().ticks); } catch (e2) {}
  try { info.out_point = paTicksToSeconds(item.getOutPoint().ticks); } catch (e3) {}
  return paJson({ ok: true, info: info });
}

function paInspectDomObject(raw) {
  var args = paParse(raw);
  var objectPath = String(args.object_path || '');
  if (!objectPath) return paJson({ ok: false, error: 'object_path is required' });
  var maxDepth = Number(args.max_depth);
  if (!isFinite(maxDepth)) maxDepth = 1;
  if (maxDepth > 3) maxDepth = 3;
  if (maxDepth < 0) maxDepth = 0;
  try {
    var target = eval('(' + objectPath + ')');
    return paJson({ ok: true, object_path: objectPath, inspected: paInspectDomObjectValue(target, 0, maxDepth) });
  } catch (err) {
    return paJson({ ok: false, error: String(err), object_path: objectPath });
  }
}
