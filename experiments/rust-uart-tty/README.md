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
Results pending. Source: exp/rust-bl808-tty; paired validation:
validation/rust-bl808-console-v2. Logs/artifacts: out/rust-uart-tty/.

Run run.py assemble with --artifacts, then run.py run with --artifacts and
--output. The host injects a 4096-byte payload after TTY_RX_READY, checks 4096
TX bytes, and retains reset, concurrent printk and KUnit acceptance. Init keeps
an FD across each unbind, closes it after hangup, queues a TX FIFO canary while
the UART is unbound through /dev/mem, verifies FIFO state after reprobe and
reopens the TTY. Firmware and storage are unchanged; RAM uploads only.
