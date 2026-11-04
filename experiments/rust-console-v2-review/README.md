# Rust console v2 review integration

Submission: `b4/rust-bl808-console` at `9f25c03880df7258097db20e81c4d32ee97652d9`.
Validation: `validation/rust-bl808-console-v2` at `d87f2e4eb4701d2fa62a49607ba6c5b3f6b8d913` in
`out/linux-console-v2-validation`. The previous validation branch remains at
`58a639de5202` and retains its original checkout.

## Changes

Keep the separate firmware-supplied APB (160 MHz) and baud (40 MHz) fixed
clocks and their names. The driver enables and retains both clocks, calculating
baud from the baud input. The review's name-removal request assumed one clock;
the hardware investigation established two inputs. These fixed rates describe
the current boot firmware, not a Linux clock-controller implementation.

Describe UART hardware in the binding, remove the unused example label and
legacy riscv,isa, generalize the SoC prompt and remove its redundant COMMON_CLK
selection. Split generic Rust serial maintenance from platform maintenance and
add `https://github.com/bl808-linux/linux.git` as the platform tree.

Preserve all seven validation commits, including the tested UART engine reset
controller and rebind test. No reset provider is added to the submission.

Sashiko findings are deferred by explicit user instruction: TTY lifetime,
failed-startup TX buffer cleanup, console baud state, and INVARIANT/inline
cleanup. Existing regression success does not establish fixes for these issues.

## Verification

All source changes and branch commits precede testing. Results pending.
Build and runtime artifacts will be under `out/console-v2-review/`.
