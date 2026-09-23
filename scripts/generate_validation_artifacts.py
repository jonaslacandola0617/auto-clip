from __future__ import annotations

import json
from pathlib import Path

from autoclip.exporters import export_otio, export_premiere_xml
from tests.helpers import sample_project


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "validation" / "outputs"
    output.mkdir(parents=True, exist_ok=True)
    project = sample_project()
    project_path = output / "phase0-fixture.autoclip.json"
    project_path.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    timeline = project.timelines[0]
    sources = {source.id: source for source in project.sources}
    diagnostics = {
        "otio": export_otio(timeline, sources, output / "phase0-fixture.otio"),
        "premiere_xml": export_premiere_xml(timeline, sources, output / "phase0-fixture.xml"),
    }
    (output / "export-diagnostics.json").write_text(json.dumps(diagnostics, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

