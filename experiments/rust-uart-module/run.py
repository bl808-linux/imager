"""Build/run a RAM-only TTY, FIFO and reset test using the established boot harness."""
import argparse
import importlib.util
import json
import gzip
import subprocess
import shutil
import hashlib
from pathlib import Path
import re
import tarfile
import imager

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
COMPAT = ROOT / 'out/linux-console-checks/lifetime-compat'
COMPAT.mkdir(parents=True, exist_ok=True)
with tarfile.open(ROOT / 'out/rust-uart-upstream-ram/harness-source.tar.gz') as archive:
    (COMPAT / 'boot.py').write_bytes(archive.extractfile('src/imager/boot.py').read())
imager.__path__.append(str(COMPAT))

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

smoke = module('tty_smoke', ROOT / 'experiments/uart-reset-validation/smoke.py')
smoke.RESET_INIT = HERE / 'init.c'
validator = module('tty_validator', HERE / 'validate.py')
validator.legacy = smoke.legacy
validator.timer_count = smoke.timer_count
smoke.validate_wire = validator.validate_wire
recover = module('tty_recover', ROOT / 'experiments/uart-reset-validation/recover.py').recover

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=['assemble', 'run'])
parser.add_argument('--build', type=Path, default=ROOT / 'out/linux-console-kunit-build')
parser.add_argument('--source', type=Path, default=ROOT / 'out/linux-console-v2-validation')
parser.add_argument('--artifacts', type=Path, required=True)
parser.add_argument('--output', type=Path)
parser.add_argument('--builtin', action='store_true')
args = parser.parse_args()
if args.builtin: smoke.RESET_INIT = HERE / 'init-builtin.c'
if args.action == 'assemble':
    result = smoke.assemble(args.build, args.source, args.artifacts, tty='ttyBFLB3')
    if not args.builtin:
        module = args.artifacts / 'bl808_uart.ko'
        shutil.copy2(args.build / 'drivers/tty/serial/bl808_uart.ko', module)
        subprocess.run(['riscv64-linux-gnu-strip', '--strip-debug', str(module)], check=True)
        listing = args.artifacts / 'initramfs.list'
        listing.write_text(listing.read_text() + f'file /bl808_uart.ko {module.resolve()} 0644 0 0\n')
        archive = subprocess.check_output([str(args.build / 'usr/gen_init_cpio') if (args.build / 'usr/gen_init_cpio').exists() else str(args.build / 'scripts/gen_init_cpio'), str(listing)])
        (args.artifacts / 'initramfs.cpio.gz').write_bytes(gzip.compress(archive, mtime=0))
        for name in ['bl808_uart.ko','initramfs.cpio.gz']:
            data = (args.artifacts / name).read_bytes()
            result['files'][name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        (args.artifacts / 'artifacts.json').write_text(json.dumps(result, indent=2) + '\n')
else:
    if args.output is None:
        parser.error('run requires --output')
    receive = smoke.Console.receive
    payload = bytes(65 + i % 26 for i in range(4096))
    def receive_with_input(self, predicate, *extra, **kw):
        def observe(text):
            if 'TTY_RX_READY' in text and not getattr(self, '_tty_payload_sent', False):
                self._tty_payload_sent = True
                self.port.write(payload)
                self.port.flush()
            return predicate(text)
        return receive(self, observe, *extra, **kw)
    smoke.Console.receive = receive_with_input
    original_files = smoke.legacy.check_files
    def check_files(console):
        help_text = console.command('help', 'uboot')
        return original_files(console) if re.search(r'^mmc\s', help_text, re.M) else []
    smoke.legacy.check_files = check_files
    try:
        result = smoke.boot(args.artifacts, args.output, 'handoff', True, 1000000)
    finally:
        recover(args.output / 'recovery')
    text = (args.output / 'console.bin').read_bytes().decode('ascii', 'replace')
    result['tty_tx_exact'] = payload.decode() in text
    result['tty_rx'] = 'TTY_RX_OK 4096' in text
    result['fifo_cycles'] = all(f'FIFO_INIT_OK {i}' in text for i in range(8))
    result['held_fd_cycles'] = all(f'HELD_FD_CLOSED {i}' in text for i in range(8))
    result['module_cycles'] = args.builtin or all(f'MODULE_RELOADED {i}' in text for i in range(8))
    result['pass'] = result['module_cycles'] and result['pass'] and all(result[k] for k in ['tty_tx_exact', 'tty_rx', 'fifo_cycles', 'held_fd_cycles'])
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    if not result['pass']:
        raise RuntimeError('TTY validation failed')
print(json.dumps(result, indent=2))
