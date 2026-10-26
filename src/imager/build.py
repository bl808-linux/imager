import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen


def firmware(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parents[2]
    for core, target in (
        ("m0", "riscv32imac-unknown-none-elf"),
        ("d0", "riscv64gc-unknown-none-elf"),
    ):
        output = root / "out/firmware" / core
        subprocess.run(
            ["cargo", "build", "--release", "--offline", "--target-dir", str(output / "target")],
            cwd=root / "firmware" / core,
            check=True,
        )
        elf = output / f"{core}.elf"
        shutil.copy2(output / "target" / target / "release" / core, elf)
        subprocess.run(
            ["riscv64-unknown-elf-objcopy", "-O", "binary", str(elf), str(elf.with_suffix(".bin"))],
            check=True,
        )


def debian(args: argparse.Namespace) -> None:
    tools = {}
    for name in ("sfdisk", "debugfs", "e2fsck"):
        tools[name] = shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin")
        if tools[name] is None:
            raise RuntimeError(f"{name} is required; install fdisk and e2fsprogs")

    output = Path(__file__).resolve().parents[2] / "out/debian"
    output.mkdir(parents=True, exist_ok=True)
    base = "https://cloud.debian.org/images/cloud/trixie/daily/latest"
    filename = "debian-13-nocloud-riscv64-daily.tar.xz"
    print("Checking Debian 13 RISC-V daily image...", flush=True)
    with urlopen(f"{base}/SHA512SUMS", timeout=60) as response:
        checksums = response.read().decode()
    expected = next(fields[0] for line in checksums.splitlines()
                    if len(fields := line.split()) == 2
                    and fields[1].lstrip("*") == filename)
    archive = output / filename

    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha512").hexdigest()

    if not archive.is_file() or digest(archive) != expected:
        print(f"Downloading {filename}...", flush=True)
        partial = archive.with_suffix(archive.suffix + ".part")
        with urlopen(f"{base}/{filename}", timeout=60) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if digest(partial) != expected:
            raise RuntimeError("Debian image SHA-512 mismatch")
        partial.replace(archive)
    (output / "SHA512SUMS").write_text(checksums)

    with tempfile.TemporaryDirectory(dir=output) as temporary:
        work = Path(temporary)
        print("Extracting Debian rootfs partition...", flush=True)
        with tarfile.open(archive, "r|xz") as contents:
            for member in contents:
                if member.isfile() and member.name.endswith(".raw"):
                    contents.extract(member, work, filter="data")
                    disk = work / member.name
                    break
            else:
                raise RuntimeError("Debian archive has no raw disk image")
        table = json.loads(subprocess.check_output(
            [tools["sfdisk"], "--json", str(disk)], text=True,
        ))["partitiontable"]
        partitions = [part for part in table["partitions"]
                      if part["type"].lower() in (
                          "83", "0fc63daf-8483-4772-8e79-3d69d8477de4",
                          "72ec70a6-cf74-40e6-bd49-4bda08e8f224",  # RISC-V root partition
                      )]
        if len(partitions) != 1:
            raise RuntimeError("Expected one Debian Linux rootfs partition")
        partition = partitions[0]
        sector = table["sectorsize"]
        image = work / "rootfs.img"
        with disk.open("rb") as source, image.open("wb") as target:
            source.seek(partition["start"] * sector)
            remaining = partition["size"] * sector
            while remaining:
                chunk = source.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise RuntimeError("Truncated Debian rootfs partition")
                target.write(chunk)
                remaining -= len(chunk)
        with image.open("rb") as stream:
            stream.seek(1024 + 56)
            if stream.read(2) != b"\x53\xef":
                raise RuntimeError("Debian rootfs is not an ext filesystem")

        def debugfs(command):
            return subprocess.run(
                [tools["debugfs"], "-w", "-R", command, str(image)],
                capture_output=True, text=True, check=True,
            ).stdout

        shadow = debugfs("cat /etc/shadow")
        lines = shadow.splitlines()
        for index, line in enumerate(lines):
            fields = line.split(":")
            if fields[0] == "root":
                fields[1] = ""
                # Disable password aging; zero would force a password change.
                fields[2] = ""
                fields[7] = ""
                lines[index] = ":".join(fields)
                break
        else:
            raise RuntimeError("Debian image has no root account")

        print("Configuring root console autologin...", flush=True)
        debugfs("mkdir /etc/systemd/system/serial-getty@.service.d")
        files = {
            "/etc/shadow": ("\n".join(lines) + "\n", "0100600"),
            "/etc/fstab": ("LABEL=rootfs / ext4 defaults 0 1\n", "0100644"),
            "/etc/systemd/system/serial-getty@.service.d/autologin.conf": (
                "[Service]\nExecStart=\n"
                "ExecStart=-/sbin/agetty --autologin root --noreset --noclear "
                "--keep-baud 115200,57600,38400,9600 - $TERM\n", "0100644"),
        }
        for index, (path, (content, mode)) in enumerate(files.items()):
            source = work / f"config-{index}"
            source.write_text(content)
            debugfs(f"rm {path}")
            debugfs(f'write "{source}" {path}')
            debugfs(f"set_inode_field {path} uid 0")
            debugfs(f"set_inode_field {path} gid 0")
            debugfs(f"set_inode_field {path} mode {mode}")
            # debugfs may report command errors while returning exit status zero.
            if debugfs(f"cat {path}") != content:
                raise RuntimeError(f"Failed to configure Debian {path}")
        debugfs("set_super_value volume_name rootfs")
        subprocess.run([tools["e2fsck"], "-f", "-n", str(image)], check=True)
        image.replace(output / "rootfs.img")
    print(f"Debian rootfs with root console autologin: {output / 'rootfs.img'}")


def register(subparsers) -> None:
    parser = subparsers.add_parser("build")
    parser.set_defaults(parser=parser)
    commands = parser.add_subparsers(dest="target")
    commands.add_parser("firmware").set_defaults(func=firmware)
    commands.add_parser("debian").set_defaults(func=debian)
