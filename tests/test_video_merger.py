import tempfile
import unittest
from pathlib import Path

from video_merger import (
    ClipInfo, _merge_command, _parse_boundary_black, _parse_boundary_fades, format_duration,
    parse_probe_output, suggested_output_path,
)


class VideoMergerTests(unittest.TestCase):
    def test_parse_probe_output(self):
        output = """
          Duration: 00:01:23.45, start: 0.000000, bitrate: 900 kb/s
          Stream #0:0: Video: h264, yuv420p, 1920x1080, 29.97 fps
          Stream #0:1: Audio: aac, 48000 Hz, stereo
        """
        info = parse_probe_output(Path("clip.mp4"), output)
        self.assertAlmostEqual(info.duration, 83.45)
        self.assertEqual((info.width, info.height), (1920, 1080))
        self.assertAlmostEqual(info.fps, 29.97)
        self.assertTrue(info.has_audio)

    def test_suggested_output_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            source = folder / "clip.mp4"
            source.touch()
            (folder / "video_unito.mp4").touch()
            self.assertEqual(suggested_output_path(source).name, "video_unito_2.mp4")

    def test_format_duration(self):
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3661), "1:01:01")

    def test_clip_without_video_is_rejected(self):
        with self.assertRaisesRegex(Exception, "traccia video"):
            parse_probe_output(Path("audio.mp3"), "Duration: 00:00:10.00\nAudio: mp3")

    def test_merge_concatenates_video_and_audio_separately(self):
        clips = [
            ClipInfo(Path("one.mp4"), 2.0, 320, 240, 30.0, True),
            ClipInfo(Path("two.mp4"), 3.0, 240, 320, 25.0, False),
        ]
        command, filters = _merge_command(
            Path("ffmpeg.exe"), clips, Path("out.mp4"), Path("filter.txt"),
            1280, 720, 30, 22, "medium",
        )
        self.assertIn("[v0][v1]concat=n=2:v=1:a=0[vout]", filters)
        self.assertIn("[a0][a1]concat=n=2:v=0:a=1[aout]", filters)
        self.assertIn("setpts=PTS-STARTPTS", filters)
        self.assertIn("anullsrc", filters)
        self.assertIn("-filter_complex_script", command)

    def test_optional_black_gap_is_inserted_only_when_requested(self):
        clips = [
            ClipInfo(Path("one.mp4"), 2.0, 320, 240, 30.0, True),
            ClipInfo(Path("two.mp4"), 2.0, 320, 240, 30.0, True),
        ]
        _, direct_filters = _merge_command(
            Path("ffmpeg.exe"), clips, Path("out.mp4"), Path("filter.txt"),
            320, 240, 30, 22, "medium", 0.0,
        )
        _, paused_filters = _merge_command(
            Path("ffmpeg.exe"), clips, Path("out.mp4"), Path("filter.txt"),
            320, 240, 30, 22, "medium", 1.5,
        )
        self.assertNotIn("[vgap0]", direct_filters)
        self.assertIn("color=c=black:s=320x240:r=30:d=1.500000", paused_filters)
        self.assertIn("[v0][vgap0][v1]concat=n=3:v=1:a=0[vout]", paused_filters)

    def test_black_frames_are_trimmed_only_at_internal_joins(self):
        first = ClipInfo(Path("one.mp4"), 10.0, 320, 240, 30.0, True, 0.07, 0.10)
        second = ClipInfo(Path("two.mp4"), 12.0, 320, 240, 30.0, True, 0.07, 0.10)
        _, filters = _merge_command(
            Path("ffmpeg.exe"), [first, second], Path("out.mp4"), Path("filter.txt"),
            320, 240, 30, 22, "medium", 0.0,
        )
        self.assertIn("[0:v:0]trim=start=0.000000:end=9.900000", filters)
        self.assertIn("[1:v:0]trim=start=0.070000:end=12.000000", filters)

    def test_boundary_black_parser(self):
        output = """
        black_start:0 black_end:0.066667 black_duration:0.066667
        black_start:10.533333 black_end:10.633333 black_duration:0.1
        """
        start, end = _parse_boundary_black(output, 10.666667, 30.0)
        self.assertAlmostEqual(start, 0.066667)
        self.assertAlmostEqual(end, 0.133334)

    def test_boundary_fade_parser(self):
        lines = []
        values = [16, 35, 70, 100, 130, 145] + [150] * 20 + [145, 130, 100, 70, 35, 16]
        for index, value in enumerate(values):
            lines.append(f"frame:{index} pts:{index} pts_time:{index / 10:.1f}")
            lines.append(f"lavfi.signalstats.YAVG={value}")
        start, end = _parse_boundary_fades("\n".join(lines), len(values) / 10, 10.0)
        self.assertGreater(start, 0.0)
        self.assertGreater(end, 0.0)


if __name__ == "__main__":
    unittest.main()
