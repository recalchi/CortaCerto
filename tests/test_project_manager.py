"""Tests for src/ui/project_manager.py (no Tk display required)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class ProjectEntryTests(unittest.TestCase):
    """Tests for ProjectEntry data class and helpers."""

    def _make_entry(self, **kw):
        from src.ui.project_manager import ProjectEntry
        defaults = dict(
            path="/fake/project.ccp",
            name="Test Project",
            category="youtube",
            status="edit",
            opened_at=time.time(),
            updated_at=time.time(),
            duration_s=125.0,
            clips_count=10,
            size_mb=250.0,
            thumb_seed=42,
            wave_seed=99,
        )
        defaults.update(kw)
        return ProjectEntry(**defaults)

    def test_duration_label_minutes_seconds(self):
        from src.ui.project_manager import ProjectEntry
        e = self._make_entry(duration_s=75.0)
        self.assertEqual(e.duration_label(), "01:15")

    def test_duration_label_hours(self):
        e = self._make_entry(duration_s=3661.0)
        self.assertEqual(e.duration_label(), "01:01:01")

    def test_duration_label_zero(self):
        e = self._make_entry(duration_s=0.0)
        self.assertEqual(e.duration_label(), "00:00")

    def test_edited_label_minutes(self):
        e = self._make_entry(updated_at=time.time() - 120)
        label = e.edited_label()
        self.assertIn("min", label)

    def test_edited_label_hours(self):
        e = self._make_entry(updated_at=time.time() - 7200)
        label = e.edited_label()
        self.assertIn("h", label)

    def test_edited_label_yesterday(self):
        e = self._make_entry(updated_at=time.time() - 86401)
        label = e.edited_label()
        self.assertIn("ontem", label)

    def test_edited_label_days(self):
        e = self._make_entry(updated_at=time.time() - 86400 * 5)
        label = e.edited_label()
        self.assertIn("dia", label)

    def test_section_key_recent(self):
        e = self._make_entry(updated_at=time.time() - 3600)
        self.assertEqual(e.section_key(), "recent")

    def test_section_key_all(self):
        e = self._make_entry(updated_at=time.time() - 86400 * 4)
        self.assertEqual(e.section_key(), "all")

    def test_section_key_old(self):
        e = self._make_entry(updated_at=time.time() - 86400 * 20)
        self.assertEqual(e.section_key(), "old")

    def test_size_label_mb(self):
        e = self._make_entry(size_mb=250.5)
        self.assertIn("MB", e.size_label())

    def test_size_label_gb(self):
        e = self._make_entry(size_mb=1500.0)
        self.assertIn("GB", e.size_label())

    def test_size_label_kb(self):
        e = self._make_entry(size_mb=0.5)
        self.assertIn("KB", e.size_label())

    def test_exists_false_for_fake_path(self):
        e = self._make_entry(path="/nonexistent_path/project.ccp")
        self.assertFalse(e.exists())

    def test_exists_true_for_real_file(self):
        with tempfile.NamedTemporaryFile(suffix=".ccp", delete=False) as f:
            path = f.name
        try:
            e = self._make_entry(path=path)
            self.assertTrue(e.exists())
        finally:
            os.unlink(path)


class RecentProjectsStoreTests(unittest.TestCase):
    """Tests for load/save/register recent projects."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_path = None

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _patch_recent_path(self):
        """Returns a context manager patching _recent_path to use tmpdir."""
        from src.ui import project_manager as pm
        recent_file = Path(self._tmpdir) / "recent_projects.json"

        def _fake_recent_path():
            return recent_file

        return patch.object(pm, "_recent_path", _fake_recent_path)

    def _make_ccp(self, name: str = "test") -> str:
        path = os.path.join(self._tmpdir, f"{name}.ccp")
        Path(path).write_text('{"name": "' + name + '"}', encoding="utf-8")
        return path

    def test_load_empty_when_no_file(self):
        with self._patch_recent_path():
            from src.ui.project_manager import _load_recent_projects
            result = _load_recent_projects()
        self.assertEqual(result, [])

    def test_register_and_load_round_trip(self):
        with self._patch_recent_path():
            from src.ui.project_manager import (
                register_recent_project, _load_recent_projects
            )
            path = self._make_ccp("myproject")
            register_recent_project(path, name="My Project", category="podcast")
            entries = _load_recent_projects()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "My Project")
        self.assertEqual(entries[0].category, "podcast")
        self.assertEqual(entries[0].path, path)

    def test_register_twice_updates_not_duplicates(self):
        with self._patch_recent_path():
            from src.ui.project_manager import (
                register_recent_project, _load_recent_projects
            )
            path = self._make_ccp("dup")
            register_recent_project(path, name="First")
            register_recent_project(path, name="Updated")
            entries = _load_recent_projects()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Updated")

    def test_register_multiple_projects(self):
        with self._patch_recent_path():
            from src.ui.project_manager import (
                register_recent_project, _load_recent_projects
            )
            for i in range(5):
                path = self._make_ccp(f"proj{i}")
                time.sleep(0.01)
                register_recent_project(path, name=f"Project {i}")
            entries = _load_recent_projects()
        self.assertEqual(len(entries), 5)

    def test_nonexistent_projects_filtered_on_load(self):
        with self._patch_recent_path():
            from src.ui.project_manager import (
                _save_recent_projects, _load_recent_projects, ProjectEntry
            )
            ghost = ProjectEntry(
                path="/nonexistent/ghost.ccp", name="Ghost",
                thumb_seed=0, wave_seed=0,
            )
            _save_recent_projects([ghost])
            entries = _load_recent_projects()
        # Ghost was filtered because file does not exist
        self.assertEqual(entries, [])

    def test_save_load_preserves_fields(self):
        with self._patch_recent_path():
            from src.ui.project_manager import (
                _save_recent_projects, _load_recent_projects, ProjectEntry
            )
            path = self._make_ccp("fields")
            e = ProjectEntry(
                path=path, name="Field Test",
                category="shorts", status="final",
                duration_s=123.5, clips_count=7, size_mb=300.0,
                thumb_seed=88, wave_seed=44,
            )
            _save_recent_projects([e])
            loaded = _load_recent_projects()
        self.assertEqual(len(loaded), 1)
        le = loaded[0]
        self.assertEqual(le.category, "shorts")
        self.assertEqual(le.status, "final")
        self.assertAlmostEqual(le.duration_s, 123.5)
        self.assertEqual(le.clips_count, 7)
        self.assertAlmostEqual(le.size_mb, 300.0)
        self.assertEqual(le.thumb_seed, 88)

    def test_register_assigns_thumb_seed(self):
        with self._patch_recent_path():
            from src.ui.project_manager import (
                register_recent_project, _load_recent_projects
            )
            path = self._make_ccp("seed_test")
            register_recent_project(path)
            entries = _load_recent_projects()
        self.assertIsInstance(entries[0].thumb_seed, int)
        self.assertGreaterEqual(entries[0].thumb_seed, 0)


class WaveformGenerationTests(unittest.TestCase):
    def test_gen_wave_length(self):
        from src.ui.project_manager import _gen_wave
        wave = _gen_wave(42, bars=32)
        self.assertEqual(len(wave), 32)

    def test_gen_wave_values_in_range(self):
        from src.ui.project_manager import _gen_wave
        wave = _gen_wave(123, bars=64)
        for v in wave:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_gen_wave_same_seed_deterministic(self):
        from src.ui.project_manager import _gen_wave
        w1 = _gen_wave(7, bars=24)
        w2 = _gen_wave(7, bars=24)
        self.assertEqual(w1, w2)

    def test_gen_wave_different_seeds_differ(self):
        from src.ui.project_manager import _gen_wave
        w1 = _gen_wave(1, bars=24)
        w2 = _gen_wave(2, bars=24)
        self.assertNotEqual(w1, w2)


class HslToRgbTests(unittest.TestCase):
    def test_red(self):
        from src.ui.project_manager import _hsl_to_rgb
        r, g, b = _hsl_to_rgb(0, 1.0, 0.5)
        self.assertGreater(r, 200)
        self.assertLess(g, 50)
        self.assertLess(b, 50)

    def test_achromatic(self):
        from src.ui.project_manager import _hsl_to_rgb
        r, g, b = _hsl_to_rgb(0, 0.0, 0.5)
        self.assertEqual(r, g)
        self.assertEqual(g, b)

    def test_returns_valid_range(self):
        from src.ui.project_manager import _hsl_to_rgb
        for h in range(0, 360, 30):
            r, g, b = _hsl_to_rgb(h, 0.5, 0.4)
            self.assertGreaterEqual(r, 0)
            self.assertLessEqual(r, 255)
            self.assertGreaterEqual(g, 0)
            self.assertLessEqual(g, 255)
            self.assertGreaterEqual(b, 0)
            self.assertLessEqual(b, 255)


class BlendColorTests(unittest.TestCase):
    def test_blend_full_alpha_returns_color(self):
        from src.ui.project_manager import _blend
        result = _blend("#ffffff", 1.0, "#000000")
        self.assertEqual(result.lower(), "#ffffff")

    def test_blend_zero_alpha_returns_bg(self):
        from src.ui.project_manager import _blend
        result = _blend("#ffffff", 0.0, "#000000")
        self.assertEqual(result.lower(), "#000000")

    def test_blend_half_is_midpoint(self):
        from src.ui.project_manager import _blend
        result = _blend("#ffffff", 0.5, "#000000")
        # Should be ~#7f7f7f
        val = int(result.lstrip("#"), 16)
        r = (val >> 16) & 0xff
        self.assertAlmostEqual(r, 127, delta=2)


class StatusConstantsTests(unittest.TestCase):
    def test_all_statuses_present(self):
        from src.ui.project_manager import STATUS
        for key in ("edit", "review", "final", "draft"):
            self.assertIn(key, STATUS)

    def test_status_tuple_has_three_parts(self):
        from src.ui.project_manager import STATUS
        for key, val in STATUS.items():
            self.assertEqual(len(val), 3, f"STATUS['{key}'] should be (label, bg, fg)")


class CategoryConstantsTests(unittest.TestCase):
    def test_all_categories_have_hues(self):
        from src.ui.project_manager import CAT_HUE, CATEGORIES
        for cat_id, *_ in CATEGORIES:
            self.assertIn(cat_id, CAT_HUE, f"Missing hue for category '{cat_id}'")

    def test_hue_values_in_range(self):
        from src.ui.project_manager import CAT_HUE
        for cat_id, (ha, hb) in CAT_HUE.items():
            self.assertGreaterEqual(ha, 0)
            self.assertLessEqual(ha, 360)
            self.assertGreaterEqual(hb, 0)
            self.assertLessEqual(hb, 360)


class ProjectEntryDictTests(unittest.TestCase):
    """Tests for dataclass serialization via asdict."""

    def test_asdict_round_trip(self):
        from src.ui.project_manager import ProjectEntry
        from dataclasses import asdict
        e = ProjectEntry(
            path="/x.ccp", name="Round Trip",
            category="review", status="final",
            duration_s=300.0, clips_count=15,
            size_mb=500.0, thumb_seed=7, wave_seed=3,
        )
        d = asdict(e)
        e2 = ProjectEntry(**d)
        self.assertEqual(e2.name, e.name)
        self.assertEqual(e2.category, e.category)
        self.assertEqual(e2.status, e.status)

    def test_default_status_is_draft(self):
        from src.ui.project_manager import ProjectEntry
        e = ProjectEntry(path="/x.ccp", name="Minimal")
        self.assertEqual(e.status, "draft")

    def test_default_category_is_youtube(self):
        from src.ui.project_manager import ProjectEntry
        e = ProjectEntry(path="/x.ccp", name="Minimal")
        self.assertEqual(e.category, "youtube")


if __name__ == "__main__":
    unittest.main()
