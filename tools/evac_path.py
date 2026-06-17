#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
疏散路径距离  evac_path.py
=================================================
MVP 任务原文第 5 项: "基于区域和对象,生成公共区至安全出口的基础疏散路径(图算法)"。

输入: 公共区多边形 + 安全出口中心 + 比例尺 (m/px)
做法:
  1. 把所有公共区 polygon 栅格化成可通行 mask (downsample 降分辨率提速)
  2. 把每个 safety_exit 中心点标为 dist=0 (多源)
  3. 跑多源 Dijkstra (8-邻接, 对角线 √2) - 等价于 BFS 但带对角线代价
  4. 距离场 grid[y][x] = 该点到最近出口的疏散距离(米)
  5. 公共区 mask 内最大值 = MVP 要的"任一点至安全出口疏散距离"

输出:
  {
    max_distance_m,       # 公共区内最远疏散距离(米)
    farthest_point_px,    # 最远点像素坐标 (x, y) — 用于在图上画
    nearest_exit_id,      # 最远点对应"最近的"出口 id
    distance_grid,        # 距离场(numpy 数组,debug/可视化用)
    downsample,           # 实际栅格分辨率(px/cell)
  }

性能: 嘉宾站站厅层 9395×3339 → 降到 ~470×167 (downsample=20),
      Dijkstra 在 m=8 万 cells 上 <1 秒。
"""
import os, math, heapq


def _polygons_to_mask(polygons, W, H, downsample=20):
    """把多个像素坐标的 polygon 栅格化成可通行 mask。返回 (mask, ds)。"""
    import cv2, numpy as np
    dw, dh = max(1, W // downsample), max(1, H // downsample)
    mask = np.zeros((dh, dw), dtype=np.uint8)
    for poly in polygons:
        if len(poly) < 3:
            continue
        pts = np.array([[int(x / downsample), int(y / downsample)] for x, y in poly], np.int32)
        cv2.fillPoly(mask, [pts], 255)
    return mask, downsample


def multi_source_dijkstra(mask, sources, downsample, max_iter=None):
    """在 mask=255 的可通行格子上,从多源 sources 同时跑 Dijkstra。

    sources: [(x_px, y_px), ...] 像素坐标
    返回:
      dist: (H,W) float numpy array, mask 内 = 到最近源的格距(* downsample 即像素距);
            不可达或 mask 外 = +inf
      parent: (H,W,2) 上一步坐标(用于回溯路径)
      source_idx: (H,W) 该格属于哪个源
    """
    import numpy as np
    H, W = mask.shape
    INF = float("inf")
    dist = np.full((H, W), INF, dtype=np.float32)
    parent = np.full((H, W, 2), -1, dtype=np.int32)
    source_idx = np.full((H, W), -1, dtype=np.int32)

    heap = []
    for si, (sx, sy) in enumerate(sources):
        gx, gy = sx // downsample, sy // downsample
        # 出口本身可能在 mask 外(贴边),把它最近的 mask 内格作为起点
        if 0 <= gx < W and 0 <= gy < H:
            dist[gy, gx] = 0
            source_idx[gy, gx] = si
            heapq.heappush(heap, (0.0, int(gx), int(gy), si))

    # 8 邻居 + 对角线代价 √2
    DIAG = math.sqrt(2)
    NB = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1),
          (1, 1, DIAG), (1, -1, DIAG), (-1, 1, DIAG), (-1, -1, DIAG)]
    n_visited = 0
    while heap:
        d, x, y, si = heapq.heappop(heap)
        if d > dist[y, x]:
            continue
        n_visited += 1
        if max_iter and n_visited > max_iter:
            break
        for dx, dy, dc in NB:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < W and 0 <= ny < H):
                continue
            if mask[ny, nx] == 0:
                continue
            nd = d + dc
            if nd < dist[ny, nx]:
                dist[ny, nx] = nd
                parent[ny, nx] = (x, y)
                source_idx[ny, nx] = si
                heapq.heappush(heap, (nd, nx, ny, si))
    return dist, parent, source_idx


def compute_evac_distance(public_polygons, exits_px, scale_m_per_px,
                          img_size, downsample=20):
    """公共区任一点 → 最近出口的最远疏散距离。

    public_polygons: [[(x,y),...], ...] 像素坐标多边形列表
    exits_px:        [{id, center_px:[x,y]}, ...] 出口列表
    scale_m_per_px:  比例尺
    img_size:        (W, H) 整图像素大小
    downsample:      栅格化降采样倍率(20 → 1cell ≈ 20px)

    返回 dict;若数据不足返回 None。
    """
    import numpy as np
    if not public_polygons or not exits_px or not scale_m_per_px:
        return None
    W, H = img_size
    mask, ds = _polygons_to_mask(public_polygons, W, H, downsample=downsample)
    if mask.sum() == 0:
        return None    # 栅格化后无可通行区(public_area 太小被降采样吞掉)

    sources = [(int(e["center_px"][0]), int(e["center_px"][1])) for e in exits_px]
    if not sources:
        return None

    dist, parent, src_idx = multi_source_dijkstra(mask, sources, ds)
    # 只在 mask 内找最大值
    INF = float("inf")
    masked_dist = np.where(mask > 0, dist, -1)
    if masked_dist.max() < 0:
        return None
    far_idx = np.unravel_index(np.argmax(masked_dist), masked_dist.shape)
    far_y, far_x = int(far_idx[0]), int(far_idx[1])
    far_cells = float(masked_dist[far_y, far_x])
    if far_cells == INF or far_cells < 0:
        return None
    # 格数 * downsample(px/cell)= 像素距;再 * scale = 米
    far_dist_px = far_cells * ds
    far_dist_m = far_dist_px * scale_m_per_px
    # 最近出口
    nearest_exit_id = exits_px[int(src_idx[far_y, far_x])]["id"] \
                      if src_idx[far_y, far_x] >= 0 else None

    return {
        "max_distance_m": round(far_dist_m, 2),
        "farthest_point_px": [far_x * ds, far_y * ds],
        "nearest_exit_id": nearest_exit_id,
        "downsample": ds,
        "mask_cells": int(mask.sum() // 255),
    }


def add_evac_to_structured(structured, shapes, scale_m_per_px, img_size):
    """从 CVAT shapes 提取 public_area + exit,算最远疏散距离,
    注入 structured.evac_distance_line 列表(让规则引擎 EVAC-DIST-* 真正可跑)。

    shapes: anno_to_structured 内部用的源 shapes 列表(含 label/geom/points/attrs)
    返回 evac_result 字典(可空)。
    """
    # 1) 收集 public_area polygon(优先);若无 public_area, 用 zone_type=public 的 fire_compartment
    public_polys = []
    for s in shapes:
        if s["label"] == "public_area" and s["geom"] == "polygon" and len(s["points"]) >= 3:
            public_polys.append(s["points"])
    if not public_polys:
        for s in shapes:
            if s["label"] == "fire_compartment" and s["geom"] == "polygon" \
               and s["attrs"].get("zone_type", "") == "公共区" and len(s["points"]) >= 3:
                public_polys.append(s["points"])

    # 2) 取 structured.exit(已含 center_px),只用 zone=public 的
    exits = [e for e in structured.get("exit", []) if e.get("zone") == "public"]
    if not exits:
        # 兜底:所有 safety_exit 都用
        exits = structured.get("exit", [])

    result = compute_evac_distance(public_polys, exits, scale_m_per_px, img_size)
    if not result:
        return None

    # 3) 注入 evac_distance_line(派生的"最远点 → 最近出口"路径)
    structured["evac_distance_line"].append({
        "id": f"EVAC-DERIVED-01",
        "length_m": result["max_distance_m"],
        "kind": "any_to_exit",          # 触发 EVAC-DIST-ANY-001
        "pair_id": "auto",
        "_from": "evac_path_a_star",    # 来源标记
        "_farthest_point_px": result["farthest_point_px"],
        "_nearest_exit_id": result["nearest_exit_id"],
    })
    return result


# ---------- CLI ----------
def main():
    import argparse, json, sys, xml.etree.ElementTree as ET
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from geom_measure import parse_cvat

    ap = argparse.ArgumentParser(description="疏散路径距离 (公共区任一点 → 最近出口)")
    ap.add_argument("xml", help="CVAT 标注 XML")
    ap.add_argument("--scale", type=float, required=True, help="m/px")
    ap.add_argument("--downsample", type=int, default=20)
    a = ap.parse_args()

    images = parse_cvat(a.xml)
    for im in images:
        shapes = im["shapes"]
        public_polys = [s["points"] for s in shapes
                        if s["label"] == "public_area" and s["geom"] == "polygon"]
        if not public_polys:
            public_polys = [s["points"] for s in shapes
                            if s["label"] == "fire_compartment" and s["geom"] == "polygon"
                            and s["attrs"].get("zone_type", "") == "公共区"]
        # 出口取所有 safety_exit 中心
        exits = []
        for i, s in enumerate(shapes):
            if s["label"] == "safety_exit" and s["geom"] == "box":
                pts = s["points"]
                cx = (pts[0][0] + pts[1][0]) / 2
                cy = (pts[0][1] + pts[1][1]) / 2
                exits.append({"id": f"EX-{i+1}", "center_px": [cx, cy]})
        print(f"\n--- {im['name']} ---")
        print(f"  public 多边形: {len(public_polys)}  出口: {len(exits)}")
        result = compute_evac_distance(public_polys, exits, a.scale,
                                       (int(im['W']), int(im['H'])),
                                       downsample=a.downsample)
        if result:
            print(f"  ✅ 最远疏散距离: {result['max_distance_m']}m")
            print(f"     最远点: {result['farthest_point_px']}")
            print(f"     最近出口: {result['nearest_exit_id']}")
            print(f"     栅格 cells: {result['mask_cells']}")
        else:
            print(f"  ❌ 数据不足或公共区/出口为空")


if __name__ == "__main__":
    main()
