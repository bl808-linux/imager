# BL808 UART3 TTY and IRQ validation

Implement IRQ-driven, two-wire TTY TX/RX alongside the polling printk console.
DMA, modem control, loadable modules and dynamic Driver unregistration are
outside this experiment. The DT already supplies the UART3 PLIC interrupt.

Built-in Driver registration retains one permanent Arc (matching a static C
uart_driver); caller handles and device-managed ports can be dropped without
freeing uart_state behind a surviving TTY. Startup failure detaches the C TX
buffer under the UART lock and frees outside it. Baud configuration distinguishes
an original B0 request from a zero introduced by serial-core fallback.

Probe and startup mask IRQs/DMA, drain TX and clear TX/RX FIFOs. Termios changes
do not clear FIFOs. IRQ registration and rollback remain Rust-owned; free_irq
drains callbacks before hardware data is released.

No C implementation is changed. The Rust bindgen header includes tty_flip.h to
expose the existing TTY flip-buffer API. The userspace validation init is C.

MAINTAINERS follows the PWM pattern: SERIAL SUBSYSTEM BINDINGS [RUST] owns the
abstractions; SERIAL SUBSYSTEM DRIVERS [RUST] reviews drivers/tty/serial/*.rs.
The separate BL808 platform entry keeps its platform-specific coverage.

## Validation

All initial implementation and regression changes are committed before testing.
Submission: `b4/rust-bl808-console` at `2491aa1dcefd7fb9b727a4668e4993350b3a3a03`.
Paired validation: `validation/rust-bl808-console-v2` at `512b4432c6f3e952d01385b06784bf50b3cfd37a`.
The runtime image was built at `42cdce760d39`; the subsequent validation
commit changes only the historical review document. Logs and binaries remain
under `out/rust-uart-tty/`.

| Check | Result |
| --- | --- |
| Rust 1.85.0 / LLVM 19, W=1 full kernel build | PASS |
| Stable clippy (kernel crate and UART driver) | PASS, one existing unused method warning |
| Stable rustfmtcheck | PASS |
| Submission / validation DTB checks | PASS |
| b4 checkpatch | 0 errors, 8 warnings |
| Python tests | 33 passed |
| Serial KUnit | 14 passed, 0 failed |
| TTY TX and RX | 4096 bytes each, exact match |
| Held FD across unbind, close after hangup | 8 cycles |
| TX FIFO canary and reprobe initialization | 8 cycles |
| UART engine reset and unbind/rebind | 8 cycles |
| Concurrent printk records | 32 verified |
| UART IRQ count | 0 -> 4239; Linux IRQ 12 / PLIC source 20 |
| M0 POR and U-Boot recovery | PASS |

The FIFO canary deliberately queues a TX byte while TX is disabled, then
reprobes. Probe drains firmware output before clearing both FIFOs; the test
checks the TX/RX counts and disabled DMA requests after takeover. This does
not separately seed a dirty hardware RX FIFO.

The three new KUnit cases exercise failed-startup TX allocation cleanup,
console_setup fallback and explicit B0 preservation, and dropping a caller's
Driver handle while the permanent registration retains state. The FD runtime
test exercises port removal while userspace retains an open TTY.

The two old extracted-code/mock Python fixtures were replaced by real public
API compile checks; their rollback/console-lock coverage lives in the actual
serial-core KUnit suite. API checks select the compiler matching the built
kernel rlibs, fixing the former toolchain mismatch.

The first runtime run passed hardware/KUnit checks but its FD completion marker
was emitted with the console unbound. The final harness reports that marker
after rebind, retaining the same read/hangup/close assertions.

Evidence: [board result](2026-11-04/board-result.json),
[raw UART capture](2026-11-04/board-console.bin),
[artifact identities](2026-11-04/artifacts.json),
[postflight recovery](2026-11-04/recovery-result.json).


Run run.py assemble with --artifacts, then run.py run with --artifacts and
--output. The host injects a 4096-byte payload after TTY_RX_READY, checks 4096
TX bytes, and retains reset, concurrent printk and KUnit acceptance. Init keeps
an FD across each unbind, closes it after hangup, queues a TX FIFO canary while
the UART is unbound through /dev/mem, verifies FIFO state after reprobe and
reopens the TTY. Firmware and storage are unchanged; RAM uploads only.
