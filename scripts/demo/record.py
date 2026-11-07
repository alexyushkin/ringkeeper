"""Record the demo again, after the UI changes.

    uv run --with imageio-ffmpeg==0.6.0 python scripts/demo/record.py [folder of an earlier run]

Builds a scratch install with two made-up clients, drives it with Playwright at 1920x1080 using
this install's Claude sign-in, and cuts:

    docs/media/ringkeeper-demo.mp4     about 90 seconds, captions burned in
    docs/images/demo.gif               15 seconds, for the README
    docs/media/clip-refusal.mp4, clip-telegram.mp4, clip-undo.mp4     15 seconds each

The web app must be built first (web: node scripts/build-desktop.mjs). Run it with a scratch
install's environment, as for `ringkeeper scenarios run`: no real client data goes near it.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import imageio_ffmpeg

from ringkeeper import claude_auth
from ringkeeper.config import get_settings
from ringkeeper.scenarios import launcher
from ringkeeper.services import subscription
from ringkeeper.vault import get_vault

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MEDIA = ROOT / "docs" / "media"
IMAGES = ROOT / "docs" / "images"
LENGTH = 90.0
CLIP = 15.0
START_SECONDS = 180


def ffmpeg(*args: str) -> None:
    subprocess.run(  # noqa: S603 (a fixed program)
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *args], check=True
    )


def duration(video: Path) -> float:
    told = subprocess.run(  # noqa: S603 (a fixed program)
        [imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(video), "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    ).stderr  # fmt: skip
    hours, minutes, seconds = re.findall(r"time=(\d+):(\d+):([\d.]+)", told)[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def cut(source: Path, parts: list[dict[str, Any]], target: Path, seconds: float | None) -> None:
    """Just `parts` of the recording, in order, sped up to fit `seconds` if they run over."""
    total = sum(p["end"] - p["start"] for p in parts)
    speed = max(1.0, total / seconds) if seconds else 1.0
    trims = "".join(
        f"[0:v]trim={p['start']:.2f}:{p['end']:.2f},setpts=(PTS-STARTPTS)/{speed:.3f}[v{i}];"
        for i, p in enumerate(parts)
    )
    joined = "".join(f"[v{i}]" for i in range(len(parts)))
    ffmpeg(
        "-i", str(source), "-filter_complex",
        f"{trims}{joined}concat=n={len(parts)}:v=1:a=0,fps=30,format=yuv420p[out]",
        "-map", "[out]", "-c:v", "libx264", "-crf", "24", "-movflags", "+faststart", str(target),
    )  # fmt: skip


def gif(source: Path, target: Path) -> None:
    ffmpeg(
        "-i", str(source), "-t", str(CLIP), "-filter_complex",
        "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=4",
        str(target),
    )  # fmt: skip


def start_stage(folder: Path) -> tuple[subprocess.Popen[bytes], dict[str, str]]:
    settings = get_settings()
    vault = get_vault(settings)
    subscription.refresh(vault, settings)
    credential = claude_auth.resolve(vault, settings)
    for part in ("home", "tmp", "workspaces", "data"):
        (folder / part).mkdir(parents=True)
    job = {"mode": settings.claude_auth_mode.value, "secret": credential.secret.reveal()}
    log = (folder / "stage.log").open("wb")
    process = subprocess.Popen(  # noqa: S603 (this Python, our own script)
        [sys.executable, str(HERE / "stage.py"), str(folder), str(ROOT / "web/.desktop/out")],
        stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
        env=launcher.environment(folder, settings), cwd=folder,
    )  # fmt: skip
    if process.stdin is not None:
        process.stdin.write(json.dumps(job).encode("utf-8"))
        process.stdin.close()
    log.close()
    deadline = time.monotonic() + START_SECONDS
    while not (folder / "stage.json").exists():
        if time.monotonic() > deadline or process.poll() is not None:
            raise SystemExit(f"the stage didn't start: see {folder / 'stage.log'}")
        time.sleep(0.5)
    return process, json.loads((folder / "stage.json").read_text(encoding="utf-8"))


def sign_in_code(stage: dict[str, str]) -> str:
    request = urllib.request.Request(  # noqa: S310 (this computer)
        f"{stage['url']}/desktop/codes", method="POST",
        headers={"Authorization": f"Bearer {stage['secret']}"},
    )  # fmt: skip
    with urllib.request.urlopen(request) as answer:  # noqa: S310 (this computer)
        return str(json.load(answer)["code"])


def record(folder: Path, video: Path) -> None:
    process, stage = start_stage(folder)
    try:
        subprocess.run(  # noqa: S603 (node, our own script)
            [
                shutil.which("node") or "node", str(HERE / "drive.cjs"),
                str(ROOT / "web" / "node_modules" / "playwright"), stage["url"],
                sign_in_code(stage), stage["chat"], str(video),
            ],
            check=True, cwd=ROOT / "web",
        )  # fmt: skip
    finally:
        (folder / "stop").touch()
        try:
            process.wait(60)
        except subprocess.TimeoutExpired:
            process.kill()


def edit(folder: Path, video: Path) -> None:
    found: dict[str, Any] = json.loads((video / "keep.json").read_text(encoding="utf-8"))
    source = next(video.glob("*.webm"))
    # The video starts a moment after the clock does: line the two up.
    late = max(0.0, float(found["elapsed"]) - duration(source))
    parts: list[dict[str, Any]] = [
        {**p, "start": max(0.0, p["start"] - late), "end": p["end"] - late} for p in found["keep"]
    ]
    MEDIA.mkdir(parents=True, exist_ok=True)
    cut(source, parts, MEDIA / "ringkeeper-demo.mp4", LENGTH)
    for name in ("refused", "telegram", "undo"):
        clip = [p for p in parts if p["name"] == name]
        label = {"refused": "refusal"}.get(name, name)
        cut(source, clip, MEDIA / f"clip-{label}.mp4", CLIP)
    short = folder / "short.mp4"
    cut(source, parts, short, CLIP)
    gif(short, IMAGES / "demo.gif")
    print(f"Cut {len(parts)} parts into {MEDIA} and {IMAGES / 'demo.gif'}")


def main() -> int:
    """With a folder from an earlier run, cuts it again instead of recording."""
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="rk-demo-"))
    video = folder / "video"
    if len(sys.argv) == 1:
        video.mkdir()
        record(folder, video)
    edit(folder, video)
    launcher.remove(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
