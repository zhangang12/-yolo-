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
import naming


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
    global_fails = []    # ★ target_id 为空的违规(如 station 全局对象,EXIT-NUM-PUB 等)
    for f in findings:
        tid = f.get("target_id")
        if not tid:
            if f.get("passed") is False or f.get("review_required"):
                global_fails.append(f)
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

    # ★ 全局违规徽章(没具体坐标的 FAIL,如 station 整体规则:出口数/商铺数量等)
    # 画在右上角,确保任何图都能在视觉上看到这些违规存在,不会被静默跳过
    if global_fails:
        import cv2
        H, W = annotated.shape[:2]
        # 徽章宽 = 图宽 30%(min 800),高度按条数 + 表头算
        bw = max(800, int(W * 0.30))
        line_h = 38
        bh = 70 + line_h * (len(global_fails) + 1)   # 70 = 标题区
        bx0 = W - bw - 40
        by0 = 40
        # 半透明白底 + 红框
        overlay = annotated.copy()
        cv2.rectangle(overlay, (bx0, by0), (bx0 + bw, by0 + bh), (255, 255, 255), -1)
        cv2.addWeighted(overlay, 0.92, annotated, 0.08, 0, annotated)
        cv2.rectangle(annotated, (bx0, by0), (bx0 + bw, by0 + bh), COL_FAIL, 4)
        # 标题
        annotated = _put_chinese(annotated,
                                 f"⚠ 全局违规 {len(global_fails)} 条(无具体位置)",
                                 (bx0 + 16, by0 + 12), COL_FAIL, font_size=30)
        # 每条违规一行:序号 + 规则名 + 关键数值
        for i, f in enumerate(global_fails):
            ymsg = by0 + 70 + i * line_h
            label = f"#{i+1} {f.get('name', f.get('rule_id', ''))}: "
            msg = f.get("message", "")
            # 截断过长的 message
            if len(msg) > 60:
                msg = msg[:58] + "…"
            annotated = _put_chinese(annotated, label + msg,
                                     (bx0 + 20, ymsg), (40, 40, 40), font_size=22)
            rendered_labels += 1

    return annotated, rendered_labels


# ---------- 主流程 ----------

def run(xml_path, img_path, out_dir, scale=None, station_meta=None, rules_path=None,
        pdf_path=None, yolo_weights=None, yolo_conf=0.25, yolo_iou=0.5,
        yolo_tile=1024, yolo_overlap=128, yolo_device="0"):
    """端到端入口。
    数据源优先级:
      1) xml_path: CVAT 真值标注(最准,审查员标好的)
      2) yolo_weights: 用 best.pt 跑 sahi 推理 → 转成临时 CVAT XML 再走全流程
    两者都未提供 → 抛错(让客户端走 e2e_demo 兜底)。
    """
    rules_path = rules_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "rules", "rules.json")
    os.makedirs(out_dir, exist_ok=True)
    # 产物 stem 用 naming 模块抽出"站名-图类型",比设计院图号清晰得多
    stem = naming.product_stem(img_path)
    print(f"[准备] 产物命名前缀: {stem}  (源: {os.path.basename(img_path)})")

    # ★ 数据源决策: CVAT 真值优先,否则用 YOLO 自动识别生成临时 CVAT
    if not xml_path:
        if yolo_weights:
            print(f"[准备] 未提供 CVAT 标注,用 YOLO 自动识别: {yolo_weights}")
            import yolo_to_cvat
            tmp_xml = os.path.join(out_dir, f"{stem}_yolo_auto.xml")
            yolo_to_cvat.yolo_to_cvat(yolo_weights, img_path, tmp_xml,
                                      conf=yolo_conf, iou=yolo_iou,
                                      tile=yolo_tile, overlap=yolo_overlap,
                                      device=yolo_device)
            xml_path = tmp_xml
        else:
            raise ValueError("必须提供 xml_path(CVAT 真值)或 yolo_weights(自动识别)之一")

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

    # 1.5) 疏散路径(A* / Dijkstra)— 自动算"任一点至最近出口"最远距离
    # 把派生的 evac_distance_line 注入 structured,让 EVAC-DIST-ANY-001 真正可跑
    print("[1.5/4] 疏散路径计算 (多源 Dijkstra)...")
    if scale:
        try:
            import evac_path
            # 从 CVAT XML 重新拿 source shapes(adapter 丢失了原 polygon)
            from anno_to_structured import parse_cvat as _parse_cvat
            _images = _parse_cvat(xml_path)
            _src_img = None
            for _im in _images:
                if _im["name"] == img_basename or img_stem in _im["name"]:
                    _src_img = _im; break
            if _src_img:
                evac_result = evac_path.add_evac_to_structured(
                    structured, _src_img["shapes"], scale,
                    (int(_src_img["W"]), int(_src_img["H"])))
                if evac_result:
                    print(f"  ✅ 最远疏散距离: {evac_result['max_distance_m']}m  "
                          f"(最远点 {evac_result['farthest_point_px']} → "
                          f"最近出口 {evac_result['nearest_exit_id']})")
                    # 把最远点存到 image_node 上下文,标注图阶段画出来
                    structured["_evac_farthest_px"] = evac_result["farthest_point_px"]
                else:
                    print("  ⏭ 跳过(无 public_area 多边形或出口)")
        except Exception as e:
            print(f"  ⚠️ 疏散路径计算失败: {e}")
    else:
        print("  ⏭ 跳过(scale 未标定)")

    # 1.6) 栅格 OCR 兜底 — 对 area_m2_design 为空的 fire_compartment, 用 tesseract
    #      在 fc polygon 内找数字补上。栅格 PDF + 无矢量文字层时也能拿到面积。
    fcs_no_area = [fc for fc in structured.get("fire_compartment", [])
                   if not fc.get("area_m2_design")]
    if fcs_no_area:
        print(f"[1.6/4] 栅格 OCR 兜底 ({len(fcs_no_area)}/{len(structured['fire_compartment'])} "
              f"个 fc 缺 area_m2_design)...")
        try:
            import raster_ocr
            from PIL import Image
            # 派生 fc 的 polygon 索引(adapter 没存 polygon,这里从 CVAT shapes 重派生)
            from anno_to_structured import parse_cvat as _parse_cvat2
            _images2 = _parse_cvat2(xml_path)
            _src_img2 = None
            for _im in _images2:
                if _im["name"] == img_basename or img_stem in _im["name"]:
                    _src_img2 = _im; break
            if _src_img2:
                fc_records = []
                fc_idx = 0
                for s in _src_img2["shapes"]:
                    if s["label"] == "fire_compartment" and s["geom"] == "polygon" \
                       and len(s["points"]) >= 3 and fc_idx < len(structured["fire_compartment"]):
                        fc_records.append({
                            "id": structured["fire_compartment"][fc_idx]["id"],
                            "polygon_pts": s["points"],
                        })
                        fc_idx += 1
                # 读底图(中文路径安全, 同 _imread)
                img_arr = _imread(img_path)
                if img_arr is not None:
                    pil_img = Image.fromarray(img_arr[:, :, ::-1])  # BGR→RGB
                    filled = raster_ocr.fill_area_m2_design(structured, fc_records, pil_img, verbose=True)
                    print(f"  ✅ OCR 补全 area_m2_design: {filled} 个 fc")
                else:
                    print("  ⏭ 读底图失败,跳过 OCR")
        except Exception as e:
            print(f"  ⚠️ OCR 兜底失败(不阻塞): {e}")

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
        # 报告头部"工程名称"用站名(去掉图类型),如 "嘉宾站";没站名则用 stem
        st = naming.extract_station(os.path.basename(img_path)) or stem
        mvp_report_docx.build(
            findings_path=os.path.join(out_dir, f"{stem}_findings.json"),
            annotated_img=out_jpg,
            out_docx=out_docx,
            station_name=st,
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
    ap = argparse.ArgumentParser(description="MVP 端到端 (CVAT 或 YOLO + 底图 → 标注违规位置)")
    ap.add_argument("xml", nargs="?", default="",
                   help="CVAT 标注 XML 路径;留空字符串则必须传 --yolo-weights")
    ap.add_argument("img")
    ap.add_argument("out")
    ap.add_argument("--scale", type=float, default=None,
                   help="手动 m/px,优先级最高")
    ap.add_argument("--pdf", default=None,
                   help="矢量 PDF 路径;从中自动读 1:N 比例 + 与 jpg 大小算 m/px")
    ap.add_argument("--station-meta", default=None)
    ap.add_argument("--yolo-weights", default=None,
                   help="YOLO best.pt 路径;无 CVAT 标注时用 sahi 自动识别")
    ap.add_argument("--yolo-conf", type=float, default=0.25)
    ap.add_argument("--yolo-iou", type=float, default=0.5)
    ap.add_argument("--yolo-tile", type=int, default=1024)
    ap.add_argument("--yolo-overlap", type=int, default=128)
    ap.add_argument("--yolo-device", default="0")
    a = ap.parse_args()
    sm = json.load(open(a.station_meta, encoding="utf-8")) if a.station_meta else None
    xml = a.xml if a.xml else None
    run(xml, a.img, a.out, scale=a.scale, station_meta=sm, pdf_path=a.pdf,
        yolo_weights=a.yolo_weights, yolo_conf=a.yolo_conf, yolo_iou=a.yolo_iou,
        yolo_tile=a.yolo_tile, yolo_overlap=a.yolo_overlap, yolo_device=a.yolo_device)


if __name__ == "__main__":
    main()
