"""Архив согласованной версии серии: каждый шот перекодируется в MP4 FHD 1080x1920, 60 кадров/с (через ffmpeg)."""
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

from . import db

WIDTH, HEIGHT, FPS = 1080, 1920, 60


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # ffmpeg, который ставится вместе с зависимостями студии
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def export_path(eid: int, version: int) -> Path:
    return db.MEDIA_DIR / "exports" / str(eid) / f"v{version}.zip"


def transcode(ffmpeg: str, src: Path, dst: Path) -> None:
    """Вписывает кадр в 1080x1920 (если пропорции другие, добавляются чёрные поля), 60 кадров/с, H.264 + AAC."""
    vf = (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
          f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FPS},format=yuv420p")
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a?", "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ffmpeg завершился с ошибкой").strip().splitlines()[-1][:300])


def build(eid: int, version: int, folder: str, slug: str, shots: list[tuple[int, str]], missing: list[str] | None = None) -> None:
    """shots: [(номер шота, путь к видео относительно media)], missing: подписи шотов без видео. Пишет zip в media/exports и обновляет статус сборки."""
    try:
        ffmpeg = ffmpeg_path()
        if not ffmpeg:
            raise RuntimeError("Не найден ffmpeg. Перезапустите студию через start.bat (он доустановит нужное) или установите ffmpeg.")
        out = export_path(eid, version)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tmp_zip = tmp / "out.zip"
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_STORED) as z:
                for n, rel in shots:
                    src = db.MEDIA_DIR / rel
                    if not src.is_file():
                        raise RuntimeError(f"Нет файла видео шота {n}")
                    dst = tmp / f"{n:02d}.mp4"
                    transcode(ffmpeg, src, dst)
                    z.write(dst, f"{folder}/{slug}/{slug}_{n:02d}.mp4")
                    dst.unlink()
                if missing:
                    txt = ("В архиве этой версии нет видео у шотов: " + ", ".join(missing) + ".\r\n"
                           "Сгенерируйте их и согласуйте версию заново — архив будет полным.\r\n")
                    z.writestr(f"{folder}/{slug}/missing_shots.txt", txt.encode("utf-8"))
            shutil.move(str(tmp_zip), out)
        status, err = "ready", ""
    except Exception as e:  # noqa: BLE001
        status, err = "error", str(e)[:300]
    with db.connect() as c:
        c.execute("UPDATE episodes SET export_status=?, export_error=? WHERE id=? AND approved_version=?",
                  (status, err, eid, version))


def start(eid: int, version: int, folder: str, slug: str, shots: list[tuple[int, str]], missing: list[str] | None = None) -> None:
    threading.Thread(target=build, args=(eid, version, folder, slug, shots, missing), daemon=True).start()


def recover() -> None:
    with db.connect() as c:
        c.execute("UPDATE episodes SET export_status='error', export_error='Приложение было закрыто во время сборки архива' "
                  "WHERE export_status='building'")
