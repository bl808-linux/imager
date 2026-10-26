import argparse
from contextlib import contextmanager
import struct
from types import SimpleNamespace

import pytest

from imager import sd


class MemorySD(sd.SD):
    def __init__(self):
        self.info = {"blocks": 4 * 1024 * 1024, "cid": "test-cid", "csd": "test-csd"}
        self.sectors = {}
        self.writes = []
        table = bytearray(512)
        for index, (kind, lba, count) in enumerate(((0x0c, 64, 16), (0x83, 128, 16))):
            struct.pack_into("<B3sB3sII", table, 446 + index * 16,
                             0, bytes(3), kind, bytes(3), lba, count)
        table[510:] = b"\x55\xaa"
        self.sectors[0] = bytes(table)

    def read(self, lba, blocks):
        sd.check_range(lba, blocks, self.info["blocks"])
        assert blocks <= 8
        return b"".join(self.sectors.get(index, b"\xa5" * 512)
                        for index in range(lba, lba + blocks))

    def write_chunk(self, lba, data):
        assert (self.output / "backup.json").is_file()
        assert data and len(data) % 512 == 0 and len(data) <= 4096
        self.writes.append((lba, data))
        for offset in range(0, len(data), 512):
            self.sectors[lba + offset // 512] = data[offset:offset + 512]


@pytest.fixture
def memory_card(tmp_path, monkeypatch):
    memory = MemorySD()

    @contextmanager
    def connection():
        yield memory

    backup = tmp_path / "backup"
    backup.mkdir()
    monkeypatch.setattr(sd, "card", connection)
    monkeypatch.setattr(sd, "backup_directory", lambda: backup)
    return memory, backup


def test_info_reports_capacity_and_partitions_without_writes(memory_card, capsys):
    memory, _ = memory_card
    sd.info(argparse.Namespace())
    output = capsys.readouterr().out
    assert "2.00 GiB" in output and "test-cid" in output
    assert "FAT32" in output and "Linux" in output
    assert not memory.writes


def test_partition_creates_three_aligned_regions_and_backs_up_mbr(memory_card):
    memory, backup = memory_card
    original = memory.read(0, 1)
    guard = memory.read(1, 1)
    sd.partition(argparse.Namespace())
    assert (backup / "original.bin").read_bytes() == original
    parts = sd.partitions(memory.read(0, 1), memory.info["blocks"])
    boot, root, exchange = parts
    assert [part["type"] for part in parts] == ["0c", "83", "da"]
    assert boot["bytes"] == 256 * 1024**2
    assert exchange["bytes"] == 512 * 1024**2
    assert all(part["lba"] % 2048 == part["blocks"] % 2048 == 0 for part in parts)
    assert boot["lba"] + boot["blocks"] == root["lba"]
    assert root["lba"] + root["blocks"] == exchange["lba"]
    assert exchange["lba"] + exchange["blocks"] <= memory.info["blocks"]
    assert len(memory.writes) == 1 and memory.writes[0][0] == 0
    assert memory.read(1, 1) == guard


def test_read_backs_up_selected_partition_without_writes(memory_card, tmp_path):
    memory, _ = memory_card
    output = tmp_path / "partition.img"
    sd.read(argparse.Namespace(partition=2, output=output))
    assert output.read_bytes() == b"\xa5" * (16 * 512)
    assert not memory.writes
    with pytest.raises(FileExistsError):
        sd.read(argparse.Namespace(partition=2, output=output))


def test_write_preserves_table_and_neighbors_and_backs_up_all_changed_blocks(memory_card, tmp_path):
    memory, backup = memory_card
    original_table = memory.read(0, 1)
    source = tmp_path / "rootfs.img"
    source.write_bytes(b"x" * (9 * 512))
    sd.write(argparse.Namespace(partition=2, input=source))
    assert (backup / "original.bin").read_bytes() == b"\xa5" * (9 * 512)
    assert [(lba, len(data)) for lba, data in memory.writes] == [(128, 4096), (136, 512)]
    assert memory.read(128, 8) + memory.read(136, 1) == source.read_bytes()
    assert memory.read(0, 1) == original_table
    assert memory.read(127, 1) == memory.read(137, 1) == b"\xa5" * 512


def test_oversized_image_and_missing_partition_fail_before_writes(memory_card, tmp_path):
    memory, backup = memory_card
    source = tmp_path / "too-large.img"
    source.write_bytes(bytes(17 * 512))
    with pytest.raises(ValueError, match="exceeds"):
        sd.write(argparse.Namespace(partition=2, input=source))
    with pytest.raises(ValueError, match="does not exist"):
        sd.read(argparse.Namespace(partition=3, output=tmp_path / "missing.img"))
    assert not memory.writes and not list(backup.iterdir())


@pytest.mark.parametrize("kind,lba,count,error", [
    (0xee, 128, 16, "GPT/extended"),
    (0x0f, 128, 16, "GPT/extended"),
    (0x83, 70, 16, "Overlapping"),
    (0x83, 128, 2**32 - 1, "capacity"),
])
def test_unsafe_partition_maps_are_rejected(kind, lba, count, error):
    memory = MemorySD()
    table = bytearray(memory.read(0, 1))
    table[462 + 4] = kind
    struct.pack_into("<II", table, 462 + 8, lba, count)
    with pytest.raises(ValueError, match=error):
        sd.partitions(table, memory.info["blocks"])


def test_too_small_card_is_rejected():
    with pytest.raises(ValueError, match="too small"):
        sd.layout(768 * 2048)


def test_write_file_rechecks_partition_limit_before_backup(memory_card, tmp_path):
    memory, backup = memory_card
    memory.output = backup
    source = tmp_path / "image.img"
    source.write_bytes(bytes(17 * 512))
    with pytest.raises(ValueError, match="exceeds"):
        memory.write_file(128, source, max_blocks=16)
    assert not memory.writes and not list(backup.iterdir())


def test_dfu_write_requires_independent_matching_readback(tmp_path):
    storage = sd.SD.__new__(sd.SD)
    storage.output, storage.info = tmp_path, {"blocks": 100}
    storage.sequence = 0
    storage.device = SimpleNamespace(select_sd=lambda *args: None,
                                     command=lambda: ["dfu-util"])
    commands = []

    def run(command):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="DFU state(2) = dfuIDLE, status(0)")

    storage.runner = SimpleNamespace(run=run)
    storage.read = lambda lba, blocks: b"wrong" * 1024
    with pytest.raises(RuntimeError, match="readback mismatch"):
        storage.write_chunk(10, b"x" * 512)
    assert not any("-e" in command for command in commands)
