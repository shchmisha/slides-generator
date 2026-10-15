"""Fitting scene lengths into a total, in tenths of a second so sums are exact.

Shared by the video scenario (lengths for the words on paper) and the voice-over (lengths for
the clips as spoken): each scene gets at least what it needs, and time over the limit is taken
back from scenes with room to spare, in proportion to that room.
"""

from __future__ import annotations


def take_back(lengths: list[int], floors: list[int], excess: int) -> list[int]:
    """`lengths` minus up to `excess` tenths, never below a scene's floor. If the room is short
    of the excess, every scene ends at its floor."""
    if excess <= 0:
        return list(lengths)
    room = [max(0, length - floor) for length, floor in zip(lengths, floors, strict=True)]
    available = sum(room)
    wanted = min(excess, available)
    cut = [r * wanted // available if available else 0 for r in room]
    left = wanted - sum(cut)
    for i in sorted(range(len(room)), key=lambda i: room[i] - cut[i], reverse=True):
        if left <= 0:
            break
        if room[i] - cut[i] > 0:
            cut[i] += 1
            left -= 1
    return [length - c for length, c in zip(lengths, cut, strict=True)]


def give(lengths: list[int], ceilings: list[int], extra: int) -> list[int]:
    """`lengths` plus up to `extra` tenths, in proportion to their size, never above a ceiling."""
    out = list(lengths)
    while extra > 0:
        room = [c - x for x, c in zip(out, ceilings, strict=True)]
        open_ = [i for i, r in enumerate(room) if r > 0]
        if not open_:
            break
        base = sum(out[i] for i in open_) or 1
        step = [min(room[i], extra * out[i] // base) for i in open_]
        if not any(step):  # a few tenths left: one each, longest first
            for i in sorted(open_, key=lambda i: out[i], reverse=True)[:extra]:
                out[i] += 1
            break
        for i, s in zip(open_, step, strict=True):
            out[i] += s
        extra -= sum(step)
    return out
