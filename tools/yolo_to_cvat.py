#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLO 预测 → CVAT XML 适配器  yolo_to_cvat.py
=================================================
让 YOLO 模型(best.pt)真正接入 mvp_e2e 端到端流程:
  best.pt + PDF/jpg
    → sahi_predict 切片推理
    → 转 CVAT 1.1 XML 格式(label/box/polygon 与 anno_to_structured 一致)
    → 喂 mvp_e2e 走全套规则引擎 + 标注图 + Word 报告

CVAT 标签 schema 必须与 fire_anno_tool.py LABELS 完全一致(否则下游适配器认不出)。

用法:
  python yolo_to_cvat.py <best.pt> <图.pdf|jpg> <输出.xml>
        [--conf 0.25] [--iou 0.5] [--tile 1024] [--overlap 128] [--device 0]

输出: CVAT 1.1 XML,可直接给 mvp_e2e 当 --xml 输入。
"""
import os, sys, argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------- 字段默认值(YOLO 识别不到的必填属性) ----------
# CVAT 标注允许 unknown 的必填(详见 fire_anno_tool.py ENUMS)
LABEL_DEFAULT_ATTRS = {
    "fire_door": {"class": "unknown", "swing_dir": "顺着疏散方向"},
    "fire_compartment": {"zone_type": "公共区"},
    "surrounding_building": {"name": "", "building_type": "unknown", "fire_rating": "unknown"},
    "vent_group_ground": {"vent_function": "", "discharge_type": "unknown"},
    "safety_exit": {},
    "gate": {},
    "fire_shutter": {},
    "stair_escalator": {},
    "draft_curtain": {},
    "commercial_shop": {},
    "public_area": {},
    "station_exit_ground": {},
    "room_title": {"text_content": ""},
    "val_text": {"text_content": ""},
    "dimension_val": {"text_content": ""},
    "building_meta": {"text_content": ""},
    "vent_meta": {"text_content": ""},
}

# 哪些类天然是 polygon(分割类),其它走 box
POLYGON_LABELS = {
    "fire_compartment", "commercial_shop", "surrounding_building",
    "station_exit_ground", "vent_group_ground", "public_area",
}


def _pretty(root):
    """格式化 XML,带缩进。"""
    rough = ET.tostring(root, encoding="utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8")


def detections_to_cvat(detections, img_basename, W, H, label_set=None):
    """把 sliced_predict 返回的 detection list 转成 CVAT 1.1 XML root。

    label_set: 用到的 label 集合,自动写进 <meta><task><labels>。
    """
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"

    # ---- meta/task/labels ----
    meta = ET.SubElement(root, "meta")
    task = ET.SubElement(meta, "task")
    ET.SubElement(task, "name").text = "yolo_auto"
    ET.SubElement(task, "size").text = "1"
    labels_el = ET.SubElement(task, "labels")
    label_set = label_set or {d["cls_name"] for d in detections}
    for lab in sorted(label_set):
        le = ET.SubElement(labels_el, "label")
        ET.SubElement(le, "name").text = lab
        # 属性声明(让 CVAT 编辑器认)
        attrs = ET.SubElement(le, "attributes")
        for ak, _ in LABEL_DEFAULT_ATTRS.get(lab, {}).items():
            ae = ET.SubElement(attrs, "attribute")
            ET.SubElement(ae, "name").text = ak
            ET.SubElement(ae, "input_type").text = "text"
            ET.SubElement(ae, "mutable").text = "True"
            ET.SubElement(ae, "values").text = ""

    # ---- image ----
    image = ET.SubElement(root, "image",
                          id="0", name=img_basename,
                          width=str(W), height=str(H))

    # ---- 每个 detection 转成 box 或 polygon ----
    for d in detections:
        lab = d["cls_name"]
        defaults = LABEL_DEFAULT_ATTRS.get(lab, {})

        is_polygon = lab in POLYGON_LABELS and d.get("polygon")
        if is_polygon:
            pts = ";".join(f"{p[0]:.2f},{p[1]:.2f}" for p in d["polygon"])
            shape = ET.SubElement(image, "polygon",
                                  label=lab, points=pts, occluded="0", z_order="0")
        else:
            x0, y0, x1, y1 = d["box"]
            shape = ET.SubElement(image, "box",
                                  label=lab,
                                  xtl=f"{x0:.2f}", ytl=f"{y0:.2f}",
                                  xbr=f"{x1:.2f}", ybr=f"{y1:.2f}",
                                  occluded="0", z_order="0")
        # 写默认属性
        for ak, av in defaults.items():
            ae = ET.SubElement(shape, "attribute", name=ak)
            ae.text = av

    return root


def yolo_to_cvat(weights, img_path, out_xml,
                 conf=0.25, iou=0.5, tile=1024, overlap=128, device="0"):
    """端到端: best.pt + 图 → 切片推理 → 写 CVAT XML。

    返回 (out_xml_path, detection_count, by_class_dict, pred_jpg_path)。
    """
    from sahi_predict import sliced_predict, _imread, _count_by_class

    # 跑切片推理
    detections, pred_jpg, _ = sliced_predict(
        weights, img_path, tile=tile, overlap=overlap,
        conf=conf, iou=iou, device=device,
        out_dir=os.path.dirname(out_xml) or ".",   # 顺带存一份 YOLO 识别图(pred_jpg),供上游预览
    )
    # 读图拿到 W/H(中文路径安全)
    img = _imread(img_path)
    H, W = img.shape[:2]

    # 转 CVAT XML
    img_basename = os.path.basename(img_path)
    root = detections_to_cvat(detections, img_basename, W, H)
    pretty = _pretty(root)
    os.makedirs(os.path.dirname(os.path.abspath(out_xml)) or ".", exist_ok=True)
    with open(out_xml, "wb") as f:
        f.write(pretty)

    by_class = _count_by_class(detections)
    print(f"[yolo→cvat] {len(detections)} 个检测 → {out_xml}")
    for c, n in sorted(by_class.items(), key=lambda x: -x[1]):
        print(f"    {c:24s} {n}")
    return out_xml, len(detections), by_class, pred_jpg


def main():
    ap = argparse.ArgumentParser(description="YOLO 预测 → CVAT 1.1 XML")
    ap.add_argument("weights", help=".pt 权重路径")
    ap.add_argument("img", help="底图(PDF 或 jpg/png),PDF 内部用 fitz 200DPI 渲染")
    ap.add_argument("out_xml", help="输出 CVAT XML 路径")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--tile", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=128)
    ap.add_argument("--device", default="0")
    a = ap.parse_args()
    yolo_to_cvat(a.weights, a.img, a.out_xml,
                 conf=a.conf, iou=a.iou, tile=a.tile, overlap=a.overlap, device=a.device)


if __name__ == "__main__":
    main()
