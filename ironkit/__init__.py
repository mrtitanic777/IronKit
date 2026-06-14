"""IronKit - a reverse-engineering toolkit for Heavy Iron Studios' "GoodEngine" games.

Built while porting Disney/Pixar's *Up* (Xbox 360) to PC via static recompilation.
Handles the formats that engine ships: HEB/.HO archives, Xbox 360 GPU textures,
Scaleform GFx UI movies + fonts, and the GPU draw traces emitted by the recompiled port.

Subpackages map 1:1 to CLI groups:
    archive  - Heavy Iron HEB/.HO archive read / inspect / extract / replace
    texture  - Xbox 360 tiled DXT/CTX1 texture decode
    gfx      - Scaleform GFx movie parsing (images, placements, fonts, timeline)
    font     - extract embedded GFx DefineFont3 vector fonts to TTF
    trace    - decode UP_TRACE_GFX / UP_DUMP_TEX traces into screen layouts
"""
__version__ = "0.1.0"
