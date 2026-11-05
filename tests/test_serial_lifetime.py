"""Compile the public serial API against real kernel rlibs.

Registration rollback and console lock behavior are exercised by the on-board
Rust serial KUnit suite, rather than extracted code with simulated C objects.
"""
from pathlib import Path
import os
import re
import shlex
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get('BL808_SERIAL_BUILD', ROOT / 'out/linux-console-kunit-build'))


API = r'''
#![allow(dead_code, unused_imports)]
extern crate kernel;
use kernel::{prelude::*,serial::{self,DriverRegistration,Driver,Port,PortConfig,SerialOps,Termios,LockedPort},device::{Device,Bound},sync::Arc};
struct Hardware;
impl SerialOps for Hardware {
    fn tx_empty(&self,_:&Port<'_>)->bool {true}
    fn set_termios(&self,_:&LockedPort<'_>,_:&mut Termios,_:Option<&Termios>) {}
    fn console_write(&self,_:&Port<'_>,_:&[u8]) {}
}
'''


@pytest.mark.parametrize(('body', 'error'), [
    (r'''fn register(driver:Arc<Driver<Hardware>>, dev:&Device<Bound>)->Result<()> {
        serial::register_port(driver,dev,PortConfig {line:3,mapbase:0,clock:40000000,fifo_size:32,irq:0},Ok(Hardware))
    }''', None),
    (r'''struct Borrowed<'a>(&'a u32);
    impl SerialOps for Borrowed<'_> {
        fn tx_empty(&self,_:&Port<'_>)->bool {true}
        fn set_termios(&self,_:&LockedPort<'_>,_:&mut Termios,_:Option<&Termios>) {}
        fn console_write(&self,_:&Port<'_>,_:&[u8]) {}
    }''', 'lifetime'),
    (r'''fn escape(port:&Port<'_>)->&'static Device {port.device()}''', 'lifetime'),
    (r'''fn escape(port:&LockedPort<'_>)->&'static Device {port.device()}''', 'lifetime'),
    (r'''fn relock(port:&Port<'_>) {port.with_lock(|_|());}''', 'private'),
    (r'''fn emergency(port:&Port<'_>) {port.with_console_access(||());}''', 'private'),
    (r'''fn zero_baud(locked:&LockedPort<'_>,termios:&mut Termios) {locked.configure(termios,0);}''', 'NonZero'),
    (r"fn send<T:Send>() {} fn check() {send::<Port<'static>>();}", 'Send'),
    (r"fn sync<T:Sync>() {} fn check() {sync::<Port<'static>>();}", 'Sync'),
    (r"fn send<T:Send>() {} fn check() {send::<LockedPort<'static>>();}", 'Send'),
    (r"fn sync<T:Sync>() {} fn check() {sync::<LockedPort<'static>>();}", 'Sync'),
    (r'''fn built_in(module:&'static kernel::ThisModule)->Result<DriverRegistration<Hardware>> {
        DriverRegistration::new(serial::DriverConfig {name:c"api_test",device_name:c"ttyApi",ports:4,default_baud:115200},module)
    }''', None),
    (r'''fn dynamic(module:&'static kernel::ThisModule) { let _=Driver::<Hardware>::register_builtin(serial::DriverConfig {name:c"api_test",device_name:c"ttyApi",ports:4,default_baud:115200},module); }''', 'register_builtin'),
])
def test_real_kernel_api_rejects_borrowed_or_escaping_state(tmp_path, body, error):
    command_file = BUILD / 'drivers/tty/serial/.bl808_uart.o.cmd'
    if not command_file.is_file():
        pytest.skip('build the console kernel before checking its public API')
    command = shlex.split(command_file.read_text().splitlines()[0].split(':=', 1)[1])
    compiler = command.index('rustc')
    env = os.environ | {'RUSTC_BOOTSTRAP': '1'}
    env.update(arg.split('=', 1) for arg in command[:compiler])
    args, skip = [], False
    for arg in command[compiler:]:
        if skip:
            skip = False
            continue
        if arg == '--out-dir':
            skip = True
        elif arg.startswith('--emit=') or arg.endswith('/bl808_uart.rs'):
            continue
        else:
            args.append(arg)
    source = tmp_path / 'api.rs'
    source.write_text(API + body)
    # Match the compiler that produced the actual kernel rlibs, regardless of PATH.
    version = re.search(r'CONFIG_RUSTC_VERSION_TEXT="rustc ([0-9.]+)', (BUILD / '.config').read_text())[1]
    args[0] = subprocess.check_output(['rustup', 'which', '--toolchain', version, 'rustc'], text=True).strip()
    args += ['--crate-name=serial_api_check' , '--emit=metadata', '-o', str(tmp_path / 'api.rmeta'), str(source)]
    result = subprocess.run(args, cwd=BUILD, env=env, text=True, capture_output=True)
    if error is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert error in result.stderr, result.stderr

