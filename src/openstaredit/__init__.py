"""openstaredit - a read/write library for StarCraft map data."""

from .chk import SECTION_HEADER_SIZE, Chk, Diagnostic, Section

__all__ = ["Chk", "Section", "Diagnostic", "SECTION_HEADER_SIZE"]
__version__ = "0.0.1"
