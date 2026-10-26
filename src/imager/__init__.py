import argparse
from pathlib import Path
import shutil
import subprocess


def handle_build(args: argparse.Namespace) -> None:
    root = Path(__file__).resolve().parents[2]
    for core, target in (
        ("m0", "riscv32imac-unknown-none-elf"),
        ("d0", "riscv64gc-unknown-none-elf"),
    ):
        output = root / "out/firmware" / core
        subprocess.run(
            ["cargo", "build", "--release", "--offline", "--target-dir", str(output / "target")],
            cwd=root / "firmware" / core,
            check=True,
        )
        elf = output / f"{core}.elf"
        shutil.copy2(output / "target" / target / "release" / core, elf)
        subprocess.run(
            ["riscv64-unknown-elf-objcopy", "-O", "binary", str(elf), str(elf.with_suffix(".bin"))],
            check=True,
        )


def handle_flash(args: argparse.Namespace) -> None:
    pass


def handle_boot(args: argparse.Namespace) -> None:
    pass


def handle_sd(args: argparse.Namespace) -> None:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="imager")
    subparsers = parser.add_subparsers(dest="command")
    build_parser = subparsers.add_parser("build")
    build_commands = build_parser.add_subparsers(dest="target")
    build_commands.add_parser("firmware").set_defaults(func=handle_build)
    for name, handler in (
        ("flash", handle_flash),
        ("boot", handle_boot),
        ("sd", handle_sd),
    ):
        subparsers.add_parser(name).set_defaults(func=handler)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if args.command == "build" and args.target is None:
        build_parser.print_help()
        return
    args.func(args)
