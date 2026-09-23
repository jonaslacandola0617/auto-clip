import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoclip.media import FFmpegService
from tests.helpers import mt


class MediaCommandTests(unittest.TestCase):
    def test_invalid_cut_is_rejected_before_subprocess(self) -> None:
        service = FFmpegService()
        with patch.object(FFmpegService, "_run") as run:
            with self.assertRaises(ValueError):
                service.extract_clip(Path("source.mp4"), Path("out.mp4"), mt(10), mt(5))
            run.assert_not_called()

    def test_audio_command_is_argument_array_and_preserves_source(self) -> None:
        service = FFmpegService()
        with tempfile.TemporaryDirectory() as directory, patch.object(FFmpegService, "_run") as run:
            service.extract_speech_audio(Path("source.mp4"), Path(directory) / "audio.wav")
            args = run.call_args.args[0]
            self.assertIn(Path(args[0]).name.lower(), {"ffmpeg", "ffmpeg.exe"})
            self.assertIn("pcm_s16le", args)
            self.assertEqual(args[args.index("-i") + 1], "source.mp4")

    def test_project_local_static_ffmpeg_is_discovered_when_installed(self) -> None:
        try:
            import static_ffmpeg  # noqa: F401
        except ImportError:
            self.skipTest("static-ffmpeg is not installed")
        self.assertTrue(FFmpegService().available)


if __name__ == "__main__":
    unittest.main()
