"""The shell wrappers must be pure ASCII.

`dg.ps1` shipped with an em-dash inside a double-quoted string. The file is UTF-8 without
a BOM, and Windows PowerShell 5.1 decodes BOM-less scripts using the system ANSI codepage
-- 1252 on a default Windows install. There, the three UTF-8 bytes of an em-dash decode as
`a~EUR"`, and that final byte is U+201D RIGHT DOUBLE QUOTATION MARK, which PowerShell
accepts as a string terminator. The string ended early, quote balance broke, and the
parser reported `The string is missing the terminator: "` ten lines further down.

A BOM would also fix it. ASCII is the stronger rule: these three files bootstrap the tool
before anything else exists, they are read by whatever codepage the host happens to use,
and nothing they say needs a character outside ASCII to say it. A BOM has to survive git
attributes, editors and copy-paste; ASCII cannot be un-fixed.

This is checked rather than remembered because the character is invisible in every editor
that renders it correctly, which is all of them.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every file executed by a host shell before the container exists.
WRAPPERS = ("dg.ps1", "dg.bat", "dg")

# The exact byte sequence that shipped: U+2014 inside a double-quoted PowerShell string.
EM_DASH_SAMPLE = b'Fail "Update Docker Desktop \xe2\x80\x94 Compose v2 is required."\r\n'


def non_ascii_positions(data: bytes) -> list[tuple[int, int, int]]:
    """(line, column, byte) for every byte outside ASCII. Byte-wise, not decoded, because
    the whole failure is that the host decodes these bytes differently than we intend."""
    found = []
    line = column = 1
    for byte in data:
        if byte > 0x7F:
            found.append((line, column, byte))
        if byte == 0x0A:
            line += 1
            column = 1
        else:
            column += 1
    return found


class WrapperEncodingTest(unittest.TestCase):
    def test_wrappers_exist(self) -> None:
        for name in WRAPPERS:
            self.assertTrue((ROOT / name).is_file(), f"{name} is missing")

    def test_wrappers_are_pure_ascii(self) -> None:
        for name in WRAPPERS:
            with self.subTest(wrapper=name):
                offenders = non_ascii_positions((ROOT / name).read_bytes())
                detail = ", ".join(
                    f"line {ln} col {col}: 0x{b:02X}" for ln, col, b in offenders[:5]
                )
                self.assertEqual(
                    offenders,
                    [],
                    f"{name} contains non-ASCII bytes ({detail}). Windows PowerShell 5.1 "
                    f"decodes BOM-less scripts as the ANSI codepage, where these become "
                    f"different characters -- an em-dash becomes a closing quote.",
                )

    def test_the_check_is_not_vacuous(self) -> None:
        # If this ever passes, the check above has stopped checking.
        self.assertNotEqual(
            non_ascii_positions(EM_DASH_SAMPLE),
            [],
            "the ASCII check accepted the exact byte sequence that broke dg.ps1",
        )
