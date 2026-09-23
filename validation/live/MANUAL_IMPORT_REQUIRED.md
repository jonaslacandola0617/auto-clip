# Manual Import Required

## DaVinci Resolve

1. Open the project and choose File then Import Timeline.
2. Select `D:\auto-clip\validation\live\timeline_001.otio`.
3. If prompted, relink `source.mp4` to a compatible representative source longer than 55 seconds.
4. Verify two ordered video clips and two matching audio clips, a 29.97 fps timeline, source ranges near frames 240-840 and 1200-1650, and a 1080x1920 canvas where Resolve preserves OTIO metadata.

## Adobe Premiere Pro

Premiere Pro was not detected. On a machine with Premiere installed, import `D:\auto-clip\validation\live\timeline_001.xml`, relink `source.mp4`, and verify the same ordering, cuts, audio, frame rate, and vertical sequence dimensions.

These fixtures reference placeholder media because no representative validation video was supplied. A physical import cannot promote Gate B to PASS until a real source is used and the imported timeline is inspected.

