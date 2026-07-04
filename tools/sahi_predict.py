#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
切片推理 (SAHI 风格)  sahi_predict.py
=================================================
解决"大图 (14000x3300) 缩到 imgsz=1024 后小目标(防火门、闸机)漏检"的问题。
做法：把大图按 tile=1024 + overlap=128 切片，每片独立推理，全局坐标合并 + NMS。

用法：
  python sahi_predict.py <best.pt> <图.jpg> [--tile 1024] [--overlap 128]
        [--conf 0.25] [--iou 0.5] [--out 输出目录]

输出：
  - <stem>_pred.jpg : 全图标注（合并后的检测框/分割多边形）
  - <stem>_pred.json: 全部检测的结构化结果（含 bbox/mask/cls/conf/global coords）
"""
import os, sys, json, argparse
from collections import defaultdict


def _imread(path):
    """中文路径安全读图;若 path 是 PDF,自动 fitz 渲染第一页 200DPI。"""
    import cv2, numpy as np
    if path.lower().endswith(".pdf"):
        import fitz
        doc = fitz.open(path)
        pg = doc[0]
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


def _nms(boxes, scores, classes, iou_thresh=0.5):
    """简单逐类 NMS。boxes: [(x0,y0,x1,y1), ...]; 返回保留索引列表。"""
    import numpy as np
    if not boxes:
        return []
    boxes = np.array(boxes, dtype=np.float32)
    scores = np.array(scores, dtype=np.float32)
    classes = np.array(classes)
    keep = []
    for cls in np.unique(classes):
        m = classes == cls
        idx = np.where(m)[0]
        bx = boxes[idx]
        sc = scores[idx]
        # 按 score 降序
        order = sc.argsort()[::-1]
        bx = bx[order]; sc = sc[order]; idx = idx[order]
        x1 = bx[:, 0]; y1 = bx[:, 1]; x2 = bx[:, 2]; y2 = bx[:, 3]
        areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
        suppressed = [False] * len(bx)
        for i in range(len(bx)):
            if suppressed[i]:
                continue
            keep.append(int(idx[i]))
            xx1 = np.maximum(x1[i], x1[i+1:])
            yy1 = np.maximum(y1[i], y1[i+1:])
            xx2 = np.minimum(x2[i], x2[i+1:])
            yy2 = np.minimum(y2[i], y2[i+1:])
            inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
            union = areas[i] + areas[i+1:] - inter
            iou = np.where(union > 0, inter / union, 0)
            for j, v in enumerate(iou):
                if v > iou_thresh:
                    suppressed[i+1+j] = True
    return sorted(keep)


def _resolve_device(device):
    """请求的是 GPU 但机器没有可用 CUDA 时自动降级到 CPU（无 GPU 的机器如 Mac/纯 CPU 笔记本会跑到这）。"""
    dev = str(device).strip().lower()
    if dev in ("", "cpu", "mps"):
        return device
    try:
        import torch
        if not torch.cuda.is_available():
            print(f"[提示] 请求 device={device} 但未检测到可用 CUDA 显卡，自动改用 CPU 推理（会比较慢）。", flush=True)
            return "cpu"
    except Exception:
        return "cpu"
    return device


def sliced_predict(weights, img_path, tile=1024, overlap=128, conf=0.25,
                   iou=0.5, device=0, out_dir=None):
    """对单张大图做切片推理，返回 (合并后的检测列表, 输出文件路径)。

    检测项格式：
      {cls_id, cls_name, conf, box:[x0,y0,x1,y1], polygon:[[x,y],...] or None}
    全部为全图原始坐标(像素)。
    """
    import cv2, numpy as np
    from ultralytics import YOLO

    device = _resolve_device(device)
    model = YOLO(weights)
    class_names = model.names

    img = _imread(img_path)
    if img is None:
        raise FileNotFoundError(f"读不到图片: {img_path}")
    H, W = img.shape[:2]

    # 计算切片坐标
    step = max(1, tile - overlap)
    tiles = []
    for ty in range(0, H, step):
        if ty + tile > H:
            ty = max(0, H - tile)
        for tx in range(0, W, step):
            if tx + tile > W:
                tx = max(0, W - tile)
            tw = min(tile, W - tx); th = min(tile, H - ty)
            tiles.append((tx, ty, tw, th))
            if tx + tile >= W:
                break
        if ty + tile >= H:
            break
    # 去重
    tiles = list({(t[0], t[1], t[2], t[3]) for t in tiles})
    tiles.sort()

    all_boxes, all_scores, all_classes, all_polys = [], [], [], []
    for tx, ty, tw, th in tiles:
        crop = img[ty:ty + th, tx:tx + tw]
        results = model.predict(source=crop, imgsz=tile, conf=conf, iou=iou,
                                save=False, device=device, workers=0, verbose=False)
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            continue
        bx = r.boxes.xyxy.cpu().numpy()  # [N,4] tile 局部坐标
        sc = r.boxes.conf.cpu().numpy()
        cl = r.boxes.cls.cpu().numpy().astype(int)
        # 还原到全图坐标
        bx_global = bx.copy()
        bx_global[:, [0, 2]] += tx
        bx_global[:, [1, 3]] += ty
        # 分割多边形（若有）
        polys_global = [None] * len(bx)
        if r.masks is not None and r.masks.xy is not None:
            for i, poly in enumerate(r.masks.xy):
                if poly is None or len(poly) == 0:
                    continue
                p = poly.copy()
                p[:, 0] += tx
                p[:, 1] += ty
                polys_global[i] = p.tolist()
        for i in range(len(bx)):
            all_boxes.append(bx_global[i].tolist())
            all_scores.append(float(sc[i]))
            all_classes.append(int(cl[i]))
            all_polys.append(polys_global[i])

    # NMS 跨片去重
    keep = _nms(all_boxes, all_scores, all_classes, iou_thresh=iou)
    detections = []
    for i in keep:
        detections.append({
            "cls_id": all_classes[i],
            "cls_name": class_names[all_classes[i]],
            "conf": all_scores[i],
            "box": all_boxes[i],
            "polygon": all_polys[i],
        })

    # 可视化
    vis = img.copy()
    palette = [(54, 67, 244), (244, 145, 16), (16, 172, 132), (132, 16, 244),
               (16, 244, 96), (244, 244, 16), (244, 16, 110), (16, 220, 244),
               (180, 244, 16), (16, 80, 244), (244, 80, 16), (200, 16, 244),
               (16, 244, 244), (244, 244, 80), (110, 244, 16), (140, 16, 200)]
    for d in detections:
        x0, y0, x1, y1 = map(int, d["box"])
        cls = d["cls_id"]
        col = palette[cls % len(palette)]
        cv2.rectangle(vis, (x0, y0), (x1, y1), col, 3)
        label = f"{d['cls_name']} {d['conf']:.2f}"
        cv2.putText(vis, label, (x0, max(0, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
        if d["polygon"] is not None:
            pts = np.array(d["polygon"], np.int32).reshape(-1, 1, 2)
            cv2.polylines(vis, [pts], True, col, 2)

    out_dir = out_dir or os.path.dirname(os.path.abspath(img_path))
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(img_path))[0]
    out_jpg = os.path.join(out_dir, f"{stem}_pred.jpg")
    out_json = os.path.join(out_dir, f"{stem}_pred.json")
    _imwrite(out_jpg, vis)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "image": os.path.basename(img_path),
            "size": [W, H],
            "tile": tile, "overlap": overlap, "tiles_run": len(tiles),
            "total_detections": len(detections),
            "by_class": dict(_count_by_class(detections)),
            "detections": detections,
        }, f, ensure_ascii=False, indent=2)

    print(f"[切片推理] {os.path.basename(img_path)}  尺寸 {W}x{H}  切片 {len(tiles)} 块")
    print(f"[切片推理] 合并后检测 {len(detections)} 个")
    cnt = _count_by_class(detections)
    for c, n in sorted(cnt.items(), key=lambda x: -x[1]):
        print(f"    {c:24s} {n}")
    print(f"[切片推理] 标注图: {out_jpg}")
    print(f"[切片推理] 结构化: {out_json}")
    return detections, out_jpg, out_json


def _count_by_class(detections):
    cnt = defaultdict(int)
    for d in detections:
        cnt[d["cls_name"]] += 1
    return dict(cnt)


def main():
    ap = argparse.ArgumentParser(description="切片推理 (SAHI 风格)")
    ap.add_argument("weights")
    ap.add_argument("img")
    ap.add_argument("--tile", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=128)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    sliced_predict(a.weights, a.img, tile=a.tile, overlap=a.overlap,
                   conf=a.conf, iou=a.iou, device=a.device, out_dir=a.out)


if __name__ == "__main__":
    main()
