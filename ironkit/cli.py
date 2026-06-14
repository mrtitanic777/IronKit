"""IronKit unified command-line interface."""
import argparse

from . import __version__, archive, texture, gfx, font, trace


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="ironkit",
        description="Reverse-engineering toolkit for Heavy Iron Studios' GoodEngine games "
                    "(HEB/.HO archives, Xbox 360 textures, Scaleform GFx, fonts, port traces).")
    p.add_argument("--version", action="version", version="ironkit " + __version__)
    sub = p.add_subparsers(dest="group", required=True, metavar="{ho,tex,gfx,font,trace}")
    for mod in (archive, texture, gfx, font, trace):
        mod.add_parser(sub)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
