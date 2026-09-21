"""Synthetic FIT and TCX activity files for the MCP delivery probe.

Nothing here touches Garmin or user data. The files are generated so the
repository carries no binaries, and so the FIT encoder can be tested.

A synthetic file is a weaker test than a real export: if an assistant rejects
one, the file itself is a suspect. Point ``MCP_PROBE_SAMPLES_DIR`` at a folder
of real Garmin exports to remove that doubt.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# FIT timestamps count seconds from this epoch, not the Unix one.
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
SEMICIRCLES = 2**31 / 180.0


@dataclass(frozen=True)
class Sample:
    activity_id: str
    name: str
    sport: str
    started_at: datetime
    duration_sec: int
    points: int

    @property
    def filename(self) -> str:
        return f"{self.activity_id}.{{extension}}"


SAMPLES = (
    Sample(
        activity_id="sample-short-run",
        name="Easy 20 minutes",
        sport="running",
        started_at=datetime(2026, 9, 14, 6, 30, tzinfo=timezone.utc),
        duration_sec=1200,
        points=20,
    ),
    Sample(
        activity_id="sample-long-run",
        name="Long run, two hours",
        sport="running",
        started_at=datetime(2026, 9, 17, 5, 45, tzinfo=timezone.utc),
        duration_sec=7200,
        points=1800,
    ),
)


def sample_by_id(activity_id: str) -> Sample | None:
    return next((item for item in SAMPLES if item.activity_id == activity_id), None)


def _crc16(data: bytes) -> int:
    """The CRC defined by the FIT protocol (nibble table, LSB first)."""
    table = (
        0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
    )
    crc = 0
    for byte in data:
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            check = table[crc & 0x0F]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ check ^ table[nibble]
    return crc


def _timestamp(moment: datetime) -> int:
    return int((moment - FIT_EPOCH).total_seconds())


def _definition(local_type: int, global_number: int, fields: list[tuple[int, int, int]]) -> bytes:
    # Record header, reserved, architecture 0 (little-endian), message, field count.
    header = struct.pack("<BBBHB", 0x40 | local_type, 0, 0, global_number, len(fields))
    return header + b"".join(struct.pack("<BBB", number, size, base) for number, size, base in fields)


def _data(local_type: int, payload: bytes) -> bytes:
    return struct.pack("<B", local_type) + payload


def fit_bytes(sample: Sample) -> bytes:
    """Build a small but structurally valid FIT activity file.

    One file_id message identifies it as an activity; record messages carry a
    timestamp, position, heart rate and speed, followed by a session summary.
    """
    started = _timestamp(sample.started_at)
    body = bytearray()

    # file_id: type=4 (activity), manufacturer=255 (development), product, serial, time_created
    body += _definition(0, 0, [(0, 1, 0x00), (1, 2, 0x84), (2, 2, 0x84), (3, 4, 0x8C), (4, 4, 0x86)])
    body += _data(0, struct.pack("<BHHII", 4, 255, 0, 0, started))

    # record: timestamp, position_lat, position_long, heart_rate, speed
    body += _definition(1, 20, [(253, 4, 0x86), (0, 4, 0x85), (1, 4, 0x85), (3, 1, 0x02), (6, 2, 0x84)])
    step = max(1, sample.duration_sec // sample.points)
    latitude, longitude = 48.8566, 2.3522
    for index in range(sample.points):
        latitude += 0.00015
        longitude += 0.00010
        body += _data(
            1,
            struct.pack(
                "<IiiBH",
                started + index * step,
                int(latitude * SEMICIRCLES),
                int(longitude * SEMICIRCLES),
                140 + (index % 20),
                3000 + (index % 200),  # millimetres per second
            ),
        )

    # session: timestamp, start_time, total_elapsed_time, total_distance, sport
    body += _definition(2, 18, [(253, 4, 0x86), (2, 4, 0x86), (7, 4, 0x86), (9, 4, 0x86), (5, 1, 0x00)])
    body += _data(
        2,
        struct.pack(
            "<IIIIB",
            started + sample.duration_sec,
            started,
            sample.duration_sec * 1000,  # milliseconds
            sample.duration_sec * 3 * 100,  # centimetres, at roughly 3 m/s
            1,  # running
        ),
    )

    header = struct.pack("<BBHI4s", 14, 0x20, 2195, len(body), b".FIT")
    header += struct.pack("<H", _crc16(header))
    return bytes(header + body + struct.pack("<H", _crc16(bytes(body))))


def read_fit_header(data: bytes) -> dict:
    """Parse and verify a FIT header; used by the tests and the probe."""
    if len(data) < 16:
        raise ValueError("fit_too_short")
    size, protocol, profile, data_size, signature = struct.unpack("<BBHI4s", data[:12])
    if size != 14 or signature != b".FIT":
        raise ValueError("fit_signature_missing")
    header_crc = struct.unpack("<H", data[12:14])[0]
    if header_crc != _crc16(data[:12]):
        raise ValueError("fit_header_crc_mismatch")
    body = data[14 : 14 + data_size]
    file_crc = struct.unpack("<H", data[14 + data_size : 16 + data_size])[0]
    if file_crc != _crc16(body):
        raise ValueError("fit_crc_mismatch")
    return {"protocol": protocol, "profile": profile, "data_size": data_size}


def tcx_text(sample: Sample) -> str:
    """Build a Garmin-shaped TCX document with one lap of trackpoints."""
    step = max(1, sample.duration_sec // sample.points)
    latitude, longitude = 48.8566, 2.3522
    trackpoints = []
    for index in range(sample.points):
        latitude += 0.00015
        longitude += 0.00010
        moment = sample.started_at + timedelta(seconds=index * step)
        trackpoints.append(
            "        <Trackpoint>\n"
            f"          <Time>{moment.strftime('%Y-%m-%dT%H:%M:%SZ')}</Time>\n"
            "          <Position>\n"
            f"            <LatitudeDegrees>{latitude:.6f}</LatitudeDegrees>\n"
            f"            <LongitudeDegrees>{longitude:.6f}</LongitudeDegrees>\n"
            "          </Position>\n"
            f"          <AltitudeMeters>{35 + index % 15}</AltitudeMeters>\n"
            f"          <DistanceMeters>{index * step * 3}</DistanceMeters>\n"
            "          <HeartRateBpm>\n"
            f"            <Value>{140 + index % 20}</Value>\n"
            "          </HeartRateBpm>\n"
            "        </Trackpoint>"
        )
    start = sample.started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">\n'
        "  <Activities>\n"
        '    <Activity Sport="Running">\n'
        f"      <Id>{start}</Id>\n"
        f'      <Lap StartTime="{start}">\n'
        f"        <TotalTimeSeconds>{sample.duration_sec}</TotalTimeSeconds>\n"
        f"        <DistanceMeters>{sample.duration_sec * 3}</DistanceMeters>\n"
        "        <Intensity>Active</Intensity>\n"
        "        <TriggerMethod>Manual</TriggerMethod>\n"
        "        <Track>\n" + "\n".join(trackpoints) + "\n"
        "        </Track>\n"
        "      </Lap>\n"
        "    </Activity>\n"
        "  </Activities>\n"
        "</TrainingCenterDatabase>\n"
    )


def sample_file(sample: Sample, extension: str, directory: Path | None = None) -> bytes:
    """Return the sample's bytes, preferring a real export when one is supplied."""
    if directory:
        override = Path(directory) / f"{sample.activity_id}.{extension}"
        if override.is_file():
            return override.read_bytes()
    if extension == "fit":
        return fit_bytes(sample)
    return tcx_text(sample).encode()
