"""Rotation réelle dans un répertoire jetable, sans toucher aux services actifs."""

import os
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rotate-logs.sh"


def rotate(directory, *args, extra_env=None):
    env = {"PATH": os.defpath, "TN_LOG_DIR": str(directory), **(extra_env or {})}
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)


def contents(archive):
    with tarfile.open(archive, "r:gz") as tar:
        return tar.extractfile("journal.log").read()


def test_rotation_keeps_writer_open_and_expires_only_its_old_archives(tmp_path):
    log = tmp_path / "api.log"
    log.write_bytes(b"premier\n")
    with log.open("ab", buffering=0) as writer:
        result = rotate(tmp_path, "--force")
        assert result.returncode == 0, result.stderr
        writer.write(b"suivant\n")
    archives = tmp_path / ".log-archives"
    current = next(archives.glob("api.log-*.tgz"))
    assert contents(current) == b"premier\n"
    assert log.read_bytes() == b"suivant\n"
    assert archives.stat().st_mode & 0o777 == 0o700

    expired = archives / "dashboard.log-20000101.tgz"
    unrelated = archives / "autre.log-20000101.tgz"
    for path in (expired, unrelated):
        path.write_bytes(b"ancien")
        old = time.time() - 4 * 86400
        os.utime(path, (old, old))
    result = rotate(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not expired.exists()
    assert unrelated.exists() and current.exists()


def test_failed_compression_keeps_raw_log_and_retries_next_run(tmp_path):
    log = tmp_path / "engine.log"
    log.write_bytes(b"alerte\n")
    result = rotate(tmp_path, "--force", extra_env={"TMPDIR": str(tmp_path / "absent")})
    assert result.returncode != 0
    archives = tmp_path / ".log-archives"
    raw = next(archives.glob("engine.log-????????"))
    assert raw.read_bytes() == b"alerte\n"
    assert not list(archives.glob("*.tgz"))

    result = rotate(tmp_path)
    assert result.returncode == 0, result.stderr
    assert not raw.exists()
    assert contents(next(archives.glob("engine.log-*.tgz"))) == b"alerte\n"


def test_cron_install_preserves_other_jobs_and_is_idempotent(tmp_path):
    crontab = tmp_path / "crontab"
    crontab.write_text("#!/bin/sh\nif [ \"$1\" = -l ]; then cat \"$TEST_CRONTAB\"; else cp \"$1\" \"$TEST_CRONTAB\"; fi\n")
    crontab.chmod(0o700)
    current = tmp_path / "current"
    current.write_text("12 3 * * * /bin/true # autre\n")
    env = {"PATH": f"{tmp_path}:/usr/sbin:/usr/bin:/bin", "TEST_CRONTAB": str(current)}
    for _ in range(2):
        result = subprocess.run(["bash", str(ROOT / "scripts" / "install-log-rotation.sh")],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    jobs = current.read_text().splitlines()
    assert jobs[0] == "12 3 * * * /bin/true # autre"
    assert len(jobs) == 3
    assert jobs[1].startswith("5 0 * * * ")
    assert jobs[2].startswith("@reboot ")
