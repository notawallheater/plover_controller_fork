from math import atan2, floor, hypot, sqrt, tau
from typing import Optional
import ctypes


def get_keys_for_stroke(stroke_str: str) -> tuple[str, ...]:
    keys = list[str]()
    passed_hyphen = False
    no_hyphen_keys = set("!@#$%^&*")
    for key in stroke_str:
        if key == "-":
            passed_hyphen = True
            continue
        if key in no_hyphen_keys:
            keys.append(key)
        elif passed_hyphen:
            keys.append(f"-{key}")
        else:
            keys.append(f"{key}-")
    return tuple(keys)


def buttons_to_keys(
    in_keys: set[str],
    unordered_mappings: list[tuple[list[str], tuple[str, ...]]],
) -> set[str]:
    keys = set[str]()
    for chord, result in unordered_mappings:
        if all(map(lambda x: x in in_keys, chord)):
            for key in chord:
                in_keys.remove(key)
            keys.update(result)
    return keys


def angle_to_segment(
    segment_count: int,
    angle: float
) -> int:
    while angle < 0:
        angle += tau
    while angle > tau:
        angle -= tau
    segment = floor(angle / tau * segment_count)
    return segment % segment_count

def stick_segment(
    offset: float,
    segment_count: int,
    previous_segment: Optional[int],
    jitter_guard: float,
    lr: float,
    ud: float,
) -> Optional[int]:
    offset = offset / 360 * tau
    angle = atan2(ud, lr) - offset
    segment = angle_to_segment(segment_count, angle)
    if (previous_segment
        and segment != previous_segment
        and any([angle_to_segment(segment_count, s) == previous_segment
            for s in [angle + jitter_guard, angle - jitter_guard]]
        )
    ):
        return None
    return segment
