/** Test fixtures — minimal factories for Clip and ProjectState. */
import type { Clip, ProjectState } from '../store/useStore'

let _id = 0
function nextId(prefix = 'clip'): string {
  _id += 1
  return `${prefix}_${_id}`
}

/** Build a fully-populated Clip with sensible defaults. */
export function makeClip(overrides: Partial<Clip> = {}): Clip {
  return {
    id:              overrides.id ?? nextId(),
    start_s:         0,
    end_s:           5,
    clip_type:       'speech',
    label:           'Clip',
    source_path:     '/tmp/video.mp4',
    volume_pct:      100,
    scale_pct:       100,
    opacity_pct:     100,
    transition:      'Corte',
    brightness:      0,
    contrast:        0,
    saturation:      0,
    crop_top_pct:    0,
    crop_bottom_pct: 0,
    crop_left_pct:   0,
    crop_right_pct:  0,
    speed_factor:    1,
    rotation_deg:    0,
    blend_mode:      'Normal',
    z_order:         0,
    ...overrides,
  }
}

/** Build a ProjectState with paired video+audio clips per segment.
 * Segments are tuples [start_s, end_s] in project time. */
export function makeProject(segments: Array<[number, number]>, sourcePath = '/tmp/video.mp4'): ProjectState {
  const videoClips: Clip[] = []
  const audioClips: Clip[] = []
  for (const [start_s, end_s] of segments) {
    videoClips.push(makeClip({ id: nextId('v'), start_s, end_s, source_path: sourcePath, clip_type: 'speech' }))
    audioClips.push(makeClip({ id: nextId('a'), start_s, end_s, source_path: sourcePath, clip_type: 'speech' }))
  }
  const duration = segments.reduce((m, [, e]) => Math.max(m, e), 0)
  return {
    loaded:        true,
    videoPath:     sourcePath,
    duration_s:    duration,
    waveform:      [],
    video_track:   { name: 'Video',   clips: videoClips },
    audio_track:   { name: 'Audio',   clips: audioClips },
    text_track:    { name: 'Texto',   clips: [] },
    overlay_track: { name: 'Overlay', clips: [] },
    extra_video_tracks:   [],
    extra_audio_tracks:   [],
    extra_overlay_tracks: [],
    removed_ranges: [],
    saved_time_s:  0,
  }
}
