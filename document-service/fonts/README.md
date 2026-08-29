# Bundled fonts

## STIXTwoMath.otf

**STIX Two Math**, © 2001–2021 The STIX Fonts Project Authors
(<https://github.com/stipub/stixfonts>).

Licensed under the SIL Open Font License, Version 1.1 — see `OFL.txt`.
Full license and FAQ: <https://scripts.sil.org/OFL>

### Why it is here

`glyphs.py` recovers the real Unicode value of glyphs in PDFs whose embedded
symbol fonts carry no usable character map (common in older scholarly
typesetting). It renders each unknown glyph and matches its shape against
reference renderings of known mathematical symbols. STIX Two Math is the
reference: it covers the operators, relations, delimiters, arrows and Greek
letters we need, in a single self-contained file, under a license that permits
redistribution.

The font is used only to generate in-memory comparison bitmaps. It is never
embedded in output and never shown to users.
