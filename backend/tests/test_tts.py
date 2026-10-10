"""Unit test untuk TTS Service & Audio-Video Merger endpoints."""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.routes as routes_module
from app.api.routes import router
from app.schemas.storyboard import Clip, ProjectInfo, Section, Storyboard, Summary
from app.services.tts_service import AVAILABLE_VOICES, clean_text_for_tts, generate_tts_sync


class TestTtsService(unittest.TestCase):
    def test_clean_text_for_tts(self):
        raw = "# Header 1\n## Section 2\n* Bullet 1\n**Bold text**\n\nNaskah narasi film."
        cleaned = clean_text_for_tts(raw)
        self.assertNotIn("# Header", cleaned)
        self.assertNotIn("*", cleaned)
        self.assertIn("Naskah narasi film.", cleaned)

    def test_available_voices(self):
        self.assertGreater(len(AVAILABLE_VOICES), 0)
        codes = [v["code"] for v in AVAILABLE_VOICES]
        self.assertIn("id-ID-ArdiNeural", codes)
        self.assertIn("id-ID-GadisNeural", codes)
        self.assertIn("en-US-AvaNeural", codes)


class TestTtsRoutes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)

        self.orig_projects_dir = routes_module.PROJECTS_DIR
        routes_module.PROJECTS_DIR = self.work_dir / "projects"
        routes_module.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

        self.project_id = "proj_tts_test"
        self.proj_dir = routes_module.PROJECTS_DIR / self.project_id
        self.proj_dir.mkdir(parents=True, exist_ok=True)

        (self.proj_dir / "voiceover.txt").write_text(
            "Di sebuah kerajaan tua, sang putri berjuang mempertahankan tahtanya.", encoding="utf-8"
        )

    def tearDown(self):
        routes_module.PROJECTS_DIR = self.orig_projects_dir
        self.temp_dir.cleanup()

    def test_get_voices(self):
        res = self.client.get("/api/tts/voices")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("voices", data)
        self.assertGreater(len(data["voices"]), 0)

    def test_generate_tts_endpoint(self):
        res = self.client.post(
            f"/api/projects/{self.project_id}/tts",
            data={"voice": "id-ID-ArdiNeural"},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["audio_filename"], "voiceover.mp3")

        audio_file = self.proj_dir / "voiceover.mp3"
        self.assertTrue(audio_file.exists())
        self.assertGreater(audio_file.stat().st_size, 0)

        # Test download voiceover.mp3
        dl_res = self.client.get(f"/api/projects/{self.project_id}/voiceover.mp3")
        self.assertEqual(dl_res.status_code, 200)
        self.assertEqual(dl_res.headers["content-type"], "audio/mpeg")


if __name__ == "__main__":
    unittest.main()
