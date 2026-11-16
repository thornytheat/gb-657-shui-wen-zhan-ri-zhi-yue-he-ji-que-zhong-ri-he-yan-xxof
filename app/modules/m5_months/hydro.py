"""水文日值月合计：零雨与缺测分开，版本优先于后写覆盖。"""

from __future__ import annotations

from decimal import Decimal


def select_daily(records: list[dict]) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in records:
        grouped.setdefault((str(row["station"]), str(row["date"])), []).append(row)
    selected, duplicates, conflicts = [], [], []
    for key, rows in sorted(grouped.items()):
        ranked = sorted(rows, key=lambda row: int(row.get("version", 0)), reverse=True)
        top_version = int(ranked[0].get("version", 0))
        same_version = [row for row in ranked if int(row.get("version", 0)) == top_version]
        values = {str(row.get("rainfall_mm")) for row in same_version}
        if len(values) > 1:
            conflicts.append({"station": key[0], "date": key[1], "reason": "VERSION_CONFLICT"})
            continue
        selected.append(ranked[0])
        duplicates.extend(ranked[1:])
    return {"selected": selected, "duplicates": duplicates, "conflicts": conflicts}


def monthly(records: list[dict], expected_days: dict[str, int]) -> list[dict]:
    totals: dict[tuple[str, str], Decimal] = {}
    counts: dict[tuple[str, str], int] = {}
    for row in records:
        key = (str(row["station"]), str(row["date"])[:7])
        totals[key] = totals.get(key, Decimal("0")) + Decimal(str(row["rainfall_mm"]))
        counts[key] = counts.get(key, 0) + 1
    output = []
    for (station, month), amount in sorted(totals.items()):
        expected = int(expected_days.get(month, counts[(station, month)]))
        output.append({"station": station, "month": month, "rainfall_mm": str(amount.quantize(Decimal("0.1"))),
                       "days": counts[(station, month)], "missing_days": expected - counts[(station, month)],
                       "state": "complete" if expected == counts[(station, month)] else "partial"})
    return output


def reconcile(source: int, accepted: int, duplicates: int, isolated: int) -> dict:
    delta = source - accepted - duplicates - isolated
    return {"source": source, "accepted": accepted, "duplicates": duplicates,
            "isolated": isolated, "delta": delta, "balanced": delta == 0}
