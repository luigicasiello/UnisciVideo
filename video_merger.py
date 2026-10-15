from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


ProgressCallback = Callable[[int, str], None]


class MergeError(RuntimeError):
    pass


class MergeCancelled(MergeError):
    pass


@dataclass(frozen=True)
class ClipInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    trim_start: float = 0.0
    trim_end: float = 0.0


@dataclass(frozen=True)
class MergeSettings:
    resolution: str = "source"
    quality: str = "balanced"
    fps: int = 30
    black_gap_seconds: float = 0.0


@dataclass(frozen=True)
class MergeResult:
    output_path: Path
    clips: int
    duration: float
    output_bytes: int
    width: int
    height: int


QUALITY_PROFILES = {
    "high": {"name": "Alta qualità", "crf": 18, "preset": "medium"},
    "balanced": {"name": "Bilanciata (consigliata)", "crf": 22, "preset": "medium"},
    "compact": {"name": "File più leggero", "crf": 27, "preset": "fast"},
}


VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv",
    ".mpg", ".mpeg", ".mts", ".m2ts", ".3gp", ".ts",
}


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b")
_FPS_RE = re.compile(r"(?:,|\s)(\d+(?:\.\d+)?)\s*fps\b")
_BLACK_RE = re.compile(
    r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)\s+black_duration:(\d+(?:\.\d+)?)"
)
_PTS_TIME_RE = re.compile(r"frame:\d+.*?pts_time:(\d+(?:\.\d+)?)")
_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=(\d+(?:\.\d+)?)")


def format_duration(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} GB"


def suggested_output_path(first_clip: Path, output_dir: Path | None = None) -> Path:
    folder = output_dir or first_clip.parent
    candidate = folder / "video_unito.mp4"
    counter = 2
    while candidate.exists():
        candidate = folder / f"video_unito_{counter}.mp4"
        counter += 1
    return candidate


def parse_probe_output(path: Path, output: str) -> ClipInfo:
    duration_match = _DURATION_RE.search(output)
    video_line = next((line for line in output.splitlines() if "Video:" in line), "")
    video_match = _VIDEO_RE.search(video_line)
    if not duration_match or not video_match:
        raise MergeError("Il file non contiene una traccia video leggibile.")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise MergeError("La durata della clip non è valida.")
    fps_match = _FPS_RE.search(video_line)
    fps = float(fps_match.group(1)) if fps_match else 30.0
    return ClipInfo(
        path=path,
        duration=duration,
        width=int(video_match.group(1)),
        height=int(video_match.group(2)),
        fps=fps,
        has_audio=any("Audio:" in line for line in output.splitlines()),
    )


def inspect_clip(path: str | Path, ffmpeg_path: str | Path) -> ClipInfo:
    source = Path(path).resolve()
    if not source.is_file():
        raise MergeError("Il file non esiste.")
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise MergeError("Formato video non supportato.")
    completed = subprocess.run(
        [str(ffmpeg_path), "-hide_banner", "-i", str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )
    info = parse_probe_output(source, completed.stderr + "\n" + completed.stdout)
    video_duration = _measure_video_duration(source, Path(ffmpeg_path), info.fps, info.duration)
    trim_start, trim_end = _detect_boundary_black(source, Path(ffmpeg_path), video_duration, info.fps)
    return ClipInfo(source, video_duration, info.width, info.height, info.fps, info.has_audio, trim_start, trim_end)


def _measure_video_duration(source: Path, ffmpeg_path: Path, fps: float, fallback: float) -> float:
    """Legge i timestamp dei pacchetti video senza decodificarli.

    La durata generale del contenitore può includere diversi secondi di coda
    audio dopo l'ultimo fotogramma. Usarla per il montaggio produce una pausa
    nera; la scansione stream-copy è veloce e misura invece la sola traccia video.
    """
    try:
        completed = subprocess.run(
            [
                str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-i", str(source),
                "-map", "0:v:0", "-c", "copy", "-f", "null", os.devnull,
                "-progress", "pipe:1", "-nostats",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=_creation_flags(),
        )
        timestamps = [int(value) for value in re.findall(r"out_time_(?:us|ms)=(-?\d+)", completed.stdout)]
        if completed.returncode == 0 and timestamps:
            # Con video che usa B-frame, out_time può fermarsi fino a due frame
            # prima della durata visuale effettiva.
            estimate = max(timestamps) / 1_000_000 + 2 / max(1.0, fps)
            return max(0.01, min(fallback, estimate))
    except (OSError, ValueError):
        pass
    return fallback


def _detect_boundary_black(source: Path, ffmpeg_path: Path, duration: float, fps: float) -> tuple[float, float]:
    try:
        completed = subprocess.run(
            [
                str(ffmpeg_path), "-hide_banner", "-loglevel", "info", "-i", str(source),
                "-vf", "scale=160:-2,signalstats,metadata=print,blackdetect=d=0.001:pix_th=0.10",
                "-an", "-f", "null", os.devnull,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=_creation_flags(),
        )
        fade_bounds = _parse_boundary_fades(completed.stderr, duration, fps)
        if fade_bounds != (0.0, 0.0):
            return fade_bounds
        return _parse_boundary_black(completed.stderr, duration, fps)
    except OSError:
        return 0.0, 0.0


def _parse_boundary_black(output: str, duration: float, fps: float) -> tuple[float, float]:
    intervals = [(float(start), float(end)) for start, end, _ in _BLACK_RE.findall(output)]
    if not intervals:
        return 0.0, 0.0
    tolerance = 2 / max(1.0, fps) + 0.01
    trim_start = intervals[0][1] if intervals[0][0] <= tolerance else 0.0
    trim_end = duration - intervals[-1][0] if duration - intervals[-1][1] <= tolerance else 0.0
    # Mantiene sempre almeno due fotogrammi utili anche nei file quasi neri.
    maximum_total = max(0.0, duration - 2 / max(1.0, fps))
    if trim_start + trim_end > maximum_total:
        return 0.0, 0.0
    return max(0.0, trim_start), max(0.0, trim_end)


def _parse_boundary_fades(output: str, duration: float, fps: float) -> tuple[float, float]:
    samples: list[tuple[float, float]] = []
    current_timestamp: float | None = None
    for line in output.splitlines():
        timestamp_match = _PTS_TIME_RE.search(line)
        if timestamp_match:
            current_timestamp = float(timestamp_match.group(1))
            continue
        luma_match = _YAVG_RE.search(line)
        if luma_match and current_timestamp is not None:
            samples.append((current_timestamp, float(luma_match.group(1))))
            current_timestamp = None
    if len(samples) < 6:
        return 0.0, 0.0
    frame_duration = 1 / max(1.0, fps)

    def window_values(start: float, end: float) -> list[float]:
        return [value for timestamp, value in samples if start <= timestamp <= end]

    start_reference = window_values(min(0.5, duration * 0.2), min(1.25, duration * 0.4))
    end_reference = window_values(max(0.0, duration - min(1.25, duration * 0.4)), max(0.0, duration - min(0.5, duration * 0.2)))
    if not start_reference or not end_reference:
        return 0.0, 0.0
    start_baseline = statistics.median(start_reference)
    end_baseline = statistics.median(end_reference)

    trim_start = 0.0
    if samples[0][1] < start_baseline * 0.45:
        threshold = start_baseline * 0.90
        for index in range(len(samples) - 2):
            timestamp = samples[index][0]
            if timestamp > min(1.5, duration * 0.45):
                break
            if all(samples[index + offset][1] >= threshold for offset in range(3)):
                trim_start = timestamp
                break

    trim_end = 0.0
    if samples[-1][1] < end_baseline * 0.45:
        threshold = end_baseline * 0.90
        last_bright_time = 0.0
        lower_bound = max(0.0, duration - min(1.5, duration * 0.45))
        for index in range(len(samples) - 2):
            timestamp = samples[index][0]
            if timestamp < lower_bound:
                continue
            if all(samples[index + offset][1] >= threshold for offset in range(3)):
                last_bright_time = samples[index + 2][0] + frame_duration
        if last_bright_time:
            trim_end = max(0.0, duration - last_bright_time)

    maximum_total = max(0.0, duration - 2 * frame_duration)
    if trim_start + trim_end > maximum_total:
        return 0.0, 0.0
    return trim_start, trim_end


def merge_videos(
    clips: Sequence[ClipInfo],
    output_path: str | Path,
    settings: MergeSettings,
    ffmpeg_path: str | Path,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> MergeResult:
    if len(clips) < 2:
        raise MergeError("Aggiungi almeno due clip da unire.")
    if settings.quality not in QUALITY_PROFILES:
        raise MergeError("Profilo di qualità sconosciuto.")
    executable = Path(ffmpeg_path)
    if not executable.is_file():
        raise MergeError("Il motore FFmpeg incluso non è disponibile.")
    destination = Path(output_path).resolve()
    if destination.suffix.lower() != ".mp4":
        destination = destination.with_suffix(".mp4")
    if destination in {clip.path.resolve() for clip in clips}:
        raise MergeError("Il file di destinazione deve essere diverso dalle clip originali.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    width, height = _target_dimensions(clips[0], settings.resolution)
    fps = max(15, min(60, int(settings.fps)))
    black_gap = max(0.0, min(10.0, float(settings.black_gap_seconds)))
    total_duration = sum(
        _effective_clip_bounds(clip, index, len(clips))[2] for index, clip in enumerate(clips)
    ) + black_gap * (len(clips) - 1)
    profile = QUALITY_PROFILES[settings.quality]

    work_dir = Path(tempfile.mkdtemp(prefix="mergevideo_", dir=destination.parent))
    temporary_output = work_dir / "video_unito.mp4"
    try:
        _check_cancelled(cancel_event)
        filter_script = work_dir / "merge-filter.txt"
        command, filter_text = _merge_command(
            executable, clips, temporary_output, filter_script, width, height, fps,
            int(profile["crf"]), str(profile["preset"]), black_gap,
        )
        filter_script.write_text(filter_text, encoding="utf-8")

        def update_merge(current_seconds: float) -> None:
            percent = round(min(total_duration, max(0.0, current_seconds)) / total_duration * 96)
            _notify(progress, percent, "Unione delle clip")

        _run_ffmpeg(command, work_dir, update_merge, cancel_event)
        _notify(progress, 98, "Verifica del video")
        result_info = inspect_clip(temporary_output, executable)
        if result_info.duration < total_duration * 0.90:
            raise MergeError("La verifica del video prodotto non è riuscita.")
        os.replace(temporary_output, destination)
        _notify(progress, 100, "Completato")
        return MergeResult(destination, len(clips), result_info.duration, destination.stat().st_size, width, height)
    except (MergeError, MergeCancelled):
        raise
    except Exception as exc:
        raise MergeError(str(exc)) from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _target_dimensions(first: ClipInfo, resolution: str) -> tuple[int, int]:
    presets = {
        "1080p": (1920, 1080),
        "720p": (1280, 720),
        "vertical": (1080, 1920),
        "square": (1080, 1080),
    }
    if resolution in presets:
        return presets[resolution]
    width = max(2, min(3840, first.width))
    height = max(2, min(2160, first.height))
    return width - width % 2, height - height % 2


def _merge_command(
    executable: Path,
    clips: Sequence[ClipInfo],
    output: Path,
    filter_script: Path,
    width: int,
    height: int,
    fps: int,
    crf: int,
    preset: str,
    black_gap_seconds: float = 0.0,
) -> tuple[list[str], str]:
    command = [str(executable), "-y", "-hide_banner", "-loglevel", "error"]
    for clip in clips:
        command += ["-i", str(clip.path)]

    filters: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []
    for index, clip in enumerate(clips):
        trim_start, trim_end, effective_duration = _effective_clip_bounds(clip, index, len(clips))
        duration = f"{effective_duration:.6f}"
        video_end = max(trim_start, clip.duration - trim_end)
        video_label = f"v{index}"
        audio_label = f"a{index}"
        filters.append(
            f"[{index}:v:0]trim=start={trim_start:.6f}:end={video_end:.6f},"
            f"settb=AVTB,setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps},format=yuv420p[{video_label}]"
        )
        if clip.has_audio:
            filters.append(
                f"[{index}:a:0]atrim=start={trim_start:.6f}:end={video_end:.6f},"
                f"aresample=48000:async=1:first_pts=0,"
                f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS,apad=whole_dur={duration},atrim=duration={duration}[{audio_label}]"
            )
        else:
            filters.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=duration={duration},asetpts=PTS-STARTPTS[{audio_label}]"
            )
        video_labels.append(f"[{video_label}]")
        audio_labels.append(f"[{audio_label}]")

    sequence_video: list[str] = []
    sequence_audio: list[str] = []
    gap = max(0.0, min(10.0, float(black_gap_seconds)))
    for index, (video_label, audio_label) in enumerate(zip(video_labels, audio_labels)):
        sequence_video.append(video_label)
        sequence_audio.append(audio_label)
        if gap > 0 and index < len(clips) - 1:
            gap_duration = f"{gap:.6f}"
            filters.append(
                f"color=c=black:s={width}x{height}:r={fps}:d={gap_duration},"
                f"format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[vgap{index}]"
            )
            filters.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=duration={gap_duration},asetpts=PTS-STARTPTS[agap{index}]"
            )
            sequence_video.append(f"[vgap{index}]")
            sequence_audio.append(f"[agap{index}]")

    # Video e audio vengono concatenati separatamente. In questo modo una coda
    # audio più lunga non può creare fotogrammi neri prima della clip successiva.
    segment_count = len(sequence_video)
    filters.append("".join(sequence_video) + f"concat=n={segment_count}:v=1:a=0[vout]")
    filters.append("".join(sequence_audio) + f"concat=n={segment_count}:v=0:a=1[aout]")
    filter_text = ";\n".join(filters)
    command += [
        "-filter_complex_script", str(filter_script), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output),
    ]
    return command, filter_text


def _effective_clip_bounds(clip: ClipInfo, index: int, count: int) -> tuple[float, float, float]:
    trim_start = clip.trim_start if index > 0 else 0.0
    trim_end = clip.trim_end if index < count - 1 else 0.0
    duration = max(1 / max(1.0, clip.fps), clip.duration - trim_start - trim_end)
    return trim_start, trim_end, duration


def _run_ffmpeg(
    command: Sequence[str],
    cwd: Path,
    progress: Callable[[float], None] | None,
    cancel_event: threading.Event | None,
) -> None:
    process = subprocess.Popen(
        list(command), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=_creation_flags(),
    )
    assert process.stdout is not None
    try:
        while True:
            if cancel_event and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise MergeCancelled("Operazione annullata.")
            line = process.stdout.readline()
            if line:
                key, _, value = line.strip().partition("=")
                if progress and key in {"out_time_ms", "out_time_us"}:
                    try:
                        progress(float(value) / 1_000_000)
                    except ValueError:
                        pass
            elif process.poll() is not None:
                break
        stderr = process.stderr.read() if process.stderr else ""
        if process.returncode:
            detail = stderr.strip().splitlines()[-1] if stderr.strip() else "errore sconosciuto"
            raise MergeError(f"FFmpeg non ha completato l'operazione: {detail}")
    finally:
        if process.poll() is None:
            process.kill()


def _notify(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback:
        callback(max(0, min(100, percent)), message)


def _check_cancelled(event: threading.Event | None) -> None:
    if event and event.is_set():
        raise MergeCancelled("Operazione annullata.")


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
