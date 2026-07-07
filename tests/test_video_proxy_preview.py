from unittest import mock

from fastapi.testclient import TestClient

from src.api import server as api_server


def test_mov_container_requires_preview_proxy_even_with_h264() -> None:
    assert api_server._needs_preview_proxy(
        "C:/midia/IMG_9913.MOV",
        {"codec": "h264", "pix_fmt": "yuv420p", "profile": "High"},
    )


def test_safe_mp4_h264_does_not_require_preview_proxy() -> None:
    assert not api_server._needs_preview_proxy(
        "C:/midia/video.mp4",
        {"codec": "h264", "pix_fmt": "yuv420p", "profile": "High"},
    )


def test_video_proxy_ensure_passes_force_flag() -> None:
    client = TestClient(api_server.app)
    with mock.patch("os.path.isfile", return_value=True), \
         mock.patch.object(api_server, "_start_proxy_if_needed", return_value=("h264", "transcoding", "C:/tmp/proxy.mp4")) as start:
        response = client.post("/api/video-proxy-ensure", json={"path": "C:/midia/video.mp4", "force": True})

    assert response.status_code == 200
    assert response.json()["proxy_status"] == "transcoding"
    start.assert_called_once()
    called_path = start.call_args.args[0].replace("\\", "/")
    assert called_path == "C:/midia/video.mp4"
    assert start.call_args.kwargs == {"force": True}
