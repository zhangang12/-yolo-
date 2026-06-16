#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
产物命名工具  naming.py
=================================================
从图纸文件名(常含设计院图号、全角斜杠等噪声)抽出"站名 + 图类型",
作为审查产物的简洁命名前缀。

例:
  5╱17╱D01╱S╱S02╱COO╱QT╱010003╱A_车站-嘉宾站站厅层平面示意图.pdf
  → 嘉宾站-站厅层

  001 石龙站 总平面图000000000.jpeg-1.jpg
  → 石龙站-总平面图

  01【25号线黄君山站】建筑平面图.pdf
  → 黄君山站-平面图

  01 明海-1-2-页面-2站厅层.pdf
  → 明海-站厅层

抽不出时退化为 safe_name(原 stem 短版)。

主接口: product_stem(img_path) → 安全的产物文件名前缀(站名-图类型)
"""
import os, re

# 已知站名表(本项目数据所及 + 常见深圳地铁站)。新增站直接追加。
# 优先级:含 token 即用,加上"站"后缀。
_KNOWN_STATIONS = [
    "明海", "珠光", "贝尔路", "黄君山", "上油松", "石龙", "侨社", "嘉宾", "欧洲城",
    # 后续新站补在这里
]

# 站名正则兜底:中文 2~5 字 + "站",但排除"车站"这种泛指词
_STATION_RE = re.compile(r"(?<![号车])([一-龥]{2,5}站)")

# 图类型按优先级匹配
_KIND_RULES = [
    (re.compile(r"总平面图?"), "总平面图"),
    (re.compile(r"站厅层(?:平面)?(?:示意)?(?:图)?"), "站厅层"),
    (re.compile(r"站台层(?:平面)?(?:示意)?(?:图)?"), "站台层"),
    (re.compile(r"建筑平面图"), "平面图"),
    (re.compile(r"建筑平剖面图"), "平剖面图"),
    (re.compile(r"建筑总平面图"), "总平面图"),
    (re.compile(r"平面布置图"), "平面布置图"),
    (re.compile(r"全局平面图"), "全局平面图"),
]

# Windows / 跨平台不可用作文件名的字符
_BAD_CHARS = re.compile(r"[\\/:*?\"<>|╱╲\s]+")  # ╱ ╲ 也包含


def safe_name(s, max_len=80):
    """清洗成可作文件名的安全字符串:替换非法字符为 -;长度截断。"""
    out = _BAD_CHARS.sub("-", s).strip("-_ ")
    out = re.sub(r"-{2,}", "-", out)
    if len(out) > max_len:
        out = out[:max_len].rstrip("-_")
    return out or "unnamed"


def extract_station(s):
    """从字符串里抽"XX站"。返回站名(含"站"),抽不到返回 None。

    优先级: 已知站名关键词表(精度 100%)→ 中文正则兜底。
    """
    # 1. 已知站名 token(最稳)
    for tok in _KNOWN_STATIONS:
        if tok in s:
            return tok + "站"
    # 2. 正则兜底,排除"车站"/"号X站"
    m = _STATION_RE.search(s)
    if m:
        cand = m.group(1)
        if cand not in ("车站",):
            return cand
    return None


def extract_kind(s):
    """从字符串里抽图类型。"""
    for pat, label in _KIND_RULES:
        if pat.search(s):
            return label
    return None


def product_stem(img_path):
    """从图纸文件路径派生短而清晰的产物文件名前缀。

    优先 "站名-图类型",抽不出退化为 safe_name(原 stem)。
    """
    raw = os.path.splitext(os.path.basename(img_path))[0]
    station = extract_station(raw)
    kind = extract_kind(raw)
    if station and kind:
        return f"{station}-{kind}"
    if station:
        return station
    if kind:
        return kind
    return safe_name(raw, 60)


# 自测
if __name__ == "__main__":
    samples = [
        r"5╱17╱D01╱S╱S02╱COO╱QT╱010003╱A_车站-嘉宾站站厅层平面示意图.pdf",
        r"5╱17╱D01╱S╱S02╱COO╱QT╱010001╱A_车站-嘉宾站总平面图.pdf",
        r"5-29-D02-S-S04-COO-QT-010001-A-总平面图.pdf",
        r"01 总平面图.pdf",
        r"001 石龙站 总平面图.pdf",
        r"01 明海-1-2-页面-2站厅层.pdf",
        r"02【25号线黄君山站】建筑平面图.pdf",
        r"5-25-D01-S-S13-COO-QT-01002A站厅层平面布置图.pdf",
        r"5╱17╱D01╱S╱S02╱COO╱QT╱010003╱A_车站-嘉宾站站厅层平面示意图000000000.jpeg-1.jpg",
    ]
    for s in samples:
        print(f"  {s[:60]:<60} → {product_stem(s)}")
