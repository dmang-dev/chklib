"""PKWARE Data Compression Library "implode" decompression.

MPQ archives call this compression method 0x08, and genuine Blizzard maps use it
heavily -- it is the reason an off-the-shelf permissive MPQ reader is not enough
to open a real ``.scm``.

The format is a bit stream, LSB first, preceded by two header bytes:

===== ===========================================================
byte  meaning
===== ===========================================================
0     literal mode: 0 = literals are raw 8-bit, 1 = Huffman coded
1     dictionary size in bits: 4, 5 or 6 (1 KiB, 2 KiB, 4 KiB)
===== ===========================================================

Then, repeatedly: one bit selects a literal (0) or a length/distance pair (1).
Lengths and distances are Huffman coded with three fixed tables that are part of
the format. A decoded length of 519 terminates the stream.

Two details make this easy to get subtly wrong, and both are why the tests
compare against StormLib's output rather than trusting inspection:

**Codes are read LSB-first but built MSB-first**, so each bit is inverted as it
is folded into the accumulator during decoding.

**Length symbol 0 means 3 and symbol 1 means 2** -- the base table is not
monotonic at the start.

Written from the published format description, not derived from any existing
implementation.

**Coverage note.** Across 488 real maps -- 423 genuine Blizzard maps plus the 65
StarCraft 64 scenarios -- this ran 22,308 times and produced byte-identical
output to StormLib every time, exercising all three dictionary sizes. But the
literal mode byte was **0 in every one of them**, so the raw 8-bit literal path
is heavily validated while :data:`_LITERAL_CODE`, the 256-symbol Huffman table
for coded literals, has never once been exercised against ground truth. Treat
that path as unverified.
"""

from __future__ import annotations

__all__ = ["explode", "PkwareError"]


class PkwareError(Exception):
    """The imploded stream is malformed or uses an unsupported configuration."""


# Code-length tables, run-length encoded: each byte packs a repeat count in the
# high nibble (stored as count - 1) and a code length in the low nibble.
#: **Unverified**: no real map uses coded literals (see the coverage note).
_LITERAL_LENGTHS = bytes((
    11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
    9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
    7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12,
    8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27,
    44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45,
    44, 173,
))
_LENGTH_LENGTHS = bytes((2, 35, 36, 53, 38, 23))
_DISTANCE_LENGTHS = bytes((2, 20, 53, 230, 247, 151, 248))

#: Base match length per length symbol. Note symbol 0 is 3 and symbol 1 is 2.
_LENGTH_BASE = (3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264)
#: Extra bits to read after each length symbol.
_LENGTH_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)

_END_OF_STREAM = 519
_MAX_CODE_BITS = 16


class _Huffman:
    """A canonical Huffman table decoded LSB-first with inverted bits."""

    __slots__ = ("counts", "symbols")

    def __init__(self, packed: bytes, expected_symbols: int) -> None:
        lengths: list[int] = []
        for value in packed:
            repeat = (value >> 4) + 1
            length = value & 0x0F
            lengths.extend([length] * repeat)
        if len(lengths) != expected_symbols:
            raise PkwareError(
                f"code table describes {len(lengths)} symbols, expected "
                f"{expected_symbols} - the packed table is wrong"
            )

        counts = [0] * (_MAX_CODE_BITS + 1)
        for length in lengths:
            counts[length] += 1
        if counts[0] == expected_symbols:
            raise PkwareError("code table is empty")

        # Symbols ordered by code length, then by symbol value: the canonical
        # ordering that makes the incremental decode below correct.
        offsets = [0] * (_MAX_CODE_BITS + 2)
        for length in range(1, _MAX_CODE_BITS + 1):
            offsets[length + 1] = offsets[length] + counts[length]
        symbols = [0] * expected_symbols
        for symbol, length in enumerate(lengths):
            if length:
                symbols[offsets[length]] = symbol
                offsets[length] += 1

        self.counts = counts
        self.symbols = symbols


_LITERAL_CODE = _Huffman(_LITERAL_LENGTHS, 256)
_LENGTH_CODE = _Huffman(_LENGTH_LENGTHS, 16)
_DISTANCE_CODE = _Huffman(_DISTANCE_LENGTHS, 64)


class _BitReader:
    """LSB-first bit reader over a byte string."""

    __slots__ = ("_bit_buffer", "_bit_count", "_data", "_pos")

    def __init__(self, data: bytes, start: int) -> None:
        self._data = data
        self._pos = start
        self._bit_buffer = 0
        self._bit_count = 0

    def bits(self, count: int) -> int:
        """Read ``count`` bits, least significant first."""
        while self._bit_count < count:
            if self._pos >= len(self._data):
                raise PkwareError("imploded stream ended mid-symbol")
            self._bit_buffer |= self._data[self._pos] << self._bit_count
            self._pos += 1
            self._bit_count += 8
        value = self._bit_buffer & ((1 << count) - 1)
        self._bit_buffer >>= count
        self._bit_count -= count
        return value

    def decode(self, table: _Huffman) -> int:
        """Decode one Huffman symbol.

        Codes are canonical and assigned most-significant-bit first, but the
        stream delivers them least-significant-bit first, so each incoming bit
        is inverted as it is folded in.
        """
        code = 0
        first = 0
        index = 0
        for length in range(1, _MAX_CODE_BITS + 1):
            code |= self.bits(1) ^ 1
            count = table.counts[length]
            if code - count < first:
                return table.symbols[index + (code - first)]
            index += count
            first = (first + count) << 1
            code <<= 1
        raise PkwareError("no valid Huffman code found within 16 bits")


def explode(data: bytes, expected_size: int | None = None) -> bytes:
    """Decompress a PKWARE-imploded stream.

    ``expected_size`` is advisory: when given, decompression stops once that
    many bytes are produced. MPQ sectors sometimes omit the end-of-stream
    marker, so the caller's known output size is what actually terminates them.
    """
    if len(data) < 3:
        raise PkwareError("imploded stream is too short to hold a header")

    literal_mode = data[0]
    dictionary_bits = data[1]
    if literal_mode not in (0, 1):
        raise PkwareError(f"invalid literal mode {literal_mode}, expected 0 or 1")
    if not 4 <= dictionary_bits <= 6:
        raise PkwareError(
            f"invalid dictionary size {dictionary_bits}, expected 4, 5 or 6"
        )

    reader = _BitReader(data, 2)
    out = bytearray()

    while True:
        if expected_size is not None and len(out) >= expected_size:
            break
        try:
            is_match = reader.bits(1)
            if is_match:
                symbol = reader.decode(_LENGTH_CODE)
                length = _LENGTH_BASE[symbol] + reader.bits(_LENGTH_EXTRA[symbol])
                if length == _END_OF_STREAM:
                    break
                # A 2-byte match always uses a 2-bit low distance field,
                # regardless of the dictionary size.
                low_bits = 2 if length == 2 else dictionary_bits
                distance = (reader.decode(_DISTANCE_CODE) << low_bits) | reader.bits(low_bits)
                distance += 1
                if distance > len(out):
                    raise PkwareError(
                        f"match distance {distance} reaches before the start of "
                        f"the output ({len(out)} bytes so far)"
                    )
                # Overlapping copies are legal and common: the run is produced
                # byte by byte so a distance smaller than the length repeats.
                start = len(out) - distance
                for offset in range(length):
                    out.append(out[start + offset])
            elif literal_mode:
                out.append(reader.decode(_LITERAL_CODE))
            else:
                out.append(reader.bits(8))
        except PkwareError:
            if expected_size is not None and len(out) >= expected_size:
                break
            raise

    if expected_size is not None and len(out) < expected_size:
        raise PkwareError(
            f"imploded stream produced {len(out)} bytes, expected {expected_size}"
        )
    return bytes(out[:expected_size] if expected_size is not None else out)
