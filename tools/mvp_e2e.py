#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MVP 端到端  mvp_e2e.py
=================================================
MVP 任务 4 项一条命令贯通：

  CVAT 标注 + 底图
      → (adapter) 结构化对象
      → (rule_engine) 规则比对
      → 在底图上标注 FAIL 位置(红框/红线+违规原因)
      → 输出标注图 + JSON + 简易 Markdown 报告

用法：
  python mvp_e2e.py <CVAT.xml> <底图.jpg> <输出目录> [--scale 0.025]
                    [--station-meta meta.json]

要求：CVAT 标注的图名要与底图文件名一致（找第一张匹配的 image 即可）。
"""
import os, sys, json, argparse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anno_to_structured
import rule_engine
import scale_calibrate


# ---------- 中文路径安全读写图 ----------

def _imread(path):
    """中文路径安全读图;若 path 是 PDF 自动渲染第一页为内存 jpg 再解码。"""
    import cv2, numpy as np
    if path.lower().endswith(".pdf"):
        try:
            import fitz
        except ImportError:
            raise RuntimeError("读 PDF 需要 pymupdf: pip install pymupdf")
        doc = fitz.open(path)
        pg = doc[0]
        # 与训练时保持一致:200 DPI 渲染
        pix = pg.get_pixmap(matrix=fitz.Matrix(200 / 72.0, 200 / 72.0))
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        doc.close()
        if pix.n == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def _imwrite(path, img):
    import cv2
    ext = os.path.splitext(path)[1] or ".jpg"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(path)
    return ok


# ---------- 中文文字渲染 (cv2.putText 不支持中文) ----------

def _put_chinese(img, text, xy, color, font_size=22):
    """用 PIL 在图上画中文。"""
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    # 找系统字体
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    font_path = next((p for p in candidates if os.path.exists(p)), None)
    pil_img = Image.fromarray(img[..., ::-1])  # BGR→RGB
    draw = ImageDraw.Draw(pil_img)
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    # color: BGR → RGB
    rgb = (color[2], color[1], color[0])
    draw.text(xy, text, fill=rgb, font=font)
    return np.array(pil_img)[..., ::-1].copy()  # RGB→BGR


# ---------- 找出对应一张图的 shapes(用于在图上找几何位置) ----------

def parse_one_image(xml_path, img_basename):
    """返回与底图匹配的那张 image 的 shapes 列表。匹配规则:image name 含 stem 或全等。"""
    root = ET.parse(xml_path).getroot()
    stem = os.path.splitext(img_basename)[0]
    for im in root.findall(".//image"):
        nm = im.get("name", "")
        if nm == img_basename or stem in nm or os.path.basename(nm) == img_basename:
            return im
    # 退而求其次:返回第一张
    return root.find(".//image")


def get_shape_points(el):
    if el.tag == "box":
        return [(float(el.get("xtl")), float(el.get("ytl"))),
                (float(el.get("xbr")), float(el.get("ybr")))]
    if el.tag in ("polygon", "polyline"):
        return [tuple(map(float, p.split(","))) for p in (el.get("points") or "").split(";") if p]
    return None


# ---------- 把 FAIL findings 映射回图上几何位置 ----------

def annotate_failures(img, image_node, structured, findings, fail_only=True):
    """根据 findings 的 target_id（如 FC-02 / DR-005 / EX-01）找到对应 source shape，画红框。"""
    import cv2, numpy as np

    # 先拿 CVAT 源 shapes 的有序列表（与适配器内部顺序对齐）
    shapes = []
    for el in list(image_node):
        pts = get_shape_points(el)
        if pts:
            shapes.append({"label": el.get("label"), "geom": el.tag, "points": pts})

    # 建立 structured 对象 id → 源 shape 位置 的映射
    def find_shape(label_kind, n_target):
        """返回该类标签的第 n_target(从 1 起)个 shape。"""
        cnt = 0
        for s in shapes:
            if s["label"] == label_kind:
                cnt += 1
                if cnt == n_target:
                    return s
        return None

    id_to_shape = {}
    for i, fc in enumerate(structured["fire_compartment"]):
        s = find_shape("fire_compartment", i + 1)
        if s: id_to_shape[fc["id"]] = s
    for i, dr in enumerate(structured["door"]):
        s = find_shape("fire_door", i + 1)
        if s: id_to_shape[dr["id"]] = s
    for i, ex in enumerate(structured["exit"]):
        s = find_shape("safety_exit", i + 1)
        if s: id_to_shape[ex["id"]] = s
    for i, sh in enumerate(structured["shop"]):
        s = find_shape("commercial_shop", i + 1)
        if s: id_to_shape[sh["id"]] = s
    for i, ev in enumerate(structured["evac_distance_line"]):
        s = find_shape("evac_distance_line", i + 1)
        if s: id_to_shape[ev["id"]] = s
    for i, fs in enumerate(structured["fire_shutter"]):
        s = find_shape("fire_shutter", i + 1)
        if s: id_to_shape[fs["id"]] = s

    # 颜色（BGR）
    COL_FAIL = (0, 0, 230)         # 红
    COL_REVIEW = (0, 180, 230)     # 黄
    COL_PASS = (0, 160, 0)         # 绿

    annotated = img.copy()
    by_target = {}
    for f in findings:
        tid = f.get("target_id")
        if not tid:
            continue
        by_target.setdefault(tid, []).append(f)

    rendered_labels = 0
    for tid, fs in by_target.items():
        s = id_to_shape.get(tid)
        if not s:
            continue
        has_fail = any(x["passed"] is False for x in fs)
        has_review = any(x.get("review_required") for x in fs)
        if fail_only and not has_fail:
            continue
        col = COL_FAIL if has_fail else (COL_REVIEW if has_review else COL_PASS)
        pts = s["points"]
        if s["geom"] == "box":
            x0, y0 = map(int, pts[0]); x1, y1 = map(int, pts[1])
            import cv2
            cv2.rectangle(annotated, (x0, y0), (x1, y1), col, 4)
            label_pos = (x0, max(0, y0 - 30))
        elif s["geom"] == "polygon":
            import cv2
            arr = np.array(pts, np.int32)
            cv2.polylines(annotated, [arr], True, col, 4)
            label_pos = (int(arr[:, 0].min()), max(0, int(arr[:, 1].min()) - 30))
        else:
            import cv2
            arr = np.array(pts, np.int32)
            cv2.polylines(annotated, [arr], False, col, 4)
            label_pos = (int(arr[0][0]), max(0, int(arr[0][1]) - 30))
        # 标 ID + 第一条违规简短描述
        first_fail = next((x for x in fs if x["passed"] is False), fs[0])
        short = first_fail["rule_id"]
        annotated = _put_chinese(annotated, f"{tid} {short}", label_pos, col, font_size=26)
        rendered_labels += 1

    return annotated, rendered_labels


# ---------- 主流程 ----------

def run(xml_path, img_path, out_dir, scale=None, station_meta=None, rules_path=None,
        pdf_path=None):
    rules_path = rules_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "rules", "rules.json")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(img_path))[0]

    # 0) 比例尺标定 - 优先级:manual > pdf > review_required
    print("[0/4] 比例尺标定 ...")
    img_for_size = _imread(img_path)
    jpg_size = (img_for_size.shape[1], img_for_size.shape[0]) if img_for_size is not None else None
    scale, scale_info = scale_calibrate.resolve_scale(pdf=pdf_path, jpg_size=jpg_size, manual=scale)
    if scale:
        print(f"  ✅ scale = {scale:.6f} m/px  (source={scale_info['source']})")
        if scale_info.get("primary_ratio"):
            print(f"     PDF 比例 1:{scale_info['primary_ratio']}  "
                  f"渲染 DPI≈{scale_info.get('rendered_dpi')}")
    else:
        print(f"  ⚠️ 未标定: {scale_info.get('warn') or scale_info.get('error')}")
        print(f"     几何相关规则将走 review_required")

    # 1) adapter
    print("[1/4] CVAT 标注 → 结构化对象 ...")
    results = anno_to_structured.adapt(xml_path, scale=scale, station_meta=station_meta, verbose=False)
    target = None
    img_basename = os.path.basename(img_path)
    img_stem = os.path.splitext(img_basename)[0]
    for r in results:
        nm = r["image"]
        if nm == img_basename or img_stem in nm or os.path.splitext(os.path.basename(nm))[0] == img_stem:
            target = r; break
    if not target:
        target = results[0]
        print(f"  ⚠️ 没在标注里找到完全匹配的图,使用第一张: {target['image']}")
    structured = target["structured"]
    print(f"  fc={len(structured['fire_compartment'])}  door={len(structured['door'])}  "
          f"exit={len(structured['exit'])}  shop={len(structured['shop'])}  "
          f"evac={len(structured['evac_distance_line'])}")

    # 2) 规则引擎
    print("[2/4] 规则引擎评估 ...")
    findings = rule_engine.evaluate(rules_path, structured)
    summary = rule_engine.summarize(findings)
    print(f"  触发 {summary['total']} 条规则: PASS {summary['passed']}  FAIL {summary['failed']}  "
          f"REVIEW {summary['review_required']}")

    # 3) 标注 FAIL 到图上
    print("[3/4] 在底图标注违规位置 ...")
    img = _imread(img_path)
    if img is None:
        raise FileNotFoundError(f"读图失败: {img_path}")
    image_node = parse_one_image(xml_path, img_basename)
    annotated, n_labels = annotate_failures(img, image_node, structured, findings, fail_only=True)
    out_jpg = os.path.join(out_dir, f"{stem}_e2e_fail.jpg")
    _imwrite(out_jpg, annotated)
    print(f"  标注图 ({n_labels} 个 FAIL 位置已圈出): {out_jpg}")

    # 4) 输出 JSON + Markdown 报告
    print("[4/4] 写出 JSON + 报告 ...")
    with open(os.path.join(out_dir, f"{stem}_structured.json"), "w", encoding="utf-8") as f:
        json.dump(structured, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, f"{stem}_findings.json"), "w", encoding="utf-8") as f:
        json.dump(findings, f, ensure_ascii=False, indent=2)

    # Markdown 报告
    fails = [f for f in findings if f["passed"] is False]
    reviews = [f for f in findings if f.get("review_required")]
    md = [f"# MVP 端到端报告: {stem}\n", "## 总览\n",
          f"- 触发规则数: **{summary['total']}**",
          f"- PASS: {summary['passed']}",
          f"- **FAIL: {summary['failed']}**",
          f"- REVIEW: {summary['review_required']}", ""]
    if fails:
        md.append("## 违规清单(按规则分类)\n")
        from collections import defaultdict
        by_rule = defaultdict(list)
        for f in fails:
            by_rule[f["rule_id"]].append(f)
        for rid, items in sorted(by_rule.items(), key=lambda x: -len(x[1])):
            md.append(f"### {rid} — {items[0]['name']}  ({len(items)} 条)\n")
            md.append(f"**规范出处:** {items[0]['source']}  **严重度:** {items[0]['severity']}\n")
            for f in items[:10]:
                md.append(f"- {f['target_id']}: {f['message']}")
            if len(items) > 10:
                md.append(f"- ...另 {len(items)-10} 条同类")
            md.append("")
    out_md = os.path.join(out_dir, f"{stem}_report.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"  Markdown 报告: {out_md}")

    # 5) 同时产出 Word 审查意见表 docx(仿真实审查报告格式,可交付)
    out_docx = os.path.join(out_dir, f"{stem}_审查意见表.docx")
    try:
        import mvp_report_docx
        mvp_report_docx.build(
            findings_path=os.path.join(out_dir, f"{stem}_findings.json"),
            annotated_img=out_jpg,
            out_docx=out_docx,
            station_name=stem,
            design_stage="初审 (AI 预审)",
            reviewer="AI 预审系统 (MVP)",
            source_pdf_name=os.path.basename(img_path),
        )
        print(f"  Word 审查意见表: {out_docx}")
    except Exception as e:
        print(f"  ⚠️ Word 报告生成失败(可手动跑 mvp_report_docx.py): {e}")
        out_docx = None

    print(f"\n[完成] {out_dir}")
    return {"summary": summary, "annotated": out_jpg, "report": out_md, "docx": out_docx}


def main():
    ap = argparse.ArgumentParser(description="MVP 端到端 (CVAT + 底图 → 标注违规位置)")
    ap.add_argument("xml")
    ap.add_argument("img")
    ap.add_argument("out")
    ap.add_argument("--scale", type=float, default=None,
                   help="手动 m/px,优先级最高")
    ap.add_argument("--pdf", default=None,
                   help="矢量 PDF 路径;从中自动读 1:N 比例 + 与 jpg 大小算 m/px")
    ap.add_argument("--station-meta", default=None)
    a = ap.parse_args()
    sm = json.load(open(a.station_meta, encoding="utf-8")) if a.station_meta else None
    run(a.xml, a.img, a.out, scale=a.scale, station_meta=sm, pdf_path=a.pdf)


if __name__ == "__main__":
    main()
