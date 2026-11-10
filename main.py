#!/usr/bin/env python3
"""
human_cursor.py - human-like, hard-to-pattern cursor movement (terminal only, no UI).

Randomness comes from several layers stacked on top of each other:

  1. MARKOV CHAIN over behaviours (levy-jump, interest-region, tab strip, revisit,
     micro drift, idle, edge, right-click). The transition matrix is NOT fixed:
     each row does a slow random walk every time it is used.
  2. FUNCTIONS OF TIME modulate every behaviour's weight:
       - a sinusoid per behaviour with its own random period (1.5-15 min) and phase
       - a slow Ornstein-Uhlenbeck "mood" (activity level) that changes speed,
         idle time and how much the cursor roams
       - a drifting "interest map" of the screen (some areas get attention for a
         while, then it fades) so there are no favourite spots
       - cooldowns so tab strip / edges / right-clicks can never become a routine
  3. LEVY FLIGHTS for target choice (many short hops, rare long jumps) and a
     memory of recent places the cursor "returns" to, like a person re-checking things.
  4. BEZIER PATHS with random style per move: S-curves, arcs, skewed speed profile
     (peak velocity early/late), two-phase "ballistic + corrective" moves with
     overshoot/undershoot, mid-path hesitations, correlated hand tremor.
  5. HEAVY-TAILED DWELL TIMES (log-normal, with rare long "away" pauses).

Hover only by default. With --right-clicks it also does right-click + Esc (context
menu opens, then is dismissed). It never left-clicks, never picks a menu item.

Install:  pip install pyautogui
Run:      python human_cursor.py
          python human_cursor.py --minutes 45 --tab-y 20 --tab-width 200 --verbose
          python human_cursor.py --right-clicks --click-area 300,120,1600,900

Stop:     Ctrl+C, or slam the mouse into any screen corner (pyautogui failsafe).
macOS:    grant your terminal Accessibility permission. Linux: X11 only.
"""

import argparse
import math
import random
import sys
import time
from collections import deque

try:
    import pyautogui
except ImportError:
    sys.exit("Missing dependency. Install it with:  pip install pyautogui")

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

SCREEN_W, SCREEN_H = pyautogui.size()
MARGIN = 8                      # keep away from corners (failsafe) and edges
T0 = time.time()
ARGS = None                     # filled in main()


# ------------------------------------------------------------------ basics --
def now():
    return time.time() - T0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def clamp_point(x, y):
    return clamp(x, MARGIN, SCREEN_W - MARGIN), clamp(y, MARGIN, SCREEN_H - MARGIN)


def reflect(v, lo, hi):
    """Bounce a coordinate back inside [lo, hi] instead of clipping to the edge."""
    span = hi - lo
    v = (v - lo) % (2 * span)
    return lo + (2 * span - v if v > span else v)


def lognorm(median, sigma):
    return random.lognormvariate(math.log(median), sigma)


def ease(t):
    """Minimum-jerk profile: slow start, fast middle, slow end."""
    return 10 * t**3 - 15 * t**4 + 6 * t**5


def bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1])


class OU:
    """Ornstein-Uhlenbeck process: a slow, mean-reverting random walk in real time."""

    def __init__(self, mean, theta, sigma, lo=None, hi=None):
        self.mean, self.theta, self.sigma = mean, theta, sigma
        self.lo, self.hi = lo, hi
        self.x = mean + random.gauss(0, sigma / math.sqrt(2 * theta))
        self.t = time.time()

    def value(self):
        t = time.time()
        dt, self.t = t - self.t, t
        if dt > 0:
            decay = math.exp(-self.theta * dt)
            sd = self.sigma * math.sqrt((1 - decay**2) / (2 * self.theta))
            self.x = self.mean + (self.x - self.mean) * decay + random.gauss(0, sd)
        if self.lo is not None:
            self.x = max(self.lo, self.x)
        if self.hi is not None:
            self.x = min(self.hi, self.x)
        return self.x


MOOD = OU(1.0, 0.004, 0.02, 0.4, 1.8)     # activity level, ~4 min time constant


# ---------------------------------------------------------------- movement --
def _glide(sx, sy, tx, ty, spd):
    """One Bezier move from (sx,sy) to (tx,ty) with a randomly styled path."""
    dist = math.hypot(tx - sx, ty - sy)
    if dist < 2:
        pyautogui.moveTo(int(tx), int(ty))
        return

    v = lognorm(1400, 0.3)                                   # px/s
    dur = (0.12 + dist / v) / max(spd, 0.2)

    ang = math.atan2(ty - sy, tx - sx)
    perp = ang + math.pi / 2
    bend = dist * abs(random.gauss(0.10, 0.07))
    s1 = random.choice((-1, 1))
    s2 = s1 if random.random() < 0.6 else -s1                # arc vs S-curve
    f1, f2 = random.uniform(0.15, 0.40), random.uniform(0.60, 0.90)
    c1 = (sx + (tx - sx) * f1 + math.cos(perp) * s1 * bend * random.uniform(0.4, 1),
          sy + (ty - sy) * f1 + math.sin(perp) * s1 * bend * random.uniform(0.4, 1))
    c2 = (sx + (tx - sx) * f2 + math.cos(perp) * s2 * bend * random.uniform(0.4, 1),
          sy + (ty - sy) * f2 + math.sin(perp) * s2 * bend * random.uniform(0.4, 1))

    skew = random.uniform(0.85, 1.2)                         # where peak speed sits
    steps = max(8, int(dur * random.uniform(70, 140)))       # varying "polling rate"
    hesitate = (random.randint(int(steps * 0.3), int(steps * 0.7))
                if dist > 300 and random.random() < 0.07 else -1)

    ex = ey = 0.0
    last = None
    for i in range(1, steps + 1):
        u = i / steps
        x, y = bezier((sx, sy), c1, c2, (tx, ty), ease(u**skew))
        amp = 0.35 + 0.65 * math.sin(math.pi * u)            # tremor strongest mid-move
        ex = 0.8 * ex + random.gauss(0, 0.45) * amp          # correlated, not white
        ey = 0.8 * ey + random.gauss(0, 0.45) * amp
        px, py = clamp_point(x + ex, y + ey)
        p = (int(px), int(py))
        if p != last:
            pyautogui.moveTo(p[0], p[1])
            last = p
        d = dur / steps * random.uniform(0.7, 1.3)
        if i == hesitate:
            d += random.uniform(0.05, 0.25)
        time.sleep(d)

    fx, fy = clamp_point(tx, ty)
    pyautogui.moveTo(int(fx), int(fy))


def move_to(tx, ty, speed=1.0, precise=True):
    """Human-ish move. Long moves are often two-phase: ballistic hop that slightly
    misses (over/undershoot + sideways error), then a small slow correction."""
    tx, ty = clamp_point(tx, ty)
    sx, sy = pyautogui.position()
    dist = math.hypot(tx - sx, ty - sy)
    if dist < 3:
        return
    spd = speed * MOOD.value() * ARGS.speed * random.lognormvariate(0, 0.22)

    if precise and dist > 140 and random.random() < 0.40:
        ang = math.atan2(ty - sy, tx - sx)
        along = random.gauss(0.01, 0.03) * dist
        side = random.gauss(0, 0.025) * dist
        ex, ey = clamp_point(tx + math.cos(ang) * along - math.sin(ang) * side,
                             ty + math.sin(ang) * along + math.cos(ang) * side)
        _glide(sx, sy, ex, ey, spd)
        time.sleep(random.uniform(0.02, 0.12))
        cx, cy = pyautogui.position()
        _glide(cx, cy, tx, ty, spd * 0.55)
    else:
        _glide(sx, sy, tx, ty, spd)


# --------------------------------------------------- places / target choice --
GX, GY = 6, 4
CELLS = [OU(0.0, 0.004, 0.06) for _ in range(GX * GY)]   # drifting interest map
MEMORY = deque(maxlen=14)                                # places visited recently
HEADING = random.uniform(0, 2 * math.pi)


def remember():
    MEMORY.append(pyautogui.position())


def levy_target():
    """Heavy-tailed hop with persistent-ish heading, reflected at the borders."""
    global HEADING
    cx, cy = pyautogui.position()
    L = min(60 * random.paretovariate(1.3), 0.9 * math.hypot(SCREEN_W, SCREEN_H))
    if random.random() < 0.5:
        HEADING += random.gauss(0, 1.2)
    else:
        HEADING = random.uniform(0, 2 * math.pi)
    x = reflect(cx + math.cos(HEADING) * L, MARGIN, SCREEN_W - MARGIN)
    y = reflect(cy + math.sin(HEADING) * L, MARGIN, SCREEN_H - MARGIN)
    return x, y


def region_target():
    ws = []
    for k, c in enumerate(CELLS):
        nx = ((k % GX) + 0.5) / GX - 0.5
        ny = ((k // GX) + 0.5) / GY - 0.5
        ws.append(math.exp(-(nx * nx + ny * ny) * 2.0) * math.exp(c.value()))
    k = random.choices(range(len(ws)), weights=ws)[0]
    x = ((k % GX) + random.betavariate(2, 2)) * SCREEN_W / GX
    y = ((k // GX) + random.betavariate(2, 2)) * SCREEN_H / GY
    return x, y


# ---------------------------------------------------------------- behaviours --
def do_levy():
    for _ in range(random.choice((1, 1, 2, 3))):
        move_to(*levy_target(), speed=lognorm(1.0, 0.25))
        sleep_dwell(0.3)
    remember()


def do_region():
    move_to(*region_target(), speed=lognorm(1.0, 0.25))
    if random.random() < 0.35:
        micro()
    remember()


def do_tabs():
    """Glance at 1-4 tabs at random spots on the tab strip (not a sweep)."""
    lo, hi = 130, max(300, SCREEN_W - 220)
    x = random.uniform(lo, min(hi, lo + ARGS.tab_width * 8))
    for _ in range(random.choice((1, 1, 2, 2, 3, 4))):
        x = clamp(x + random.gauss(0, 1.5 * ARGS.tab_width), lo, hi)
        move_to(x, ARGS.tab_y + random.gauss(0, 3),
                speed=lognorm(1.0, 0.25), precise=False)
        time.sleep(min(lognorm(0.55, 0.6), 4.0))
    remember()


def do_revisit():
    if not MEMORY:
        return do_region()
    pts = list(MEMORY)
    weights = [1.0 + i for i in range(len(pts))]          # newer memories more likely
    px, py = random.choices(pts, weights=weights)[0]
    move_to(px + random.gauss(0, 25), py + random.gauss(0, 25), speed=lognorm(1.0, 0.25))


def do_edge():
    """Drift toward a side (sidebar / scrollbar) and maybe glide along it."""
    if random.random() < 0.55:
        x = SCREEN_W - random.uniform(10, 28)               # scrollbar-ish
    else:
        x = random.uniform(20, SCREEN_W * 0.12)             # sidebar-ish
    y = random.uniform(SCREEN_H * 0.15, SCREEN_H * 0.85)
    move_to(x, y, speed=lognorm(1.0, 0.25))
    if random.random() < 0.4:
        sleep_dwell(0.2)
        move_to(x + random.gauss(0, 4), y + random.gauss(0, 150), speed=0.7, precise=False)
    remember()


def micro():
    for _ in range(random.randint(2, 6)):
        x, y = pyautogui.position()
        r = lognorm(7, 0.6)
        a = random.uniform(0, 2 * math.pi)
        move_to(x + math.cos(a) * r, y + math.sin(a) * r, speed=0.5, precise=False)
        time.sleep(min(lognorm(0.25, 0.7), 2.0))


def do_idle():
    if random.random() < 0.02:
        time.sleep(random.uniform(15, 45))                  # rare "stepped away"
    else:
        time.sleep(min(lognorm(2.5, 0.8) / max(MOOD.value(), 0.4), 25))
    if random.random() < 0.5:
        micro()


def do_rclick():
    """Right-click, 'read' the menu, dismiss with a single Esc. Selects nothing."""
    if ARGS.click_area:
        l, t, r, b = ARGS.click_area
    else:
        l, t, r, b = SCREEN_W * 0.1, SCREEN_H * 0.15, SCREEN_W * 0.9, SCREEN_H * 0.85
    move_to(random.uniform(l, r), random.uniform(t, b), speed=lognorm(1.0, 0.25))
    time.sleep(random.uniform(0.3, 1.2))
    pyautogui.click(button="right")
    time.sleep(random.uniform(0.6, 2.2))
    pyautogui.press("esc")
    time.sleep(random.uniform(0.2, 0.6))
    remember()


def sleep_dwell(median=0.6):
    time.sleep(min(lognorm(median, 0.7) / max(MOOD.value(), 0.4), 6.0))


# ------------------------------------------------------- Markov controller --
LEVY, REGION, TABS, REVISIT, MICRO, IDLE, EDGE, RCLICK = range(8)
NAMES = ["levy", "region", "tabs", "revisit", "micro", "idle", "edge", "rclick"]
ACTIONS = [do_levy, do_region, do_tabs, do_revisit, micro, do_idle, do_edge, do_rclick]
N = len(NAMES)

PRIOR = [26, 24, 5, 14, 12, 12, 5, 0]                     # RCLICK set in main()
COOLDOWN = {TABS: 75, EDGE: 50, RCLICK: 35}               # seconds
LAST = [time.time()] * N

# log-weights of the transition matrix: random start, random-walks while in use
LOGM = [[random.gauss(0, 0.6) for _ in range(N)] for _ in range(N)]
AMP = [random.uniform(0.3, 0.8) for _ in range(N)]        # time-sinusoid per behaviour
PER = [random.uniform(90, 900) for _ in range(N)]
PHASE = [random.uniform(0, 2 * math.pi) for _ in range(N)]


def pick_state(cur, recent):
    t, mood = now(), MOOD.value()
    row = LOGM[cur]
    for j in range(N):                                    # matrix drifts over time
        row[j] = row[j] * 0.99 + random.gauss(0, 0.05)

    w = []
    for j in range(N):
        x = PRIOR[j] * math.exp(row[j])
        x *= max(0.1, 1 + AMP[j] * math.sin(2 * math.pi * t / PER[j] + PHASE[j]))
        if j in (IDLE, MICRO):
            x *= mood ** -0.8                             # low mood -> more idling
        if j in (LEVY, REGION):
            x *= mood ** 0.6                              # high mood -> more roaming
        if j in COOLDOWN:
            x *= max(0.03, min(1.0, (time.time() - LAST[j]) / COOLDOWN[j]))
        if j in recent:
            x *= 0.6                                      # discourage short loops
        w.append(x)
    return random.choices(range(N), weights=w)[0]


# -------------------------------------------------------------------- main --
def main():
    global ARGS
    ap = argparse.ArgumentParser(description="Human-like random cursor movement.")
    ap.add_argument("--minutes", type=float, default=0, help="stop after N minutes (default: until Ctrl+C)")
    ap.add_argument("--tab-y", type=int, default=20, help="Y pixel of the tab strip (default 20)")
    ap.add_argument("--tab-width", type=int, default=200, help="approx. tab width in px (default 200)")
    ap.add_argument("--speed", type=float, default=1.0, help="global speed multiplier (default 1.0)")
    ap.add_argument("--right-clicks", action="store_true", help="enable right-click + Esc behaviour")
    ap.add_argument("--right-click-chance", type=float, default=0.15,
                    help="how often right-clicks are chosen, 0-1 (default 0.15)")
    ap.add_argument("--click-area", type=str, default=None,
                    help="LEFT,TOP,RIGHT,BOTTOM box for right-clicks (keep it off your terminal!)")
    ap.add_argument("--seed", type=int, default=None, help="fixed random seed (for testing)")
    ap.add_argument("--verbose", action="store_true", help="print behaviour transitions")
    ARGS = ap.parse_args()

    if ARGS.seed is not None:
        random.seed(ARGS.seed)
    if ARGS.click_area:
        try:
            ARGS.click_area = tuple(int(v) for v in ARGS.click_area.split(","))
            assert len(ARGS.click_area) == 4
        except (ValueError, AssertionError):
            sys.exit("--click-area must look like LEFT,TOP,RIGHT,BOTTOM, e.g. 300,120,1600,900")
    if ARGS.right_clicks:
        PRIOR[RCLICK] = max(0.0, ARGS.right_click_chance) * 60

    for j, cd in COOLDOWN.items():                         # random initial cooldown state
        LAST[j] = time.time() - random.uniform(0, cd)

    end = time.time() + ARGS.minutes * 60 if ARGS.minutes > 0 else None
    print(f"Running on {SCREEN_W}x{SCREEN_H}. Ctrl+C or a screen corner to stop.")
    time.sleep(2)

    cur, recent = IDLE, deque(maxlen=3)
    try:
        while end is None or time.time() < end:
            cur = pick_state(cur, recent)
            recent.append(cur)
            if cur in COOLDOWN:
                LAST[cur] = time.time()
            if ARGS.verbose:
                print(f"{now():7.0f}s  mood={MOOD.x:4.2f}  -> {NAMES[cur]}")
            ACTIONS[cur]()
            if cur != IDLE:
                sleep_dwell(0.6)
    except KeyboardInterrupt:
        print("\nStopped.")
    except pyautogui.FailSafeException:
        print("\nStopped (failsafe corner triggered).")


if __name__ == "__main__":
    main()