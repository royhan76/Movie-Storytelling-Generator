"""Unit test untuk Plan 2 Video Renderer (probe, transform, concat, remove audio, FastAPI routes)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.routes import router
from app.schemas.storyboard import Clip, ProjectInfo, Section, Storyboard, Summary
from app.services.plan2_renderer import Plan2Renderer
from app.services.video_prober import probe_video
from app.services.video_processor import FFmpegProcessor


def create_synthetic_video(output_path: Path, duration_sec: int = 10, width: int = 640, height: int = 360) -> Path:
    """Buat file mp4 sintetis sederhana menggunakan FFmpeg testsrc untuk testing."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("FFmpeg tidak ditemukan di system PATH.")

    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration_sec}:size={width}x{height}:rate=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        raise RuntimeError(f"Gagal membuat synthetic video: {res.stderr}")
    return output_path


class TestPlan2Renderer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)

        # Create synthetic source video
        self.video_path = self.work_dir / "sample_movie.mp4"
        create_synthetic_video(self.video_path, duration_sec=10, width=640, height=360)

        # Setup sample Storyboard
        self.storyboard = Storyboard(
            project=ProjectInfo(title="Test Plan 2", source_subtitle="test.srt", target_duration_minutes=1),
            sections=[
                Section(
                    section_id=1,
                    visual="Hero walk",
                    voice_over="Di sebuah desa kecil...",
                    clips=[
                        Clip(clip_id=1, beat="walk", start="00:00:01", src=2.0, trx="baref", out=2.0),
                        Clip(clip_id=2, beat="stare", start="00:00:04", src=1.0, trx="fz12", out=1.2),
                        Clip(clip_id=3, beat="run", start="00:00:06", src=2.0, trx="s50", out=4.0),
                    ],
                )
            ],
            summary=Summary(total_sections=1, total_clips=3, total_clip_duration=7.2),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_probe_video(self):
        meta = probe_video(self.video_path)
        self.assertAlmostEqual(meta["duration"], 10.0, delta=0.5)
        self.assertEqual(meta["width"], 640)
        self.assertEqual(meta["height"], 360)
        self.assertAlmostEqual(meta["fps"], 30.0, delta=0.5)
        self.assertFalse(meta["has_audio"])

    def test_renderer_pipeline(self):
        projects_dir = self.work_dir / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        renderer = Plan2Renderer(projects_dir=projects_dir)
        project_id = "test_project_001"

        events = []

        def progress_cb(stage: str, data: dict):
            events.append((stage, data))

        # Render without TTS (video only)
        res_no_tts = renderer.render(
            video_path=self.video_path,
            storyboard=self.storyboard,
            project_id=project_id,
            include_tts=False,
            progress=progress_cb,
        )

        self.assertTrue(res_no_tts["success"])
        out_file = Path(res_no_tts["output_path"])
        self.assertTrue(out_file.exists())
        self.assertGreater(out_file.stat().st_size, 0)

        # Probe output video final (no audio)
        final_meta = probe_video(out_file)
        self.assertFalse(final_meta["has_audio"], "Output no_audio harus NO AUDIO")
        self.assertEqual(final_meta["width"], 640)
        self.assertEqual(final_meta["height"], 360)

        # Render with TTS (video + audio merged)
        res_tts = renderer.render(
            video_path=self.video_path,
            storyboard=self.storyboard,
            project_id=project_id,
            include_tts=True,
            progress=None,
        )
        self.assertTrue(res_tts["success"])
        out_tts_file = Path(res_tts["output_path"])
        self.assertTrue(out_tts_file.exists())
        self.assertGreater(out_tts_file.stat().st_size, 0)

        final_tts_meta = probe_video(out_tts_file)
        self.assertTrue(final_tts_meta["has_audio"], "Output 1-click TTS harus mengandung AUDIO")

    def test_audio_duration_precision(self):
        """Verifikasi bahwa probe_audio membaca durasi MP3 presisi dan durasi video final cocok dengan audio."""
        from app.services.tts_service import generate_storyboard_tts_sections
        from app.services.video_prober import probe_audio

        proj_dir = self.work_dir / "projects" / "test_precision"
        proj_dir.mkdir(parents=True, exist_ok=True)
        tts_work = proj_dir / "tts"

        audio_path, section_durs = generate_storyboard_tts_sections(
            storyboard=self.storyboard,
            voice="id-ID-ArdiNeural",
            work_dir=tts_work,
            final_output_path=proj_dir / "voiceover.mp3",
        )

        self.assertTrue(audio_path.exists())
        self.assertIn(1, section_durs)

        # Probe total audio duration
        audio_meta = probe_audio(audio_path)
        total_audio_dur = audio_meta["duration"]

        self.assertGreater(total_audio_dur, 0.5)

        # Render Plan 2 dengan TTS
        renderer = Plan2Renderer(projects_dir=self.work_dir / "projects")
        res = renderer.render(
            video_path=self.video_path,
            storyboard=self.storyboard,
            project_id="test_precision",
            voice="id-ID-ArdiNeural",
            include_tts=True,
        )

        final_file = Path(res["output_path"])
        video_meta = probe_video(final_file)

        self.assertTrue(video_meta["has_audio"])
        # Video duration dan audio duration harus cocok presisi (toleransi <= 0.3s)
        self.assertAlmostEqual(video_meta["duration"], total_audio_dur, delta=0.3)


class TestPlan2Routes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)

        # Override PROJECTS_DIR in routes for testing
        import app.api.routes as routes_module

        self.orig_projects_dir = routes_module.PROJECTS_DIR
        routes_module.PROJECTS_DIR = self.work_dir / "projects"
        routes_module.PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

        # Setup sample project folder with storyboard.json
        self.project_id = "proj_test_api"
        self.proj_dir = routes_module.PROJECTS_DIR / self.project_id
        self.proj_dir.mkdir(parents=True, exist_ok=True)

        self.video_path = self.work_dir / "test_api_movie.mp4"
        create_synthetic_video(self.video_path, duration_sec=8, width=640, height=360)

        sb = Storyboard(
            project=ProjectInfo(title="Test API", source_subtitle="api.srt"),
            sections=[
                Section(
                    section_id=1,
                    voice_over="Kisah petualangan hebat di desa kecil yang indah.",
                    clips=[
                        Clip(clip_id=1, beat="action", start="00:00:00", src=2.0, trx="baref", out=2.0),
                    ],
                )
            ],
            summary=Summary(total_sections=1, total_clips=1, total_clip_duration=2.0),
        )
        (self.proj_dir / "storyboard.json").write_text(json.dumps(sb.model_dump(), indent=2), encoding="utf-8")

    def tearDown(self):
        import app.api.routes as routes_module

        routes_module.PROJECTS_DIR = self.orig_projects_dir
        self.temp_dir.cleanup()

    def test_plan2_render_and_job_status(self):
        # Trigger render endpoint with video_path
        res = self.client.post(
            "/api/plan2/render",
            data={
                "project_id": self.project_id,
                "video_path": str(self.video_path),
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        job_id = data["job_id"]

        # Poll job status until done or error
        import time

        done = False
        for _ in range(30):
            time.sleep(0.5)
            status_res = self.client.get(f"/api/plan2/jobs/{job_id}")
            self.assertEqual(status_res.status_code, 200)
            status_data = status_res.json()
            if status_data["status"] in ("done", "error"):
                done = True
                self.assertEqual(status_data["status"], "done", f"Job error: {status_data.get('message')}")
                break

        self.assertTrue(done, "Job Plan 2 did not complete within timeout.")

        # Test video stream/download endpoint
        vid_res = self.client.get(f"/api/projects/{self.project_id}/video")
        self.assertEqual(vid_res.status_code, 200)
        self.assertEqual(vid_res.headers["content-type"], "video/mp4")


if __name__ == "__main__":
    unittest.main()
