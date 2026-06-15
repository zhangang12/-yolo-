#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量端到端  mvp_batch.py
=================================================
遍历一个目录下所有 jpg/png 底图，自动匹配到对应 PDF (按文件 stem 模糊匹配)，
对每张图跑 mvp_e2e，最后产出全站汇总报告。

用法：
  python mvp_batch.py <底图目录> <CVAT_XML 目录或文件> <PDF 搜索目录> <输出目录>

匹配逻辑：
  对每张底图 stem，去掉常见后缀 (000000000.jpeg-1 / 全局 / 示意 / 平面)，
  在 PDF 目录下递归 glob 同 stem 的 *.pdf；找不到记 ⚠️ 但仍跑 (review_required)。
"""
import os, sys, glob, json, argparse, re
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mvp_e2e


def _strip_noise(name):
    """规范化文件 stem，便于跨 jpg/pdf 匹配。"""
    s = os.path.splitext(os.path.basename(name))[0]
    # 移除 CVAT 渲染后缀
    s = re.sub(r"000000000\.jpeg-?\d*$", "", s)
    s = re.sub(r"\.jpeg-?\d*$", "", s)
    # 标准化分隔符
    s = s.replace("╱", "/").replace("╲", "\\")
    # 去多余空白
    s = " ".join(s.split())
    return s


def find_pdf(img_path, pdf_root):
    """在 pdf_root 递归找最匹配 img_path 的 PDF。"""
    img_norm = _strip_noise(img_path).lower()
    pdfs = glob.glob(os.path.join(pdf_root, "**", "*.pdf"), recursive=True)
    best = None; best_score = 0
    for pdf in pdfs:
        pn = _strip_noise(pdf).lower()
        # 计算公共子串覆盖率
        common = sum(1 for c in pn if c in img_norm)
        # 偏好长 stem 完全包含
        if pn in img_norm or img_norm in pn:
            score = 1000 + len(pn)
        else:
            score = common
        if score > best_score:
            best = pdf; best_score = score
    return best, best_score


def run(img_dir, xml_path, pdf_root, out_dir, station_meta=None):
    os.makedirs(out_dir, exist_ok=True)
    imgs = sorted(glob.glob(os.path.join(img_dir, "*.jpg")) + glob.glob(os.path.join(img_dir, "*.png")))
    print(f"========== 批量端到端: {len(imgs)} 张图 ==========\n")

    # 如果 xml_path 是文件，全部用同一个;如果是目录,按图找最匹配
    xml_files = []
    if os.path.isdir(xml_path):
        xml_files = glob.glob(os.path.join(xml_path, "**", "*.xml"), recursive=True)

    results = []
    for i, img in enumerate(imgs):
        print(f"--- [{i+1:2d}/{len(imgs)}] {os.path.basename(img)} ---")
        # 选 xml
        if os.path.isfile(xml_path):
            xml = xml_path
        else:
            # 找哪个 xml 里有这张图
            xml = None
            img_basename = os.path.basename(img)
            img_stem = os.path.splitext(img_basename)[0]
            for xf in xml_files:
                try:
                    txt = open(xf, encoding="utf-8").read()
                    if img_basename in txt or img_stem in txt:
                        xml = xf; break
                except Exception:
                    continue
            if not xml:
                print(f"  ⚠️ 未找到该图的 CVAT XML，跳过")
                continue
        # 找 PDF
        pdf, sc = find_pdf(img, pdf_root)
        if pdf:
            print(f"  匹配 PDF: {os.path.basename(pdf)}  (score={sc})")
        else:
            print(f"  ⚠️ 未找到对应 PDF; scale 将走兜底")
        # 单图输出子目录
        sub_out = os.path.join(out_dir, f"img_{i+1:02d}")
        os.makedirs(sub_out, exist_ok=True)
        try:
            ret = mvp_e2e.run(xml, img, sub_out, scale=None, station_meta=station_meta,
                              pdf_path=pdf)
            results.append({
                "image": os.path.basename(img),
                "pdf": os.path.basename(pdf) if pdf else None,
                "out_dir": sub_out,
                "summary": ret["summary"],
            })
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            results.append({"image": os.path.basename(img), "error": str(e)})

    # 汇总
    total = {"total": 0, "passed": 0, "failed": 0, "review_required": 0}
    by_rule_fail = defaultdict(int)
    for r in results:
        s = r.get("summary")
        if s:
            for k in total:
                total[k] += s.get(k, 0)
    # 写汇总 JSON + 总报告
    with open(os.path.join(out_dir, "_batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump({"by_image": results, "totals": total}, f, ensure_ascii=False, indent=2)

    md = ["# MVP 批量审查 汇总报告\n", f"图数: {len(results)}\n", "## 总览\n",
          f"- 触发规则数: **{total['total']}**",
          f"- PASS:       {total['passed']}",
          f"- **FAIL: {total['failed']}**",
          f"- REVIEW: {total['review_required']}", "",
          "## 各图明细\n",
          "| 图 | PDF | 触发 | PASS | FAIL | REVIEW |",
          "|---|---|---:|---:|---:|---:|"]
    for r in results:
        s = r.get("summary") or {}
        md.append(f"| {r['image'][:50]} | {r.get('pdf','-')[:30] if r.get('pdf') else '-'} | "
                  f"{s.get('total','-')} | {s.get('passed','-')} | "
                  f"**{s.get('failed','-')}** | {s.get('review_required','-')} |")
    md.append("")
    with open(os.path.join(out_dir, "_batch_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\n========== 批量完成 ==========")
    print(f"图数: {len(results)}  触发 {total['total']}  PASS {total['passed']}  "
          f"FAIL {total['failed']}  REVIEW {total['review_required']}")
    print(f"汇总报告: {os.path.join(out_dir, '_batch_report.md')}")


def main():
    ap = argparse.ArgumentParser(description="MVP 批量端到端")
    ap.add_argument("img_dir")
    ap.add_argument("xml")
    ap.add_argument("pdf_root")
    ap.add_argument("out_dir")
    ap.add_argument("--station-meta", default=None)
    a = ap.parse_args()
    sm = json.load(open(a.station_meta, encoding="utf-8")) if a.station_meta else None
    run(a.img_dir, a.xml, a.pdf_root, a.out_dir, station_meta=sm)


if __name__ == "__main__":
    main()
