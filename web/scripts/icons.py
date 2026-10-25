"""Draw the app icons (a ring) as PNGs with the standard library only.

Run from web/: python scripts/icons.py. Writes public/icon-192.png,
public/icon-512.png, public/icon-maskable-512.png and app/apple-icon.png.
"""

import struct
import zlib
from pathlib import Path

BACKGROUND = (0x2F, 0x5B, 0xD3)
RING = (0xFF, 0xFF, 0xFF)


def png(size: int, ring_radius: float, ring_width: float) -> bytes:
    center = (size - 1) / 2
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter: none
        for x in range(size):
            distance = ((x - center) ** 2 + (y - center) ** 2) ** 0.5
            # Antialiased edge: blend over one pixel at each side of the ring.
            edge = min(
                distance - (ring_radius - ring_width / 2), (ring_radius + ring_width / 2) - distance
            )
            alpha = max(0.0, min(1.0, edge + 0.5))
            rows.extend(round(b + (r - b) * alpha) for b, r in zip(BACKGROUND, RING, strict=True))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    root = Path(__file__).parents[1]
    # Regular icons: the ring fills most of the square.
    (root / "public" / "icon-192.png").write_bytes(png(192, 192 * 0.30, 192 * 0.11))
    (root / "public" / "icon-512.png").write_bytes(png(512, 512 * 0.30, 512 * 0.11))
    # Maskable: launchers crop to a circle, so keep the ring inside the safe zone.
    (root / "public" / "icon-maskable-512.png").write_bytes(png(512, 512 * 0.22, 512 * 0.08))
    (root / "app" / "apple-icon.png").write_bytes(png(180, 180 * 0.30, 180 * 0.11))
    (root / "app" / "icon.png").write_bytes(png(64, 64 * 0.30, 64 * 0.12))


if __name__ == "__main__":
    main()
