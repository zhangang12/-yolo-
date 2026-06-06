#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""防火间距比对 —— 总平面图标注 → 结构化 building_clearance → rule_engine 判定 → 标注回图
与 e2e_demo 同范式：本脚本只做"识别/结构化/标注"，规范判定交给 rule_engine + rules/rules.json。
用法: python fangju_demo.py 标注.xml 总平面图.pdf [输出目录] [--rules ../rules/rules.json]
依赖: pymupdf opencv-python numpy shapely
"""
import sys, os, re, json, argparse
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon, LineString

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rule_engine

DEFAULT_RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rules", "rules.json")

# CVAT 标签(中/英) → 内部类型
LAB = {"周边建筑":"b","surrounding_building":"b",
       "车站地面出入口":"exit","station_exit_ground":"exit",
       "风亭实体":"vent","vent_group_ground":"vent",
       "防火间距线":"cl","fire_clearance_line":"cl",
       "防火间距尺寸数字":"dim","dimension_val":"dim",
       "建筑属性文字":"meta","building_meta":"meta"}

def pts(el):
    if el.tag == "box":
        x0,y0,x1,y1=float(el.get("xtl")),float(el.get("ytl")),float(el.get("xbr")),float(el.get("ybr"))
        return [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
    return [tuple(map(float,p.split(","))) for p in el.get("points").split(";")]

# building_type(标注规范 v3) → 规则引擎 building_category 对齐。
# 两者基本同名；"超高层民用"暂按"高层民用"参与间距判定(更严档规则待补)。
BUILDING_CAT_MAP = {
    "多层民用": "多层民用", "高层民用": "高层民用",
    "超高层民用": "高层民用",          # 暂按高层(≥9m)判，避免漏判
    "加油加气加氢站": "加油加气加氢站",
}
# 有防火间距规则覆盖的类别(用于"未覆盖→提示人工")
COVERED_CATS = {"多层民用", "高层民用", "加油加气加氢站"}

def attr(at, *keys):
    """按多个候选键(中/英)取属性，返回第一个非空值。"""
    for k in keys:
        v = at.get(k)
        if v not in (None, ""):
            return v.strip() if isinstance(v, str) else v
    return ""

def category_from_height(floors, height_m):
    """无 building_type 时，用高度/层数兜底推 多层/高层(民用)。"""
    try:
        h = float(height_m) if str(height_m).strip() not in ("", "None") else None
    except (TypeError, ValueError):
        h = None
    if h is not None:
        return "高层民用" if h > 24 else "多层民用"
    try:
        f = int(float(floors)) if str(floors).strip() not in ("", "None") else None
    except (TypeError, ValueError):
        f = None
    if f is not None:
        return "高层民用" if f >= 8 else "多层民用"   # 粗略：≥8 层约 >24m
    return None

def classify_building(meta):
    """最后兜底：从 building_meta 文字猜(兼容旧数据 / 属性未填时)。"""
    t = meta or ""
    if any(k in t for k in ("加油","加气","加氢")): return "加油加气加氢站"
    if "高层" in t: return "高层民用"
    return "多层民用"

def extract_structured(xml_path, station=None):
    """总平面图 CVAT 标注 → (结构化数据, 标注用几何信息)"""
    root=ET.parse(xml_path).getroot()
    img=root.find(".//image"); W=float(img.get("width")); H=float(img.get("height"))
    buildings=[]; attached=[]; clines=[]; dims=[]; metas=[]
    for el in list(img):
        k=LAB.get(el.get("label"))
        if not k: continue
        P=pts(el); at={a.get("name"):a.text for a in el.findall("attribute")}
        c=(sum(p[0] for p in P)/len(P), sum(p[1] for p in P)/len(P))
        if k=="b": buildings.append((Polygon(P), at))
        elif k=="exit": attached.append(("出入口",Polygon(P)))
        elif k=="vent": attached.append(("风亭",Polygon(P)))
        elif k=="cl" and len(P)>=2: clines.append(LineString(P))
        elif k=="dim":
            v=at.get("尺寸") or at.get("text_content") or ""
            m=re.findall(r"[\d.]+",v)
            if m: dims.append((c,float(m[0])))
        elif k=="meta": metas.append((c, at.get("文字内容") or at.get("text_content") or ""))
    # 比例尺(米/像素): 用每条间距线像素长 配 最近的尺寸数字
    scales=[]
    for ln in clines:
        lc=ln.centroid.coords[0]
        if dims and ln.length>5:
            nd=min(dims,key=lambda d:(d[0][0]-lc[0])**2+(d[0][1]-lc[1])**2)
            scales.append(nd[1]/ln.length)
    scale=sorted(scales)[len(scales)//2] if scales else None
    def meta_of(poly):
        cc=poly.centroid.coords[0]
        return min(metas,key=lambda m:(m[0][0]-cc[0])**2+(m[0][1]-cc[1])**2)[1] if metas else ""
    bcs=[]; geo=[]; cat_warns=[]
    for i,(bp,at) in enumerate(buildings):
        # 属性优先(标注规范 v3 直接录在 surrounding_building 上)，回退 building_meta 文字
        btype   = attr(at, "building_type", "建筑类型")
        name    = attr(at, "name", "建筑名称", "名称")
        frating = attr(at, "fire_rating", "耐火等级")
        floors  = attr(at, "floors", "层数")
        height  = attr(at, "height_m", "建筑高度", "高度")
        meta    = meta_of(bp)
        if not name:
            name = (meta.split("\n")[0] if meta else f"建筑{i+1}")
        # building_category：building_type 优先 → 高度/层数兜底 → building_meta 文字兜底
        if btype:
            cat = BUILDING_CAT_MAP.get(btype, btype)   # 已知映射；未知(厂房/其他)忠实保留
        else:
            cat = category_from_height(floors, height) or classify_building(meta)
        if cat not in COVERED_CATS:
            cat_warns.append((name, btype or cat))
        near=None; dmin=1e18
        for kind,op in attached:
            d=bp.distance(op)
            if d<dmin: dmin=d; near=(kind,op)
        dist_m=round(dmin*scale,2) if scale else None
        bid=f"BC-{i+1:02d}"
        bcs.append(dict(id=bid, building_name=name, building_category=cat,
                        building_type=btype or None, fire_rating=frating or None,
                        floors=floors or None, height_m=height or None,
                        nearest_kind=near[0] if near else None, distance_m=dist_m,
                        _from="annotation_attr" if btype else "fallback_meta"))
        geo.append(dict(id=bid, b=bp, o=near[1] if near else None))
    s=station or {}   # 总平面图无站厅层数据；空 station 使站厅层规则(出口/商铺)不在此触发
    structured=dict(station=s, building_clearance=bcs)
    return structured, dict(W=W,H=H,scale=scale,geo=geo,cat_warns=cat_warns)

def annotate(pdf, info, findings, out_png):
    import fitz, cv2, numpy as np
    doc=fitz.open(pdf); pg=doc[0]
    sc=info["W"]/pg.rect.width
    pix=pg.get_pixmap(matrix=fitz.Matrix(sc,sc))
    im=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)
    im=cv2.cvtColor(im, cv2.COLOR_RGBA2BGR if pix.n==4 else cv2.COLOR_RGB2BGR).copy()
    by_id={}
    for f in findings: by_id.setdefault(f["target_id"],[]).append(f)
    for g in info["geo"]:
        fs=by_id.get(g["id"],[])
        if any(f["passed"] is False for f in fs): col=(0,0,230)       # 红=不合规
        elif any(f["review_required"] for f in fs): col=(0,180,230)   # 黄=待复核
        else: col=(0,160,0)                                           # 绿=合规
        for poly in (g["b"],g["o"]):
            if poly is None: continue
            cv2.polylines(im,[np.array(poly.exterior.coords,np.int32)],True,col,3)
        if g["o"] is not None:
            a=g["b"].centroid.coords[0]; b=g["o"].centroid.coords[0]
            cv2.line(im,(int(a[0]),int(a[1])),(int(b[0]),int(b[1])),col,2)
    cv2.imwrite(out_png,im)

def main():
    ap=argparse.ArgumentParser(description="防火间距比对(接 rule_engine)")
    ap.add_argument("xml"); ap.add_argument("pdf"); ap.add_argument("out",nargs="?",default="fangju_out")
    ap.add_argument("--rules",default=DEFAULT_RULES)
    a=ap.parse_args()
    os.makedirs(a.out,exist_ok=True)
    structured, info = extract_structured(a.xml)
    n=len(structured["building_clearance"])
    print(f"周边建筑 {n}  比例尺≈{info['scale']*1000:.1f} mm/px" if info["scale"] else f"周边建筑 {n}  ⚠️无法标定比例尺")
    if info.get("cat_warns"):
        print("  ⚠️ 以下建筑类型暂无防火间距规则覆盖，需人工复核：")
        for nm, bt in info["cat_warns"]:
            print(f"     - {nm}（{bt or '类型未填'}）")
    findings = rule_engine.evaluate(a.rules, structured)
    summ = rule_engine.summarize(findings)
    print(f"== 防火间距评估: 触发 {summ['total']} 项 | PASS {summ['passed']}  FAIL {summ['failed']}  待复核 {summ['review_required']} ==")
    for f in findings:
        icon = "X" if f["passed"] is False else ("?" if f["review_required"] else "OK")
        m = "强" if f["mandatory"] else "宜"
        print(f"  [{icon}|{m}] {f['rule_id']}  {f['message']}")
        print(f"          来源: {f['source']}")
    annotate(a.pdf, info, findings, os.path.join(a.out,"fangju_annotated.png"))
    json.dump(dict(structured=structured, findings=findings),
              open(os.path.join(a.out,"fangju_result.json"),"w"), ensure_ascii=False, indent=2)
    print(f"标注图: {a.out}/fangju_annotated.png   结果: {a.out}/fangju_result.json")

if __name__=="__main__":
    main()
