#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比例尺自动标定  scale_calibrate.py
=================================================
从矢量 PDF 文字层直读"1:N"比例 + 与渲染出的 jpg 大小推算 m/px。

数学:
  比例 1:N  → PDF 上 1 pt 对应实际 (N/72) inch = (N/72)×0.0254 m
  jpg 是 PDF 渲染产物，jpg_w = pdf_w × (DPI/72)
  m/px = m/pt × pt/px = (N/72)×0.0254 × (pdf_w/jpg_w)

兜底链:
  1) 用户手动传入 --scale → 直接用
  2) PDF 矢量文字层 + jpg 大小 → 自动算
  3) 同站 site 标注的 dimension_val + polyline 配对 (calibrate_scale)
  4) 都没有 → 返回 None + source="missing" (规则引擎走 review_required)
"""
import os, re, json, argparse
from collections import Counter

INCH_M = 0.0254
RATIO_RE = re.compile(r"1\s*[:：]\s*(\d{2,4})")


def from_pdf(pdf_path, jpg_size=None, page=0):
    """从 PDF 第 page 页读比例 + 算 m/px。

    jpg_size: (w_px, h_px)，由 CVAT 标注或上游渲染指定；若为 None 仅返回 m/pt。
    返回 (scale_m_per_px or None, info_dict)。
    """
    import fitz
    info = {"source": "pdf_vector_text", "pdf": os.path.basename(pdf_path), "page": page}
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return None, {**info, "error": f"pdf 打开失败: {e}"}
    if page >= len(doc):
        return None, {**info, "error": f"页 {page} 超出 PDF 页数 {len(doc)}"}
    pg = doc[page]
    r = pg.rect
    info["pdf_size_pt"] = [r.width, r.height]
    text = pg.get_text("text")
    info["has_text_layer"] = bool(text.strip())
    if not info["has_text_layer"]:
        doc.close()
        return None, {**info, "error": "PDF 该页无矢量文字层（栅格 PDF）"}

    # 找所有 1:N
    matches = [int(x) for x in RATIO_RE.findall(text)]
    # 过滤合理范围(站厅层常见 1:50~1:500,总平面 1:500~1:2000)
    ratios = [n for n in matches if 20 <= n <= 2000]
    info["ratios_found"] = ratios
    if not ratios:
        doc.close()
        return None, {**info, "error": "PDF 文字层未找到 '1:N' 比例标识"}

    # 主图比例 = 出现频次最高;频次相同时取较小值(主图比缩略图更详细)
    cnt = Counter(ratios)
    primary = sorted(cnt.items(), key=lambda x: (-x[1], x[0]))[0][0]
    info["primary_ratio"] = primary
    info["ratio_freq"] = dict(cnt)
    m_per_pt = (primary / 72.0) * INCH_M
    info["m_per_pt"] = m_per_pt

    if jpg_size is None:
        doc.close()
        return None, {**info, "warn": "未传 jpg_size,无法直接给 m/px;仅返回 m/pt"}

    jpg_w = float(jpg_size[0])
    scale = m_per_pt * (r.width / jpg_w)
    info["jpg_size_px"] = list(jpg_size)
    info["rendered_dpi"] = round(jpg_w / r.width * 72)
    info["m_per_px"] = scale
    doc.close()
    return scale, info


def resolve_scale(pdf=None, jpg_size=None, manual=None, page=0):
    """整合多路径,按优先级出结果。

    返回 (scale, info)，info["source"] 标明来源:
      "manual" / "pdf_vector_text" / "missing"
    """
    if manual is not None and manual > 0:
        return float(manual), {"source": "manual", "m_per_px": float(manual)}
    if pdf:
        s, info = from_pdf(pdf, jpg_size=jpg_size, page=page)
        if s is not None:
            return s, info
        # 失败但保留 info 以便上游展示
        return None, {**info, "source": "pdf_failed"}
    return None, {"source": "missing", "warn": "无 PDF 也无手动 scale,无法标定"}


def main():
    ap = argparse.ArgumentParser(description="比例尺自动标定(PDF → m/px)")
    ap.add_argument("pdf", help="矢量 PDF 路径")
    ap.add_argument("--jpg-size", nargs=2, type=int, default=None,
                   metavar=("W", "H"), help="对应 jpg 像素大小(算 m/px 必传)")
    ap.add_argument("--page", type=int, default=0)
    a = ap.parse_args()
    scale, info = from_pdf(a.pdf, jpg_size=tuple(a.jpg_size) if a.jpg_size else None, page=a.page)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if scale:
        print(f"\n✅ m/px = {scale:.6f}")
    else:
        print(f"\n❌ 标定失败")


if __name__ == "__main__":
    main()
