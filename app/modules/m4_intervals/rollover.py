"""满量翻转判定：翻量与倒走只差一个前提，判错就把真用量丢掉。

规则：本次小于上次时，只有「上次读数已达满量的 90%」且「补满量后的差值不超过容量」
才算翻转；否则是倒走，必须隔离而不是静默取补数。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config


class ReverseMovement(ValueError):
    code = "Q_REVERSE_READING"


def case_class(prev: Decimal, cur: Decimal, capacity: Decimal) -> str:
    """返回 forward / rollover / reverse 三种判定之一。"""
    if cur >= prev:
        return "forward"
    floor = capacity * config.ROLLOVER_MIN_RATIO
    if prev >= floor and (capacity - prev + cur) <= capacity:
        return "rollover"
    return "reverse"


DEFAULT_CLASSIFIER = case_class


def classify_jump(prev: Decimal, cur: Decimal, capacity: Decimal) -> str:
    return case_class(prev, cur, capacity)


def rollover_delta(prev: Decimal, cur: Decimal, capacity: Decimal) -> Decimal:
    return capacity - prev + cur


def delta_for(prev: Decimal, cur: Decimal, capacity: Decimal) -> tuple[Decimal, str]:
    verdict = case_class(prev, cur, capacity)
    if verdict == "reverse":
        raise ReverseMovement(f"读数倒走：上次 {prev}，本次 {cur}")
    if verdict == "rollover":
        return rollover_delta(prev, cur, capacity), "rollover"
    return cur - prev, "normal"
