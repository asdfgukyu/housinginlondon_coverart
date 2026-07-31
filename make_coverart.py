#!/usr/bin/env python3
"""
Housing in London 2026 — cover art generator.

Renders a stylish isometric 3D map of the 33 London boroughs, where each
borough is extruded to a height proportional to an input metric (default:
net additional dwellings, i.e. new homes added). Coloured with Paul Tol's
colour schemes and saved as a PNG with a transparent background.

--------------------------------------------------------------------------
HOW TO UPDATE THE DATA
--------------------------------------------------------------------------
Everything the picture depends on lives in ./data and in the CONFIG block
below. To refresh with newer figures:

  * data/borough_data.csv   -- one row per borough. Columns:
                                 code, name, year, <VALUE_COLUMN>
                               Edit the numbers, or regenerate it from
                               data/net_additions_raw.csv (see build_data.py),
                               or swap in any other per-borough metric.
  * VALUE_COLUMN            -- name of the numeric column to visualise.
  * data/london_boroughs.geojson -- borough boundaries (lon/lat). Rarely needs
                                     changing; boroughs are joined on `code`.

Run:  python make_coverart.py
Out:  housing_in_london_2026.png  (transparent background)
--------------------------------------------------------------------------
"""

import csv
import json
import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, to_rgb
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from shapely.geometry import shape

# =========================================================================
# CONFIG  -- tweak freely
# =========================================================================
GEOJSON   = "data/london_boroughs.geojson"
DATA_CSV  = "data/borough_data.csv"
JOIN_KEY  = "code"          # column shared by geojson properties + csv
VALUE_COLUMN = "net_additions"   # numeric column in DATA_CSV to visualise

OUTFILE   = "housing_in_london_2026.png"
DPI       = 300
FIG_INCHES = (12, 12)

STYLE     = "sketch"        # "clean" | "sketch" (hand-drawn / chalky / graphite)

PALETTE   = "iridescent"    # "sunset" | "YlOrBr" | "iridescent" | "bright_seq"
MAX_HEIGHT_KM = 17.0        # visual height of the tallest borough
MIN_HEIGHT_KM = 0.8         # floor so every borough is a visible slab
HEIGHT_GAMMA  = 0.85        # <1 lifts the smaller boroughs a little

# camera — north-aligned (azim=-90 keeps north pointing up), steep look-down
ELEV = 56.0
AZIM = -90.0

# lighting for the extruded side walls (azimuth the light comes FROM, degrees)
LIGHT_AZIM = 135.0
WALL_MIN, WALL_MAX = 0.50, 0.90   # brightness range across wall orientation
WALL_BASE_DARKEN  = 0.62          # ambient-occlusion: wall shade at the ground
WALL_SEGMENTS     = 22            # vertical subdivisions -> smooth wall gradient
TOP_SHADE = 1.0                   # brightness of the top faces
EDGE_TOP  = (0.10, 0.12, 0.16, 0.55)  # crisp outline, top faces only
EDGE_LW   = 0.4

SIMPLIFY_DEG = 0.00035     # geometry simplification (~25 m); lower = crisper
LABEL_TOP_N  = 0            # annotate the N tallest boroughs (0 = none)

# --- hand-drawn / chalky "sketch" style -------------------------------------
INK        = (0.17, 0.15, 0.14)   # pencil/ink outline colour
SKETCH_WOBBLE = (2.2, 90, 24)     # matplotlib path.sketch: (scale, length, randomness)
CHALK_DESAT   = 0.30              # pull fills toward grey (0=none, 1=full grey)
CHALK_LIGHTEN = 0.14              # pull fills toward white (pastel/chalk tint)
GRAIN_SIGMA   = 0.085             # chalk/graphite grain strength (0 = off)
GRAIN_SEED    = 7

if STYLE == "sketch":
    matplotlib.rcParams["path.sketch"] = SKETCH_WOBBLE
    matplotlib.rcParams["path.effects"] = []
    SIMPLIFY_DEG   = 0.0013        # chunkier, more cartoon-like shapes
    WALL_SEGMENTS  = 1             # flat cel-shaded walls (no internal seams to wobble)
    WALL_MIN, WALL_MAX = 0.66, 0.90
    WALL_BASE_DARKEN = 1.0
    EDGE_TOP  = (*INK, 0.95)       # bold hand-drawn outline on the tops
    EDGE_WALL = (*INK, 0.40)       # lighter sketch lines down the sides
    EDGE_LW   = 1.6
else:
    EDGE_WALL = (0, 0, 0, 0)

# =========================================================================
# Paul Tol colour schemes  (https://personal.sron.nl/~pault/)
# =========================================================================
TOL_SCHEMES = {
    "sunset": ['#364B9A', '#4A7BB7', '#6EA6CD', '#98CAE1', '#C2E4EF',
               '#EAECCC', '#FEDA8B', '#FDB366', '#F67E4B', '#DD3D2D', '#A50026'],
    "YlOrBr": ['#FFFFE5', '#FFF7BC', '#FEE391', '#FEC44F', '#FB9A29',
               '#EC7014', '#CC4C02', '#993404', '#662506'],
    "iridescent": ['#FEFBE9', '#F5F3C1', '#DDECBF', '#C2E3D2', '#A8D8DC',
                   '#8DCBE4', '#7BBCE7', '#88A5DD', '#9B8AC4', '#9A709E',
                   '#805770', '#684957', '#46353A'],
    "bright_seq": ['#4477AA', '#66CCEE', '#228833', '#CCBB44', '#EE6677', '#AA3377'],
}


def tol_cmap(name):
    return LinearSegmentedColormap.from_list(f"tol_{name}", TOL_SCHEMES[name], N=256)


# =========================================================================
# Data
# =========================================================================
def load_values():
    vals = {}
    with open(DATA_CSV) as f:
        for row in csv.DictReader(f):
            vals[row[JOIN_KEY]] = {
                "name": row.get("name", row[JOIN_KEY]),
                "value": float(row[VALUE_COLUMN]),
            }
    return vals


def load_geometries():
    with open(GEOJSON) as f:
        gj = json.load(f)
    geoms = {}
    for feat in gj["features"]:
        key = feat["properties"][JOIN_KEY]
        g = shape(feat["geometry"])
        if SIMPLIFY_DEG:
            g = g.simplify(SIMPLIFY_DEG, preserve_topology=True)
        geoms[key] = g
    return geoms


# =========================================================================
# Projection: lon/lat -> local kilometres (equirectangular about the centre)
# =========================================================================
def make_projector(geoms):
    xs, ys = [], []
    for g in geoms.values():
        minx, miny, maxx, maxy = g.bounds
        xs += [minx, maxx]
        ys += [miny, maxy]
    lon0 = (min(xs) + max(xs)) / 2
    lat0 = (min(ys) + max(ys)) / 2
    kx = 111.32 * math.cos(math.radians(lat0))
    ky = 111.32

    def proj(lon, lat):
        return (lon - lon0) * kx, (lat - lat0) * ky
    return proj


def polygon_pieces(geom):
    """Yield exterior rings (list of (lon,lat)) for Polygon / MultiPolygon."""
    if geom.geom_type == "Polygon":
        yield list(geom.exterior.coords)
    elif geom.geom_type == "MultiPolygon":
        for p in geom.geoms:
            yield list(p.exterior.coords)


# =========================================================================
# Render
# =========================================================================
def shade(rgb, factor):
    return tuple(min(1.0, c * factor) for c in rgb)


def chalkify(rgb):
    """Mute a colour toward grey, then toward white — a soft chalk/pastel tint."""
    if STYLE != "sketch":
        return rgb
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    r, g, b = (c + (lum - c) * CHALK_DESAT for c in rgb)
    return tuple(c + (1.0 - c) * CHALK_LIGHTEN for c in (r, g, b))


def main():
    values = load_values()
    geoms = load_geometries()
    proj = make_projector(geoms)
    cmap = tol_cmap(PALETTE)

    keys = [k for k in geoms if k in values]
    vmax = max(values[k]["value"] for k in keys)
    vmin = min(values[k]["value"] for k in keys)

    def height_of(v):
        t = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.0
        return MIN_HEIGHT_KM + (MAX_HEIGHT_KM - MIN_HEIGHT_KM) * (t ** HEIGHT_GAMMA)

    def color_of(v):
        t = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
        return chalkify(cmap(0.08 + 0.9 * t)[:3])   # trim the extreme-pale end

    # light direction in the xy plane
    la = math.radians(LIGHT_AZIM)
    light = (math.cos(la), math.sin(la))

    fig = plt.figure(figsize=FIG_INCHES)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_proj_type("ortho")

    # single face list so matplotlib depth-sorts walls + tops together
    faces, facecolors, edgecolors = [], [], []
    labels = []

    for k in keys:
        v = values[k]["value"]
        h = height_of(v)
        base_rgb = color_of(v)

        best_area = -1
        best_centroid = None
        for ring in polygon_pieces(geoms[k]):
            pts = [proj(lon, lat) for lon, lat in ring]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]

            # top face
            faces.append([(x, y, h) for x, y in zip(xs, ys)])
            facecolors.append((*shade(base_rgb, TOP_SHADE), 1.0))
            edgecolors.append(EDGE_TOP)

            # side walls: shaded by orientation to light, plus a vertical
            # gradient (darker toward the ground) built from stacked segments
            for i in range(len(pts) - 1):
                x0, y0 = pts[i]
                x1, y1 = pts[i + 1]
                dx, dy = x1 - x0, y1 - y0
                seg = math.hypot(dx, dy)
                if seg == 0:
                    continue
                nx, ny = dy / seg, -dx / seg          # outward-ish normal
                lit = (nx * light[0] + ny * light[1] + 1) / 2   # 0..1
                b = WALL_MIN + (WALL_MAX - WALL_MIN) * lit
                for s in range(WALL_SEGMENTS):
                    z0 = h * s / WALL_SEGMENTS
                    z1 = h * (s + 1) / WALL_SEGMENTS
                    # 0 at ground -> 1 at top
                    frac = (s + 0.5) / WALL_SEGMENTS
                    vert = WALL_BASE_DARKEN + (1 - WALL_BASE_DARKEN) * frac
                    faces.append([(x0, y0, z0), (x1, y1, z0),
                                  (x1, y1, z1), (x0, y0, z1)])
                    facecolors.append((*shade(base_rgb, b * vert), 1.0))
                    edgecolors.append(EDGE_WALL)

            # track biggest piece for optional labelling
            area = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if area > best_area:
                best_area = area
                best_centroid = (sum(xs) / len(xs), sum(ys) / len(ys), h)

        labels.append((values[k]["name"], v, best_centroid))

    coll = Poly3DCollection(
        faces, facecolors=facecolors, edgecolors=edgecolors,
        linewidths=EDGE_LW, shade=False)
    coll.set_zsort("average")
    ax.add_collection3d(coll)

    # optional labels on the tallest boroughs
    if LABEL_TOP_N:
        for name, v, c in sorted(labels, key=lambda t: -t[1])[:LABEL_TOP_N]:
            if c:
                ax.text(c[0], c[1], c[2] + 1.0, name, ha="center", va="bottom",
                        fontsize=8, color="white", zorder=10, weight="bold")

    # frame the scene
    allx, ally = [], []
    for f in faces:
        for x, y, _ in f:
            allx.append(x); ally.append(y)
    pad = 3
    ax.set_xlim(min(allx) - pad, max(allx) + pad)
    ax.set_ylim(min(ally) - pad, max(ally) + pad)
    ax.set_zlim(0, MAX_HEIGHT_KM * 2.0)
    try:
        ax.set_box_aspect((max(allx) - min(allx),
                           max(ally) - min(ally),
                           MAX_HEIGHT_KM * 1.4))
    except Exception:
        pass

    ax.view_init(elev=ELEV, azim=AZIM)
    ax.set_axis_off()
    for pane in (ax.xaxis, ax.yaxis, ax.zaxis):
        pane.pane.set_visible(False)

    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    fig.savefig(OUTFILE, dpi=DPI, transparent=True,
                bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    autocrop(OUTFILE, pad_frac=0.03)
    if STYLE == "sketch" and GRAIN_SIGMA > 0:
        add_grain(OUTFILE, sigma=GRAIN_SIGMA, seed=GRAIN_SEED)
    print(f"wrote {OUTFILE}  ({len(keys)} boroughs, metric='{VALUE_COLUMN}', "
          f"palette='{PALETTE}', style='{STYLE}', range {vmin:.0f}-{vmax:.0f})")


def add_grain(path, sigma=0.085, seed=7):
    """Multiply a chalk/graphite grain over the opaque pixels only."""
    from PIL import Image
    im = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)
    rng = np.random.default_rng(seed)
    h, w = im.shape[:2]
    # fine tooth + a little low-frequency mottle for uneven chalk coverage
    fine = rng.normal(0, sigma, (h, w))
    coarse = rng.normal(0, sigma * 0.6, (max(1, h // 6), max(1, w // 6)))
    coarse = np.asarray(Image.fromarray(coarse).resize((w, h), Image.BILINEAR))
    grain = np.clip(1.0 + fine + coarse, 0.55, 1.15)[..., None]
    alpha = im[..., 3:4] > 0
    im[..., :3] = np.where(alpha, np.clip(im[..., :3] * grain, 0, 255), im[..., :3])
    Image.fromarray(im.astype(np.uint8), "RGBA").save(path)


def autocrop(path, pad_frac=0.03):
    """Crop transparent margins to the image's alpha bounding box (+ padding)."""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    bbox = im.split()[-1].getbbox()
    if not bbox:
        return
    im = im.crop(bbox)
    pad = int(max(im.size) * pad_frac)
    out = Image.new("RGBA", (im.size[0] + 2 * pad, im.size[1] + 2 * pad), (0, 0, 0, 0))
    out.paste(im, (pad, pad))
    out.save(path)


if __name__ == "__main__":
    main()
