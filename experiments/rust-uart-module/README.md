# BL808 UART module lifetime validation

Work in exp/rust-bl808-uart-module and paired validation/rust-bl808-console-v2.
No C implementation changes. The old permanent Driver reference is removed.
DriverRegistration owns admission; ports/handles and installed TTYs retain shared
nongeneric DriverState. TTY install/cleanup hooks also retain/release the UART
module, with final cleanup executing in built-in Rust code. Shared operation
tables have kernel lifetime. Registration names are copied, and proc reads are
drained before the final C uart_unregister_driver call.

Software interfaces use bl808_uart.rs, bl808_uart.ko and SERIAL_BL808_RUST.
DT compatible and ttyBFLB3 are unchanged. MAINTAINERS retains the PWM-style
abstraction M / Rust drivers R pattern and BL808 hardware-specific coverage.
Small accessors now have inline attributes; lifetime bridges cannot be inlined.

Validation retains TX/RX, FIFO, reset and held-FD tests. The module harness loads
the UART after init starts with SBI earlycon. While a TTY is open (also after
unbind), delete_module must return busy. After close and asynchronous cleanup,
removal must succeed. Eight remove/reload cycles must leave usable TTYs.
The built-in variant is checked separately. No forced module removal is used.

## Results (2026-11-04)

Submission b4/rust-bl808-console: `94bb423e0b7b2612c84930afad04dcda86c0c119`.
Paired validation/rust-bl808-console-v2: `bc1b99598935e4d8243877293892f03a6d31a4a7`.
Both final runtime artifacts identify this validation commit.
Artifacts/logs remain under out/rust-uart-module and out/module-*.

| Check | Result |
| --- | --- |
| Minimum Rust 1.85.0 / LLVM 19 kernel and .ko builds | PASS |
| Stable clippy and rustfmtcheck | PASS (one pre-existing unused io helper warning) |
| Driver module MODPOST | PASS |
| Private C references in .ko | None: no tty_mutex or proc_tty_unregister_driver import |
| Shared install/cleanup callbacks | Defined in built-in vmlinux, not .ko |
| Python API/project tests | 33 passed in both configurations |
| Serial KUnit | 14 passed, including real TTY retained after owner/port drop |
| CONFIG_SERIAL_BL808_RUST=m | PASS |
| Live-FD removal (also after unbind) | Rejected as busy, asserted by init |
| Final close then module removal/reload | 8 successful cycles |
| CONFIG_SERIAL_BL808_RUST=y | PASS |
| TX/RX | 4096 bytes each, exact, in both modes |
| FIFO canary / held-FD unbind-close / reset-rebind | 8 each in both modes |
| Concurrent printk records | 32 in each mode |
| DT checks | PASS |
| b4 checkpatch | 0 errors, 8 warnings |
| M0 POR / U-Boot recovery | PASS after both final runs |

The registration constructor is unsafe at the module lifetime boundary: its
owner must be dropped before module unloading and cannot be forgotten/leaked.
SerialOwner retains it; UartModule removes platform devices first, then drops
the global driver handle and registration. Per-TTY references are temporary
and balanced by built-in cleanup, not permanent leaked registration references.

One intermediate image stopped producing early boot output at 0.816 seconds,
before UART module loading, and the harness timed out. Its exact artifact
identity and console log are retained here. The cause was not established;
this is not claimed to be fixed or proved unrelated to kernel changes. The
initial module run and final module/built-in images passed. Final module
results are in module-result.json; final built-in results are in
builtin-result.json. Built-in mode does not claim module removal coverage.

Reproduction:

```
.venv/bin/python experiments/rust-uart-module/run.py assemble --artifacts <new-directory>
.venv/bin/python experiments/rust-uart-module/run.py run --artifacts <directory> --output <new-run-directory>
```

Add `--builtin` to both commands for a y-configured kernel. Assemble requires
the selected build's source symlink to point at the validation worktree.

