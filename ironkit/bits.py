"""Shared MSB-first bit reader for SWF/Scaleform-GFx structures (shapes, matrices, fonts)."""


class Bits:
    """Reads bits most-significant-first from a bytes buffer, SWF convention."""

    __slots__ = ("d", "p", "bit")

    def __init__(self, d, p=0):
        self.d = d
        self.p = p
        self.bit = 0

    def read(self, n):
        """Read n bits as an unsigned integer."""
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.p += 1
        return v

    def reads(self, n):
        """Read n bits as a signed (two's-complement) integer."""
        v = self.read(n)
        return v - (1 << n) if n and (v >> (n - 1)) else v

    def align(self):
        """Advance to the next whole byte boundary."""
        if self.bit:
            self.bit = 0
            self.p += 1
