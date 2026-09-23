import copy
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from autoclip.exporters.otio import export_otio
from autoclip.exporters.premiere_xml import export_premiere_xml
from tests.helpers import sample_source, sample_timeline


class ExporterTests(unittest.TestCase):
    def test_otio_serializer_preserves_order_ranges_audio_and_source(self) -> None:
        timeline = sample_timeline()
        original = copy.deepcopy(timeline)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.otio"
            warnings = export_otio(timeline, {"media_001": sample_source()}, output)
            data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(data["OTIO_SCHEMA"], "Timeline.1")
        self.assertEqual(data["metadata"]["autoclip:canvas"], {"width": 1080, "height": 1920})
        tracks = data["tracks"]["children"]
        self.assertEqual([track["kind"] for track in tracks], ["Video", "Audio"])
        self.assertEqual([item["name"] for item in tracks[0]["children"]], ["First", "Second"])
        self.assertEqual(tracks[0]["children"][0]["source_range"]["start_time"]["value"], 240.0)
        self.assertTrue(warnings)
        self.assertEqual(timeline, original)

    def test_otio_file_is_readable_by_installed_runtime(self) -> None:
        try:
            import opentimelineio as otio
        except ImportError:
            self.skipTest("OpenTimelineIO runtime is not installed")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.otio"
            export_otio(sample_timeline(), {"media_001": sample_source()}, output)
            restored = otio.adapters.read_from_file(str(output))
        self.assertEqual(restored.name, "timeline_001")
        self.assertEqual([len(track) for track in restored.tracks], [2, 2])

    def test_premiere_xml_serializer_preserves_cuts_and_relink_path(self) -> None:
        timeline = sample_timeline()
        original = copy.deepcopy(timeline)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fixture.xml"
            warnings = export_premiere_xml(timeline, {"media_001": sample_source()}, output)
            root = ET.parse(output).getroot()
        self.assertEqual(root.tag, "xmeml")
        self.assertEqual(root.findtext("./sequence/media/video/format/samplecharacteristics/width"), "1080")
        video = root.findall("./sequence/media/video/track/clipitem")
        audio = root.findall("./sequence/media/audio/track/clipitem")
        self.assertEqual([node.findtext("name") for node in video], ["First", "Second"])
        self.assertEqual(len(audio), 2)
        self.assertIn("source.mp4", video[0].findtext("file/pathurl") or "")
        self.assertTrue(warnings)
        self.assertEqual(timeline, original)


if __name__ == "__main__":
    unittest.main()
