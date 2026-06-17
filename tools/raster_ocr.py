#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
栅格 OCR 兜底  raster_ocr.py
=================================================
当 vector_extract.py 无法用(栅格 PDF 无文字层)时, 用 tesseract OCR 找
图上的"防火分区面积"数字,通过几何匹配关联到 fire_compartment polygon。

设计要点:
- **只 OCR 数字 + '.'**(whitelist 0123456789.):
  · 不依赖 chi_sim 中文包(用户机器可能没装)
  · 误识别少,速度快
- **过滤合理范围**:50 < value < 50000 ㎡(防火分区面积常识)
- **几何匹配**:数字位置落在哪个 fire_compartment polygon 内,就赋给那个 fc;
  同 polygon 内多个数字取最大值(通常是"3861.71"这种,而不是"30"页码)

返回 list of (cx, cy, value) — value 单位假定就是 ㎡(图上写的就是 ㎡)。

用法 (单独跑):
  python raster_ocr.py 图.jpg [--psm 11] [--whitelist "0123456789."]
"""
import os, sys, re, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def extract_numbers_with_pos(image_pil, psm=11, min_val=200, max_val=10000):
    """OCR 整图,返回 [(cx, cy, value), ...]。

    image_pil: PIL.Image (RGB)
    psm:       Page Segmentation Mode(11=稀疏文本,适合 CAD 图)
    min_val/max_val: 数值范围过滤
    """
    import pytesseract
    from tesseract_init import ensure_tesseract
    ok, _ = ensure_tesseract()
    if not ok:
        raise RuntimeError("Tesseract 未安装或探测失败 — 见 docs/client_guide.html §1.2")

    cfg = f'--psm {psm} -c tessedit_char_whitelist=0123456789.'
    data = pytesseract.image_to_data(image_pil, config=cfg,
                                     output_type=pytesseract.Output.DICT)
    out = []
    for i, txt in enumerate(data["text"]):
        t = (txt or "").strip()
        if not t:
            continue
        # 提取浮点数(可能 OCR 识别成"3861.71"或"3861" 或"3861.71.")
        m = re.findall(r"\d+(?:\.\d+)?", t)
        for s in m:
            try:
                v = float(s)
            except ValueError:
                continue
            if min_val <= v <= max_val:
                cx = data["left"][i] + data["width"][i] / 2
                cy = data["top"][i] + data["height"][i] / 2
                out.append((cx, cy, v))
    return out


def _point_in_polygon(pt, poly_pts):
    """射线法判定。poly_pts: [(x,y),...]"""
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


def assign_numbers_to_compartments(numbers, fc_records, require_decimal=True):
    """把 OCR 出的数字按几何位置关联到 fire_compartment polygon。

    设计院写防火分区面积通常带小数(如 3861.71㎡),而 CAD 标的尺寸是整数毫米(如 15400)。
    require_decimal=True 时只保留小数数字 → 大幅过滤尺寸噪声。

    numbers:    [(cx, cy, value), ...]
    fc_records: [{id, polygon_pts:[(x,y),...]}, ...]
    返回 {fc_id: median_value_m2}  (同 fc 内多个数字取中位数,避免被极端值主导)
    """
    import statistics
    out = {}
    for fc in fc_records:
        poly = fc["polygon_pts"]
        if len(poly) < 3:
            continue
        candidates = []
        for cx, cy, v in numbers:
            if not _point_in_polygon((cx, cy), poly):
                continue
            # 要求带小数(过滤尺寸毫米数,它们都是整数)
            if require_decimal and v == int(v):
                continue
            candidates.append(v)
        if candidates:
            out[fc["id"]] = round(statistics.median(candidates), 2)
    return out


def fill_area_m2_design(structured, fc_records, image_pil, verbose=True):
    """对 structured 里 area_m2_design 为空的 fire_compartment, 用 OCR 兜底填上。

    fc_records: 同适配器内部派生时一并保存的 fc polygon 索引,格式:
                [{id, polygon_pts:[(x,y),...]}, ...]
    返回填充计数。
    """
    needs_ocr = [fc for fc in structured.get("fire_compartment", [])
                 if not fc.get("area_m2_design")]
    if not needs_ocr:
        if verbose:
            print("  ⏭ 所有 fire_compartment 都已有 area_m2_design,无需 OCR")
        return 0
    if verbose:
        print(f"  OCR 整图找数字 (psm=11, whitelist=0-9.)...")
    nums = extract_numbers_with_pos(image_pil)
    if verbose:
        print(f"  识别到 {len(nums)} 个候选数字 (50-50000 范围)")
    assigned = assign_numbers_to_compartments(nums, fc_records)

    filled = 0
    for fc in structured["fire_compartment"]:
        if fc.get("area_m2_design"):
            continue
        if fc["id"] in assigned:
            fc["area_m2_design"] = assigned[fc["id"]]
            fc["_area_m2_source"] = "raster_ocr"
            # area_m2 优先级: design > geom; 此前可能是 None,补上
            if not fc.get("area_m2"):
                fc["area_m2"] = assigned[fc["id"]]
            filled += 1
            if verbose:
                print(f"    [{fc['id']}] OCR 补 area_m2_design = {assigned[fc['id']]}㎡")
    return filled


def main():
    ap = argparse.ArgumentParser(description="栅格 OCR 找面积数字")
    ap.add_argument("img", help="图片路径(jpg/png/pdf)")
    ap.add_argument("--psm", type=int, default=11)
    ap.add_argument("--min", type=float, default=50)
    ap.add_argument("--max", type=float, default=50000)
    a = ap.parse_args()

    # 支持 PDF: 用 fitz 渲染
    from PIL import Image
    import numpy as np
    if a.img.lower().endswith(".pdf"):
        import fitz
        doc = fitz.open(a.img)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(200/72, 200/72))
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4: arr = arr[:, :, :3]
        img = Image.fromarray(arr)
        doc.close()
    else:
        import cv2
        bgr = cv2.imdecode(np.fromfile(a.img, dtype=np.uint8), cv2.IMREAD_COLOR)
        img = Image.fromarray(bgr[:, :, ::-1])

    print(f"图尺寸: {img.size}")
    nums = extract_numbers_with_pos(img, psm=a.psm, min_val=a.min, max_val=a.max)
    print(f"识别到 {len(nums)} 个数字:")
    for cx, cy, v in nums[:30]:
        print(f"  ({cx:.0f}, {cy:.0f})  {v}")
    if len(nums) > 30:
        print(f"  ... 另 {len(nums)-30} 个")


if __name__ == "__main__":
    main()
