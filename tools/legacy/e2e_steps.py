#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分步执行 e2e（为绕开沙箱45s单步限制；用户单机可直接用 e2e_demo.py 一步跑完）
  step geom   : get_drawings + 渲染全分辨率底图 + 区域候选 + 文字框  -> cache
  step ocr    : 读底图做全分辨率OCR                                  -> cache
  step asm    : 装配 结构化/比对/标注                                -> 产物
"""
import sys, os, json, io, re, pickle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 归档到 legacy/ 后指向上级 tools/
import e2e_demo as E

PDF = "/sessions/wonderful-trusting-cori/mnt/uploads/图纸.pdf"
CACHE = sys.argv[2] if len(sys.argv) > 2 else "e2e_cache"
os.makedirs(CACHE, exist_ok=True)
step = sys.argv[1]

if step == "geom":
    import fitz, cv2, numpy as np
    doc = fitz.open(PDF); page = doc[0]
    pix, sc = E.render(page, 150); Wp, Hp = pix.width, pix.height
    page.get_pixmap(matrix=fitz.Matrix(sc, sc)).save(os.path.join(CACHE, "base.png"))
    draws = page.get_drawings()
    # 文字框
    small = []
    for d in draws:
        r = d["rect"]; w=(r.x1-r.x0)*sc; h=(r.y1-r.y0)*sc
        if 0 < w < 70 and 0 < h < 70: small.append([r.x0*sc, r.y0*sc, r.x1*sc, r.y1*sc])
    text_boxes = E._cluster(small)
    # 区域候选
    mask = np.zeros((Hp, Wp), np.uint8)
    for d in draws:
        for it in d["items"]:
            if it[0]=="l":
                (x0,y0),(x1,y1)=it[1],it[2]
                cv2.line(mask,(int(x0*sc),int(y0*sc)),(int(x1*sc),int(y1*sc)),255,1)
    closed=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_RECT,(5,5)),iterations=2)
    cnts,_=cv2.findContours(closed,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
    A=Wp*Hp; regions=[]
    for c in cnts:
        a=cv2.contourArea(c)
        if a<A*0.001 or a>A*0.05: continue
        ap=cv2.approxPolyDP(c,0.01*cv2.arcLength(c,True),True)
        if not(4<=len(ap)<=12): continue
        xs=[p[0][0] for p in ap]; ys=[p[0][1] for p in ap]
        if (max(xs)-min(xs))>Wp*0.7 or (max(ys)-min(ys))>Hp*0.7: continue
        regions.append(dict(poly=[(int(p[0][0]),int(p[0][1])) for p in ap], area_px=float(a)))
    regions=sorted(regions,key=lambda r:-r["area_px"])[:25]
    json.dump(dict(W=Wp,H=Hp,regions=regions,text_boxes=text_boxes),
              open(os.path.join(CACHE,"geom.json"),"w"))
    print(f"[geom] 底图{Wp}x{Hp} 区域{len(regions)} 文字框{len(text_boxes)} -> {CACHE}/geom.json")

elif step == "ocr":
    import pytesseract
    from PIL import Image
    img = Image.open(os.path.join(CACHE, "base.png")).convert("RGB")
    cfg = '--psm 11 -c tessedit_char_whitelist=0123456789.m'
    data = pytesseract.image_to_data(img, config=cfg, output_type=pytesseract.Output.DICT)
    words=[]
    for i in range(len(data['text'])):
        t=(data['text'][i] or '').strip()
        if not t: continue
        for m in re.findall(r'\d+\.?\d*', t):
            try: v=float(m)
            except: continue
            x,y,w,h=data['left'][i],data['top'][i],data['width'][i],data['height'][i]
            words.append(dict(value=v, raw=t, cx=x+w/2, cy=y+h/2, box=[x,y,x+w,y+h]))
    json.dump(words, open(os.path.join(CACHE,"ocr.json"),"w"))
    print(f"[ocr] 全分辨率OCR数字 {len(words)} 个 -> {CACHE}/ocr.json")

elif step == "asm":
    from PIL import Image
    g=json.load(open(os.path.join(CACHE,"geom.json")))
    words=json.load(open(os.path.join(CACHE,"ocr.json")))
    base=Image.open(os.path.join(CACHE,"base.png")).convert("RGB")
    data=E.stage2_structure(g["regions"], words)
    findings=E.stage3_compare(data)
    out=os.path.join(CACHE,"..","e2e_out")
    os.makedirs(out, exist_ok=True)
    E.stage4_annotate(base, data, findings, os.path.join(out,"e2e_annotated.png"))
    clean={k:v for k,v in data.items() if not k.startswith("_")}; clean["findings"]=findings
    json.dump(clean, open(os.path.join(out,"e2e_structured.json"),"w"), ensure_ascii=False, indent=2)
    Image.open(os.path.join(out,"e2e_annotated.png")).convert("RGB").save(os.path.join(out,"e2e_annotated.pdf"))
    nfail=sum(1 for f in findings if not f["passed"])
    print("比例尺≈", clean["scale_mm_per_px"], "mm/px")
    print("面积标注㎡:", clean["compartment_area_labels_m2"])
    print("疏散距离m:", clean["evac_distance_values_m"])
    for f in findings: print(("❌" if not f["passed"] else "✅"), f["type"], f["value"], f["rule"])
    print(f"共{len(findings)}项, {nfail}项不合规 -> {out}/")
