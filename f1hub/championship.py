"""Title maths on real standings: countback tie-breaks and when a champion is mathematically decided."""
from collections import Counter
from dataclasses import dataclass, field

from .rules import RULES


# --------------------------------------------------------------------------- entries
@dataclass
class Entry:
    """A driver or constructor in the standings."""
    name: str
    code: str
    team: str = ""
    points: float = 0
    finishes: list = field(default_factory=list)   # GP finishing positions (countback); None = not classified
    wins: int = 0
    podiums: int = 0
    poles: int = 0
    dnfs: int = 0
    sprint_wins: int = 0
    starts: int = 0


def countback_key(finishes, size=30):
    """F1 tie-break: most wins, then most 2nds, then most 3rds..."""
    c = Counter(p for p in finishes if p)
    return tuple(c.get(p, 0) for p in range(1, size + 1))


def standings_key(entry):
    return (entry.points, countback_key(entry.finishes))


def sort_standings(entries):
    return sorted(entries, key=standings_key, reverse=True)


# --------------------------------------------------------------------------- clinch maths
def max_round_points(sprint, constructor=False, rules=RULES):
    gp = rules["gp_points"][0] + (rules["gp_points"][1] if constructor else 0)
    if rules["fastest_lap_point"]:
        gp += 1
    sp = 0
    if sprint:
        sp = rules["sprint_points"][0] + (rules["sprint_points"][1] if constructor else 0)
    return gp + sp


def max_remaining(remaining_rounds, constructor=False, rules=RULES):
    """remaining_rounds: list of booleans (True = sprint weekend)."""
    return sum(max_round_points(s, constructor, rules) for s in remaining_rounds)


def has_clinched_over(leader, rival, remaining_rounds, constructor=False, rules=RULES):
    """True if `rival` can no longer beat `leader`, whatever happens."""
    rival_max = rival.points + max_remaining(remaining_rounds, constructor, rules)
    if leader.points > rival_max:
        return True
    if leader.points < rival_max:
        return False
    # Dead heat on points in the rival's best case (and the leader scoring nothing):
    # the rival would win every remaining GP (and finish 2nd too, for a constructor).
    extra = [1] * len(remaining_rounds) + ([2] * len(remaining_rounds) if constructor else [])
    return countback_key(leader.finishes) > countback_key(rival.finishes + extra)


def title_status(entries, remaining_rounds, constructor=False, rules=RULES):
    """Returns (champion_or_None, contenders). With no rounds left the leader is champion."""
    table = sort_standings(entries)
    leader = table[0]
    if not remaining_rounds:
        return leader, [leader]
    contenders = [leader] + [e for e in table[1:]
                             if not has_clinched_over(leader, e, remaining_rounds, constructor, rules)]
    champion = leader if len(contenders) == 1 else None
    return champion, contenders


def clinch_scenario(entries, remaining_rounds, constructor=False, rules=RULES):
    """If the leader could seal it at the very next round, explain what they need. Else None."""
    if len(remaining_rounds) < 2:
        return None
    table = sort_standings(entries)
    leader, second = table[0], table[1]
    next_max = max_round_points(remaining_rounds[0], constructor, rules)
    after = max_remaining(remaining_rounds[1:], constructor, rules)
    lead = leader.points - second.points
    if lead + next_max <= after:
        return None
    need = after - lead + 1   # points the leader must outscore P2 by at the next round
    if need <= 0:
        return (f"{leader.name} clinches the title next round unless {second.name} outscores them "
                f"by more than {-need:g} points (lead {lead:g}, {after:g} left after it).")
    return (f"{leader.name} can clinch the title next round by outscoring {second.name} "
            f"by {need:g}+ points (needs a lead of {after + 1:g} with {after:g} left; currently {lead:g}).")
