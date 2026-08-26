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
      "export": {
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
