#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端流程 DEMO  e2e_demo.py
拖入矢量PDF -> ①识别 -> ②结构化 -> ③规范比对 -> ④原图标注
本脚本是"跑通流程"的演示：识别为粗候选+OCR数字，规则为真，标注落回原图。
用法: python e2e_demo.py 图纸.pdf [输出目录] [--page 0] [--dpi 200]
依赖: pymupdf, opencv-python, numpy, pytesseract(+tesseract-eng)
"""
import sys, os, re, json, argparse, io
from collections import defaultdict
import xml.etree.ElementTree as ET

# 规则引擎在同目录，直接 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rule_engine

# 默认规则文件位置（仓库根/rules/rules.json）
_RULES_DEFAULT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules", "rules.json"))


def render(page, dpi):
    import fitz
    sc = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(sc, sc))
    return pix, sc


# ============ ① 识别：矢量几何 + OCR 数字 ============
def stage1_recognize(page, sc, Wp, Hp):
    import cv2, numpy as np, pytesseract
    from PIL import Image
    draws = page.get_drawings()

    # --- 文字块候选（聚类小笔画）---
    small = []
    for d in draws:
        r = d["rect"]
        w = (r.x1 - r.x0) * sc; h = (r.y1 - r.y0) * sc
        if 0 < w < 70 and 0 < h < 70:
            small.append([r.x0*sc, r.y0*sc, r.x1*sc, r.y1*sc])
    text_boxes = _cluster(small)

    # --- 区域候选（栅格化线->闭合区域）---
    mask = np.zeros((Hp, Wp), np.uint8)
    for d in draws:
        for it in d["items"]:
            if it[0] == "l":
                (x0,y0),(x1,y1) = it[1], it[2]
                cv2.line(mask,(int(x0*sc),int(y0*sc)),(int(x1*sc),int(y1*sc)),255,1)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT,(5,5)), iterations=2)
    cnts,_ = cv2.findContours(closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    A = Wp*Hp; regions=[]
    for c in cnts:
        a = cv2.contourArea(c)
        if a < A*0.001 or a > A*0.05: continue
        ap = cv2.approxPolyDP(c, 0.01*cv2.arcLength(c,True), True)
        if not (4 <= len(ap) <= 12): continue
        xs=[p[0][0] for p in ap]; ys=[p[0][1] for p in ap]
        if (max(xs)-min(xs))>Wp*0.7 or (max(ys)-min(ys))>Hp*0.7: continue
        regions.append(dict(poly=[(int(p[0][0]),int(p[0][1])) for p in ap], area_px=float(a)))
    regions = sorted(regions, key=lambda r:-r["area_px"])[:25]

    # --- OCR: 整图一次 image_to_data(快), 取数字词, 再归并到最近文字框 ---
    pix,_ = render(page, int(72*sc))
    base_img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    cfg = '--psm 11 -c tessedit_char_whitelist=0123456789.m'
    # OCR 用缩小图加速(<=2200宽), 坐标再放大回全分辨率
    capW=2200
    f = min(1.0, capW/float(base_img.width))
    ocr_img = base_img.resize((int(base_img.width*f), int(base_img.height*f))) if f<1 else base_img
    inv = 1.0/f
    data = pytesseract.image_to_data(ocr_img, config=cfg, output_type=pytesseract.Output.DICT)
    words=[]
    for i in range(len(data['text'])):
        t=(data['text'][i] or '').strip()
        if not t: continue
        for m in re.findall(r'\d+\.?\d*', t):
            try: v=float(m)
            except: continue
            x,y,w,h=data['left'][i]*inv,data['top'][i]*inv,data['width'][i]*inv,data['height'][i]*inv
            words.append(dict(value=v, raw=t, cx=x+w/2, cy=y+h/2, box=[x,y,x+w,y+h]))
    return regions, words, base_img


def _cluster(boxes, gap=14, min_members=3):
    if not boxes: return []
    n=len(boxes); parent=list(range(n))
    def find(a):
        while parent[a]!=a: parent[a]=parent[parent[a]]; a=parent[a]
        return a
    def uni(a,b):
        ra,rb=find(a),find(b)
        if ra!=rb: parent[ra]=rb
    cell=40; grid=defaultdict(list)
    for i,b in enumerate(boxes):
        grid[(int((b[0]+b[2])/2//cell),int((b[1]+b[3])/2//cell))].append(i)
    for (cx,cy),idxs in grid.items():
        neigh=[]
        for dx in(-1,0,1):
            for dy in(-1,0,1): neigh+=grid.get((cx+dx,cy+dy),[])
        for i in idxs:
            for j in neigh:
                if i<j:
                    bi,bj=boxes[i],boxes[j]
                    if bi[0]-gap<bj[2] and bj[0]-gap<bi[2] and bi[1]-gap<bj[3] and bj[1]-gap<bi[3]:
                        uni(i,j)
    g=defaultdict(list)
    for i in range(n): g[find(i)].append(i)
    out=[]
    for mem in g.values():
        if len(mem)<min_members: continue
        x0=min(boxes[i][0] for i in mem); y0=min(boxes[i][1] for i in mem)
        x1=max(boxes[i][2] for i in mem); y1=max(boxes[i][3] for i in mem)
        if 8<(x1-x0)<700 and 6<(y1-y0)<120: out.append((x0,y0,x1,y1))
    return out


# ============ ② 结构化 ============
def stage2_structure(regions, ocr_items):
    # 分类 OCR 数字：面积(>200 且像带小数的㎡值) / 疏散距离(<100)
    areas=[]; dists=[]
    for it in ocr_items:
        v=it["value"]
        if 300 <= v <= 20000:
            areas.append(it)
        elif 10 <= v <= 80:
            dists.append(it)
    # 比例尺反标定：用最大区域 px面积 配 最大OCR面积值
    scale_m_per_px = None
    if regions and areas:
        biggest_area_val = max(a["value"] for a in areas)
        biggest_poly_px  = regions[0]["area_px"]
        if biggest_poly_px>0:
            scale_m_per_px = (biggest_area_val / biggest_poly_px) ** 0.5
    # 给区域估算 m²
    for r in regions:
        r["area_m2_est"] = round(r["area_px"]*scale_m_per_px**2,1) if scale_m_per_px else None

    data = {
        "drawing": "图纸.pdf (站厅层, 矢量)",
        "scale_mm_per_px": round(scale_m_per_px*1000,2) if scale_m_per_px else None,
        "fire_compartments_detected": len(regions),
        "compartment_area_labels_m2": sorted({round(a["value"],2) for a in areas}, reverse=True),
        "evac_distance_values_m": sorted({round(d["value"],1) for d in dists}),
        "_areas_raw": areas, "_dists_raw": dists, "_regions": regions,
    }
    return data


# ============ ③ 规范比对（接规则引擎） ============
def stage3_compare(data, rules_path=None, station_meta=None):
    """调用 rules/rules.json 规则引擎。

    输入是旧版扁平结构（仅有 OCR 面积/距离值），通过 rule_engine.from_e2e_flat 适配为
    新引擎结构。注意：分区类型是猜的（公共区），真实使用前应替换为标注真值。
    """
    structured = rule_engine.from_e2e_flat(data, station=station_meta)
    rules_path = rules_path or _RULES_DEFAULT
    raw = rule_engine.evaluate(rules_path, structured)
    # 转回旧版 finding 形状，保持 stage4 标注兼容；review_required 也保留（passed=None）
    findings = []
    for f in raw:
        if f["target_type"] == "fire_compartment":
            display_val = f"{f['value']}㎡"
        elif f["target_type"] == "evac_distance_line":
            display_val = f"{f['value']}m"
        else:
            display_val = str(f["value"])
        findings.append(dict(
            type=f["category"], value=display_val,
            rule=f"{f['threshold']} ({f['source']})",
            passed=f["passed"],  # True/False/None
            review_required=f["review_required"],
            rule_id=f["rule_id"],
            mandatory=f["mandatory"],
            severity=f["severity"],
            note=f["message"],
        ))
    return findings


# ============ ④ 原图标注 ============
def stage4_annotate(base_img, data, findings, out_png):
    import cv2, numpy as np
    im = np.array(base_img)[:,:,::-1].copy()  # RGB->BGR
    # 画区域候选(蓝, 候选=待人工确认)
    for r in data["_regions"][:15]:
        cv2.polylines(im,[np.array(r["poly"],np.int32)],True,(200,120,0),2)
    # 距离/面积数字处：根据是否合规画 绿√/红×
    val2pass={}
    for f in findings:
        try: val2pass[float(re.findall(r'[\d.]+',f["value"])[0])]=f["passed"]
        except: pass
    for it in data["_areas_raw"]+data["_dists_raw"]:
        x0,y0,x1,y1=[int(v) for v in it["box"]]
        passed=val2pass.get(round(it["value"],2), val2pass.get(round(it["value"],1)))
        # True=绿 / False=红 / None(待复核)=黄
        if passed is True:
            color=(0,160,0)
        elif passed is False:
            color=(0,0,230)
        else:
            color=(0,180,220)
        cv2.rectangle(im,(x0-2,y0-2),(x1+2,y1+2),color,3)
        if passed is False:
            cv2.putText(im,"X >limit",(x0,max(0,y0-6)),cv2.FONT_HERSHEY_SIMPLEX,0.9,(0,0,230),2)
        elif passed is None:
            cv2.putText(im,"?review",(x0,max(0,y0-6)),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,180,220),2)
    cv2.imwrite(out_png, im)
    return out_png


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("out", nargs="?", default=".")
    ap.add_argument("--page", type=int, default=0); ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--rules", default=_RULES_DEFAULT,
                    help="规则文件路径（默认 rules/rules.json）")
    ap.add_argument("--station",
                    help="站点元数据 JSON 路径；缺省按单线地下站处理")
    a=ap.parse_args()
    station_meta = None
    if a.station:
        with open(a.station, "r", encoding="utf-8") as f:
            station_meta = json.load(f)
    import fitz
    os.makedirs(a.out, exist_ok=True)
    doc=fitz.open(a.pdf); page=doc[a.page]
    pix,sc=render(page,a.dpi); Wp,Hp=pix.width,pix.height
    print(f"[拖入] {os.path.basename(a.pdf)}  第{a.page}页  {Wp}x{Hp}px")

    print("[①识别] 抽取矢量区域 + OCR 文字数字 ...")
    regions, ocr_items, base_img = stage1_recognize(page, sc, Wp, Hp)
    print(f"        区域候选 {len(regions)}  OCR数字 {len(ocr_items)} 个")

    print("[②结构化] 整理为结构化数据 ...")
    data = stage2_structure(regions, ocr_items)
    print(f"        比例尺≈{data['scale_mm_per_px']} mm/px")
    print(f"        防火分区面积标注(㎡): {data['compartment_area_labels_m2']}")
    print(f"        疏散距离值(m): {data['evac_distance_values_m']}")

    print("[③规范比对] 撞规则 ...")
    findings = stage3_compare(data, rules_path=a.rules, station_meta=station_meta)
    nfail=sum(1 for f in findings if f["passed"] is False)
    nrev=sum(1 for f in findings if f.get("review_required"))
    for f in findings:
        mark = '[强]' if f.get('mandatory') else '[宜]'
        if f["passed"] is False:
            icon = '❌'
        elif f.get("review_required"):
            icon = '⚠️ '
        else:
            icon = '✅'
        print(f"        {icon} {mark} {f.get('rule_id','?')}  {f['type']} {f['value']}")
        print(f"            {f['note']}")
    print(f"        共 {len(findings)} 项检查, {nfail} 项不合规, {nrev} 项待人工复核")

    print("[④原图标注] 标回原图 ...")
    out_png=os.path.join(a.out,"e2e_annotated.png")
    stage4_annotate(base_img, data, findings, out_png)
    # 导出 JSON 与 标注版PDF
    clean={k:v for k,v in data.items() if not k.startswith("_")}
    clean["findings"]=findings
    json.dump(clean, open(os.path.join(a.out,"e2e_structured.json"),"w"),
              ensure_ascii=False, indent=2)
    # png -> pdf
    try:
        from PIL import Image
        Image.open(out_png).convert("RGB").save(os.path.join(a.out,"e2e_annotated.pdf"))
    except Exception as e:
        print("   (PDF导出跳过:",e,")")
    print(f"[完成] 结构化: e2e_structured.json   标注图: e2e_annotated.png/.pdf")

if __name__=="__main__":
    main()
