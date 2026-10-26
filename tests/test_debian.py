import argparse
import hashlib
import io
import shutil
import struct
import subprocess
import tarfile

import pytest

from imager import build


def test_debian_rootfs_has_root_autologin_and_preserves_source(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    (tree / "etc/systemd/system").mkdir(parents=True)
    shadow = "root:!unprovisioned:0:0:99999:7:::\ndaemon:*:1:0:99999:7:::\n"
    (tree / "etc/shadow").write_text(shadow)
    (tree / "etc/fstab").write_text("UUID=old / ext4 defaults 0 1\n")
    filesystem = tmp_path / "filesystem.img"
    with filesystem.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024)
    subprocess.run(["/usr/sbin/mke2fs", "-q", "-t", "ext4", "-F", "-d", str(tree),
                    str(filesystem)], check=True)

    disk = tmp_path / "disk.raw"
    mbr = bytearray(512)
    struct.pack_into("<B3sB3sII", mbr, 446, 0, b"\0\2\0", 0x83, b"\xff" * 3,
                     2048, filesystem.stat().st_size // 512)
    mbr[510:] = b"\x55\xaa"
    with disk.open("wb") as stream, filesystem.open("rb") as source:
        stream.write(mbr)
        stream.seek(2048 * 512)
        shutil.copyfileobj(source, stream)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:xz") as contents:
        contents.add(disk, arcname="disk.raw")
    data = archive.getvalue()
    checksum = hashlib.sha512(data).hexdigest()
    manifest = f"{checksum}  debian-13-nocloud-riscv64-daily.tar.xz\n".encode()
    monkeypatch.setattr(build, "__file__", str(tmp_path / "src/imager/build.py"))
    monkeypatch.setattr(build, "urlopen", lambda url, **kwargs:
                        io.BytesIO(manifest if url.endswith("SHA512SUMS") else data))

    build.debian(argparse.Namespace())
    image = tmp_path / "out/debian/rootfs.img"

    def debugfs(command, source=image):
        return subprocess.run(["/usr/sbin/debugfs", "-R", command, str(source)],
                              capture_output=True, text=True, check=True).stdout

    root = debugfs("cat /etc/shadow").splitlines()[0].split(":")
    assert root[1] == root[2] == root[7] == ""
    assert "daemon:*:1:0:99999:7:::" in debugfs("cat /etc/shadow")
    assert debugfs("cat /etc/shadow", filesystem) == shadow
    assert debugfs("cat /etc/fstab") == "LABEL=rootfs / ext4 defaults 0 1\n"
    assert "--autologin root" in debugfs(
        "cat /etc/systemd/system/serial-getty@.service.d/autologin.conf")
    stat = debugfs("stat /etc/shadow")
    assert "Mode:  0600" in stat
    assert "User:     0   Group:     0" in stat
    with image.open("rb") as stream:
        stream.seek(1024 + 120)
        assert stream.read(16).rstrip(b"\0") == b"rootfs"


def test_debian_checksum_failure_preserves_previous_image(tmp_path, monkeypatch):
    output = tmp_path / "out/debian"
    output.mkdir(parents=True)
    image = output / "rootfs.img"
    image.write_bytes(b"previous image")
    manifest = f"{'0' * 128}  debian-13-nocloud-riscv64-daily.tar.xz\n".encode()
    monkeypatch.setattr(build, "__file__", str(tmp_path / "src/imager/build.py"))
    monkeypatch.setattr(build, "urlopen", lambda url, **kwargs:
                        io.BytesIO(manifest if url.endswith("SHA512SUMS") else b"corrupt"))
    with pytest.raises(RuntimeError, match="SHA-512 mismatch"):
        build.debian(argparse.Namespace())
    assert image.read_bytes() == b"previous image"
