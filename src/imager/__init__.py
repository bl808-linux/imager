import argparse

from imager import build, sd


def handle_flash(args: argparse.Namespace) -> None:
    pass


def handle_boot(args: argparse.Namespace) -> None:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="imager")
    parser.set_defaults(func=None, parser=parser)
    subparsers = parser.add_subparsers(dest="command")
    build.register(subparsers)
    for name, handler in (
        ("flash", handle_flash),
        ("boot", handle_boot),
    ):
        subparsers.add_parser(name).set_defaults(func=handler)
    sd.register(subparsers)

    args = parser.parse_args()
    if args.func is None:
        args.parser.print_help()
        return
    args.func(args)
