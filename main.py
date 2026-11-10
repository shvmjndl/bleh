#!/usr/bin/env python3
"""
human_cursor.py - human-like, hard-to-pattern cursor movement (terminal only, no UI).

Randomness layers:

  1. SECOND-ORDER MARKOV CHAIN over behaviours. The next behaviour depends on the
     last two (so A->B->A loops are discouraged), with a first-order back-off.
     Every row is a random walk that drifts each time it is used.
  2. TIME MODULATION per behaviour: a sum of 3 sinusoids with random periods that
     are RE-ROLLED every 20-45 min (no rhythm survives long enough to be learned),
     plus a slow Ornstein-Uhlenbeck noise term.
  3. COOLDOWNS are exponential recoveries with a fresh random time constant each
     time a behaviour fires, so "tab strip every ~75 s" never happens.
  4. DRIFTING SPATIAL PRIORS: the interest-map centre and spread, the tab-strip
     height and the edge zones all wander, so no fixed coordinates repeat.
  5. LEVY FLIGHTS for target choice, a memory of recent places, and BEZIER PATHS
     with random style per move (arcs, S-curves, wobble, skewed speed, two-phase
     overshoot + correction, hesitations, correlated tremor).
  6. HEAVY-TAILED DWELL TIMES (log-normal, rare long "away" pauses).

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
MARGIN = 8
T0 = time.time()
ARGS = None


# ------------------------------------------------------------------ basics --
def now():
    return time.time() - T0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def clamp_point(x, y):
    return clamp(x, MARGIN, SCREEN_W - MARGIN), clamp(y, MARGIN, SCREEN_H - MARGIN)


def reflect(v, lo, hi):
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
    # heavier-tailed bend than before, capped so paths don't loop
    bend = min(dist * 0.35, dist * random.lognormvariate(math.log(0.10), 0.6))
    s1 = random.choice((-1, 1))
    s2 = s1 if random.random() < 0.6 else -s1                # arc vs S-curve
    f1, f2 = random.uniform(0.10, 0.45), random.uniform(0.55, 0.92)
    c1 = (sx + (tx - sx) * f1 + math.cos(perp) * s1 * bend * random.uniform(0.2, 1),
          sy + (ty - sy) * f1 + math.sin(perp) * s1 * bend * random.uniform(0.2, 1))
    c2 = (sx + (tx - sx) * f2 + math.cos(perp) * s2 * bend * random.uniform(0.2, 1),
          sy + (ty - sy) * f2 + math.sin(perp) * s2 * bend * random.uniform(0.2, 1))

    # low-amplitude wobble that differs per move (frequency, phase, size)
    wob_amp = dist * random.uniform(0, 0.02)
    wob_k = random.choice((1, 2, 3))
    wob_ph = random.uniform(0, 2 * math.pi)

    skew = random.uniform(0.8, 1.25)                         # where peak speed sits
    steps = max(8, int(dur * random.uniform(60, 150)))       # varying "polling rate"
    hesitate = (random.randint(int(steps * 0.3), int(steps * 0.7))
                if dist > 300 and random.random() < 0.07 else -1)
    tremor_scale = random.uniform(0.6, 1.4)

    ex = ey = 0.0
    last = None
    for i in range(1, steps + 1):
        u = i / steps
        x, y = bezier((sx, sy), c1, c2, (tx, ty), ease(u**skew))
        off = wob_amp * math.sin(math.pi * wob_k * u + wob_ph) * math.sin(math.pi * u)
        x += math.cos(perp) * off
        y += math.sin(perp) * off
        amp = tremor_scale * (0.35 + 0.65 * math.sin(math.pi * u))
        ex = 0.8 * ex + random.gauss(0, 0.45) * amp
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
    spd = speed * MOOD.value() * ARGS.speed * random.lognormvariate(0, 0.25)

    if precise and dist > 140 and random.random() < 0.40:
        ang = math.atan2(ty - sy, tx - sx)
        along = random.gauss(0.01, 0.035) * dist
        side = random.gauss(0, 0.03) * dist
        ex, ey = clamp_point(tx + math.cos(ang) * along - math.sin(ang) * side,
                             ty + math.sin(ang) * along + math.cos(ang) * side)
        _glide(sx, sy, ex, ey, spd)
        time.sleep(random.uniform(0.02, 0.15))
        cx, cy = pyautogui.position()
        _glide(cx, cy, tx, ty, spd * 0.55)
    else:
        _glide(sx, sy, tx, ty, spd)


# --------------------------------------------------- drifting spatial priors --
GX, GY = 6, 4
CELLS = [OU(0.0, 0.004, 0.06) for _ in range(GX * GY)]   # per-cell interest
CENTER_X = OU(0.5, 0.003, 0.02, 0.2, 0.8)                # where interest centres
CENTER_Y = OU(0.5, 0.003, 0.02, 0.2, 0.8)
SPREAD = OU(0.5, 0.003, 0.03, 0.2, 0.9)                  # how wide interest spreads
TAB_DRIFT = OU(0.0, 0.01, 4.0, -10, 10)                  # tab-strip height wanders px
MEMORY = deque(maxlen=14)
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
    cx, cy, s = CENTER_X.value(), CENTER_Y.value(), SPREAD.value()
    ws = []
    for k, c in enumerate(CELLS):
        nx = ((k % GX) + 0.5) / GX
        ny = ((k // GX) + 0.5) / GY
        ws.append(math.exp(-((nx - cx)**2 + (ny - cy)**2) / (2 * s * s)) * math.exp(c.value()))
    k = random.choices(range(len(ws)), weights=ws)[0]
    x = ((k % GX) + random.betavariate(2, 2)) * SCREEN_W / GX
    y = ((k // GX) + random.betavariate(2, 2)) * SCREEN_H / GY
    return x, y


# ---------------------------------------------------------------- behaviours --
def sleep_dwell(median=0.6):
    time.sleep(min(lognorm(median, 0.7) / max(MOOD.value(), 0.4), 6.0))


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
    """Glance at 1-4 tabs at random spots on the tab strip (not a sweep).
    Sometimes the glance lands just below the strip instead, like hitting content."""
    lo, hi = 130, max(300, SCREEN_W - 220)
    x = random.uniform(lo, hi)
    for _ in range(random.choice((1, 1, 2, 2, 3, 4))):
        x = clamp(x + random.gauss(0, 1.5 * ARGS.tab_width), lo, hi)
        if random.random() < 0.15:
            y = ARGS.tab_y + random.uniform(20, 60)
        else:
            y = ARGS.tab_y + TAB_DRIFT.value() + random.gauss(0, 6)
        move_to(x, y, speed=lognorm(1.0, 0.25), precise=False)
        time.sleep(min(lognorm(random.choice((0.4, 0.55, 0.9)), 0.6), 4.0))
    remember()


def do_revisit():
    if not MEMORY:
        return do_region()
    pts = list(MEMORY)
    weights = [1.0 + i for i in range(len(pts))]
    px, py = random.choices(pts, weights=weights)[0]
    move_to(px + random.gauss(0, 30), py + random.gauss(0, 30), speed=lognorm(1.0, 0.25))


def do_edge():
    """Drift toward a side (scrollbar / sidebar) or the bottom, maybe glide along it."""
    r = random.random()
    if r < 0.4:                                              # right: scrollbar-ish
        x = SCREEN_W - random.uniform(4, 40)
        y = random.uniform(0.05, 0.95) * SCREEN_H
    elif r < 0.8:                                            # left: sidebar-ish
        x = random.uniform(10, SCREEN_W * random.uniform(0.08, 0.25))
        y = random.uniform(0.05, 0.95) * SCREEN_H
    else:                                                    # bottom: taskbar-ish
        x = random.uniform(0.1, 0.9) * SCREEN_W
        y = SCREEN_H - random.uniform(6, 60)
    move_to(x, y, speed=lognorm(1.0, 0.25))
    if random.random() < 0.4:
        sleep_dwell(0.2)
        move_to(x + random.gauss(0, 6), y + random.gauss(0, random.uniform(60, 220)),
                speed=0.7, precise=False)
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
        time.sleep(random.uniform(15, 45))                   # rare "stepped away"
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


# ------------------------------------------------------- Markov controller --
LEVY, REGION, TABS, REVISIT, MICRO, IDLE, EDGE, RCLICK = range(8)
NAMES = ["levy", "region", "tabs", "revisit", "micro", "idle", "edge", "rclick"]
ACTIONS = [do_levy, do_region, do_tabs, do_revisit, micro, do_idle, do_edge, do_rclick]
N = len(NAMES)

PRIOR = [26, 24, 5, 14, 12, 12, 5, 0]                       # RCLICK set in main()
COOL_MEDIAN = {TABS: 70.0, EDGE: 45.0, RCLICK: 30.0}        # seconds, median recovery
COOL = {}                                                   # j -> [last_fired, tau]


class Modulator:
    """Per-behaviour time factor: 3 sinusoids with random periods, re-rolled every
    20-45 minutes so the rhythm never stays fixed, plus slow OU noise."""

    def __init__(self, n):
        self.n = n
        self.noise = [OU(0.0, 0.002, 0.01) for _ in range(n)]
        self._reroll()

    def _reroll(self):
        self.next_reroll = time.time() + random.uniform(20, 45) * 60
        self.waves = [[(random.uniform(0.15, 0.5),
                        random.uniform(60, 1200),
                        random.uniform(0, 2 * math.pi)) for _ in range(3)]
                      for _ in range(self.n)]

    def factor(self, j):
        if time.time() > self.next_reroll:
            self._reroll()
        t = now()
        s = sum(a * math.sin(2 * math.pi * t / p + ph) for a, p, ph in self.waves[j]) / 2
        return max(0.1, 1 + s + self.noise[j].value())


MOD = Modulator(N)
LOGM = {}   # key (prev, cur) -> list of N log-weights; (None, cur) = first-order row


def row_for(key):
    if key not in LOGM:
        LOGM[key] = [random.gauss(0, 0.6) for _ in range(N)]
    return LOGM[key]


def cool_factor(j):
    if j not in COOL:
        return 1.0
    last, tau = COOL[j]
    return max(0.02, 1 - math.exp(-(time.time() - last) / tau))


def mark_fired(j):
    COOL[j] = [time.time(), lognorm(COOL_MEDIAN[j], 0.6)]


def pick_state(prev, cur, recent):
    t, mood = now(), MOOD.value()
    row2 = row_for((prev, cur))
    row1 = row_for((None, cur))
    for j in range(N):                                       # rows drift when used
        row2[j] = row2[j] * 0.98 + random.gauss(0, 0.08)
        row1[j] = row1[j] * 0.99 + random.gauss(0, 0.05)

    w = []
    for j in range(N):
        x = PRIOR[j] * math.exp(row2[j] + 0.5 * row1[j])
        x *= MOD.factor(j)
        if j in (IDLE, MICRO):
            x *= mood ** -0.8                                # low mood -> more idling
        if j in (LEVY, REGION):
            x *= mood ** 0.6                                 # high mood -> more roaming
        if j in COOL_MEDIAN:
            x *= cool_factor(j)
        if j in recent:
            x *= 0.6                                         # discourage short loops
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

    for j, med in COOL_MEDIAN.items():                       # random initial cooldown state
        COOL[j] = [time.time() - random.uniform(0, med), lognorm(med, 0.6)]

    end = time.time() + ARGS.minutes * 60 if ARGS.minutes > 0 else None
    print(f"Running on {SCREEN_W}x{SCREEN_H}. Ctrl+C or a screen corner to stop.")
    time.sleep(2)

    prev, cur, recent = None, IDLE, deque(maxlen=3)
    try:
        while end is None or time.time() < end:
            nxt = pick_state(prev, cur, recent)
            prev, cur = cur, nxt
            recent.append(cur)
            if cur in COOL_MEDIAN:
                mark_fired(cur)
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