import struct
from types import SimpleNamespace
import pytest
from imager.dfu import Device
from imager.sd import SD, check_range


def test_protocol_rejects_legacy_before_any_mutating_request():
    d = Device.__new__(Device)
    calls = []
    def control(*args, **kwargs):
        calls.append(args)
        return b'M0US\x01\x00' + bytes(10)
    d.control = control
    with pytest.raises(RuntimeError, match='older firmware'):
        d.protocol()
    assert calls == [(0xc0, 1, 16)]


def test_full_lba_and_count_survive_selection():
    d = Device.__new__(Device)
    calls = []
    d.enter = lambda target: calls.append(('enter', target))
    def control(*args, **kwargs):
        calls.append((args, kwargs))
        return struct.pack('<4sHHII', b'M0SD', 2, 8, 0x12345678, 4096)
    d.control = control
    d.select_sd(0x12345678, 8)
    assert calls[1] == ((0x40, 0x17), {'value': 0x5678, 'index': 0x1234})
    for lba, count in [(-1, 1), (2**32, 1), (0, 0), (0, 9), (2**32-1, 2)]:
        with pytest.raises(ValueError):
            d.select_sd(lba, count)
    assert len(calls) == 3


def test_capacity_checks_include_last_block():
    check_range(2**32-1, 1, 2**32)
    for values in [(100, 1, 100), (99, 2, 100), (0, 0, 100), (2**32-1, 2, 2**32)]:
        with pytest.raises(ValueError):
            check_range(*values)


def fake_sd(tmp_path):
    sd = SD.__new__(SD)
    sd.output, sd.info = tmp_path, {'blocks': 100}
    return sd


def test_whole_backup_precedes_any_write_and_chunks_at_4k(tmp_path):
    sd = fake_sd(tmp_path)
    source = tmp_path / 'source.bin'
    source.write_bytes(b'x' * (4096 + 512))
    calls = []
    sd.read = lambda lba, blocks: calls.append(('read', lba, blocks)) or b'o' * (blocks * 512)
    def write(lba, data):
        assert (tmp_path / 'original.bin').read_bytes() == b'o' * (4096 + 512)
        assert (tmp_path / 'backup.json').is_file()
        calls.append(('write', lba, len(data)))
    sd.write_chunk = write
    sd.write_file(10, source)
    assert calls == [('read', 10, 8), ('read', 18, 1), ('write', 10, 4096), ('write', 18, 512)]


def test_backup_failure_never_starts_writing(tmp_path):
    sd = fake_sd(tmp_path)
    source = tmp_path / 'source.bin'
    source.write_bytes(b'x' * 8192)
    def read(lba, blocks):
        if lba == 18:
            raise RuntimeError('card removed')
        return b'o' * 4096
    sd.read = read
    sd.write_chunk = lambda *args: pytest.fail('write before complete backup')
    with pytest.raises(RuntimeError, match='card removed'):
        sd.write_file(10, source)


def test_short_sd_read_is_failure(tmp_path):
    sd = fake_sd(tmp_path)
    sd.sequence = 0
    sd.device = SimpleNamespace(select_sd=lambda *args: None, command=lambda: ['dfu-util'])
    def run(command):
        from pathlib import Path
        Path(command[-1]).write_bytes(bytes(511))
        return SimpleNamespace(returncode=0)
    sd.runner = SimpleNamespace(run=run)
    with pytest.raises(RuntimeError, match='Short SD'):
        sd.read(1, 1)
