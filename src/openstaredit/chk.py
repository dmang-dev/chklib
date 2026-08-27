"""The CHK section container.

A ``scenario.chk`` is a flat sequence of sections, each an 8-byte header -- a
4-byte name and a signed little-endian 32-bit length -- followed by that many
payload bytes.

The container below preserves three things that existing CHK readers throw
away, and that anything claiming to round-trip a map has to keep:

**Order.** StarCraft applies sections in file order, with later sections
overriding earlier ones of the same name. A reader that returns a mapping has
already destroyed the information needed to know which one wins.

**Duplicates.** Deliberately duplicated sections are a standard map-protection
technique. A ``dict[name] -> bytes`` silently keeps one and discards the rest.

**Raw bytes.** Sections this library does not understand -- and sections whose
declared length disagrees with reality -- survive a read/write cycle untouched.

Malformed input is reported as :class:`Diagnostic` values rather than raised.
A large share of interesting maps in the wild are malformed on purpose, and a
parser that refuses them is useless for exactly the maps people care about.

``Chk.from_bytes(raw).to_bytes() == raw`` holds for every input, including
truncated and protected files. That is guaranteed by construction: each section
re-emits its own verbatim header fields and payload, and anything the parser
could not interpret is preserved in :attr:`Chk.trailing`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator, Sequence

__all__ = ["Chk", "Section", "Diagnostic", "SECTION_HEADER_SIZE"]

SECTION_HEADER_SIZE = 8

_NAME_STRUCT = struct.Struct("<4si")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Something notable about the input, reported rather than raised."""

    code: str
    message: str
    offset: int
    severity: str = "warning"

    def __str__(self) -> str:
        return f"{self.severity}: {self.code} at 0x{self.offset:X}: {self.message}"


@dataclass(frozen=True, slots=True)
class Section:
    """One CHK section, exactly as it appeared in the file."""

    name: bytes
    """The 4 name bytes verbatim. Not necessarily printable ASCII."""

    declared_size: int
    """The header's signed int32 length, verbatim -- may disagree with ``data``."""

    data: bytes
    """The payload bytes actually present in the file."""

    offset: int
    """Byte offset of this section's header within the source."""

    @property
    def label(self) -> str:
        """A printable name for display. Non-printable bytes become ``.``."""
        return "".join(
            chr(b) if 0x20 <= b < 0x7F else "." for b in self.name
        )

    @property
    def key(self) -> bytes:
        """The name with trailing spaces stripped, e.g. ``b'VER'`` for ``b'VER '``."""
        return self.name.rstrip(b" ")

    @property
    def is_truncated(self) -> bool:
        """True when the file ended before ``declared_size`` bytes were available."""
        return len(self.data) != self.declared_size

    @property
    def header_bytes(self) -> bytes:
        return _NAME_STRUCT.pack(self.name, self.declared_size)

    def to_bytes(self) -> bytes:
        return self.header_bytes + self.data

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        trunc = (
            f", truncated(declared={self.declared_size})" if self.is_truncated else ""
        )
        return (
            f"Section({self.label!r}, {len(self.data)} bytes"
            f"{trunc}, offset=0x{self.offset:X})"
        )


def _normalize(name: str | bytes) -> bytes:
    """Accept ``'VER'``, ``'VER '`` or ``b'VER '`` and return the 4-byte form."""
    raw = name.encode("ascii") if isinstance(name, str) else bytes(name)
    if len(raw) > 4:
        raise ValueError(f"section name must be at most 4 bytes, got {raw!r}")
    return raw.ljust(4, b" ")


@dataclass(slots=True)
class Chk:
    """An ordered, duplicate-preserving sequence of CHK sections."""

    sections: list[Section] = field(default_factory=list)
    """Every section, in file order, duplicates included."""

    trailing: bytes = b""
    """Bytes after the last section the parser could interpret."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    """Problems found while parsing. Never raised."""

    # -- reading ----------------------------------------------------------

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Chk":
        """Parse ``raw``. Never raises on malformed input."""
        sections: list[Section] = []
        diagnostics: list[Diagnostic] = []
        trailing = b""
        total = len(raw)
        offset = 0

        while offset + SECTION_HEADER_SIZE <= total:
            name, declared = _NAME_STRUCT.unpack_from(raw, offset)
            body = offset + SECTION_HEADER_SIZE

            if declared < 0:
                # A negative length is not a size; it is a backwards jump used
                # by "jump section" protectors. Following it would mean
                # emulating StarCraft's parser, including its overlaps. We stop
                # and keep the remainder verbatim so the file still round-trips.
                diagnostics.append(
                    Diagnostic(
                        "negative-section-length",
                        f"section {name!r} declares length {declared}; stopping and "
                        f"preserving the remaining {total - offset} bytes verbatim",
                        offset,
                        "error",
                    )
                )
                trailing = raw[offset:]
                offset = total
                break

            end = body + declared
            if end > total:
                available = total - body
                diagnostics.append(
                    Diagnostic(
                        "truncated-section",
                        f"section {name!r} declares {declared} bytes but only "
                        f"{available} remain",
                        offset,
                        "error",
                    )
                )
                sections.append(Section(name, declared, raw[body:], offset))
                offset = total
                break

            sections.append(Section(name, declared, raw[body:end], offset))
            offset = end

        if offset < total and not trailing:
            trailing = raw[offset:]
            diagnostics.append(
                Diagnostic(
                    "trailing-bytes",
                    f"{len(trailing)} bytes after the last section are too few to "
                    f"form a {SECTION_HEADER_SIZE}-byte header",
                    offset,
                )
            )

        chk = cls(sections=sections, trailing=trailing, diagnostics=diagnostics)
        chk._diagnose_names()
        return chk

    def _diagnose_names(self) -> None:
        for section in self.sections:
            if any(b < 0x20 or b >= 0x7F for b in section.name):
                self.diagnostics.append(
                    Diagnostic(
                        "non-printable-section-name",
                        f"section name {section.name!r} is not printable ASCII",
                        section.offset,
                    )
                )

    # -- writing ----------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialize. Round-trips byte-exactly if nothing has been modified."""
        return b"".join(s.to_bytes() for s in self.sections) + self.trailing

    # -- lookup -----------------------------------------------------------

    def find(self, name: str | bytes) -> list[Section]:
        """Every section with this name, in file order."""
        wanted = _normalize(name)
        return [s for s in self.sections if s.name == wanted]

    def last(self, name: str | bytes) -> Section | None:
        """The section StarCraft would use: the last one with this name.

        Later sections override earlier ones, so this is the effective value.
        Returns ``None`` when absent.
        """
        wanted = _normalize(name)
        for section in reversed(self.sections):
            if section.name == wanted:
                return section
        return None

    def __contains__(self, name: str | bytes) -> bool:
        return self.last(name) is not None

    def __iter__(self) -> Iterator[Section]:
        return iter(self.sections)

    def __len__(self) -> int:
        return len(self.sections)

    # -- summary ----------------------------------------------------------

    @property
    def duplicated_names(self) -> list[bytes]:
        """Names appearing more than once, in first-appearance order."""
        seen: dict[bytes, int] = {}
        for section in self.sections:
            seen[section.name] = seen.get(section.name, 0) + 1
        return [name for name, count in seen.items() if count > 1]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    def describe(self) -> str:
        """A short human-readable summary. Not the ``inspect`` output format."""
        lines = [f"{len(self.sections)} sections, {len(self.to_bytes())} bytes"]
        for section in self.sections:
            lines.append(f"  {section.label}  {len(section.data):>8} bytes")
        if self.trailing:
            lines.append(f"  <trailing> {len(self.trailing):>8} bytes")
        for diagnostic in self.diagnostics:
            lines.append(f"  ! {diagnostic}")
        return "\n".join(lines)
