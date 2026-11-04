"""Wire checks retained from the reset experiment, with TTY acceptance enabled."""
import re

def validate_wire(text, manifest, mode):
    tag = 'BL808_SMOKE_' + manifest['tag']
    tty = manifest.get('tty', 'ttyBFLB3')
    if re.search(r'\bnot ok\b|BUG:|Oops:|Kernel panic|use-after-free|possible circular locking', text):
        raise RuntimeError('kernel/KUnit failure in wire log')
    # Native (no SBI bootconsole) rebind can replay the printk ring. The
    # timestamp and payload identify a record: ignore its identical wire
    # replay, but retain distinct observations even when payloads match.
    wire = re.findall(r'\[\s*([0-9.]+)\]\s+' + tag + r' ([^\n]*)', text.replace('\r', ''))
    records = [record for _, record in dict.fromkeys(wire)]
    if 'DONE' not in records or any(r.startswith('FAIL ') for r in records):
        raise RuntimeError('autonomous init did not finish')
    for label in ['CONSOLE_BEFORE', 'CONSOLE_AFTER']:
        consoles = '\n'.join(r[len(label)+1:] for r in records if r.startswith(label+' '))
        cmdline = next(r[8:] for r in records if r.startswith('CMDLINE '))
        if mode == 'stdout':
            # With no console= argument, defconfig's VT can remain enabled.
            # Native UART must still back /dev/console and emit the wire markers.
            entries = [re.fullmatch(r'(\S+)\s+\S+\s+\(([^)]*)\)(?:\s+\d+:\d+)?', line.strip())
                       for line in consoles.splitlines()]
            if not entries or any(entry is None for entry in entries):
                raise RuntimeError('malformed stdout console list')
            enabled = {entry[1] for entry in entries if 'E' in entry[2]}
            native = [entry for entry in entries if entry[1] == tty]
            if not (enabled <= {'tty0', tty} and len(native) == 1 and
                    'E' in native[0][2] and 'C' in native[0][2]):
                raise RuntimeError('DT stdout did not select the native UART console')
            if re.search(r'\b(?:hvc\d+|sbi\d+)\b', consoles) or 'console=' in cmdline:
                raise RuntimeError('runtime fallback or explicit console in stdout test')
        else:
            legacy.validate_console(consoles, cmdline, False, tty=tty)
    if 'platform_address' in manifest:
        info = '\n'.join(r[10:] for r in records if r.startswith('UART_INFO '))
        address = manifest['platform_address']
        if not re.search(r'\bmmio(?:8|16|32|64)?:0x0*' + address + r'\b', info, re.I):
            raise RuntimeError('native UART MMIO identity missing or incorrect')
    if 'RACE_RING_OK 32' not in records:
        raise RuntimeError('racing printk records were not verified in the kernel ring')
    for i in range(32):
        if f'RECORD {i:02} abcdefghijklmnopqrstuvwxyz' not in records:
            raise RuntimeError(f'missing or corrupt printk record {i}')
    for i in range(8):
        if f'CYCLE {i}' not in records:
            raise RuntimeError(f'missing unbind/rebind cycle {i}')
    before = timer_count('\n'.join(r[11:] for r in records if r.startswith('IRQ_BEFORE ')))
    after = timer_count('\n'.join(r[10:] for r in records if r.startswith('IRQ_AFTER ')))
    if after <= before:
        raise RuntimeError('timer interrupts did not advance')
    if 'TTY_RX_OK 4096' not in records:
        raise RuntimeError('TTY receive was not verified')
    if manifest['kunit']:
        if not re.search(r'\bok \d+ -?\s*rust_serial\b', text):
            raise RuntimeError('Rust serial KUnit suite did not pass')
        for name in ['registration_lives_until_parent_unbind', 'duplicate_port_failure_preserves_original',
                     'data_initialization_failure_does_not_publish_port', 'invalid_line_releases_unpublished_data',
                     'console_removal_waits_for_srcu_readers', 'adapters_supply_callback_lock_contexts']:
            if not re.search(r'\bok \d+ -?\s*' + name + r'\b', text):
                raise RuntimeError('KUnit case missing: ' + name)
    if mode == 'handoff' and not re.search(r'bootconsole.*disabled|boot console.*disabled', text):
        raise RuntimeError('SBI earlycon handoff was not observed')
    reset_records = [r for r in records if r.startswith('RESET_RESULT ')]
    if len(reset_records) != 8 or any('ret=0 ' not in r or
            'busy_before=1 busy_held=0 busy_after=0' not in r for r in reset_records):
        raise RuntimeError('eight hardware reset results did not pass')
    if 'RESET_BOUND_REJECTED errno=16' not in records:
        raise RuntimeError('bound-UART reset rejection missing')
    return {'reset_cycles': 8, 'records': 32, 'unbind_rebind_cycles': 8, 'timer_before': before, 'timer_after': after,
            'kunit': manifest['kunit']}

