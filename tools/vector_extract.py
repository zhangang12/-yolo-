#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
矢量文字层直读  vector_extract.py
=================================================
矢量 PDF 的文字层里，设计院常把「防火分区名称 + 区域类型 + 面积」直接写成文字
（如：防火分区一（公共区）：面积3861.71m²）。本模块直接解析这些文字，产出
规则引擎要的结构化数据 —— 比 OCR 准、比几何重建简单，**不依赖标注/模型**。

定位（MVP）：覆盖 e2e 的「①识别 + ②结构化」中“图纸已写明数值”的部分，
主攻防火分区面积。读不到的指标（出口个数、净宽、门方向等）不在此模块，留给识别。

⚠️ 文字是设计院**声称值**，可能笔误/算错。本模块：
  - 明确标注来源 source="vector_text"（声称值，非实测）；
  - 做零成本自洽检查（分区面积之和、是否有矛盾）并随数据返回；
  - 真伪的几何复核留待 V2（几何精提取作校验器）。

用法：
  python vector_extract.py 图纸.pdf [--page 0] [--out structured.json] [--rules rules/rules.json]
"""
import sys, os, re, json, argparse, glob

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 中文数字序号
_CN_NUM = "一二三四五六七八九十"

# 区域类型关键词 → 规则引擎 zone_type
def _zone_type(zone_text):
    if "公共" in zone_text:
        return "public"
    if any(k in zone_text for k in ("设备", "无人", "有人", "管理")):
        return "equipment"
    return "unknown"


def _iter_blocks(page):
    """产出 (整块文字, bbox) ；bbox 为 PDF 点坐标 [x0,y0,x1,y1]。"""
    for b in page.get_text("dict").get("blocks", []):
        txt = "".join(sp["text"] for l in b.get("lines", []) for sp in l.get("spans", []))
        if txt.strip():
            yield txt, b.get("bbox", [0, 0, 0, 0])


def extract(pdf_path, page_no=0):
    """解析文字层，返回结构化数据 dict（含 station / fire_compartment / 自洽检查）。"""
    import fitz
    doc = fitz.open(pdf_path)
    page = doc[page_no]

    comps = {}   # 序号 -> 分区 dict（去重）
    # 兼容多种制图格式：
    #   防火分区一（公共区）：面积3861.71m   (欧洲城)
    #   防火分区一：4443㎡(公共区,...)        (嘉宾，整数无小数)
    #   防火分区三:1135.59m2有人值守区        (东莞，英文冒号、类型在后)
    # 思路：定位每个“防火分区X”，在其后窗口取第一个面积数字，再在前后窗口找区域关键词。
    seq_re = re.compile(rf"防火分区[（(]?([{_CN_NUM}])")
    area_re = re.compile(r"(\d{2,5}(?:\.\d{1,2})?)")
    zone_re = re.compile(r"(公共区|无人区|有人值守区|有人区|设备管理区|设备区|无人值守区)")
    for txt, bbox in _iter_blocks(page):
        if "防火分区" not in txt:
            continue
        for m in seq_re.finditer(txt):
            seq = m.group(1)
            window = txt[m.end(): m.end() + 40]      # 序号之后找面积
            am = area_re.search(window)
            if not am:
                continue
            area = float(am.group(1))
            if area < 50:                            # 面积过小→多半误抓，跳过
                continue
            zwin = txt[max(0, m.start() - 4): m.end() + 45]
            zm = zone_re.search(zwin)
            zone_text = zm.group(1) if zm else ""
            rec = dict(
                id=f"防火分区{seq}",
                zone_type=_zone_type(zone_text),
                area_m2=area,
                is_shared_concourse=False,
                _zone_text=zone_text,
                _source="vector_text",
                _bbox=[round(v, 1) for v in bbox],
                _raw=txt[:60],
            )
            # 去重：带区域类型的(正文)优先于无类型的(图例)；否则保留首次
            old = comps.get(seq)
            if old is None or (old["zone_type"] == "unknown" and rec["zone_type"] != "unknown"):
                comps[seq] = rec

    fcs = [comps[k] for k in sorted(comps)]

    # ---- 零成本自洽检查 ----
    checks = []
    if fcs:
        total = round(sum(f["area_m2"] for f in fcs), 2)
        checks.append(f"读到 {len(fcs)} 个防火分区，面积合计 {total}㎡（设计院声称值，未经几何复核）")
        unknown = [f["id"] for f in fcs if f["zone_type"] == "unknown"]
        if unknown:
            checks.append(f"⚠️ 区域类型未识别：{', '.join(unknown)} —— 影响适用规则，建议人工确认")

    structured = dict(
        source="vector_text",
        drawing=os.path.basename(pdf_path),
        station=dict(type="underground", height_m=0, transfer_lines=1),
        fire_compartment=fcs,
        _consistency=checks,
    )
    return structured


def main():
    ap = argparse.ArgumentParser(description="矢量 PDF 文字层直读 → 结构化数据")
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--out", help="结构化 JSON 输出路径")
    ap.add_argument("--rules", help="给定 rules.json 则顺便跑一遍规则引擎")
    a = ap.parse_args()

    data = extract(a.pdf, a.page)
    print(f"[文字层直读] {data['drawing']} 第{a.page}页")
    print(f"[文字层直读] 防火分区 {len(data['fire_compartment'])} 个：")
    for f in data["fire_compartment"]:
        print(f"    {f['id']} ({f['_zone_text'] or '?'}) {f['area_m2']}㎡  -> zone_type={f['zone_type']}")
    for c in data["_consistency"]:
        print(f"    · {c}")

    if a.rules:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import rule_engine
        findings = rule_engine.evaluate(a.rules, data)
        print("\n[规则引擎] 防火分区面积判定：")
        for f in findings:
            if f["target_type"] != "fire_compartment":
                continue
            v = {True: "✅通过", False: "❌不通过", None: "⚠️待复核"}[f["passed"]]
            print(f"    [{v}] {f['target_id']} {f['value']}㎡  规则≤{f['threshold']}  ({f['source']})")

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n[输出] {a.out}")


if __name__ == "__main__":
    main()
