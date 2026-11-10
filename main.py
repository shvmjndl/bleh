#!/usr/bin/env python3
"""
human_cursor.py - moves the mouse cursor around like a real person would.

- No UI, runs in the terminal only. Hover only, it NEVER clicks or types.
- Curved (Bezier) paths, variable speed, slight hand jitter, occasional overshoot.
- Randomly mixes: wandering around the screen, sliding across the browser
  tab strip (hovering tab by tab), small idle drifts, and pauses.

Install:   pip install pyautogui
Run:       python human_cursor.py
Options:   python human_cursor.py --minutes 30 --tab-y 20 --tab-width 200

Stop:      Ctrl+C in the terminal, or slam the mouse into any screen corner
           (pyautogui failsafe).

Notes:
- macOS: give your terminal Accessibility permission
  (System Settings > Privacy & Security > Accessibility).
- Linux: works on X11 (not on pure Wayland).
- The script can't "see" your real tabs, so it hovers along the tab strip at the
  top of the screen. Adjust --tab-y / --tab-width to match your browser
  (maximized/fullscreen browser works best).
"""

import argparse
import math
import random
import sys
import time

try:
    import pyautogui
except ImportError:
    sys.exit("Missing dependency. Install it with:  pip install pyautogui")

pyautogui.PAUSE = 0          # we handle our own timing
pyautogui.FAILSAFE = True    # move mouse to a screen corner to abort

SCREEN_W, SCREEN_H = pyautogui.size()
MARGIN = 8  # stay away from corners so failsafe isn't triggered by accident


# ----------------------------------------------------------------- helpers --
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def clamp_point(x, y):
    return (clamp(x, MARGIN, SCREEN_W - MARGIN),
            clamp(y, MARGIN, SCREEN_H - MARGIN))


def ease(t):
    """Minimum-jerk easing: slow start, fast middle, slow finish (human-like)."""
    return 10 * t**3 - 15 * t**4 + 6 * t**5


def bezier(p0, p1, p2, p3, t):
    u = 1 - t
    x = (u**3 * p0[0] + 3 * u**2 * t * p1[0] +
         3 * u * t**2 * p2[0] + t**3 * p3[0])
    y = (u**3 * p0[1] + 3 * u**2 * t * p1[1] +
         3 * u * t**2 * p2[1] + t**3 * p3[1])
    return x, y


def sleep(a, b=None):
    time.sleep(random.uniform(a, b) if b is not None else a)


# ---------------------------------------------------------------- movement --
def move_to(tx, ty, speed=1.0, allow_overshoot=True):
    """Move the cursor to (tx, ty) along a curved, slightly shaky human path."""
    tx, ty = clamp_point(tx, ty)
    sx, sy = pyautogui.position()
    dist = math.hypot(tx - sx, ty - sy)
    if dist < 3:
        return

    # Occasionally overshoot the target a little, then correct back.
    if allow_overshoot and dist > 250 and random.random() < 0.18:
        ang = math.atan2(ty - sy, tx - sx)
        over = random.uniform(8, 25)
        ox, oy = clamp_point(tx + math.cos(ang) * over, ty + math.sin(ang) * over)
        move_to(ox, oy, speed, allow_overshoot=False)
        sleep(0.05, 0.18)
        move_to(tx, ty, speed * 0.6, allow_overshoot=False)
        return

    # Duration grows with distance (Fitts-like) with natural variance.
    duration = (0.25 + dist / random.uniform(1100, 2200)) / speed
    duration *= random.uniform(0.85, 1.25)

    # Two random control points bend the path like a real wrist/arm motion.
    ang = math.atan2(ty - sy, tx - sx)
    perp = ang + math.pi / 2
    bend = dist * random.uniform(0.05, 0.25)
    c1 = (sx + (tx - sx) * random.uniform(0.2, 0.4) + math.cos(perp) * random.uniform(-bend, bend),
          sy + (ty - sy) * random.uniform(0.2, 0.4) + math.sin(perp) * random.uniform(-bend, bend))
    c2 = (sx + (tx - sx) * random.uniform(0.6, 0.85) + math.cos(perp) * random.uniform(-bend, bend),
          sy + (ty - sy) * random.uniform(0.6, 0.85) + math.sin(perp) * random.uniform(-bend, bend))

    steps = max(12, int(duration * random.uniform(70, 120)))
    for i in range(1, steps + 1):
        t = ease(i / steps)
        x, y = bezier((sx, sy), c1, c2, (tx, ty), t)

        # Tiny hand tremor that fades out as we land on the target.
        shake = (1 - i / steps) * random.uniform(0, 1.6)
        x += random.uniform(-shake, shake)
        y += random.uniform(-shake, shake)

        x, y = clamp_point(x, y)
        pyautogui.moveTo(int(x), int(y), _pause=False)
        time.sleep(duration / steps * random.uniform(0.7, 1.3))


def micro_drift():
    """Tiny idle movements, like a hand resting on the mouse."""
    for _ in range(random.randint(2, 6)):
        x, y = pyautogui.position()
        move_to(x + random.randint(-12, 12), y + random.randint(-9, 9),
                speed=0.5, allow_overshoot=False)
        sleep(0.1, 0.6)


# --------------------------------------------------------------- behaviors --
def wander(_args):
    """Drift to a few random places on the screen, favouring the middle."""
    for _ in range(random.randint(1, 4)):
        x = random.gauss(SCREEN_W / 2, SCREEN_W / 4)
        y = random.gauss(SCREEN_H / 2, SCREEN_H / 4)
        move_to(x, y, speed=random.uniform(0.7, 1.3))
        sleep(0.2, 2.0)
        if random.random() < 0.3:
            micro_drift()


def hover_tabs(args):
    """Slide along the browser tab strip, pausing over tab after tab (no clicks)."""
    tab_count = random.randint(3, 8)
    x = random.uniform(130, 130 + args.tab_width)
    y = args.tab_y + random.uniform(-4, 4)
    direction = 1 if random.random() < 0.75 else -1  # mostly left -> right

    if direction == -1:
        x = clamp(x + args.tab_width * tab_count, 130, SCREEN_W - 220)

    for _ in range(tab_count):
        x = clamp(x, 60, SCREEN_W - 220)
        move_to(x + random.uniform(-25, 25), y + random.uniform(-3, 3),
                speed=random.uniform(0.8, 1.4), allow_overshoot=False)
        sleep(0.25, 1.4)  # linger so the tab "preview" shows, like reading a title
        if random.random() < 0.2:                 # sometimes back up one tab
            x -= direction * args.tab_width
        else:
            x += direction * args.tab_width * random.uniform(0.85, 1.1)

    sleep(0.2, 0.8)
    # then drift back down into the page
    move_to(random.uniform(SCREEN_W * 0.2, SCREEN_W * 0.8),
            random.uniform(SCREEN_H * 0.3, SCREEN_H * 0.7), speed=1.0)


def long_pause(_args):
    """Do nothing for a bit, like someone reading."""
    sleep(1.5, 7.0)
    if random.random() < 0.5:
        micro_drift()


BEHAVIORS = [
    (wander, 50),
    (hover_tabs, 25),
    (long_pause, 15),
    (micro_drift, 10),
]


def pick_behavior():
    funcs, weights = zip(*BEHAVIORS)
    return random.choices(funcs, weights=weights, k=1)[0]


# -------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser(description="Human-like random cursor movement (hover only).")
    ap.add_argument("--minutes", type=float, default=0,
                    help="stop after N minutes (default: run until Ctrl+C)")
    ap.add_argument("--tab-y", type=int, default=20,
                    help="Y pixel of your browser's tab strip (default 20)")
    ap.add_argument("--tab-width", type=int, default=200,
                    help="approx. width of one tab in pixels (default 200)")
    args = ap.parse_args()

    end = time.time() + args.minutes * 60 if args.minutes > 0 else None
    print(f"Running on {SCREEN_W}x{SCREEN_H} screen. Ctrl+C or a screen corner to stop.")
    time.sleep(2)  # grace period so you can switch to your browser

    try:
        while end is None or time.time() < end:
            b = pick_behavior()
            if b is micro_drift:
                micro_drift()
            else:
                b(args)
            sleep(0.2, 1.5)
    except KeyboardInterrupt:
        print("\nStopped.")
    except pyautogui.FailSafeException:
        print("\nStopped (failsafe corner triggered).")


if __name__ == "__main__":
    main()