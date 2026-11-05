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

Results pending; artifacts/logs are under out/rust-uart-module and out/module-*.
