#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强预标注  prelabel_pro.py
=================================================
在 fire_anno_tool.py 的基础上把"画框占位"升级成"带语义、带内容、可审核"：

  ① 语义细分：文字/线/区域不再一锅烩，按 内容(OCR) / 几何 / 颜色 / 图类型 分到 schema 各子类。
  ② 内容填充：OCR(可选) 读文字框内容写入 text_content；fire_compartment 预填 zone_type。
  ③ 辅助人工确认：
       - source 分级：高置信=auto(浅色,默认信任)  低置信=auto_review(醒目,重点审)
       - 颜色编码叠加预览：待审项高亮
       - 确认清单 _review.txt：主动列出"疑似漏分区/数字没配上线/类型是猜的"等待办
       - --qc：预标完自动跑质检

OCR 优雅降级：装了 tesseract 就读内容并按内容分类；没装则文字框留空、统一待审，
            线/区域照常按几何+颜色细分。装上 tesseract 重跑即生效。

用法：
  python app/backend/prelabel_pro.py <矢量PDF> [输出目录]
        [--page 0|all] [--dpi 200] [--type auto|site|hall] [--ocr] [--qc]
"""
import os, sys, re, json, argparse
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 复用 fire_anno_tool 的 schema（标签表/设备房关键词/中文映射）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tools")))
try:
    from fire_anno_tool import LABELS, EQUIP_ROOM_KW
except Exception:
    LABELS = {}
    EQUIP_ROOM_KW = ["机房", "泵房", "配电", "控制室", "通信", "信号", "环控", "气瓶",
                     "变电", "电缆", "风室", "管理", "票务", "站长", "卫生间", "厕所"]

# 文字内容分类关键词
VENT_KW = ["敞口", "侧出", "排风", "新风", "活塞", "风亭", "风井"]
BLDG_KW = ["高层", "住宅", "耐火", "商业", "办公", "多层", "厂房", "仓库"]
ROOM_KW = EQUIP_ROOM_KW + ["站厅", "站台", "通道", "出入口", "大厅", "设备", "用房", "室"]
NUM_RE = re.compile(r"^\d+(\.\d+)?m?$")
ZH_RE = re.compile(r"[一-鿿]")


# ---------------- 中文路径安全读写 ----------------
def _imread(path, cv2, np):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def _imwrite(path, img, cv2):
    ext = os.path.splitext(path)[1] or ".jpg"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(path)


# ---------------- 图纸类型 ----------------
def guess_type(pdf_path, dtype):
    if dtype != "auto":
        return dtype
    name = os.path.basename(pdf_path)
    if "总平面" in name or "总图" in name:
        return "site"
    if "站厅" in name or "站台" in name:
        return "hall"
    return "site"


# ---------------- 文字块定位（无文字层，靠小笔画聚类）----------------
def cluster_text_boxes(draws, scale, gap=12, min_members=4):
    small = []
    for d in draws:
        r = d["rect"]
        w = (r.x1 - r.x0) * scale; h = (r.y1 - r.y0) * scale
        if 0 < w < 60 and 0 < h < 60:
            small.append([r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale])
    if not small:
        return []
    n = len(small); parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    cell = 40; grid = defaultdict(list)
    for i, b in enumerate(small):
        grid[(int((b[0] + b[2]) / 2 // cell), int((b[1] + b[3]) / 2 // cell))].append(i)
    for (cx, cy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh += grid.get((cx + dx, cy + dy), [])
        for i in idxs:
            for j in neigh:
                if i < j:
                    bi, bj = small[i], small[j]
                    if (bi[0] - gap < bj[2] and bj[0] - gap < bi[2] and
                            bi[1] - gap < bj[3] and bj[1] - gap < bi[3]):
                        union(i, j)
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    out = []
    for g in groups.values():
        if len(g) < min_members:
            continue
        x0 = min(small[i][0] for i in g); y0 = min(small[i][1] for i in g)
        x1 = max(small[i][2] for i in g); y1 = max(small[i][3] for i in g)
        if (x1 - x0) < 8 or (y1 - y0) < 6 or (x1 - x0) > 1200:
            continue
        out.append((x0, y0, x1, y1))
    return out


# ---------------- 线候选（带颜色）----------------
def line_candidates(draws, scale, top=60):
    lines = []
    for d in draws:
        col = d.get("color") or (0, 0, 0)
        for it in d["items"]:
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                x0 *= scale; y0 *= scale; x1 *= scale; y1 *= scale
                L = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                if L > 120 and (abs(y1 - y0) < 1.5 or abs(x1 - x0) < 1.5):
                    is_blue = (len(col) == 3 and col[2] > 0.5 and col[0] < 0.3)
                    lines.append((x0, y0, x1, y1, L, is_blue))
    lines.sort(key=lambda t: -t[4])
    return lines[:top]


# ---------------- 区域候选 ----------------
def region_candidates(draws, scale, Wp, Hp, cv2, np):
    mask = np.zeros((Hp, Wp), np.uint8)
    for d in draws:
        for it in d["items"]:
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                cv2.line(mask, (int(x0 * scale), int(y0 * scale)),
                         (int(x1 * scale), int(y1 * scale)), 255, 1)
            elif it[0] == "re":
                r = it[1]
                cv2.rectangle(mask, (int(r.x0 * scale), int(r.y0 * scale)),
                              (int(r.x1 * scale), int(r.y1 * scale)), 255, 1)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    cnts, _ = cv2.findContours(closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    A = Wp * Hp
    for c in cnts:
        a = cv2.contourArea(c)
        if a < A * 0.0008 or a > A * 0.05:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.01 * peri, True)
        if len(approx) < 4 or len(approx) > 12:
            continue
        xs = [p[0][0] for p in approx]; ys = [p[0][1] for p in approx]
        if (max(xs) - min(xs)) > Wp * 0.75 or (max(ys) - min(ys)) > Hp * 0.75:
            continue
        polys.append(dict(poly=[(int(p[0][0]), int(p[0][1])) for p in approx], area=float(a)))
    polys.sort(key=lambda p: -p["area"])
    return polys[:30]


# ---------------- OCR（可选） ----------------
def ocr_words(base_img, np):
    """整图 OCR 一次，返回 [(cx,cy,text)...]；无 tesseract 返回 None。"""
    try:
        import pytesseract, io
        from PIL import Image
        cfg = "--psm 11"
        capW = 2400
        f = min(1.0, capW / float(base_img.shape[1]))
        import cv2
        small = cv2.resize(base_img, None, fx=f, fy=f) if f < 1 else base_img
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        data = pytesseract.image_to_data(Image.fromarray(rgb), config=cfg,
                                         output_type=pytesseract.Output.DICT)
        inv = 1.0 / f
        out = []
        for i in range(len(data["text"])):
            t = (data["text"][i] or "").strip()
            if not t:
                continue
            x = (data["left"][i] + data["width"][i] / 2) * inv
            y = (data["top"][i] + data["height"][i] / 2) * inv
            out.append((x, y, t))
        return out
    except Exception:
        return None


def text_in_box(words, box):
    """收集落在框内的 OCR 词，拼成内容。"""
    if not words:
        return ""
    x0, y0, x1, y1 = box
    hits = [t for (cx, cy, t) in words if x0 <= cx <= x1 and y0 <= cy <= y1]
    return "".join(hits)


def text_in_poly(words, poly, cv2, np):
    if not words:
        return ""
    cnt = np.array(poly, np.int32)
    hits = [t for (cx, cy, t) in words if cv2.pointPolygonTest(cnt, (float(cx), float(cy)), False) >= 0]
    return "".join(hits)


# ---------------- 分类规则 ----------------
def classify_text(content, group):
    """返回 (label, conf)。content 为空(无OCR)时低置信待审。"""
    if not content:
        return ("val_text", 0.3)
    if NUM_RE.match(content):
        return ("dimension_val" if group == "site" else "val_text", 0.75)
    if ZH_RE.search(content):
        if any(k in content for k in VENT_KW):
            return ("vent_meta", 0.8)
        if group == "site" and any(k in content for k in BLDG_KW):
            return ("building_meta", 0.8)
        if any(k in content for k in ROOM_KW):
            return ("room_title", 0.8)
        return ("room_title" if group == "hall" else "building_meta", 0.5)
    return ("val_text", 0.5)


def classify_region(area_ratio, group, inner_text):
    """返回 (label, attrs, conf)。"""
    if group == "site":
        if area_ratio > 0.01:
            return ("surrounding_building", {}, 0.55)
        return ("station_exit_ground", {}, 0.45)
    # hall
    zone, zc = "公共区", 0.4
    if inner_text and any(k in inner_text for k in EQUIP_ROOM_KW):
        zone, zc = "无人区", 0.6
    return ("fire_compartment", {"zone_type": zone}, max(0.5, zc))


def classify_line(is_blue, length, group, Wp):
    """返回 (label, conf)。"""
    if group == "site":
        return ("fire_clearance_line", 0.5)
    # hall：很长的更像疏散距离线，短的像净宽尺寸线
    if length > Wp * 0.18:
        return ("evac_distance_line", 0.45)
    return ("width_dimension_line", 0.5)


# ---------------- 写 CVAT ----------------
SITE_LABELS = ["station_exit_ground", "vent_group_ground", "surrounding_building",
               "fire_clearance_line", "building_meta", "vent_meta", "dimension_val"]
HALL_LABELS = ["fire_compartment", "commercial_shop", "fire_door", "stair_escalator",
               "draft_curtain", "evac_distance_line", "width_dimension_line",
               "room_title", "val_text"]


def write_cvat(out_xml, img_name, W, H, group, items):
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"
    meta = ET.SubElement(root, "meta"); job = ET.SubElement(meta, "job")
    labels_el = ET.SubElement(job, "labels")
    for nm in (SITE_LABELS if group == "site" else HALL_LABELS):
        le = ET.SubElement(labels_el, "label")
        ET.SubElement(le, "name").text = nm
    img = ET.SubElement(root, "image", id="0", name=img_name, width=str(W), height=str(H))
    for it in items:
        src = "auto" if it["conf"] >= 0.7 else "auto_review"
        if it["geom"] == "box":
            x0, y0, x1, y1 = it["coords"]
            el = ET.SubElement(img, "box", label=it["label"], source=src, occluded="0",
                               xtl=f"{x0:.1f}", ytl=f"{y0:.1f}", xbr=f"{x1:.1f}", ybr=f"{y1:.1f}")
        elif it["geom"] == "polyline":
            pts = ";".join(f"{x:.1f},{y:.1f}" for x, y in it["coords"])
            el = ET.SubElement(img, "polyline", label=it["label"], source=src, occluded="0", points=pts)
        else:  # polygon
            pts = ";".join(f"{x:.1f},{y:.1f}" for x, y in it["coords"])
            el = ET.SubElement(img, "polygon", label=it["label"], source=src, occluded="0", points=pts)
        for k, v in it.get("attrs", {}).items():
            a = ET.SubElement(el, "attribute", name=k); a.text = str(v)
    ET.ElementTree(root).write(out_xml, encoding="utf-8", xml_declaration=True)


# ---------------- 叠加预览（颜色编码）----------------
def overlay(base_img, items, out_path, cv2, np):
    im = base_img.copy()
    for it in items:
        review = it["conf"] < 0.7
        if it["geom"] == "box":
            x0, y0, x1, y1 = [int(v) for v in it["coords"]]
            col = (0, 140, 255) if review else (0, 170, 170)  # 待审=橙, 确定=青
            cv2.rectangle(im, (x0, y0), (x1, y1), col, 2)
        elif it["geom"] == "polyline":
            pts = np.array(it["coords"], np.int32)
            cv2.polylines(im, [pts], False, (255, 0, 255), 2)
        else:
            pts = np.array(it["coords"], np.int32)
            col = (0, 0, 230) if review else (0, 200, 0)  # 待审分区=红, 较确定=绿
            cv2.polylines(im, [pts], True, col, 3)
    _imwrite(out_path, im, cv2)


# ---------------- 确认清单 ----------------
def build_checklist(items, group, used_ocr):
    lines = []
    n_review = sum(1 for it in items if it["conf"] < 0.7)
    lines.append(f"待人工确认候选: {n_review} / {len(items)}")
    if not used_ocr:
        lines.append("⚠️ 未启用 OCR：所有文字框内容为空、统一待审。装 tesseract 后加 --ocr 重跑可自动填内容并分类。")
    # 分区类型猜测
    comp = [it for it in items if it["label"] == "fire_compartment"]
    if comp:
        lines.append(f"防火分区 {len(comp)} 个，zone_type 均为推测值，请逐一确认(公共区/无人区/有人值守区)。")
    # 设备房 vs 分区
    room_eq = [it for it in items if it["label"] == "room_title"
               and any(k in it.get("attrs", {}).get("text_content", "") for k in EQUIP_ROOM_KW)]
    if group == "hall" and room_eq:
        has_equip_zone = any(it.get("attrs", {}).get("zone_type") in ("无人区", "有人值守区") for it in comp)
        if not has_equip_zone:
            lines.append(f"检测到 {len(room_eq)} 个设备房(机房/泵房等)，但没有设备区分区 —— 疑似漏标 fire_compartment(无人区/有人值守区)。")
    # 类型统计
    cnt = Counter(it["label"] for it in items)
    lines.append("各类候选数: " + ", ".join(f"{k}={v}" for k, v in cnt.items()))
    return "\n".join(lines)


# ---------------- 主流程 ----------------
def run(pdf_path, out_dir, page_no, dpi, dtype, use_ocr, do_qc):
    import fitz, cv2, numpy as np
    out_dir = out_dir or os.path.dirname(os.path.abspath(pdf_path))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    group = guess_type(pdf_path, dtype)
    doc = fitz.open(pdf_path)
    pages = range(len(doc)) if page_no == "all" else [int(page_no)]
    scale = dpi / 72.0

    written = []
    for pg in pages:
        page = doc[pg]
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        img_name = f"{base}_p{pg}.jpg"
        img_path = os.path.join(out_dir, img_name)
        pix.save(img_path)
        Wp, Hp = pix.width, pix.height
        base_img = _imread(img_path, cv2, np)
        draws = page.get_drawings()
        print(f"[页{pg}] {Wp}x{Hp}px  矢量对象 {len(draws)}  图类型={group}")

        words = ocr_words(base_img, np) if use_ocr else None
        used_ocr = words is not None
        if use_ocr and not used_ocr:
            print("[页{}] ⚠️ OCR 不可用(未装 tesseract?)，降级为无内容模式。".format(pg))

        items = []
        # 文字
        for box in cluster_text_boxes(draws, scale):
            content = text_in_box(words, box)
            label, conf = classify_text(content, group)
            attrs = {"text_content": content} if content else {}
            items.append(dict(geom="box", label=label, coords=box, attrs=attrs, conf=conf))
        # 线
        for (x0, y0, x1, y1, L, is_blue) in line_candidates(draws, scale):
            label, conf = classify_line(is_blue, L, group, Wp)
            if is_blue:
                conf = min(0.85, conf + 0.2)  # 蓝色线更可能是尺寸/防火专用线
            items.append(dict(geom="polyline", label=label, coords=[(x0, y0), (x1, y1)], attrs={}, conf=conf))
        # 区域
        A = Wp * Hp
        for r in region_candidates(draws, scale, Wp, Hp, cv2, np):
            inner = text_in_poly(words, r["poly"], cv2, np)
            label, attrs, conf = classify_region(r["area"] / A, group, inner)
            items.append(dict(geom="polygon", label=label, coords=r["poly"], attrs=attrs, conf=conf))

        out_xml = os.path.join(out_dir, f"{base}_p{pg}_prelabel.xml")
        write_cvat(out_xml, img_name, Wp, Hp, group, items)
        out_ov = os.path.join(out_dir, f"{base}_p{pg}_overlay.jpg")
        overlay(base_img, items, out_ov, cv2, np)
        out_rv = os.path.join(out_dir, f"{base}_p{pg}_review.txt")
        with open(out_rv, "w", encoding="utf-8") as f:
            f.write(build_checklist(items, group, used_ocr))

        n_auto = sum(1 for it in items if it["conf"] >= 0.7)
        print(f"[页{pg}] 候选 {len(items)}(高置信 {n_auto} / 待审 {len(items)-n_auto})  OCR={'on' if used_ocr else 'off'}")
        print(f"[页{pg}] 预标 {out_xml}")
        print(f"[页{pg}] 预览 {out_ov}（橙/红=待审，青/绿=较确定）")
        print(f"[页{pg}] 确认清单 {out_rv}")
        written.append(out_xml)

    if do_qc:
        print("\n[自检] 调用 qc 质检 ...")
        try:
            from fire_anno_tool import qc
            for x in written:
                qc(x, "auto")
        except Exception as e:
            print("  (qc 跳过:", e, ")")
    print("\n[完成] 人工只需在 CVAT 打开预标 xml：核对待审项、补全文字内容、确认分区类型。")
    return written


def main():
    ap = argparse.ArgumentParser(description="增强预标注 (语义细分+内容+可审核)")
    ap.add_argument("pdf"); ap.add_argument("out", nargs="?")
    ap.add_argument("--page", default="0", help="页码，或 all")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--type", default="auto", choices=["auto", "site", "hall"])
    ap.add_argument("--ocr", action="store_true", help="启用 OCR 读文字内容(需 tesseract)")
    ap.add_argument("--qc", action="store_true", help="预标完自动跑质检")
    a = ap.parse_args()
    run(a.pdf, a.out, a.page, a.dpi, a.type, a.ocr, a.qc)


if __name__ == "__main__":
    main()
