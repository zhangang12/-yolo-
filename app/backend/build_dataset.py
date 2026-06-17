#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建数据集  build_dataset.py
=================================================
把【CVAT 标注 XML + 对应底图】转成 YOLO-seg 训练数据集：
  CVAT(box/polygon)  ->  归一化多边形标签  ->  切片(可选)  ->  划分 train/val/test  ->  data.yaml

设计要点（与 docs/guides/yolo_training_guide.md 一致）：
  - 默认 task=seg：box 也转成 4 点多边形，一个 YOLO-seg 模型同时出框和分割。
  - polyline 类(尺寸线/疏散线)无面积，**不作为训练目标**，自动跳过。
  - 站厅图是超长条，必须切片(默认 1024)；切片时标注随之裁剪到瓦片局部坐标。
  - 划分比例 8:1:1，固定 seed 可复现。

用法：
  python build_dataset.py  <标注目录>  <输出数据集目录>  [--tile 1024] [--overlap 128] [--seed 0]
  标注目录里需成对存在：xxx.xml(CVAT) 与 xxx.jpg/png(底图，名字取 <image name=...>)
"""
import os, sys, glob, json, random, argparse, shutil
import xml.etree.ElementTree as ET

# Windows 控制台默认 GBK，脚本含 emoji/中文，强制 utf-8 输出避免崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 训练目标类（顺序即类别 id，**只追加不重排，否则旧权重对不上**）。polyline 类不在内。
CLASS_NAMES = [
    "fire_compartment", "commercial_shop", "surrounding_building",
    "station_exit_ground", "vent_group_ground",
    "fire_door", "stair_escalator", "draft_curtain",
    "building_meta", "vent_meta", "dimension_val",
    "room_title", "val_text",
    # 6.6 schema v3 新增标签 → 必须纳入训练，否则真实标注会被当背景丢掉
    "safety_exit", "gate", "fire_shutter",
]
CLASS_ID = {n: i for i, n in enumerate(CLASS_NAMES)}
SKIP_LABELS = {"fire_clearance_line", "evac_distance_line", "width_dimension_line"}

# 中文标签兜底映射：复用 fire_anno_tool 的 MAPPING，使原始(未转英文)CVAT 也能直接喂
try:
    _TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
    sys.path.insert(0, os.path.abspath(_TOOLS))
    from fire_anno_tool import MAPPING as ZH2EN
except Exception:
    ZH2EN = {}


def _find_image(anno_dir, name):
    """在标注目录里定位底图：支持顶层 / images 子目录 / 递归同名。"""
    base = os.path.basename(name)
    stem = os.path.splitext(base)[0]
    for cand in (os.path.join(anno_dir, base), os.path.join(anno_dir, "images", base)):
        if os.path.exists(cand):
            return cand
    hits = glob.glob(os.path.join(anno_dir, "**", stem + ".*"), recursive=True)
    hits = [h for h in hits if h.lower().endswith((".jpg", ".jpeg", ".png"))]
    return hits[0] if hits else None


def _shape_polygon(el):
    """把 CVAT 形状统一成多边形点列 [(x,y),...]；box->4点；polyline->None(跳过)。"""
    if el.tag == "box":
        x0, y0 = float(el.get("xtl")), float(el.get("ytl"))
        x1, y1 = float(el.get("xbr")), float(el.get("ybr"))
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if el.tag == "polygon":
        pts = el.get("points") or ""
        return [tuple(map(float, p.split(","))) for p in pts.split(";") if p]
    return None  # polyline / 其它


def _clip_poly_to_tile(poly, tx, ty, tw, th):
    """把多边形点平移到瓦片局部坐标并裁剪到 [0,tw]x[0,th]；若完全在瓦片外返回 None。"""
    local = [(x - tx, y - ty) for (x, y) in poly]
    xs = [p[0] for p in local]; ys = [p[1] for p in local]
    if max(xs) < 0 or min(xs) > tw or max(ys) < 0 or min(ys) > th:
        return None
    clipped = [(min(max(x, 0), tw), min(max(y, 0), th)) for (x, y) in local]
    # 退化（裁剪后近似一条线）则丢弃
    cxs = [p[0] for p in clipped]; cys = [p[1] for p in clipped]
    if (max(cxs) - min(cxs)) < 3 or (max(cys) - min(cys)) < 3:
        return None
    return clipped


def _norm_line(cls_id, poly, w, h):
    """生成一行 YOLO-seg 标签：class x1 y1 x2 y2 ...（归一化）。"""
    flat = []
    for (x, y) in poly:
        flat.append(f"{min(max(x / w, 0), 1):.6f}")
        flat.append(f"{min(max(y / h, 0), 1):.6f}")
    return f"{cls_id} " + " ".join(flat)


def _iter_images(anno_dir):
    """遍历目录里所有 CVAT xml，逐 <image> 产出 (底图路径, W, H, [(cls_id, poly)...])。"""
    for xml in glob.glob(os.path.join(anno_dir, "*.xml")):
        try:
            root = ET.parse(xml).getroot()
        except Exception as e:
            print(f"  [跳过] 解析失败 {os.path.basename(xml)}: {e}")
            continue
        for im in root.findall(".//image"):
            name = im.get("name") or ""
            W = float(im.get("width") or 0); H = float(im.get("height") or 0)
            img_path = _find_image(anno_dir, name)
            if not img_path:
                print(f"  [跳过] 找不到底图: {name}")
                continue
            shapes = []
            for el in list(im):
                lab = ZH2EN.get(el.get("label"), el.get("label"))  # 中文标签自动转英文
                if lab in SKIP_LABELS or lab not in CLASS_ID:
                    continue
                poly = _shape_polygon(el)
                if poly and len(poly) >= 3:
                    shapes.append((CLASS_ID[lab], poly))
            if shapes:
                yield img_path, int(W), int(H), shapes


def _imread(path):
    """OpenCV 在 Windows 读不了中文/全角路径，改用 fromfile+imdecode。"""
    import cv2, numpy as np
    try:
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def _imwrite(path, img):
    import cv2, numpy as np
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(path)
    return ok


def build(anno_dir, out_dir, tile=1024, overlap=128, seed=0):
    try:
        import cv2
    except ImportError:
        print("需要 opencv-python：pip install opencv-python"); sys.exit(1)

    random.seed(seed)
    for sub in ("images", "labels"):
        for sp in ("train", "val", "test"):
            os.makedirs(os.path.join(out_dir, sub, sp), exist_ok=True)

    # 先收集所有样本（切片后）的 (图bgr, 标签行列表, 原名)
    samples = []
    total_imgs = 0
    for img_path, W, H, shapes in _iter_images(anno_dir):
        total_imgs += 1
        img = _imread(img_path)
        if img is None:
            print(f"  [跳过] 读图失败: {img_path}"); continue
        H0, W0 = img.shape[:2]
        stem = os.path.splitext(os.path.basename(img_path))[0]

        if tile and (W0 > tile * 1.5 or H0 > tile * 1.5):
            step = max(1, tile - overlap)
            ti = 0
            for ty in range(0, H0, step):
                for tx in range(0, W0, step):
                    tw = min(tile, W0 - tx); th = min(tile, H0 - ty)
                    if tw < tile * 0.3 or th < tile * 0.3:
                        continue
                    lines = []
                    for cls_id, poly in shapes:
                        cp = _clip_poly_to_tile(poly, tx, ty, tw, th)
                        if cp:
                            lines.append(_norm_line(cls_id, cp, tw, th))
                    if not lines:
                        continue  # 不要纯背景瓦片(可按需保留一部分)
                    crop = img[ty:ty + th, tx:tx + tw]
                    samples.append((crop, lines, f"{stem}_t{ti}"))
                    ti += 1
        else:
            lines = [_norm_line(cls_id, poly, W0, H0) for cls_id, poly in shapes]
            samples.append((img, lines, stem))

    if not samples:
        print("没有可用样本。请确认标注目录里有成对的 xml + 底图，且含可训练标签。")
        sys.exit(1)

    random.shuffle(samples)
    n = len(samples)
    n_val = max(1, int(n * 0.1)); n_test = max(1, int(n * 0.1))
    split_of = {}
    for i in range(n):
        split_of[i] = "test" if i < n_test else ("val" if i < n_test + n_val else "train")

    counts = {"train": 0, "val": 0, "test": 0}
    cls_inst = {n: 0 for n in CLASS_NAMES}
    for i, (crop, lines, name) in enumerate(samples):
        sp = split_of[i]
        _imwrite(os.path.join(out_dir, "images", sp, name + ".png"), crop)
        with open(os.path.join(out_dir, "labels", sp, name + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        counts[sp] += 1
        for ln in lines:
            cls_inst[CLASS_NAMES[int(ln.split()[0])]] += 1

    # data.yaml
    yaml_path = os.path.join(out_dir, "data.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(f"path: {os.path.abspath(out_dir)}\n")
        f.write("train: images/train\nval: images/val\ntest: images/test\n\n")
        f.write(f"nc: {len(CLASS_NAMES)}\n")
        f.write("names:\n")
        for i, nm in enumerate(CLASS_NAMES):
            f.write(f"  {i}: {nm}\n")

    print(f"[建数据集] 源图 {total_imgs} 张 -> 样本(切片后) {n} 个")
    print(f"[建数据集] 划分  train {counts['train']} / val {counts['val']} / test {counts['test']}")
    print("[建数据集] 各类实例数:")
    for nm, c in cls_inst.items():
        flag = "  ⚠️不足(建议≥150)" if 0 < c < 150 else ("  ❌为0" if c == 0 else "")
        print(f"    {nm:22s} {c}{flag}")
    print(f"[建数据集] data.yaml -> {yaml_path}")
    print("[完成] 可在“训练”页选此 data.yaml 开始训练。")
    return yaml_path


def main():
    ap = argparse.ArgumentParser(description="CVAT 标注 -> YOLO-seg 数据集")
    ap.add_argument("anno_dir"); ap.add_argument("out_dir")
    ap.add_argument("--tile", type=int, default=1024)
    ap.add_argument("--overlap", type=int, default=128)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    build(a.anno_dir, a.out_dir, a.tile, a.overlap, a.seed)


if __name__ == "__main__":
    main()
