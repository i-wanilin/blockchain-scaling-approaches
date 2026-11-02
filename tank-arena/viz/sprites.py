"""Alternative vehicle sprites for the UC2 viewer and figures (the frozen arena/render.py is untouched).

Every style has the signature of arena.render._draw_tank and draws a 32x32 sprite facing up,
rotated to the hull's quarter heading (the guns are hull-fixed, so the barrel is part of it).
`recoil` is accepted for that signature but not drawn: viz/match.py draws the one muzzle flash.
"""
from __future__ import annotations

import math

from arena import render as R

INK = (14, 14, 12)


def _shade(c, a):
    return R._shade(c, a)


def _blit(pygame, screen, sprite, x, y, heading):
    quarter = round((heading + math.pi / 2.0) / (math.pi / 2.0))
    rotated = pygame.transform.rotate(sprite, -quarter * 90)
    screen.blit(rotated, rotated.get_rect(center=(round(x), round(y))))


def classic(pygame, screen, x, y, heading, turret, hp, max_hp, color, radius,
            pid=0, hit_flash=0, recoil=0):
    """A: arcade classic (Battle City): two wide segmented tracks, square body, round turret."""
    c = R.PAPER if hit_flash else color
    s = pygame.Surface((32, 32), pygame.SRCALPHA)
    for tx in (2, 23):                                   # tracks, full length, with treads
        pygame.draw.rect(s, _shade(c, -70), (tx, 5, 7, 25))
        for ty in range(6, 29, 4):
            pygame.draw.rect(s, _shade(c, -25), (tx + 1, ty, 5, 2))
    pygame.draw.rect(s, _shade(c, -15), (9, 8, 14, 20))  # body
    pygame.draw.rect(s, c, (10, 9, 12, 18))
    pygame.draw.circle(s, _shade(c, 25), (16, 19), 6)    # turret
    pygame.draw.circle(s, INK, (16, 19), 6, 1)
    pygame.draw.rect(s, _shade(c, 40), (14, 1, 4, 16))   # barrel
    pygame.draw.rect(s, INK, (14, 1, 4, 16), 1)
    _blit(pygame, screen, s, x, y, heading)


def mbt(pygame, screen, x, y, heading, turret, hp, max_hp, color, radius,
        pid=0, hit_flash=0, recoil=0):
    """B: modern main battle tank, top-down: broad hull over thin tracks, big turret, long gun."""
    c = R.PAPER if hit_flash else color
    s = pygame.Surface((32, 40), pygame.SRCALPHA)
    pygame.draw.rect(s, (30, 30, 28), (3, 12, 26, 26))                 # tracks
    for ty in range(13, 37, 3):
        pygame.draw.line(s, (70, 70, 64), (3, ty), (6, ty))
        pygame.draw.line(s, (70, 70, 64), (26, ty), (28, ty))
    pygame.draw.polygon(s, _shade(c, -30), [(8, 14), (24, 14), (25, 37), (7, 37)])   # hull, tracks show
    pygame.draw.polygon(s, c, [(9, 15), (23, 15), (24, 36), (8, 36)])
    pygame.draw.line(s, _shade(c, -45), (9, 33), (23, 33), 2)          # engine deck
    pygame.draw.circle(s, _shade(c, -20), (16, 26), 8)                 # turret
    pygame.draw.circle(s, _shade(c, 20), (16, 26), 7)
    pygame.draw.circle(s, INK, (16, 26), 8, 1)
    pygame.draw.rect(s, _shade(c, -50), (15, 1, 3, 20))                # long gun
    pygame.draw.rect(s, _shade(c, -50), (14, 1, 5, 3))                 # muzzle brake
    pygame.draw.circle(s, _shade(c, 50), (19, 28), 2)                  # hatch
    _blit(pygame, screen, s, x, y, heading)


def ifv(pygame, screen, x, y, heading, turret, hp, max_hp, color, radius,
        pid=0, hit_flash=0, recoil=0):
    """C: infantry fighting vehicle (BMP-like): wedge nose, low hull, small forward turret."""
    c = R.PAPER if hit_flash else color
    s = pygame.Surface((32, 34), pygame.SRCALPHA)
    pygame.draw.rect(s, (30, 30, 28), (3, 9, 26, 24))                  # tracks
    for ty in range(10, 32, 3):
        pygame.draw.line(s, (70, 70, 64), (3, ty), (5, ty))
        pygame.draw.line(s, (70, 70, 64), (27, ty), (28, ty))
    hull = [(16, 5), (26, 12), (26, 32), (6, 32), (6, 12)]             # wedge front
    pygame.draw.polygon(s, _shade(c, -30), hull)
    pygame.draw.polygon(s, c, [(16, 7), (24, 13), (24, 31), (8, 31), (8, 13)])
    pygame.draw.rect(s, _shade(c, -45), (10, 26, 5, 5))                # rear doors
    pygame.draw.rect(s, _shade(c, -45), (17, 26, 5, 5))
    pygame.draw.circle(s, _shade(c, 25), (16, 17), 4)                  # small turret
    pygame.draw.circle(s, INK, (16, 17), 4, 1)
    pygame.draw.line(s, INK, (16, 0), (16, 14), 2)                     # thin cannon
    _blit(pygame, screen, s, x, y, heading)


def token(pygame, screen, x, y, heading, turret, hp, max_hp, color, radius,
          pid=0, hit_flash=0, recoil=0):
    """D: abstract marker for papers: a disc in the player colour with its number and a barrel."""
    c = R.PAPER if hit_flash else color
    gun = round(heading / (math.pi / 2.0)) * (math.pi / 2.0)
    tip = R._point(x, y, gun, 20, 0)
    pygame.draw.line(screen, INK, (round(x), round(y)), tip, 6)
    pygame.draw.line(screen, _shade(c, -30), (round(x), round(y)), tip, 3)
    pygame.draw.circle(screen, INK, (round(x), round(y)), 13)
    pygame.draw.circle(screen, c, (round(x), round(y)), 11)
    label = R._font(pygame, 13, True).render(str(pid + 1), True, INK)
    screen.blit(label, label.get_rect(center=(round(x), round(y) + 1)))


def car(pygame, screen, x, y, heading, turret, hp, max_hp, color, radius,
        pid=0, hit_flash=0, recoil=0):
    """E: armoured car: four wheels, rounded body, roof gun."""
    c = R.PAPER if hit_flash else color
    s = pygame.Surface((32, 32), pygame.SRCALPHA)
    for wx in (3, 24):
        for wy in (8, 22):
            pygame.draw.rect(s, (28, 28, 26), (wx, wy, 5, 8), border_radius=2)
    pygame.draw.rect(s, _shade(c, -30), (7, 5, 18, 26), border_radius=6)
    pygame.draw.rect(s, c, (8, 6, 16, 24), border_radius=5)
    pygame.draw.rect(s, (40, 60, 70), (10, 8, 12, 4), border_radius=1)  # windscreen
    pygame.draw.rect(s, _shade(c, 30), (13, 16, 6, 6))                  # roof mount
    pygame.draw.rect(s, INK, (13, 16, 6, 6), 1)
    pygame.draw.line(s, INK, (16, 1), (16, 16), 2)
    _blit(pygame, screen, s, x, y, heading)


STYLES = {"current": R._draw_tank, "classic": classic, "mbt": mbt, "ifv": ifv,
          "token": token, "car": car}
