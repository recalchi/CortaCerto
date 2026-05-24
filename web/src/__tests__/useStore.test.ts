/** Tests for the Zustand store's core actions.
 *
 * Critical invariants covered here:
 *   - Audio↔video linkage is detected by source_path + matching time range
 *   - Deleting/splitting a linked clip cascades to its pair
 *   - appendVideo offsets new clips by the END of the last existing clip
 *     (not duration_s, which may include trailing silence)
 *   - rippleDelete closes the gap left by the deleted (and linked) clip
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useStore, getLinkedClipIds, aspectRatioToCss } from '../store/useStore'
import { computeAnimationStyle } from '../components/Preview/Preview'
import { makeProject } from './fixtures'

function resetStore() {
  useStore.setState({
    project: null,
    selectedClipId: null,
    selectedClipIds: [],
    past: [],
    future: [],
    isDirty: false,
  })
}

describe('getLinkedClipIds', () => {
  beforeEach(resetStore)

  it('finds the paired audio clip for a video clip with same source + time', () => {
    const project = makeProject([[0, 5]])
    const videoId = project.video_track.clips[0].id
    const audioId = project.audio_track.clips[0].id
    expect(new Set(getLinkedClipIds(videoId, project))).toEqual(new Set([videoId, audioId]))
  })

  it('returns only the clip itself when no pair exists', () => {
    const project = makeProject([[0, 5]])
    // Remove the audio clip
    project.audio_track.clips = []
    const videoId = project.video_track.clips[0].id
    expect(getLinkedClipIds(videoId, project)).toEqual([videoId])
  })

  it('respects unlinked flag — does not return paired clip if either is unlinked', () => {
    const project = makeProject([[0, 5]])
    project.video_track.clips[0].unlinked = true
    const videoId = project.video_track.clips[0].id
    expect(getLinkedClipIds(videoId, project)).toEqual([videoId])
  })

  it('does not pair clips with different source files', () => {
    const project = makeProject([[0, 5]])
    project.audio_track.clips[0].source_path = '/other/file.mp3'
    const videoId = project.video_track.clips[0].id
    expect(getLinkedClipIds(videoId, project)).toEqual([videoId])
  })

  it('does not pair clips with different time ranges (> 0.05s)', () => {
    const project = makeProject([[0, 5]])
    project.audio_track.clips[0].end_s = 5.2   // 0.2s off
    const videoId = project.video_track.clips[0].id
    expect(getLinkedClipIds(videoId, project)).toEqual([videoId])
  })
})

describe('deleteClip', () => {
  beforeEach(resetStore)

  it('cascades to the linked audio clip when deleting the video clip', () => {
    const project = makeProject([[0, 5], [10, 15]])
    useStore.setState({ project, selectedClipId: null, selectedClipIds: [] })
    const videoId = project.video_track.clips[0].id

    useStore.getState().deleteClip(videoId)

    const after = useStore.getState().project!
    expect(after.video_track.clips).toHaveLength(1)
    expect(after.audio_track.clips).toHaveLength(1)
    // The remaining clips should be the SECOND ones (index 1)
    expect(after.video_track.clips[0].start_s).toBe(10)
    expect(after.audio_track.clips[0].start_s).toBe(10)
  })

  it('does NOT delete audio when the clip is unlinked', () => {
    const project = makeProject([[0, 5]])
    project.video_track.clips[0].unlinked = true
    project.audio_track.clips[0].unlinked = true
    useStore.setState({ project, selectedClipId: null, selectedClipIds: [] })
    const videoId = project.video_track.clips[0].id

    useStore.getState().deleteClip(videoId)

    const after = useStore.getState().project!
    expect(after.video_track.clips).toHaveLength(0)
    expect(after.audio_track.clips).toHaveLength(1)
  })

  it('recalculates duration_s based on remaining clips', () => {
    const project = makeProject([[0, 5], [10, 30]])
    useStore.setState({ project, selectedClipId: null, selectedClipIds: [] })
    const lastVideoId = project.video_track.clips[1].id

    useStore.getState().deleteClip(lastVideoId)

    expect(useStore.getState().project!.duration_s).toBe(5)
  })

  it('multi-delete: when several clips are in selectedClipIds, deletes them all + their pairs', () => {
    const project = makeProject([[0, 5], [10, 15], [20, 25]])
    const v1 = project.video_track.clips[0].id
    const v2 = project.video_track.clips[1].id
    useStore.setState({ project, selectedClipId: v1, selectedClipIds: [v1, v2] })

    useStore.getState().deleteClip(v1)

    const after = useStore.getState().project!
    expect(after.video_track.clips).toHaveLength(1)
    expect(after.audio_track.clips).toHaveLength(1)
    expect(after.video_track.clips[0].start_s).toBe(20)
  })
})

describe('rippleDelete', () => {
  beforeEach(resetStore)

  it('closes the gap left by the deleted clip in both video and audio tracks', () => {
    const project = makeProject([[0, 5], [10, 20], [25, 35]])
    useStore.setState({ project, selectedClipId: null, selectedClipIds: [] })
    const middleVideoId = project.video_track.clips[1].id

    useStore.getState().rippleDelete(middleVideoId)

    const after = useStore.getState().project!
    expect(after.video_track.clips).toHaveLength(2)
    // The third clip (start=25, end=35) should shift LEFT by the deleted clip's duration (10s)
    expect(after.video_track.clips[1].start_s).toBe(15)
    expect(after.video_track.clips[1].end_s).toBe(25)
    // Same for audio
    expect(after.audio_track.clips[1].start_s).toBe(15)
    expect(after.audio_track.clips[1].end_s).toBe(25)
  })
})

describe('splitClip', () => {
  beforeEach(resetStore)

  it('splits both video and audio at the same time when clips are linked', () => {
    const project = makeProject([[0, 10]])
    useStore.setState({ project, selectedClipId: null, selectedClipIds: [] })
    const videoId = project.video_track.clips[0].id

    useStore.getState().splitClip(videoId, 4)

    const after = useStore.getState().project!
    expect(after.video_track.clips).toHaveLength(2)
    expect(after.audio_track.clips).toHaveLength(2)
    expect(after.video_track.clips[0].end_s).toBe(4)
    expect(after.video_track.clips[1].start_s).toBe(4)
    expect(after.audio_track.clips[0].end_s).toBe(4)
    expect(after.audio_track.clips[1].start_s).toBe(4)
  })
})

describe('updateClip', () => {
  beforeEach(resetStore)

  it('time patches cascade to the linked pair', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })
    const videoId = project.video_track.clips[0].id

    useStore.getState().updateClip(videoId, { start_s: 1, end_s: 6 })

    const after = useStore.getState().project!
    expect(after.video_track.clips[0].start_s).toBe(1)
    expect(after.video_track.clips[0].end_s).toBe(6)
    expect(after.audio_track.clips[0].start_s).toBe(1)
    expect(after.audio_track.clips[0].end_s).toBe(6)
  })

  it('source_offset_s also cascades to linked pair when present in time patch', () => {
    // Reproduces bug 3.1/3.2: dragging a video clip must shift source_offset_s
    // on BOTH the video and its linked audio so they keep showing/playing the
    // same source frames after being moved.
    const project = makeProject([[10, 20]])
    project.video_track.clips[0].source_offset_s = 5
    project.audio_track.clips[0].source_offset_s = 5
    useStore.setState({ project })
    const videoId = project.video_track.clips[0].id

    // Simulate a drag of +3s: start_s 10→13, end_s 20→23, source_offset_s 5→8
    useStore.getState().updateClip(videoId, {
      start_s: 13, end_s: 23, source_offset_s: 8,
    })

    const after = useStore.getState().project!
    expect(after.video_track.clips[0].source_offset_s).toBe(8)
    expect(after.audio_track.clips[0].source_offset_s).toBe(8)
    expect(after.audio_track.clips[0].start_s).toBe(13)
    expect(after.audio_track.clips[0].end_s).toBe(23)
  })

  it('non-time patches (e.g. volume) only affect the targeted clip', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })
    const videoId = project.video_track.clips[0].id
    const audioId = project.audio_track.clips[0].id

    useStore.getState().updateClip(videoId, { volume_pct: 50 })

    const after = useStore.getState().project!
    expect(after.video_track.clips[0].volume_pct).toBe(50)
    expect(after.audio_track.clips[0].volume_pct).toBe(100)   // unchanged
    expect(audioId).toBe(after.audio_track.clips[0].id)
  })
})

describe('aspect ratio (Phase 5.1)', () => {
  beforeEach(resetStore)

  it('aspectRatioToCss maps known ratios', () => {
    expect(aspectRatioToCss('16:9')).toBe('16 / 9')
    expect(aspectRatioToCss('9:16')).toBe('9 / 16')
    expect(aspectRatioToCss('1:1')).toBe('1 / 1')
    expect(aspectRatioToCss('4:5')).toBe('4 / 5')
    expect(aspectRatioToCss(undefined)).toBe('16 / 9')   // default
  })

  it('setAspectRatio updates project and marks dirty', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project, isDirty: false })
    useStore.getState().setAspectRatio('9:16')
    expect(useStore.getState().project!.aspect_ratio).toBe('9:16')
    expect(useStore.getState().isDirty).toBe(true)
  })

  it('setAspectRatio is a no-op when no project loaded', () => {
    useStore.getState().setAspectRatio('9:16')
    expect(useStore.getState().project).toBeNull()
  })

  it('setProject defaults aspect_ratio to 16:9 when absent', () => {
    useStore.getState().setProject({
      loaded: true, videoPath: '/v.mp4', duration_s: 1, waveform: [],
      video_track: { name: 'V', clips: [] }, audio_track: { name: 'A', clips: [] },
      text_track: { name: 'T', clips: [] }, overlay_track: { name: 'O', clips: [] },
      removed_ranges: [], saved_time_s: 0,
    } as any)
    expect(useStore.getState().project!.aspect_ratio).toBe('16:9')
  })
})

describe('adjustment layer (Phase 6.3)', () => {
  beforeEach(resetStore)

  it('addAdjustmentLayer inserts a clip with clip_type=adjustment on overlay track', () => {
    const project = makeProject([[0, 10]])
    useStore.setState({ project, previewTime: 3 })
    useStore.getState().addAdjustmentLayer()
    const after = useStore.getState().project!
    expect(after.overlay_track.clips).toHaveLength(1)
    expect(after.overlay_track.clips[0].clip_type).toBe('adjustment')
  })

  it('adjustment layer starts at the current playhead', () => {
    const project = makeProject([[0, 10]])
    useStore.setState({ project, previewTime: 4 })
    useStore.getState().addAdjustmentLayer(3)
    const adj = useStore.getState().project!.overlay_track.clips[0]
    expect(adj.start_s).toBe(4)
    expect(adj.end_s).toBe(7)   // 4 + 3
  })

  it('adjustment layer is selected after creation', () => {
    const project = makeProject([[0, 10]])
    useStore.setState({ project })
    useStore.getState().addAdjustmentLayer()
    const state = useStore.getState()
    const adjId = state.project!.overlay_track.clips[0].id
    expect(state.selectedClipId).toBe(adjId)
  })

  it('addAdjustmentLayer is a no-op when no project loaded', () => {
    useStore.getState().addAdjustmentLayer()
    expect(useStore.getState().project).toBeNull()
  })
})

describe('animations (Phase 6.2)', () => {
  it('returns null when no animation is configured', () => {
    const clip = { start_s: 0, end_s: 5 }
    expect(computeAnimationStyle(2, clip)).toBeNull()
  })

  it('returns null when previewTime is OUTSIDE the entry window', () => {
    // animation_in_duration_s = 0.5, so window is 0..0.5s
    const clip = { start_s: 0, end_s: 5, animation_in: 'fade', animation_in_duration_s: 0.5 }
    expect(computeAnimationStyle(1, clip)).toBeNull()   // 1s > 0.5s window
  })

  it('fade entry: opacity rises from 0 to 1 across the window', () => {
    const clip = { start_s: 0, end_s: 5, animation_in: 'fade', animation_in_duration_s: 1 }
    expect(computeAnimationStyle(0,    clip)?.opacity).toBe(0)     // start of window
    const mid = computeAnimationStyle(0.5, clip)
    expect(mid?.opacity).toBeGreaterThan(0)
    expect(mid?.opacity).toBeLessThan(1)
  })

  it('slide-left entry: transform translates from -100% toward 0', () => {
    const clip = { start_s: 0, end_s: 5, animation_in: 'slide-left', animation_in_duration_s: 1 }
    const start = computeAnimationStyle(0, clip)
    expect(start?.transform).toMatch(/translateX\(-100/)
  })

  it('exit animation activates near the end of the clip', () => {
    const clip = { start_s: 0, end_s: 5, animation_out: 'fade', animation_out_duration_s: 0.5 }
    // At t=4.5 we're 0.5s before end → IN the exit window
    expect(computeAnimationStyle(4.8, clip)).not.toBeNull()
  })

  it('entry animation has priority over exit when windows overlap', () => {
    // Tiny clip (0.5s) with both animations of 0.5s — entry should win at t=0
    const clip = {
      start_s: 0, end_s: 0.5,
      animation_in:  'fade', animation_in_duration_s:  0.5,
      animation_out: 'fade', animation_out_duration_s: 0.5,
    }
    const r = computeAnimationStyle(0, clip)
    expect(r?.opacity).toBe(0)   // start of fade-in
  })
})

describe('setProject (load resilience)', () => {
  beforeEach(resetStore)

  it('loads a minimal old-format project without crashing (missing extras)', () => {
    // Simulates a .ccproj saved before extra_*_tracks/source_proxies existed
    const legacy = {
      loaded: true,
      videoPath: '/old/video.mp4',
      duration_s: 30,
      waveform: [],
      video_track: { name: 'Video', clips: [] },
      audio_track: { name: 'Audio', clips: [] },
      text_track:  { name: 'Texto', clips: [] },
      overlay_track: { name: 'Overlay', clips: [] },
      removed_ranges: [],
      saved_time_s: 0,
    }
    useStore.getState().setProject(legacy as any)
    const after = useStore.getState().project!
    expect(after.extra_video_tracks).toEqual([])
    expect(after.extra_audio_tracks).toEqual([])
    expect(after.source_proxies).toEqual({})
  })

  it('preserves _projectName as projectName when present', () => {
    useStore.getState().setProject({
      loaded: true, videoPath: '/x.mp4', duration_s: 1, waveform: [],
      video_track: { name: 'V', clips: [] }, audio_track: { name: 'A', clips: [] },
      text_track: { name: 'T', clips: [] }, overlay_track: { name: 'O', clips: [] },
      removed_ranges: [], saved_time_s: 0,
      _projectName: 'Meu Projeto',
    } as any)
    expect(useStore.getState().projectName).toBe('Meu Projeto')
  })
})

describe('multi-track (Phase 2b)', () => {
  beforeEach(resetStore)

  it('addExtraTrack appends an empty video track', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })
    useStore.getState().addExtraTrack('video')
    const after = useStore.getState().project!
    expect(after.extra_video_tracks).toHaveLength(1)
    expect(after.extra_video_tracks![0].clips).toEqual([])
    expect(after.extra_video_tracks![0].name).toMatch(/Vídeo/)
  })

  it('addExtraTrack appends an empty audio track', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })
    useStore.getState().addExtraTrack('audio')
    const after = useStore.getState().project!
    expect(after.extra_audio_tracks).toHaveLength(1)
    expect(after.extra_audio_tracks![0].name).toMatch(/Áudio/)
  })

  it('removeExtraTrack removes by index', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })
    useStore.getState().addExtraTrack('video')
    useStore.getState().addExtraTrack('video')
    useStore.getState().removeExtraTrack('video', 0)
    expect(useStore.getState().project!.extra_video_tracks).toHaveLength(1)
  })

  it('removeExtraTrack ignores out-of-range index (no crash)', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })
    useStore.getState().addExtraTrack('video')
    useStore.getState().removeExtraTrack('video', 99)
    expect(useStore.getState().project!.extra_video_tracks).toHaveLength(1)
  })

  it('moveClipToTrack moves a clip from main video track to extra video track', () => {
    const project = makeProject([[0, 5], [10, 15]])
    useStore.setState({ project })
    useStore.getState().addExtraTrack('video')
    const movingId = project.video_track.clips[0].id

    useStore.getState().moveClipToTrack(movingId, { kind: 'video', index: 1 })

    const after = useStore.getState().project!
    expect(after.video_track.clips).toHaveLength(1)
    expect(after.video_track.clips[0].start_s).toBe(10)   // only second clip left
    expect(after.extra_video_tracks![0].clips).toHaveLength(1)
    expect(after.extra_video_tracks![0].clips[0].id).toBe(movingId)
  })

  it('moveClipToTrack preserves clip start_s and end_s (no time shift)', () => {
    const project = makeProject([[5, 12]])
    useStore.setState({ project })
    useStore.getState().addExtraTrack('video')
    const id = project.video_track.clips[0].id

    useStore.getState().moveClipToTrack(id, { kind: 'video', index: 1 })

    const moved = useStore.getState().project!.extra_video_tracks![0].clips[0]
    expect(moved.start_s).toBe(5)
    expect(moved.end_s).toBe(12)
  })

  it('moveClipToTrack ignores invalid clip id', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })
    useStore.getState().addExtraTrack('video')
    useStore.getState().moveClipToTrack('nonexistent', { kind: 'video', index: 1 })
    // State unchanged: main track still has the original clip
    expect(useStore.getState().project!.video_track.clips).toHaveLength(1)
  })
})

describe('appendVideo', () => {
  beforeEach(resetStore)

  it('first video (empty main track) goes to the main video track at time 0', () => {
    const project = makeProject([])   // empty main track
    useStore.setState({ project })

    useStore.getState().appendVideo(
      [{ start_s: 0, end_s: 10, source_path: '/tmp/v1.mp4', label: 'V1' }],
      [],
    )

    const after = useStore.getState().project!
    expect(after.video_track.clips).toHaveLength(1)
    expect(after.video_track.clips[0].start_s).toBe(0)
    expect(after.video_track.clips[0].end_s).toBe(10)
    expect(after.video_track.clips[0].source_offset_s).toBe(0)
    // No extra tracks created when main was empty
    expect(after.extra_video_tracks ?? []).toHaveLength(0)
  })

  it('second video (main track has clips) creates a NEW parallel extra track', () => {
    const project = makeProject([[0, 10]])   // main track occupied
    useStore.setState({ project })

    useStore.getState().appendVideo(
      [{ start_s: 0, end_s: 8, source_path: '/tmp/v2.mp4', label: 'V2' }],
      [],
    )

    const after = useStore.getState().project!
    // Main track unchanged
    expect(after.video_track.clips).toHaveLength(1)
    expect(after.video_track.clips[0].start_s).toBe(0)
    // New extra video + audio tracks created for the second import
    expect(after.extra_video_tracks).toHaveLength(1)
    expect(after.extra_video_tracks![0].clips).toHaveLength(1)
    expect(after.extra_video_tracks![0].clips[0].start_s).toBe(0)
    expect(after.extra_video_tracks![0].clips[0].source_path).toBe('/tmp/v2.mp4')
    expect(after.extra_audio_tracks).toHaveLength(1)
    expect(after.extra_audio_tracks![0].clips).toHaveLength(1)
  })

  it('third video creates a second extra track (each import gets its own row)', () => {
    const project = makeProject([[0, 10]])
    useStore.setState({ project })

    useStore.getState().appendVideo(
      [{ start_s: 0, end_s: 5, source_path: '/tmp/v2.mp4', label: 'V2' }],
      [],
    )
    useStore.getState().appendVideo(
      [{ start_s: 0, end_s: 7, source_path: '/tmp/v3.mp4', label: 'V3' }],
      [],
    )

    const after = useStore.getState().project!
    expect(after.extra_video_tracks).toHaveLength(2)
    expect(after.extra_audio_tracks).toHaveLength(2)
    expect(after.extra_video_tracks![1].clips[0].source_path).toBe('/tmp/v3.mp4')
  })

  it('duration_s expands to include parallel extra-track clips that are longer', () => {
    const project = makeProject([[0, 5]])
    useStore.setState({ project })

    useStore.getState().appendVideo(
      [{ start_s: 0, end_s: 12, source_path: '/tmp/v2.mp4', label: 'V2' }],
      [],
    )

    // Extra clip ends at 12s — longer than main 5s
    expect(useStore.getState().project!.duration_s).toBe(12)
  })
})
