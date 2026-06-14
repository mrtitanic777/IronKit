# IronKit

A reverse-engineering toolkit for **Heavy Iron Studios' "GoodEngine" games** on Xbox 360 / Wii.

It was built while statically recompiling Disney•Pixar's *Up* (2009) to PC, and consolidates
the tools that work made necessary into one CLI. GoodEngine powered a string of Heavy Iron
titles (Up, WALL·E, Ratatouille, the SpongeBob games, …) that share the same archive,
texture, and UI formats — so these tools generalize beyond any single game.

> **No game data is included or required to clone this repo.** IronKit operates on files you
> supply from a copy of a game you own. It ships zero copyrighted assets.

## What it does

| Group | Format | Capabilities |
|-------|--------|--------------|
| `ho`    | Heavy Iron **HEB / `.HO`** archives | inspect, list, dump assets, replace an asset (in-slot or grow + relocate) |
| `tex`   | **Xbox 360** tiled DXT1/DXT5 textures | decode descriptor+blob → PNG (un-tile + 8-in-16 swap), batch a whole `.HO` |
| `gfx`   | **Scaleform GFx** UI movies | list external images, embedded fonts, and placements |
| `font`  | GFx **DefineFont3** vector fonts | rebuild the embedded font as a real **TTF** (fontTools) |
| `trace` | **ReXGlue** port GPU traces | replay the engine's own transforms to recover exact on-screen layouts, segment screens |

## Install

```bash
pip install -e .          # or: pip install -r requirements.txt
```

Requires Python 3.9+, Pillow, numpy, fontTools.

## Usage

```bash
ironkit ho info        MNUS.ho                     # layers, asset counts, type breakdown
ironkit ho list        MNUS.ho --type 0x294F89DF   # filter by asset type
ironkit ho extract     MNUS.ho -o dump/            # dump raw asset bytes
ironkit ho replace     MNUS.ho --id 0000..DB7 --data new.bin -o MNUS_mod.ho

ironkit tex decode     MNUS.ho --id 000000060000327D -o logo.png
ironkit tex batch      MNUS.ho -o textures/        # every texture in the archive

ironkit gfx scan       MNUS.ho                     # GFx movies in the file
ironkit gfx images     MNUS.ho                     # DefineExternalImage (cid/dims/name)
ironkit gfx fonts      FONT.ho                     # embedded DefineFont2/3
ironkit gfx placements MNUS.ho                     # image placements (pos/scale)

ironkit font list      FONT.ho                     # font ids
ironkit font extract   FONT.ho --id 2 -o Interstate-Bold.ttf

ironkit trace screens  gfx_trace.log               # segment a port trace into screens
ironkit trace solve    gfx_trace.log --dims 512x512  # exact element rects on a screen
```

## How the formats were cracked

- **HEB/.HO** — reverse-engineered from a decompile of `HiHoFile.dll`: a 2 KB opaque header,
  a MAST/SECT layer table, and per-layer `PSL` sections (AssetList + AssetData), all big-endian.
  The writer preserves opaque regions and fixes offsets on edit.
- **Textures** — 72-byte descriptor (format byte @0x33, packed dims @0x34) points at a separate
  pixel blob (`id | bit44`). Pixels are 360-tiled (Xenia `GetTiledOffset2D`) and 8-in-16
  byte-swapped, then standard DXT.
- **GFx** — SWF-derived; movies are engine-wrapped inside `.HO` ScaleformAssets. Fonts are SWF
  `DefineFont3` (em = 20480), rebuilt to TTF by parsing the glyph shape records.
- **Traces** — the recompiled port (a XenonRecomp/ReXGlue build) is patched to log each draw's
  bound texture, viewport, transform constants and vertex buffer; IronKit replays the vertex
  pipeline (`clip = C·vert`, then viewport) to get pixel-exact rects straight from the engine.

## Legal

IronKit is an independent tool for interoperability and preservation research. It contains no
Heavy Iron / THQ / Disney / Pixar assets, code, or trademarks. You are responsible for owning
any game you use it on. Not affiliated with or endorsed by any of the above.

## License

MIT — see [LICENSE](LICENSE).
