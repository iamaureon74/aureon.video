"""find identifying metadata and encoder fingerprints in a media file."""
from __future__ import annotations

import re
from pathlib import Path

from . import ff

# exiftool groups that describe the file system / the tool itself, not the media.
_SKIP_GROUPS = {"File", "System", "ExifTool", "Composite"}

_IDENTIFYING = re.compile(
    r"gps|location|latitude|longitude|altitude|make|model|serial|software|"
    r"creat|modif|date|time.?stamp|author|artist|comment|title|description|copyright|"
    r"encoder|encoded|lens|contentidentifier|identifier|uuid|owner|camera|device|"
    r"android|apple|com\.|xmp|c2pa|jumbf|manufacturer|firmware|keywords|user",
    re.I,
)

# values that carry no information about the source
_NEUTRAL_VALUES = re.compile(
    r"^(0000:00:00 00:00:00|0|1904:01:01 00:00:00|1970:01:01 00:00:00(\+00:00)?|"
    r"videohandler|soundhandler|handler|und|)$",
    re.I,
)

_NEUTRAL_KEYS = re.compile(
    r"(TimeScale|Duration|ImageWidth|ImageHeight|SourceImage|BitDepth|"
    r"CompressorID|CompressorName|HandlerType|HandlerClass|MediaLanguageCode|"
    r"MatrixStructure|PreferredRate|PreferredVolume|PreviewTime|PosterTime|"
    r"SelectionTime|CurrentTime|NextTrackID|TrackID|TrackLayer|TrackVolume|"
    r"GraphicsMode|OpColor|AudioFormat|AudioChannels|AudioBitsPerSample|"
    r"AudioSampleRate|XResolution|YResolution|VideoFrameRate|MovieHeaderVersion|"
    r"MajorBrand|MinorVersion|CompatibleBrands|MediaDataSize|MediaDataOffset|"
    r"Balance|ColorRepresentation|ColorPrimaries|TransferCharacteristics|"
    r"MatrixCoefficients|VideoFullRangeFlag|PixelAspectRatio|BufferSize|"
    r"MaxBitrate|AverageBitrate|ChunkOffset|Rotation)$"
)

# encoder self-identification strings embedded in the bitstream / container
_FINGERPRINTS = {
    b"x264 - core": "x264 encoder settings string (h.264 sei)",
    b"x265 (build": "x265 encoder settings string (hevc sei)",
    b"Lavf": "ffmpeg muxer tag (Lavf)",
    b"Lavc": "ffmpeg encoder tag (Lavc)",
    b"HandBrake": "handbrake tag",
    b"com.apple.quicktime": "apple quicktime metadata keys",
    b"com.android": "android metadata keys",
    b"c2pa": "c2pa content credentials",
    b"jumb": "jumbf box (c2pa container)",
}


def _fingerprints(path: Path, limit: int = 8 * 1024 * 1024) -> list[dict]:
    size = path.stat().st_size
    found = {}
    with path.open("rb") as f:
        head = f.read(limit)
        tail = b""
        if size > limit:
            f.seek(max(limit, size - limit))
            tail = f.read(limit)
    for blob in (head, tail):
        for needle, label in _FINGERPRINTS.items():
            if needle in blob and needle not in found:
                i = blob.index(needle)
                snippet = blob[i:i + 60].split(b"\x00")[0].decode("latin-1", "replace")
                found[needle] = {"kind": "fingerprint", "key": label, "value": snippet.strip()}
    return list(found.values())


def scan(path: str | Path) -> dict:
    path = Path(path)
    info = ff.probe(path)
    findings: list[dict] = []

    for k, v in (info.get("format", {}).get("tags") or {}).items():
        if _IDENTIFYING.search(k) and not _NEUTRAL_VALUES.match(str(v).strip()):
            findings.append({"kind": "container", "key": k, "value": str(v)})

    for s in info.get("streams", []):
        ctype = s.get("codec_type")
        if ctype in ("data", "subtitle", "attachment"):
            findings.append({"kind": "stream", "key": f"{ctype} stream #{s['index']}",
                             "value": s.get("codec_tag_string") or s.get("codec_name", "")})
        for k, v in (s.get("tags") or {}).items():
            if _IDENTIFYING.search(k) and not _NEUTRAL_VALUES.match(str(v).strip()):
                findings.append({"kind": "stream", "key": f"{ctype}:{k}", "value": str(v)})

    if info.get("chapters"):
        findings.append({"kind": "container", "key": "chapters",
                         "value": f"{len(info['chapters'])} chapter(s)"})

    seen = {f["value"] for f in findings}
    for k, v in ff.exif(path).items():
        if ":" not in k:
            continue
        group, name = k.split(":", 1)
        if group in _SKIP_GROUPS or _NEUTRAL_KEYS.search(name):
            continue
        sv = str(v).strip()
        if _IDENTIFYING.search(name) and not _NEUTRAL_VALUES.match(sv) and sv not in seen:
            findings.append({"kind": "exif", "key": k, "value": sv})
            seen.add(sv)

    findings += _fingerprints(path)
    return {"findings": findings, "probe": info}
