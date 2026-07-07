from src.core.editor import _build_blur_filter


def test_blur_filter_disabled_is_empty() -> None:
    assert _build_blur_filter("none", 80) == ""
    assert _build_blur_filter("gaussian", 0) == ""


def test_blur_filter_gaussian_supports_directional_blur() -> None:
    assert _build_blur_filter("gaussian", 50, "horizontal") == "gblur=sigma=13.000:sigmaV=0.001"
    assert _build_blur_filter("gaussian", 50, "vertical") == "gblur=sigma=0.001:sigmaV=13.000"
    assert _build_blur_filter("gaussian", 50, "both") == "gblur=sigma=13.000"


def test_blur_filter_box_uses_luma_and_chroma_radius() -> None:
    assert _build_blur_filter("box", 50) == "boxblur=luma_radius=19:luma_power=2:chroma_radius=9:chroma_power=1"


def test_blur_filter_pixelate_uses_ffmpeg_pixelize_options() -> None:
    assert _build_blur_filter("pixelate", 55) == "pixelize=width=43:height=43:mode=avg"
