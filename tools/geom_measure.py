#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
几何量测  geom_measure.py
=================================================
从 CVAT 标注 + 矢量图，把像素几何转成"实际米数"，供规则引擎消费：

- 比例尺标定 (m/pixel)：用图上已知尺寸数字 (dimension_val) 与对应尺寸线像素长配对，取中位数
- 防火分区面积 (m²)         : Shoelace 多边形面积 × scale²
- 防火门/通道净宽 (m)        : width_dimension_line 像素长 × scale，门净宽 = 洞口宽 − 0.15
- 对象间距 (m)              : 两个对象 polygon 最近距离 × scale，或两个 box 中心欧氏距离 × scale
- 任一对象点 → 最近安全出口直线距离 (m)：用于 EVAC-DIST 兜底（非绕墙路径，仅参考）

依赖: shapely (已在 requirements 里)
"""
import os, re, sys, math, json, argparse
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon, LineString, Point
from shapely.ops import nearest_points


# ----------------------------- 解析 CVAT -----------------------------

def parse_cvat(xml_path):
    """返回每张图: {name, W, H, shapes:[{label, geom_type, points, attrs}]}。"""
    root = ET.parse(xml_path).getroot()
    out = []
    for im in root.findall(".//image"):
        shapes = []
        for el in list(im):
            t = el.tag
            if t == "box":
                pts = [(float(el.get("xtl")), float(el.get("ytl"))),
                       (float(el.get("xbr")), float(el.get("ybr")))]
            elif t in ("polygon", "polyline"):
                pts = [tuple(map(float, p.split(","))) for p in (el.get("points") or "").split(";") if p]
            else:
                continue
            attrs = {a.get("name"): (a.text or "").strip() for a in el.findall("attribute")}
            shapes.append({"label": el.get("label"), "geom": t, "points": pts, "attrs": attrs})
        out.append({"name": im.get("name"), "W": float(im.get("width") or 0),
                    "H": float(im.get("height") or 0), "shapes": shapes})
    return out


# ----------------------------- 比例尺 -----------------------------

def _num_from_text(t):
    """从 '15000mm' / '15.02m' / '面积 4967.59 ㎡' 抽第一个数字。"""
    if not t:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", t.replace(",", ""))
    return float(m.group(1)) if m else None


def calibrate_scale(shapes, dim_label_candidates=("dimension_val", "val_text")):
    """像素→米 标定。

    思路：把 dimension_val (或 val_text) 文字数字 与最近的 fire_clearance_line /
    width_dimension_line / evac_distance_line / 任意 polyline 像素长配对，取中位数 m/px。

    数字单位推断：
      - 含 'mm' 或长度 > 1000 → 毫米(mm)
      - 含 'm' 或 1 < len ≤ 1000 → 米(m)
      - 含 '㎡' 或 'm²' → 跳过 (面积值非长度)
    """
    dims = []   # (cx, cy, value_m, raw)
    for s in shapes:
        if s["label"] not in dim_label_candidates:
            continue
        t = s["attrs"].get("text_content", "")
        if "㎡" in t or "m²" in t or "平方" in t:
            continue
        v = _num_from_text(t)
        if v is None:
            continue
        # 单位推断
        if "mm" in t.lower() or (v >= 200 and "m" not in t.lower()):
            v_m = v / 1000.0   # mm
        else:
            v_m = v             # m
        if v_m <= 0 or v_m > 200:    # 异常值过滤
            continue
        pts = s["points"]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        dims.append((cx, cy, v_m, t))

    lines = []  # (cx, cy, length_px, label)
    for s in shapes:
        if s["geom"] != "polyline":
            continue
        pts = s["points"]
        if len(pts) < 2:
            continue
        L = sum(math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1]) for i in range(len(pts)-1))
        if L < 20:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        lines.append((cx, cy, L, s["label"]))

    pairs = []
    for dx, dy, vm, raw in dims:
        if not lines:
            break
        # 找距离数字最近的线
        nearest = min(lines, key=lambda L: (L[0]-dx)**2 + (L[1]-dy)**2)
        if nearest[2] > 5:
            pairs.append((vm / nearest[2], vm, nearest[2], raw, nearest[3]))

    if not pairs:
        return None, []
    pairs.sort(key=lambda x: x[0])
    median_scale = pairs[len(pairs)//2][0]
    return median_scale, pairs


# ----------------------------- 几何量测 -----------------------------

def polygon_area_m2(pts, scale_m_per_px):
    """Shoelace 多边形像素面积 × scale² → m²。"""
    if not scale_m_per_px or len(pts) < 3:
        return None
    n = len(pts)
    s = 0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i+1) % n]
        s += x1*y2 - x2*y1
    area_px = abs(s) / 2
    return area_px * (scale_m_per_px ** 2)


def polyline_length_m(pts, scale_m_per_px):
    if not scale_m_per_px or len(pts) < 2:
        return None
    L = sum(math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1]) for i in range(len(pts)-1))
    return L * scale_m_per_px


def box_center(pts):
    """box: [(x0,y0),(x1,y1)] → (cx,cy)。"""
    return ((pts[0][0]+pts[1][0])/2, (pts[0][1]+pts[1][1])/2)


def points_to_shape(label_kind, pts):
    """把 CVAT points 列表转成 shapely 对象。"""
    if label_kind == "box":
        x0, y0 = pts[0]; x1, y1 = pts[1]
        return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    if label_kind == "polygon":
        return Polygon(pts) if len(pts) >= 3 else None
    if label_kind == "polyline":
        return LineString(pts) if len(pts) >= 2 else None
    return None


def min_distance_m(geom_a, geom_b, scale_m_per_px):
    """两个 shapely 几何对象的最小像素距离 × scale。"""
    if geom_a is None or geom_b is None or not scale_m_per_px:
        return None
    return geom_a.distance(geom_b) * scale_m_per_px


# ----------------------------- 净宽（fire_door / corridor） -----------------------------

def door_net_width_m(width_line_pts, scale_m_per_px, opening_subtract_m=0.15):
    """门洞口宽 - 0.15 = 净宽（《详细版》§关键约定）。"""
    opening = polyline_length_m(width_line_pts, scale_m_per_px)
    if opening is None:
        return None
    return max(0.0, opening - opening_subtract_m)


def corridor_clear_width_m(width_line_pts, scale_m_per_px):
    """通道/楼梯净宽 = 尺寸线像素长 × scale（不扣减）。"""
    return polyline_length_m(width_line_pts, scale_m_per_px)


# ----------------------------- 关联：width_line ↔ 实体 -----------------------------

def associate_width_lines(shapes, max_dist_px=60):
    """把 width_dimension_line 与最近的 fire_door / stair_escalator / gate 关联。

    返回 [{line, target_label, target_idx, dist_px}, ...]
    """
    width_lines = [s for s in shapes if s["label"] == "width_dimension_line" and s["geom"] == "polyline"]
    candidates = [(i, s) for i, s in enumerate(shapes)
                  if s["label"] in ("fire_door", "stair_escalator", "gate") and s["geom"] == "box"]
    pairs = []
    for wl in width_lines:
        if not wl["points"]:
            continue
        # 用首尾点与中点 三个测试点
        pts = wl["points"]
        test_pts = [pts[0], pts[-1], (sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts))]
        best = None
        for idx, c in candidates:
            cx, cy = box_center(c["points"])
            # 用最小测试点到中心的距离
            d = min(math.hypot(tp[0]-cx, tp[1]-cy) for tp in test_pts)
            if best is None or d < best[1]:
                best = (idx, d, c["label"])
        if best and best[1] <= max_dist_px:
            pairs.append({"line": wl, "target_label": best[2], "target_idx": best[0], "dist_px": best[1]})
    return pairs


# ----------------------------- CLI -----------------------------

def measure(xml_path, out_json=None, verbose=True, scale_override=None):
    """对单个 CVAT XML 跑全量量测，输出每张图的 measurements。

    scale_override: 外部传入 m/px (用于 hall 标注无 dimension_val 的场景)。
    若为 None 则尝试 calibrate_scale 自动标定。
    """
    images = parse_cvat(xml_path)
    out = []
    for im in images:
        if scale_override is not None:
            scale, pairs = scale_override, []
        else:
            scale, pairs = calibrate_scale(im["shapes"])
        measurements = {
            "image": im["name"], "size_px": [im["W"], im["H"]],
            "scale_m_per_px": scale,
            "scale_pairs_used": len(pairs),
            "fire_compartments": [],
            "doors": [],
            "corridor_widths": [],
            "exits": [],
        }
        if verbose:
            print(f"\n--- {im['name']} ---")
            print(f"  比例尺: {scale:.4f} m/px" if scale else "  比例尺: 无法标定")

        # 防火分区面积
        for i, s in enumerate(im["shapes"]):
            if s["label"] == "fire_compartment" and s["geom"] == "polygon":
                area = polygon_area_m2(s["points"], scale)
                area_claim = _num_from_text(s["attrs"].get("area_m2_design", ""))
                measurements["fire_compartments"].append({
                    "idx": i, "zone_type": s["attrs"].get("zone_type", ""),
                    "area_m2_geom": round(area, 2) if area else None,
                    "area_m2_design": area_claim,
                })

        # 净宽（关联 width_line ↔ 实体）
        for ap in associate_width_lines(im["shapes"]):
            pts = ap["line"]["points"]
            opening = polyline_length_m(pts, scale)
            if ap["target_label"] == "fire_door":
                w = door_net_width_m(pts, scale)
                measurements["doors"].append({
                    "target_idx": ap["target_idx"], "kind": "fire_door",
                    "opening_m": round(opening, 3) if opening else None,
                    "clear_width_m": round(w, 3) if w else None,
                })
            else:
                w = corridor_clear_width_m(pts, scale)
                measurements["corridor_widths"].append({
                    "target_idx": ap["target_idx"], "kind": ap["target_label"],
                    "clear_width_m": round(w, 3) if w else None,
                })

        # safety_exit 位置（中心点）
        for i, s in enumerate(im["shapes"]):
            if s["label"] == "safety_exit" and s["geom"] == "box":
                cx, cy = box_center(s["points"])
                measurements["exits"].append({
                    "idx": i, "center_px": [cx, cy],
                    "pair_id": s["attrs"].get("pair_id", ""),
                })

        if verbose:
            print(f"  防火分区: {len(measurements['fire_compartments'])} 个")
            for fc in measurements["fire_compartments"][:5]:
                print(f"    [{fc['zone_type']}]  几何={fc['area_m2_geom']}㎡  声称={fc['area_m2_design']}㎡")
            door_widths = sorted(d["clear_width_m"] for d in measurements["doors"] if d["clear_width_m"])
            door_med = door_widths[len(door_widths)//2] if door_widths else None
            print(f"  防火门净宽: {len(measurements['doors'])} 条 (中位 {door_med if door_med else 'N/A'} m)")
            print(f"  通道宽度: {len(measurements['corridor_widths'])} 条")
            print(f"  安全出口: {len(measurements['exits'])} 个")
        out.append(measurements)

    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n输出: {out_json}")
    return out


def main():
    ap = argparse.ArgumentParser(description="CVAT 标注 几何量测")
    ap.add_argument("xml")
    ap.add_argument("--out", default=None)
    ap.add_argument("--scale", type=float, default=None,
                   help="外部传入 m/px (hall 无 dimension_val 时必填，常见值 0.005~0.05)")
    a = ap.parse_args()
    measure(a.xml, out_json=a.out, verbose=True, scale_override=a.scale)


if __name__ == "__main__":
    main()
