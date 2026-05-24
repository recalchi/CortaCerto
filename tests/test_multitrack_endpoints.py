"""Integration tests for the Phase 2b multi-track endpoints (/api/add-track,
/api/remove-track).  Uses FastAPI TestClient — no real server required.
"""
import unittest

from fastapi.testclient import TestClient

from src.api import server as api_server
from src.core.timeline_model import build_timeline_model


class MultiTrackEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api_server.app)
        # Seed an in-memory project so endpoints have something to operate on
        timeline = build_timeline_model(
            duration_s=10.0,
            speech_segments=[(0.0, 5.0), (5.0, 10.0)],
        )
        api_server._current_project = {
            "timeline":   timeline,
            "path":       "/fake/video.mp4",
            "duration_s": 10.0,
            "analysis":   None,
        }

    def tearDown(self) -> None:
        api_server._current_project = None

    def test_add_video_track_returns_updated_project(self) -> None:
        r = self.client.post("/api/add-track", json={"type": "video"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["extra_video_tracks"]), 1)
        self.assertEqual(body["extra_video_tracks"][0]["clips"], [])

    def test_add_audio_track(self) -> None:
        r = self.client.post("/api/add-track", json={"type": "audio"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["extra_audio_tracks"]), 1)

    def test_add_overlay_track(self) -> None:
        r = self.client.post("/api/add-track", json={"type": "overlay"})
        self.assertEqual(r.status_code, 200)
        # Overlay extras aren't in the response (legacy path), but the call
        # should still succeed without error.

    def test_add_track_invalid_type_returns_400(self) -> None:
        r = self.client.post("/api/add-track", json={"type": "garbage"})
        self.assertEqual(r.status_code, 400)

    def test_remove_video_track(self) -> None:
        # Add then remove
        self.client.post("/api/add-track", json={"type": "video"})
        self.client.post("/api/add-track", json={"type": "video"})
        r = self.client.post("/api/remove-track", json={"type": "video", "index": 0})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["extra_video_tracks"]), 1)

    def test_remove_track_out_of_range_returns_404(self) -> None:
        r = self.client.post("/api/remove-track", json={"type": "video", "index": 99})
        self.assertEqual(r.status_code, 404)

    def test_remove_track_requires_index(self) -> None:
        r = self.client.post("/api/remove-track", json={"type": "video"})
        self.assertEqual(r.status_code, 400)

    def test_add_track_with_no_project_returns_400(self) -> None:
        api_server._current_project = None
        r = self.client.post("/api/add-track", json={"type": "video"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
