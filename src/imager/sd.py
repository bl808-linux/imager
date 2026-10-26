import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from imager.dfu import Device


class Runner:
    def run(self, command):
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=30)


def committed(proc):
    replies = re.findall(r"DFU state\((\d+)\).*status\((\d+)\)", proc.stdout)
    if proc.returncode or not replies or replies[-1] != ("2", "0"):
        raise RuntimeError("DFU did not confirm successful manifestation: " + proc.stdout)


BLOCK = 512
CHUNK = 4096


def check_range(lba, blocks, capacity):
    if not 0 <= lba < 2**32 or blocks <= 0 or lba + blocks > min(capacity, 2**32):
        raise ValueError("SD range exceeds card capacity or 32-bit LBA space")


class SD:
    def __init__(self, device, output):
        self.device, self.output = device, output
        self.info = device.sd_info()
        self.runner = Runner()
        self.sequence = 0

    def read(self, lba, blocks):
        check_range(lba, blocks, self.info['blocks'])
        if blocks > 8:
            raise ValueError("Read transaction exceeds 8 blocks")
        self.device.select_sd(lba, blocks)  # invalidates previous SRAM data
        self.sequence += 1
        path = self.output / 'read.bin'
        path.unlink(missing_ok=True)
        proc = self.runner.run(self.device.command() + ['-a', 'SD Card', '-Z', str(blocks * BLOCK), '-U', str(path)])
        if proc.returncode:
            raise RuntimeError(proc.stdout)
        data = path.read_bytes()
        if len(data) != blocks * BLOCK:
            raise RuntimeError('Short SD readback')
        return data

    def write_chunk(self, lba, data):
        if not data or len(data) % BLOCK or len(data) > CHUNK:
            raise ValueError('SD data must contain 1..8 complete blocks')
        blocks = len(data) // BLOCK
        check_range(lba, blocks, self.info['blocks'])
        self.device.select_sd(lba, blocks)
        self.sequence += 1
        path = self.output / 'write.dfu'
        path.write_bytes(data)
        suffix = self.runner.run(['dfu-suffix', '-v', '1209', '-p', '0001', '-a', str(path)])
        if suffix.returncode:
            raise RuntimeError(suffix.stdout)
        committed(self.runner.run(self.device.command() + ['-a', 'SD Card', '-D', str(path)]))
        if self.read(lba, blocks) != data:
            raise RuntimeError(f'Independent SD readback mismatch at LBA {lba}')

    def backup(self, lba, blocks, path):
        check_range(lba, blocks, self.info['blocks'])
        digest = hashlib.sha256()
        with path.open('xb') as stream:
            for offset in range(0, blocks, 8):
                data = self.read(lba + offset, min(8, blocks - offset))
                stream.write(data)
                digest.update(data)
            stream.flush()
            os.fsync(stream.fileno())
        return digest.hexdigest()

    def write_file(self, lba, path, max_blocks=None):
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            if not size or size % BLOCK:
                raise ValueError('SD file must have a nonzero multiple of 512 bytes')
            blocks = size // BLOCK
            check_range(lba, blocks, self.info['blocks'])
            if max_blocks is not None and blocks > max_blocks:
                raise ValueError('Input image exceeds the selected partition')
            # Keep the opened source and back up the full range before any write.
            digest = self.backup(lba, blocks, self.output / 'original.bin')
            metadata = self.output / 'backup.json'
            with metadata.open('x') as backup:
                json.dump({'lba': lba, 'blocks': blocks, 'sha256': digest,
                           'card': self.info}, backup, indent=2)
                backup.write('\n')
                backup.flush()
                os.fsync(backup.fileno())
            for offset in range(0, blocks, 8):
                count = min(8, blocks - offset) * BLOCK
                data = stream.read(count)
                if len(data) != count:
                    raise RuntimeError('Input file changed or truncated during transfer')
                self.write_chunk(lba + offset, data)
            if stream.read(1):
                raise RuntimeError('Input file grew during transfer')
        return digest


def partitions(data, capacity):
    if len(data) != BLOCK:
        raise ValueError("Short SD partition table")
    if data[510:] != b"\x55\xaa":
        return []
    result = []
    for index in range(4):
        entry = data[446 + index * 16:462 + index * 16]
        kind, start, count = entry[4], *struct.unpack_from("<II", entry, 8)
        if not kind and not start and not count:
            continue
        if kind in (0x05, 0x0f, 0x85, 0xee):
            raise ValueError("Only primary MBR partitions are supported; GPT/extended table found")
        if not kind or start == 0:
            raise ValueError("Invalid MBR partition")
        check_range(start, count, capacity)
        if any(start < part["lba"] + part["blocks"] and part["lba"] < start + count
               for part in result):
            raise ValueError("Overlapping SD partitions")
        result.append({"number": index + 1, "type": f"{kind:02x}", "lba": start,
                       "blocks": count, "bytes": count * BLOCK})
    return result


def layout(capacity):
    # MBR: p1 BOOT 256 MiB, p2 ROOTFS, p3 raw EXCHANGE 512 MiB.
    alignment = 2048
    end = min(capacity, 2**32) // alignment * alignment
    boot_start, boot_blocks = alignment, 256 * 2048
    root_start = boot_start + boot_blocks
    exchange_blocks = 512 * 2048
    exchange_start = end - exchange_blocks
    if exchange_start <= root_start:
        raise ValueError("SD card is too small for BOOT, ROOTFS and EXCHANGE")
    data = bytearray(BLOCK)
    data[440:444] = os.urandom(4)
    for index, (kind, start, count) in enumerate((
        (0x0c, boot_start, boot_blocks),
        (0x83, root_start, exchange_start - root_start),
        (0xda, exchange_start, exchange_blocks),
    )):
        struct.pack_into("<B3sB3sII", data, 446 + index * 16,
                         0x80 if index == 0 else 0, b"\xfe\xff\xff", kind,
                         b"\xfe\xff\xff", start, count)
    data[510:] = b"\x55\xaa"
    partitions(data, capacity)
    return bytes(data)


@contextmanager
def card():
    with tempfile.TemporaryDirectory(prefix="imager-sd-") as temporary:
        yield SD(Device(), Path(temporary))


def selected(sd, number):
    for part in partitions(sd.read(0, 1), sd.info["blocks"]):
        if part["number"] == number:
            return part
    raise ValueError(f"SD partition {number} does not exist")


def backup_directory():
    root = Path(__file__).resolve().parents[2] / "out/sd"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="backup-", dir=root))


def show_partitions(parts):
    print("Partition  Type       Start LBA       Blocks       Size MiB")
    names = {"0b": "FAT32", "0c": "FAT32", "83": "Linux", "da": "Exchange"}
    for part in parts:
        print(f"{part['number']:>9}  {names.get(part['type'], part['type']):<8} "
              f"{part['lba']:>10} {part['blocks']:>12} {part['bytes'] / 1024**2:>14.1f}")


def info(args: argparse.Namespace) -> None:
    with card() as sd:
        print(f"SD: {sd.info['blocks'] * BLOCK / 1024**3:.2f} GiB "
              f"({sd.info['blocks']} blocks, {BLOCK} bytes/block)")
        print(f"CID: {sd.info['cid']}")
        print(f"CSD: {sd.info['csd']}")
        parts = partitions(sd.read(0, 1), sd.info["blocks"])
        if parts:
            show_partitions(parts)
        else:
            print("No MBR partitions found")


def partition(args: argparse.Namespace) -> None:
    with card() as sd:
        # Reject unsupported existing maps rather than leave stale GPT metadata.
        partitions(sd.read(0, 1), sd.info["blocks"])
        data = layout(sd.info["blocks"])
        show_partitions(partitions(data, sd.info["blocks"]))
        output = backup_directory()
        source = output / "mbr.bin"
        source.write_bytes(data)
        # write_file backs up the old MBR and verifies its replacement.
        sd.output = output
        sd.write_file(0, source, max_blocks=1)
        print(f"Partition table written; original MBR: {output / 'original.bin'}")
        print("BOOT/ROOTFS filesystem images can now be written with sd write")


def read(args: argparse.Namespace) -> None:
    if args.output.exists():
        raise FileExistsError(args.output)
    with card() as sd:
        part = selected(sd, args.partition)
        digest = sd.backup(part["lba"], part["blocks"], args.output)
        print(f"Partition {args.partition}: {part['bytes']} bytes -> {args.output}")
        print(f"SHA-256: {digest}")


def write(args: argparse.Namespace) -> None:
    size = args.input.stat().st_size
    if not args.input.is_file() or not size or size % BLOCK:
        raise ValueError("Input must be a nonempty file containing complete 512-byte blocks")
    with card() as sd:
        part = selected(sd, args.partition)
        if size > part["bytes"]:
            raise ValueError("Input image exceeds the selected partition")
        output = backup_directory()
        sd.output = output
        print(f"Backing up {size} bytes from partition {args.partition}: {output / 'original.bin'}",
              flush=True)
        sd.write_file(part["lba"], args.input, max_blocks=part["blocks"])
        print(f"Partition {args.partition}: {size} bytes written and independently verified")


def register(subparsers) -> None:
    parser = subparsers.add_parser("sd")
    parser.set_defaults(parser=parser)
    commands = parser.add_subparsers(dest="operation")
    commands.add_parser("info").set_defaults(func=info)
    commands.add_parser("partition").set_defaults(func=partition)
    read_parser = commands.add_parser("read")
    read_parser.set_defaults(func=read)
    read_parser.add_argument("--partition", type=int, choices=range(1, 5), required=True)
    read_parser.add_argument("--output", type=Path, required=True)
    write_parser = commands.add_parser("write")
    write_parser.set_defaults(func=write)
    write_parser.add_argument("--partition", type=int, choices=range(1, 5), required=True)
    write_parser.add_argument("--input", type=Path, required=True)
