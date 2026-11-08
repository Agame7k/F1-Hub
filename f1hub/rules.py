"""Points rules. Edit if the regulations change."""

RULES = {
    "gp_points": [25, 18, 15, 12, 10, 8, 6, 4, 2, 1],
    "sprint_points": [8, 7, 6, 5, 4, 3, 2, 1],
    "fastest_lap_point": False,   # scrapped from 2025; only matters for clinch maths on old seasons
}

# Races stopped and not resumed (Sporting Regulations, 2022+): points by distance completed by the leader.
# (fraction_from, fraction_to, points)  - below 2 laps no points are awarded; 75%+ gets full points.
REDUCED_POINTS = [
    (0.00, 0.25, [6, 4, 3, 2, 1]),
    (0.25, 0.50, [13, 10, 8, 6, 5, 4, 3, 2, 1]),
    (0.50, 0.75, [19, 14, 12, 9, 8, 6, 5, 3, 2, 1]),
]


def points_for_distance(laps_done, scheduled_laps):
    if laps_done < 2:
        return []
    frac = laps_done / scheduled_laps
    for lo, hi, table in REDUCED_POINTS:
        if lo <= frac < hi:
            return table
    return RULES["gp_points"]
