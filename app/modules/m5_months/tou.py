"""尾差闭合与电价：两处舍入都要落到可复算的规则上。

1. 分量闭合（0.001 kWh）：同月总表与峰平谷各自最大余额后仍可能差一份，
   差额并入当月用量最大的分量；并列时按 峰 > 平 > 谷 取第一个。
2. 账单闭合（0.01 元）：账单额 = 先合计再舍入；分项金额各自向下取整后
   与账单额的差额按小数部分从大到小逐分补上，补完合计必然相等。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from ... import config

DEFAULT_PRICE = Decimal("0.65")


def close_components(total: Decimal, components: dict[str, Decimal],
                     ) -> tuple[dict[str, Decimal], Decimal, str | None]:
    residual = total - sum(components.values(), Decimal("0"))
    adjusted: str | None = None
    if residual != 0 and components:
        adjusted = max(components,
                       key=lambda name: (components[name],
                                         -config.REGISTERS.index(name)
                                         if name in config.REGISTERS else 0))
        components[adjusted] = components[adjusted] + residual
    return components, residual, adjusted


def price_of(register: str, prices: dict[str, Decimal]) -> Decimal:
    return prices.get(register, config.DEFAULT_PRICES.get(register, DEFAULT_PRICE))


def build_bill(*, month: str, meter_id: str, lines: list[tuple[str, Decimal]],
               prices: dict[str, Decimal], total_usage: Decimal) -> dict | None:
    if not lines:
        return None
    exact: list[dict] = []
    for register, usage in lines:
        price = price_of(register, prices)
        amount = (usage * price)
        floor_amount = amount.quantize(config.MONEY_QUANT, rounding=ROUND_DOWN)
        exact.append({"register": register, "usage": usage, "price": price,
                      "exact": amount, "amount": floor_amount,
                      "fraction": amount - floor_amount})
    target = sum((item["exact"] for item in exact), Decimal("0")).quantize(
        config.MONEY_QUANT, rounding=ROUND_HALF_UP)
    floored = sum((item["amount"] for item in exact), Decimal("0"))
    residual_cents = int(((target - floored) / config.MONEY_QUANT).to_integral_value())
    order = sorted(
        exact,
        key=lambda item: ((-item["fraction"] if residual_cents > 0 else item["fraction"]),
                          config.REGISTERS.index(item["register"])
                          if item["register"] in config.REGISTERS else 9))
    if residual_cents > 0:
        for index in range(residual_cents):
            order[index % len(order)]["amount"] += config.MONEY_QUANT
    elif residual_cents < 0:
        for index in range(-residual_cents):
            order[index % len(order)]["amount"] -= config.MONEY_QUANT
    components = [{
        "register": item["register"],
        "usage": str(item["usage"]),
        "price": str(item["price"]),
        "amount": str(item["amount"]),
    } for item in exact]
    return {
        "month": month,
        "meter_id": meter_id,
        "usage_kwh": total_usage,
        "amount_yuan": sum((item["amount"] for item in exact), Decimal("0")),
        "components": components,
        "adjust_cents": residual_cents,
    }


def component_total(bill: dict) -> Decimal:
    return sum((Decimal(item["amount"]) for item in bill["components"]), Decimal("0"))
