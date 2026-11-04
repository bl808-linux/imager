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

All source changes and branch commits preceded testing. Evidence is in
[2026-11-04](2026-11-04/commands.txt); build artifacts remain under
`out/console-v2-review/`.

| Check | Result |
| --- | --- |
| Rust 1.85.0 / LLVM 19 full kernel build, W=1 | PASS |
| Stable 1.99.1 clippy: kernel crate and UART driver | PASS |
| Stable rustfmtcheck | PASS |
| UART binding example | PASS |
| Submission and validation DTB schema checks | PASS |
| b4 checkpatch | 0 errors, 8 warnings |
| Python tests (`pytest tests`) | 24 passed, 9 failed |
| M1s Dock handoff and serial KUnit suite | PASS |
| Reset and unbind/rebind | 8 cycles each, PASS |
| Concurrent wire records | 32, PASS |
| M0 POR and U-Boot postflight recovery | PASS |

The kernel/clippy builds report one pre-existing unused `from_raw` method
in `rust/kernel/io/resource.rs`. Patch warnings concern MAINTAINERS coverage
in earlier patches and the existing short UART Kconfig help.

The Python failures include stale standalone test fixtures (missing Driver
config / changed guard shapes) and crate/compiler version mismatches from
`out/linux-console-build`. Retrying with RUSTUP_TOOLCHAIN did not fix that
harness. These failures remain unresolved; this is not an all-green Python
suite. See [failure output](2026-11-04/pytest.log).

The [board result](2026-11-04/board-result.json),
[raw console](2026-11-04/board-console.bin),
[artifact hashes](2026-11-04/artifacts.json) and
[postflight recovery](2026-11-04/recovery-result.json) identify the image and
record the hardware results. The timer advanced from 10615 to 11411.
All five existing on-board regression checks passed. No new claim is made
about the deferred Sashiko issues.

The first assembly attempt lacked the archived `imager.boot` compatibility
module. Assembly succeeded after adding the existing compatibility helper
to the Python package search path. The successful run is `handoff-final`.

