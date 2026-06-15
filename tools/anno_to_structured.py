#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结构化适配器  anno_to_structured.py
=================================================
把 CVAT 标注(+ 几何量测) → rules/schema.md 定义的"规则消费对象"。
这是 MVP 的中枢：规则引擎只认下游对象，不认 CVAT 源标注。

输入：
  - CVAT XML（一张图）
  - scale (m/px)
  - station_meta（项目级配置：type/transfer_lines/project_all_doors_class_A 等）

输出（每张图）：
  structured = {
    station: {...},
    fire_compartment: [...],
    evac_distance_line: [...],
    door: [...],
    exit: [...],
    exit_pair: [...],
    corridor: [...],
    shop: [...], shop_pair: [...],
    fire_shutter: [...],
    vent_pair: [...],
    building_clearance: [...],
  }
"""
import os, sys, math, json, argparse
from itertools import combinations
from shapely.geometry import Polygon, LineString, Point

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geom_measure import (
    parse_cvat, calibrate_scale, polygon_area_m2, polyline_length_m,
    door_net_width_m, corridor_clear_width_m, box_center,
    associate_width_lines, _num_from_text, points_to_shape,
)


# ==================== zone_type 转换 ====================

ZONE_MAP = {
    "公共区": "public",
    "无人区": "equipment_unmanned",
    "有人值守区": "equipment_staffed",
}


# ==================== 派生规则 ====================

def _direction_group(cx, cy, image_cx, image_cy, tol_deg=15):
    """按相对图中心的方位角聚类。±15° 内为同一组。"""
    dx = cx - image_cx; dy = cy - image_cy
    if abs(dx) < 1 and abs(dy) < 1:
        return "center"
    angle = math.degrees(math.atan2(dy, dx))  # -180~180
    return str(int(angle // tol_deg))


def _point_in_polygon(pt, poly_pts):
    """射线法。poly_pts: [(x,y),...]."""
    x, y = pt
    n = len(poly_pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly_pts[i]; xj, yj = poly_pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside


# ==================== 核心适配 ====================

def adapt_image(img, scale, station_meta=None):
    """对单张图(parse_cvat 的一项)产出 structured 对象。"""
    station_meta = station_meta or {}
    W = img["W"]; H = img["H"]
    image_cx, image_cy = W / 2, H / 2
    shapes = img["shapes"]

    # ---- station 对象 ----
    station = {
        "type": station_meta.get("type", "underground"),
        "height_m": station_meta.get("height_m", 0),
        "transfer_lines": station_meta.get("transfer_lines", 1),
        "project_all_doors_class_A": station_meta.get("project_all_doors_class_A", False),
        "public_zone": {"area_m2": None, "commercial_shops": [], "exits": []},
        "equipment_zone": {"exits": []},
    }

    structured = {
        "station": station,
        "fire_compartment": [], "door": [], "exit": [], "exit_pair": [],
        "corridor": [], "shop": [], "shop_pair": [], "fire_shutter": [],
        "evac_distance_line": [], "vent_pair": [], "building_clearance": [],
    }

    # ---- 1) fire_compartment ----
    fc_records = []   # (idx, label_shape, fc_dict, polygon_obj)
    for i, s in enumerate(shapes):
        if s["label"] != "fire_compartment" or s["geom"] != "polygon" or len(s["points"]) < 3:
            continue
        zt_src = s["attrs"].get("zone_type", "")
        zt = ZONE_MAP.get(zt_src, "public")
        area_geom = polygon_area_m2(s["points"], scale)
        area_claim = _num_from_text(s["attrs"].get("area_m2_design", ""))
        fc_id = f"FC-{len(structured['fire_compartment'])+1:02d}"
        # area_m2 优先级：area_m2_design > 几何派生
        area_m2 = area_claim if area_claim else area_geom
        fc = {
            "id": fc_id, "area_m2": area_m2, "area_m2_design": area_claim,
            "area_m2_geom": area_geom, "zone_type": zt,
            "is_shared_concourse": bool(station_meta.get("is_shared_concourse", False)),
            "exits": [], "effective_exits": [],
        }
        structured["fire_compartment"].append(fc)
        fc_records.append((i, s, fc, Polygon(s["points"])))
        # station.public_zone.area_m2 取所有公共区之和
        if zt == "public":
            station["public_zone"]["area_m2"] = (station["public_zone"]["area_m2"] or 0) + (area_m2 or 0)

    # ---- 2) safety_exit → exit + 落到 fire_compartment.exits ----
    exit_records = []   # (idx, label_shape, exit_dict, center_xy, fc_index_or_None)
    for i, s in enumerate(shapes):
        if s["label"] != "safety_exit" or s["geom"] != "box":
            continue
        cx, cy = box_center(s["points"])
        # 找它落在哪个 fc(中心点在多边形内或≤30px)
        which_fc = None
        for fidx, _, fc, fp in fc_records:
            if _point_in_polygon((cx, cy), shapes[fidx]["points"]) or fp.distance(Point(cx, cy)) < 30:
                which_fc = (fidx, fc); break
        zone = which_fc[1]["zone_type"] if which_fc else "public"
        ex = {
            "id": f"EX-{len(structured['exit'])+1:02d}",
            "leads_to": "ground" if zone == "public" else "other",   # 简化:公共区出口默认通地面
            "direction_group": _direction_group(cx, cy, image_cx, image_cy),
            "zone": zone,
            "pair_id": s["attrs"].get("pair_id", ""),
            "center_px": [cx, cy],
        }
        structured["exit"].append(ex)
        exit_records.append((i, s, ex, (cx, cy), which_fc))
        if which_fc:
            which_fc[1]["exits"].append(ex)
            which_fc[1]["effective_exits"].append(ex)
        if zone == "public":
            station["public_zone"]["exits"].append(ex)
        else:
            station["equipment_zone"]["exits"].append(ex)

    # ---- 3) exit_pair ----
    for (e1, e2) in combinations(structured["exit"], 2):
        same = e1["direction_group"] == e2["direction_group"]
        dx = e1["center_px"][0] - e2["center_px"][0]
        dy = e1["center_px"][1] - e2["center_px"][1]
        dist_m = math.hypot(dx, dy) * scale if scale else None
        relation = "same_direction" if same else ("adjacent" if dist_m and dist_m <= 50 else "other")
        if relation == "other":
            continue
        structured["exit_pair"].append({
            "id": f"EXP-{e1['id']}-{e2['id']}",
            "exit_a": e1["id"], "exit_b": e2["id"],
            "relation": relation, "distance_m": dist_m,
        })

    # ---- 4) door ----
    width_pairs = associate_width_lines(shapes)  # [{line, target_label, target_idx, dist_px}, ...]
    door_width_map = {p["target_idx"]: p["line"]["points"] for p in width_pairs if p["target_label"] == "fire_door"}
    for i, s in enumerate(shapes):
        if s["label"] != "fire_door" or s["geom"] != "box":
            continue
        cx, cy = box_center(s["points"])
        # 净宽
        cw = None
        if i in door_width_map:
            cw = door_net_width_m(door_width_map[i], scale)
        # 落在哪个 fc
        which_zone = "public"
        on_firewall = False
        for fidx, _, fc, fp in fc_records:
            inside = _point_in_polygon((cx, cy), shapes[fidx]["points"])
            on_boundary = fp.exterior.distance(Point(cx, cy)) < 15
            if inside:
                which_zone = fc["zone_type"]
                if on_boundary:
                    on_firewall = True
                break
        # swing_dir 中文 → 英文枚举
        sd = s["attrs"].get("swing_dir", "")
        swing_direction = "evacuation" if "顺" in sd else ("reverse" if "逆" in sd else "evacuation")
        cls = s["attrs"].get("class", "")
        structured["door"].append({
            "id": f"DR-{len(structured['door'])+1:03d}",
            "clear_width_m": round(cw, 3) if cw else None,
            "swing_direction": swing_direction,
            "is_evacuation": True,           # 站厅层防火门默认作疏散用
            "position": "between_exits",     # 简化,真实需要更多上下文
            "zone": which_zone,
            "distance_to_nearest_exit_m": None,
            "class": cls or "unknown",
            "is_on_firewall": on_firewall,
            "is_always_open": False,
        })

    # ---- 5) corridor / stair / 闸机宽度 ----
    for p in width_pairs:
        if p["target_label"] == "fire_door":
            continue
        kind_map = {"stair_escalator": "evac_stair", "gate": "evac_corridor"}
        kind = kind_map.get(p["target_label"], "evac_corridor")
        cw = corridor_clear_width_m(p["line"]["points"], scale)
        structured["corridor"].append({
            "id": f"COR-{len(structured['corridor'])+1:02d}",
            "kind": kind,
            "clear_width_m": round(cw, 3) if cw else None,
            "length_m": None,
        })

    # ---- 6) evac_distance_line ----
    for s in shapes:
        if s["label"] != "evac_distance_line" or s["geom"] != "polyline":
            continue
        # 优先用标注文字 text_content,否则用几何长度
        v = _num_from_text(s["attrs"].get("text_content", ""))
        length_m = v if v else polyline_length_m(s["points"], scale)
        structured["evac_distance_line"].append({
            "id": f"EVAC-{len(structured['evac_distance_line'])+1:02d}",
            "length_m": length_m, "kind": "any_to_exit",
            "pair_id": s["attrs"].get("pair_id", ""),
        })

    # ---- 7) commercial_shop ----
    for i, s in enumerate(shapes):
        if s["label"] != "commercial_shop" or s["geom"] != "polygon":
            continue
        area = polygon_area_m2(s["points"], scale)
        shop = {"id": f"SHOP-{len(structured['shop'])+1:02d}", "area_m2": area}
        structured["shop"].append(shop)
        station["public_zone"]["commercial_shops"].append(shop)
    # shop_pair (商铺洞口间距,这里用中心点近似)
    shops_with_xy = []
    for i, s in enumerate(shapes):
        if s["label"] == "commercial_shop" and s["geom"] == "polygon":
            cx = sum(p[0] for p in s["points"]) / len(s["points"])
            cy = sum(p[1] for p in s["points"]) / len(s["points"])
            shops_with_xy.append((cx, cy))
    for (i, a), (j, b) in combinations(enumerate(shops_with_xy), 2):
        d = math.hypot(a[0]-b[0], a[1]-b[1]) * scale if scale else None
        structured["shop_pair"].append({
            "id": f"SP-{i+1}-{j+1}",
            "shop_a": structured["shop"][i]["id"], "shop_b": structured["shop"][j]["id"],
            "opening_distance_m": d,
        })

    # ---- 8) fire_shutter ----
    for i, s in enumerate(shapes):
        if s["label"] != "fire_shutter" or s["geom"] != "box":
            continue
        w_px = abs(s["points"][1][0] - s["points"][0][0])
        ow = w_px * scale if scale else None
        structured["fire_shutter"].append({
            "id": f"FS-{len(structured['fire_shutter'])+1:02d}",
            "opening_width_m": ow, "shutter_width_m": ow,   # 标注无单独"卷帘宽",用洞口宽
        })

    return structured


def adapt(xml_path, scale=None, station_meta=None, verbose=True):
    """对整份 CVAT XML 跑适配，逐张图产 structured。"""
    images = parse_cvat(xml_path)
    out = []
    for im in images:
        sc = scale if scale else (calibrate_scale(im["shapes"])[0] if calibrate_scale(im["shapes"]) else None)
        structured = adapt_image(im, sc, station_meta)
        if verbose:
            print(f"\n--- {im['name']}  scale={sc}m/px ---")
            for k in ["fire_compartment", "door", "exit", "exit_pair", "corridor",
                       "shop", "fire_shutter", "evac_distance_line"]:
                v = structured.get(k, [])
                if v:
                    print(f"  {k:24s} {len(v)}")
        out.append({"image": im["name"], "structured": structured})
    return out


def main():
    ap = argparse.ArgumentParser(description="CVAT 标注 → 规则引擎结构化对象")
    ap.add_argument("xml")
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--station-meta", default=None, help="JSON 文件")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    sm = json.load(open(a.station_meta, encoding="utf-8")) if a.station_meta else None
    out = adapt(a.xml, scale=a.scale, station_meta=sm)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n输出: {a.out}")


if __name__ == "__main__":
    main()
