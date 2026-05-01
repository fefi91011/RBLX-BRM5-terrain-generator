"""
Terrain Generator — 3D Isometric Editor
Controls:
  Left Mouse  — Sculpt / Paint (depending on mode)
  Right Mouse — Rotate camera
  Scroll      — Zoom
  Middle      — Pan
  Tab         — Toggle Sculpt / Paint mode
  G           — Generate Commands
  S           — Save commands to file
  R           — Reset terrain
  +/-         — Brush size
  E/Q         — Raise/Lower brush direction (sculpt)
"""

import pygame
import numpy as np
import math
import os
import sys
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import json

# ── Try noise, fallback to pure python ──
try:
    from noise import pnoise2
    def get_noise(x, y, scale, octaves, persistence, lacunarity, seed):
        return pnoise2(x * scale, y * scale, octaves=octaves,
                       persistence=persistence, lacunarity=lacunarity,
                       base=seed % 256)
except ImportError:
    def get_noise(x, y, scale, octaves, persistence, lacunarity, seed):
        val, amp, freq, mx = 0, 1, 1, 0
        random.seed(seed)
        for _ in range(octaves):
            val += math.sin(x * scale * freq + seed) * math.cos(y * scale * freq + seed) * amp
            mx += amp; amp *= persistence; freq *= lacunarity
        return val / mx

# ═══════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════

W, H = 1280, 720
FPS = 60

MATERIALS = {
    'Grass':  ('RBLX/Grass',       ( 80, 160,  60)),
    'Snow':   ('RBLX/Snow',        (220, 235, 255)),
    'Sand':   ('RBLX/Sand',        (210, 185, 120)),
    'Rock':   ('BRM5/rock_cliff',  (130, 120, 110)),
    'Water':  ('RBLX/Water',       ( 40, 120, 200)),
    'Lava':   ('RBLX/CrackedLava', (220,  80,  20)),
    'Mud':    ('BRM5/mud_tropical',(100,  70,  50)),
    'Ice':    ('RBLX/Ice',         (160, 215, 240)),
    'Dirt':   ('RBLX/Ground',      (120,  90,  60)),
    'Gravel': ('BRM5/pebble_gravel1',(150,145,140)),
}
MAT_KEYS = list(MATERIALS.keys())

# Auto-material by height (0..1)
def auto_material(n, water_level):
    if n < water_level * 0.5: return 'Water'
    if n < 0.30: return 'Sand'
    if n < 0.52: return 'Grass'
    if n < 0.70: return 'Dirt'
    if n < 0.82: return 'Rock'
    if n < 0.92: return 'Gravel'
    return 'Snow'

BIOMES = {
    'Plains':    dict(max_h=8,  scale=0.08, octaves=3, persistence=0.4, mountains=False, water=0.25),
    'Mountains': dict(max_h=40, scale=0.12, octaves=6, persistence=0.55, mountains=True,  water=0.15),
    'Desert':    dict(max_h=10, scale=0.06, octaves=2, persistence=0.35, mountains=False, water=0.05),
    'Island':    dict(max_h=20, scale=0.14, octaves=4, persistence=0.50, mountains=True,  water=0.40),
    'Canyon':    dict(max_h=30, scale=0.09, octaves=5, persistence=0.65, mountains=True,  water=0.10),
}

# ═══════════════════════════════════════════════
#  TERRAIN DATA
# ═══════════════════════════════════════════════

class Terrain:
    def __init__(self, gw=20, gd=20):
        self.gw = gw
        self.gd = gd
        self.tile_size = 4
        self.max_h = 16
        self.world_id = 1
        self.heights = np.zeros((gd, gw), dtype=np.float32)
        self.paint = np.full((gd, gw), -1, dtype=np.int8)  # -1 = auto
        self.generate()

    def generate(self, scale=0.10, octaves=4, persistence=0.5,
                 lacunarity=2.0, seed=42, mountains=True, water_level=0.25):
        self.water_level = water_level
        self.seed = seed
        for z in range(self.gd):
            for x in range(self.gw):
                n = get_noise(x, z, scale, octaves, persistence, lacunarity, seed)
                n = (n + 1) / 2  # 0..1
                if mountains:
                    n = n ** 0.7
                if n < water_level:
                    n = water_level * 0.5
                self.heights[z, x] = n
        self.paint[:] = -1

    def get_height_px(self, x, z):
        """World height in units"""
        return max(1, round(float(self.heights[z, x]) * self.max_h))

    def get_material(self, x, z):
        p = self.paint[z, x]
        if p >= 0:
            return MAT_KEYS[p]
        return auto_material(float(self.heights[z, x]), self.water_level)

    def sculpt(self, cx, cz, radius, strength, direction):
        """Raise or lower terrain with smooth falloff"""
        for z in range(self.gd):
            for x in range(self.gw):
                dx, dz = x - cx, z - cz
                dist = math.sqrt(dx*dx + dz*dz)
                if dist <= radius:
                    falloff = 1.0 - (dist / radius) ** 2
                    delta = strength * falloff * direction
                    self.heights[z, x] = float(np.clip(self.heights[z, x] + delta, 0.0, 1.0))

    def paint_tile(self, cx, cz, radius, mat_idx):
        for z in range(self.gd):
            for x in range(self.gw):
                dx, dz = x - cx, z - cz
                if math.sqrt(dx*dx + dz*dz) <= radius:
                    self.paint[z, x] = mat_idx

    def generate_commands(self):
        lines = []
        ts = self.tile_size
        wid = self.world_id
        for z in range(self.gd):
            for x in range(self.gw):
                n = float(self.heights[z, x])
                height = max(1, round(n * self.max_h))
                mat = self.get_material(x, z)
                mat_name, rgb = MATERIALS[mat]
                r, g, b = rgb

                # shape detection
                nx_ = float(self.heights[z, x+1]) if x < self.gw-1 else n
                nz_ = float(self.heights[z+1, x]) if z < self.gd-1 else n
                hr = max(1, round(nx_ * self.max_h))
                hf = max(1, round(nz_ * self.max_h))
                dr, df = hr - height, hf - height

                shape, ry = 'part', 0
                if abs(dr) >= 2 and abs(dr) > abs(df):
                    shape = 'wedge'
                    ry = 0 if dr > 0 else 180
                elif abs(df) >= 2:
                    shape = 'wedge'
                    ry = 270 if df > 0 else 90

                sx = round(ts + random.uniform(-0.2, 0.2), 2)
                sz_ = round(ts + random.uniform(-0.2, 0.2), 2)
                sy = 0.2 if shape == 'wedge' else max(0.5, height)
                px, py, pz = x * ts, height, z * ts
                name = f't_{x}_{z}'

                lines.append(f'create {wid} {shape} {px} {py} {pz}')
                lines.append(f'size {wid} % {sx} {sy} {sz_}')
                lines.append(f'move {wid} % {px} {py} {pz} 0 {ry} 0')
                lines.append(f'color {wid} % {r} {g} {b}')
                lines.append(f'material {wid} % {mat_name}')
        return '\n'.join(lines)

# ═══════════════════════════════════════════════
#  ISOMETRIC RENDERER
# ═══════════════════════════════════════════════

class IsoRenderer:
    def __init__(self, surface):
        self.surf = surface
        self.angle = 45.0      # horizontal rotation degrees
        self.pitch = 30.0      # vertical tilt
        self.zoom = 18.0
        self.pan_x = W // 2
        self.pan_y = H // 2 - 60

    def world_to_screen(self, wx, wy, wz):
        """Convert 3D world pos to 2D screen pos"""
        rad = math.radians(self.angle)
        # rotate around Y
        rx = wx * math.cos(rad) - wz * math.sin(rad)
        rz = wx * math.sin(rad) + wz * math.cos(rad)
        # pitch
        pr = math.radians(self.pitch)
        sx = rx * self.zoom
        sy = (-wy * self.zoom * 0.6) + rz * self.zoom * math.sin(pr)
        return (int(sx + self.pan_x), int(sy + self.pan_y))

    def draw_terrain(self, terrain, brush_cx, brush_cz, brush_r, mode, hovered):
        surf = self.surf
        ts = terrain.tile_size
        gw, gd = terrain.gw, terrain.gd

        # draw back-to-front (painter's algorithm)
        # sort tiles by depth
        tiles = []
        rad = math.radians(self.angle)
        for z in range(gd):
            for x in range(gw):
                wx = (x + 0.5) * ts
                wz = (z + 0.5) * ts
                depth = wx * math.sin(rad) + wz * math.cos(rad)
                tiles.append((depth, x, z))
        tiles.sort()

        for _, x, z in tiles:
            h = terrain.get_height_px(x, z)
            mat = terrain.get_material(x, z)
            _, rgb = MATERIALS[mat]
            r, g, b = rgb

            wx0, wz0 = x * ts, z * ts
            wx1, wz1 = wx0 + ts, wz0 + ts

            # 4 top corners
            tl = self.world_to_screen(wx0, h, wz0)
            tr = self.world_to_screen(wx1, h, wz0)
            br = self.world_to_screen(wx1, h, wz1)
            bl = self.world_to_screen(wx0, h, wz1)

            # bottom corners
            tl0 = self.world_to_screen(wx0, 0, wz0)
            tr0 = self.world_to_screen(wx1, 0, wz0)
            br0 = self.world_to_screen(wx1, 0, wz1)
            bl0 = self.world_to_screen(wx0, 0, wz1)

            # brightness by slope
            slope = max(0, min(1, (h / max(1, terrain.max_h)) * 0.5 + 0.5))
            cr = min(255, int(r * slope + 20))
            cg = min(255, int(g * slope + 20))
            cb = min(255, int(b * slope + 20))

            # side faces (darker)
            sr = max(0, int(cr * 0.55))
            sg = max(0, int(cg * 0.55))
            sb = max(0, int(cb * 0.55))
            sr2 = max(0, int(cr * 0.40))
            sg2 = max(0, int(cg * 0.40))
            sb2 = max(0, int(cb * 0.40))

            # draw sides only if visible (height > 0)
            if h > 0:
                # left side
                pygame.draw.polygon(surf, (sr, sg, sb), [tl, bl, bl0, tl0])
                # front side
                pygame.draw.polygon(surf, (sr2, sg2, sb2), [bl, br, br0, bl0])

            # top face
            pygame.draw.polygon(surf, (cr, cg, cb), [tl, tr, br, bl])

            # brush overlay
            dx, dz = x - brush_cx, z - brush_cz
            dist = math.sqrt(dx*dx + dz*dz)
            in_brush = dist <= brush_r

            if in_brush:
                if mode == 'sculpt_up':
                    overlay = (100, 255, 150, 80)
                elif mode == 'sculpt_down':
                    overlay = (255, 100, 100, 80)
                else:
                    mat_c = MATERIALS[MAT_KEYS[terrain.paint[z, x] if terrain.paint[z, x] >= 0 else 0]][1]
                    overlay = (*mat_c, 100)
                ov = pygame.Surface((W, H), pygame.SRCALPHA)
                pygame.draw.polygon(ov, overlay, [tl, tr, br, bl])
                surf.blit(ov, (0, 0))

            # hovered tile highlight
            if hovered and hovered == (x, z):
                pygame.draw.polygon(surf, (255, 255, 255), [tl, tr, br, bl], 2)

            # grid line
            pygame.draw.polygon(surf, (0, 0, 0), [tl, tr, br, bl], 1)

    def screen_to_tile(self, mx, my, terrain):
        """Ray-cast screen pos to nearest tile (approximate)"""
        best = None
        best_dist = 1e9
        ts = terrain.tile_size
        for z in range(terrain.gd):
            for x in range(terrain.gw):
                h = terrain.get_height_px(x, z)
                cx = (x + 0.5) * ts
                cz = (z + 0.5) * ts
                sp = self.world_to_screen(cx, h, cz)
                d = (sp[0] - mx) ** 2 + (sp[1] - my) ** 2
                if d < best_dist:
                    best_dist = d
                    best = (x, z)
        if best_dist < (self.zoom * ts) ** 2:
            return best
        return None

# ═══════════════════════════════════════════════
#  SETTINGS PANEL (tkinter side window)
# ═══════════════════════════════════════════════

class SettingsPanel:
    def __init__(self, terrain, regenerate_cb):
        self.terrain = terrain
        self.regen_cb = regenerate_cb
        self.root = None
        self._vals = {}

    def open(self):
        if self.root and self.root.winfo_exists():
            self.root.lift()
            return
        self.root = tk.Toplevel()
        self.root.title('Terrain Settings')
        self.root.configure(bg='#1a1d27')
        self.root.geometry('320x620')
        self.root.resizable(False, False)

        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TLabel', background='#1a1d27', foreground='#aab', font=('Courier', 10))
        style.configure('TScale', background='#1a1d27')
        style.configure('TButton', background='#2a2d3e', foreground='#4fffb0', font=('Courier', 10, 'bold'))
        style.configure('TFrame', background='#1a1d27')

        f = ttk.Frame(self.root)
        f.pack(fill='both', expand=True, padx=12, pady=12)

        def lbl(text): ttk.Label(f, text=text).pack(anchor='w', pady=(8,0))
        def slider(key, from_, to, default, res=1):
            v = tk.DoubleVar(value=default)
            self._vals[key] = v
            row = ttk.Frame(f)
            row.pack(fill='x')
            sc = ttk.Scale(row, from_=from_, to=to, variable=v, orient='horizontal')
            sc.pack(side='left', fill='x', expand=True)
            lv = ttk.Label(row, width=6)
            lv.pack(side='left')
            def upd(*_): lv.config(text=f'{v.get():.{0 if res>=1 else 2}f}')
            v.trace_add('write', upd); upd()
            return v

        lbl('Grid Width'); slider('gw', 4, 48, self.terrain.gw)
        lbl('Grid Depth'); slider('gd', 4, 48, self.terrain.gd)
        lbl('Tile Size');  slider('ts', 2, 12, self.terrain.tile_size)
        lbl('Max Height'); slider('mh', 2, 64, self.terrain.max_h)
        lbl('Noise Scale'); slider('scale', 1, 30, 10, res=0.1)
        lbl('Octaves');    slider('oct', 1, 8, 4)
        lbl('Persistence'); slider('pers', 10, 90, 50, res=0.01)
        lbl('Water Level'); slider('water', 0, 60, 25, res=0.01)
        lbl('Seed');       slider('seed', 0, 99999, 42)

        mtn = tk.BooleanVar(value=True)
        self._vals['mtn'] = mtn
        cb = tk.Checkbutton(f, text='Mountains', variable=mtn, bg='#1a1d27', fg='#4fffb0',
                            selectcolor='#2a2d3e', activebackground='#1a1d27', font=('Courier', 10))
        cb.pack(anchor='w', pady=(10,0))

        lbl('Biome Preset')
        bf = ttk.Frame(f); bf.pack(fill='x', pady=4)
        for b in BIOMES:
            ttk.Button(bf, text=b, command=lambda bname=b: self.apply_biome(bname)).pack(side='left', padx=2)

        lbl('World ID'); slider('wid', 1, 10, 1)

        ttk.Button(f, text='▶  REGENERATE', command=self.do_regen).pack(fill='x', pady=(16,0))

    def apply_biome(self, name):
        b = BIOMES[name]
        if 'mh' in self._vals: self._vals['mh'].set(b['max_h'])
        if 'scale' in self._vals: self._vals['scale'].set(b['scale']*100)
        if 'oct' in self._vals: self._vals['oct'].set(b['octaves'])
        if 'pers' in self._vals: self._vals['pers'].set(b['persistence']*100)
        if 'water' in self._vals: self._vals['water'].set(b['water']*100)
        if 'mtn' in self._vals: self._vals['mtn'].set(b['mountains'])

    def do_regen(self):
        v = self._vals
        gw = int(v['gw'].get()) if 'gw' in v else self.terrain.gw
        gd = int(v['gd'].get()) if 'gd' in v else self.terrain.gd
        self.terrain.gw = gw
        self.terrain.gd = gd
        self.terrain.heights = np.zeros((gd, gw), dtype=np.float32)
        self.terrain.paint = np.full((gd, gw), -1, dtype=np.int8)
        self.terrain.tile_size = int(v.get('ts', tk.DoubleVar(value=4)).get())
        self.terrain.max_h = int(v.get('mh', tk.DoubleVar(value=16)).get())
        self.terrain.world_id = int(v.get('wid', tk.DoubleVar(value=1)).get())
        scale = v.get('scale', tk.DoubleVar(value=10)).get() / 100
        octaves = int(v.get('oct', tk.DoubleVar(value=4)).get())
        persistence = v.get('pers', tk.DoubleVar(value=50)).get() / 100
        water = v.get('water', tk.DoubleVar(value=25)).get() / 100
        seed = int(v.get('seed', tk.DoubleVar(value=42)).get())
        mountains = v.get('mtn', tk.BooleanVar(value=True)).get()
        self.terrain.generate(scale=scale, octaves=octaves, persistence=persistence,
                              lacunarity=2.0, seed=seed, mountains=mountains, water_level=water)
        self.regen_cb()

# ═══════════════════════════════════════════════
#  MAIN APP
# ═══════════════════════════════════════════════

class App:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((W, H))
        pygame.display.set_caption('Terrain Generator 3D')
        self.clock = pygame.time.Clock()

        self.terrain = Terrain(20, 20)
        self.renderer = IsoRenderer(self.screen)
        self.settings = SettingsPanel(self.terrain, self.on_regen)

        # init tkinter root (hidden)
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()

        self.mode = 'sculpt_up'  # sculpt_up, sculpt_down, paint
        self.brush_r = 2.0
        self.brush_strength = 0.015
        self.active_mat = 0   # index into MAT_KEYS
        self.hovered = None
        self.brush_cx = 0
        self.brush_cz = 0

        self.dragging = False
        self.rotating = False
        self.panning = False
        self.last_mouse = (0, 0)

        self.font = pygame.font.SysFont('Courier', 13)
        self.font_big = pygame.font.SysFont('Courier', 16, bold=True)
        self.status = 'Ready — press S to open Settings'

        self.dirty = True  # needs redraw

    def on_regen(self):
        self.dirty = True
        self.status = f'Regenerated {self.terrain.gw}×{self.terrain.gd} terrain'

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_TAB:
                    modes = ['sculpt_up', 'sculpt_down', 'paint']
                    self.mode = modes[(modes.index(self.mode) + 1) % len(modes)]
                    self.status = f'Mode: {self.mode}'
                elif event.key == pygame.K_g:
                    self.export_commands()
                elif event.key == pygame.K_s:
                    threading.Thread(target=self.settings.open, daemon=True).start()
                    self.tk_root.update()
                elif event.key == pygame.K_r:
                    self.terrain.generate()
                    self.status = 'Terrain reset'
                    self.dirty = True
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                    self.brush_r = min(8, self.brush_r + 0.5)
                    self.status = f'Brush radius: {self.brush_r:.1f}'
                elif event.key == pygame.K_MINUS:
                    self.brush_r = max(0.5, self.brush_r - 0.5)
                    self.status = f'Brush radius: {self.brush_r:.1f}'
                elif event.key == pygame.K_e:
                    self.mode = 'sculpt_up'
                elif event.key == pygame.K_q:
                    self.mode = 'sculpt_down'
                elif event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4,
                                   pygame.K_5, pygame.K_6, pygame.K_7, pygame.K_8,
                                   pygame.K_9, pygame.K_0):
                    idx = int(pygame.key.name(event.key))
                    if idx == 0: idx = 10
                    if idx - 1 < len(MAT_KEYS):
                        self.active_mat = idx - 1
                        self.mode = 'paint'
                        self.status = f'Paint: {MAT_KEYS[self.active_mat]}'

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    self.dragging = True
                elif event.button == 3:
                    self.rotating = True
                    self.last_mouse = event.pos
                elif event.button == 2:
                    self.panning = True
                    self.last_mouse = event.pos
                elif event.button == 4:
                    self.renderer.zoom = min(40, self.renderer.zoom * 1.1)
                    self.dirty = True
                elif event.button == 5:
                    self.renderer.zoom = max(5, self.renderer.zoom * 0.9)
                    self.dirty = True

            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1: self.dragging = False
                if event.button == 3: self.rotating = False
                if event.button == 2: self.panning = False

            elif event.type == pygame.MOUSEMOTION:
                mx, my = event.pos
                if self.rotating:
                    dx, dy = mx - self.last_mouse[0], my - self.last_mouse[1]
                    self.renderer.angle += dx * 0.4
                    self.renderer.pitch = max(10, min(80, self.renderer.pitch + dy * 0.3))
                    self.last_mouse = event.pos
                    self.dirty = True
                elif self.panning:
                    dx, dy = mx - self.last_mouse[0], my - self.last_mouse[1]
                    self.renderer.pan_x += dx
                    self.renderer.pan_y += dy
                    self.last_mouse = event.pos
                    self.dirty = True

                # hover tile
                tile = self.renderer.screen_to_tile(mx, my, self.terrain)
                if tile != self.hovered:
                    self.hovered = tile
                    self.dirty = True
                if tile:
                    self.brush_cx, self.brush_cz = tile

        return True

    def update(self):
        mx, my = pygame.mouse.get_pos()
        if self.dragging and self.hovered:
            x, z = self.hovered
            if 'sculpt' in self.mode:
                direction = 1 if self.mode == 'sculpt_up' else -1
                self.terrain.sculpt(x, z, self.brush_r, self.brush_strength, direction)
                self.dirty = True
            elif self.mode == 'paint':
                self.terrain.paint_tile(x, z, self.brush_r, self.active_mat)
                self.dirty = True

        # update tkinter
        try:
            self.tk_root.update()
        except Exception:
            pass

    def draw(self):
        if not self.dirty:
            return
        self.dirty = False

        self.screen.fill((14, 15, 20))

        # draw terrain
        self.renderer.draw_terrain(
            self.terrain,
            self.brush_cx, self.brush_cz, self.brush_r,
            self.mode, self.hovered
        )

        # ── HUD ──
        self.draw_hud()

        pygame.display.flip()

    def draw_hud(self):
        s = self.screen
        # top bar
        pygame.draw.rect(s, (21, 23, 31), (0, 0, W, 36))
        pygame.draw.line(s, (42, 45, 62), (0, 36), (W, 36))

        title = self.font_big.render('⛰ TERRAIN GEN 3D', True, (79, 255, 176))
        s.blit(title, (12, 9))

        mode_colors = {'sculpt_up': (100,255,150), 'sculpt_down': (255,100,100), 'paint': (124,111,255)}
        mc = mode_colors.get(self.mode, (200,200,200))
        mode_surf = self.font.render(f'[TAB] Mode: {self.mode}  [+/-] Brush: {self.brush_r:.1f}  [S] Settings  [G] Export  [R] Reset', True, mc)
        s.blit(mode_surf, (240, 11))

        # material palette (bottom)
        bh = 48
        pygame.draw.rect(s, (21, 23, 31), (0, H - bh, W, bh))
        pygame.draw.line(s, (42, 45, 62), (0, H - bh), (W, H - bh))
        sw = 36
        for i, key in enumerate(MAT_KEYS):
            _, rgb = MATERIALS[key]
            rx = 10 + i * (sw + 6)
            ry = H - bh + 6
            color = rgb
            pygame.draw.rect(s, color, (rx, ry, sw, sw - 4), border_radius=4)
            if i == self.active_mat:
                pygame.draw.rect(s, (255, 255, 255), (rx - 2, ry - 2, sw + 4, sw), 2, border_radius=5)
            lbl = self.font.render(str((i + 1) % 10), True, (200, 200, 200))
            s.blit(lbl, (rx + 2, ry + 2))

        # status bar
        st = self.font.render(self.status, True, (100, 110, 140))
        s.blit(st, (10 + len(MAT_KEYS) * (sw + 6) + 10, H - 30))

        # controls hint
        hint = self.font.render('RMB: Rotate  MMB/Scroll: Pan/Zoom  E/Q: Raise/Lower  1-9: Materials', True, (60, 65, 90))
        s.blit(hint, (10, H - 14))

    def export_commands(self):
        cmds = self.terrain.generate_commands()
        count = cmds.count('\ncreate ')
        self.status = f'Generated {count} parts — choose save location'
        self.dirty = True

        def save():
            path = filedialog.asksaveasfilename(
                defaultextension='.txt',
                filetypes=[('Text', '*.txt'), ('All', '*.*')],
                title='Save Commands',
                initialfile='terrain_commands.txt'
            )
            if path:
                with open(path, 'w') as f:
                    f.write(cmds)
                self.status = f'Saved {count} parts → {os.path.basename(path)}'
                self.dirty = True
            # also copy to clipboard via tkinter
            try:
                self.tk_root.clipboard_clear()
                self.tk_root.clipboard_append(cmds)
                self.tk_root.update()
            except Exception:
                pass

        threading.Thread(target=save, daemon=True).start()

    def run(self):
        running = True
        while running:
            running = self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(FPS)
        pygame.quit()
        try:
            self.tk_root.destroy()
        except Exception:
            pass

if __name__ == '__main__':
    App().run()
