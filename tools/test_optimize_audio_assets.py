import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.optimize_audio_assets import has_invalid_vorbis_comments, run_ffmpeg

def ogg_page(packet: bytes, sequence: int) -> bytes:
    segments = [255] * (len(packet) // 255)
    remainder = len(packet) % 255
    segments.append(remainder)
    header = struct.pack("<4sBBqIIIB", b"OggS", 0, 0, 0, 1, sequence, 0, len(segments))
    return header + bytes(segments) + packet

def vorbis_comment(comments: list[bytes]) -> bytes:
    vendor = b"UnitTestVendor"
    packet = bytearray(b"\x03vorbis")
    packet.extend(struct.pack("<I", len(vendor)))
    packet.extend(vendor)
    packet.extend(struct.pack("<I", len(comments)))
    for comment in comments:
        packet.extend(struct.pack("<I", len(comment)))
        packet.extend(comment)
    packet.append(1)
    return bytes(packet)

class VorbisCommentTests(unittest.TestCase):
    def write_fake_ogg(self, folder: Path, comments: list[bytes]) -> Path:
        path = folder / "sample.ogg"
        identification = b"\x01vorbis" + bytes(23)
        path.write_bytes(ogg_page(identification, 0) + ogg_page(vorbis_comment(comments), 1))
        return path

    def test_detects_non_key_value_vendor_comment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_fake_ogg(Path(folder), [b"ENCODER=FL Studio", b"Sony Ogg Vorbis 1.0 Final"])
            self.assertTrue(has_invalid_vorbis_comments(path))

    def test_accepts_valid_vorbis_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_fake_ogg(Path(folder), [b"TITLE=Test", b"ARTIST=Test"])
            self.assertFalse(has_invalid_vorbis_comments(path))

    def test_accepts_empty_comment_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_fake_ogg(Path(folder), [])
            self.assertFalse(has_invalid_vorbis_comments(path))


class FFmpegProgressTests(unittest.TestCase):
    def test_ffmpeg_receives_hard_timeout(self):
        completed = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")
        with patch("tools.optimize_audio_assets.subprocess.run", return_value=completed) as mocked:
            ok, detail = run_ffmpeg(
                "ffmpeg", ["-i", "input.ogg", "output.ogg"],
                timeout_seconds=7, heartbeat_interval_seconds=60,
            )
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(mocked.call_args.kwargs["timeout"], 7)

    def test_timeout_is_reported_without_crashing_optimizer(self):
        with patch(
            "tools.optimize_audio_assets.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1),
        ):
            ok, detail = run_ffmpeg(
                "ffmpeg", ["-i", "input.ogg", "output.ogg"],
                timeout_seconds=1, heartbeat_interval_seconds=60,
            )
        self.assertFalse(ok)
        self.assertIn("timeout_after_1s", detail)

if __name__ == "__main__":
    unittest.main()
