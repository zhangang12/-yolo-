#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
消防图纸标注 一体化工具  fire_anno_tool.py
=================================================
三合一：
  1) convert  ：CVAT 中文标签 -> 英文标签（带"漏转自检"，不再静默漏转）
  2) qc       ：按规则 schema 质检 CVAT 标注（格式 / 几何 / 完整性 / 数量异常）
  3) prelabel ：从【矢量】PDF 自动预标注（文字块 / 尺寸线 / 房间·分区多边形候选）
                生成 CVAT 文件，人工只需"审核+修正"，不必从零画。

用法：
  python fire_anno_tool.py convert  输入.xml  [输出.xml]
  python fire_anno_tool.py qc       标注.xml  [--type hall|site|auto]
  python fire_anno_tool.py prelabel 矢量图纸.pdf  [输出目录]  [--page 0] [--dpi 200]
  python fire_anno_tool.py all      标注.xml            # 先 convert 再 qc

依赖：pymupdf(fitz)、opencv-python、numpy  （prelabel 才需要 cv2/fitz；convert/qc 只用标准库）
所有阈值/标签表都集中在下面 SCHEMA 区，可直接改。
"""
import sys, os, re, json, argparse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

# =====================================================================
#  SCHEMA —— 规则配置区（要改标签/阈值/必填项，改这里即可）
# =====================================================================

# 中文 -> 英文 映射（标签 + 属性键）。已在原脚本基础上补全漏掉的键。
MAPPING = {
    # ---- 标签：总平面图 ----
    "车站地面出入口": "station_exit_ground",
    "风亭实体": "vent_group_ground", "地面通风机组": "vent_group_ground",
    "周边建筑": "surrounding_building",
    "防火间距线": "fire_clearance_line", "防火间距尺寸线": "fire_clearance_line",
    "建筑属性文字": "building_meta",
    "风亭属性文字": "vent_meta", "通风设施属性文字": "vent_meta",
    "防火间距尺寸数字": "dimension_val",
    # ---- 标签：站厅层 ----
    "防火分区": "fire_compartment",
    "站厅公共区": "public_area", "公共区": "public_area",
    "商铺": "commercial_shop",
    "安全出口": "safety_exit",
    "楼梯扶梯": "stair_escalator", "楼梯及自动扶梯": "stair_escalator",
    "闸机": "gate", "进出站闸机": "gate",
    "防火门": "fire_door",
    "防火卷帘": "fire_shutter", "特级防火卷帘": "fire_shutter",
    "挡烟垂壁": "draft_curtain",
    "疏散距离线": "evac_distance_line", "公共区最远疏散距离线": "evac_distance_line",
    "宽度控制线": "width_dimension_line", "控制通道宽度尺寸线": "width_dimension_line",
    "房间名称": "room_title",
    "数值及文本": "val_text",
    # ---- 属性键 ----
    "区域类型": "zone_type",
    "分类": "class",
    "门扇开启方向": "swing_dir",
    "宽度": "width",
    "文字内容": "text_content",
    "尺寸": "text_content",   # 补：原脚本漏了这个键，导致"尺寸"残留中文
    # ---- 属性键：详细版 v3 新增（周边建筑 / 风亭）----
    "名称": "name", "建筑名称": "name", "风亭名称": "name",
    "建筑类型": "building_type",
    "耐火等级": "fire_rating",
    "层数": "floors",
    "建筑高度": "height_m", "高度": "height_m",
    "风亭功能": "vent_function", "通风功能": "vent_function", "风口功能": "vent_function",
    "出风方式": "discharge_type", "排放方式": "discharge_type", "出风形式": "discharge_type",
    "面积": "area_m2", "风口面积": "area_m2",
    # fire_compartment 设计院声称面积（无文字层 + 主图无数字时由标注员从示意图抄录）
    "设计面积": "area_m2_design", "声称面积": "area_m2_design", "分区面积": "area_m2_design",
}

# 每个英文标签的：几何类型 / 所属图纸 / 允许的属性 / 必填属性
LABELS = {
    # group 'site' = 总平面图, 'hall' = 站厅层
    # ---- 总平面图：标注团队要标的图形/轮廓 ----
    "station_exit_ground":  dict(geom="polygon",  group="site", attrs=[], required=[]),
    # 修正：规范 (《标注规则》《标注规范说明_详细版》§6) 未把 vent_function/discharge_type 列为必填，
    # 属性键仍接受但不强制；强制只会误报。
    "vent_group_ground":    dict(geom="polygon",  group="site",
                                 attrs=["vent_function", "discharge_type", "name", "area_m2"],
                                 required=[]),
    # 依据《标注规范说明_详细版》§4.3（新版）属性细则：
    #   name / building_type / fire_rating = 必填；floors / height_m = 可选。
    # 说明：有文字层时这些属性由程序从文字层抽取，标注员不用填；无文字层时才人工录入，
    # 图上无信息一律填 unknown（仅 fire_rating/building_type 有 unknown 枚举值，name 留空）。
    # QC 这里发 WARN（非 ERROR），便于有/无文字层混编批次不影响交付。
    "surrounding_building": dict(geom="polygon",  group="site",
                                 attrs=["name", "building_type", "fire_rating", "floors", "height_m"],
                                 required=["name", "building_type", "fire_rating"]),
    # ---- 站厅层：标注团队要标的图形/轮廓 ----
    # area_m2_design = 设计院声称的分区面积（米²，数字）。两种填法：
    #   ① 有文字层 → 程序从文字层抽，写回此字段，标注员不用填
    #   ② 无文字层 + 主图/示意图上有面积数字 → 标注员从图上抄录（如 4967.59）
    #   ③ 无文字层 + 图上完全没标 → 留空（规则引擎会触发 review_required）
    # 该字段优先级 > 几何派生面积；规则引擎用此字段做 ≤5000㎡/≤1500㎡ 等合规判定。
    "fire_compartment":     dict(geom="polygon",  group="hall",
                                 attrs=["zone_type", "area_m2_design"],
                                 required=["zone_type"]),
    "public_area":          dict(geom="polygon",  group="hall", attrs=[], required=[]),
    "commercial_shop":      dict(geom="polygon",  group="hall", attrs=[], required=[]),
    # safety_exit 加 pair_id（与 evac_distance_line 起终点配对，可选）
    "safety_exit":          dict(geom="box",      group="hall", attrs=["pair_id"], required=[]),
    "stair_escalator":      dict(geom="box",      group="hall", attrs=[], required=[]),
    "gate":                 dict(geom="box",      group="hall", attrs=[], required=[]),
    # text_content：团队按规范§7关系绑定方案，把 FM甲1023 等型号文字填到 fire_door 属性
    # 这是允许的（详细版§7"主对象=防火门 box"），加入受控属性集避免误报 EXTRA_ATTR
    "fire_door":            dict(geom="box",      group="hall", attrs=["class", "swing_dir", "text_content"], required=["class", "swing_dir"]),
    "fire_shutter":         dict(geom="box",      group="hall", attrs=[], required=[]),
    "draft_curtain":        dict(geom="box",      group="hall", attrs=[], required=[]),
    # ---- 文字/尺寸类：v3 由程序从矢量直抽；仅【无文字层】图纸回退人工标，故非必现 ----
    "building_meta":        dict(geom="box",      group="site", attrs=["text_content"], required=["text_content"]),
    "vent_meta":            dict(geom="box",      group="site", attrs=["text_content"], required=["text_content"]),
    "dimension_val":        dict(geom="box",      group="site", attrs=["text_content"], required=["text_content"]),
    "fire_clearance_line":  dict(geom="polyline", group="site", attrs=["text_content"], required=[]),
    # evac_distance_line 改方案：沿设计院最远疏散折线人工标，替代 public_area。
    # text_content 填图纸上标注的距离数字（如 "48.6"），无则留空，程序自动用折线坐标累计长度。
    # pair_id 与对应 safety_exit 配对，便于程序生成"01 号疏散距离 = X米"报告。
    "evac_distance_line":   dict(geom="polyline", group="hall", attrs=["text_content", "pair_id"], required=[]),
    "width_dimension_line": dict(geom="polyline", group="hall", attrs=["width"],        required=[]),  # width 选填(可走几何)
    "room_title":           dict(geom="box",      group="hall", attrs=["text_content"], required=["text_content"]),
    "val_text":             dict(geom="box",      group="hall", attrs=["text_content"], required=["text_content"]),
}

# 枚举属性的允许取值
ENUMS = {
    "zone_type":      {"公共区", "无人区", "有人值守区"},
    "class":          {"甲级", "乙级", "unknown"},
    "swing_dir":      {"顺着疏散方向", "逆着疏散方向"},
    "vent_function":  {"新风", "排风", "活塞风", "冷却塔风", "排烟"},
    "discharge_type": {"侧出", "敞口", "高风亭", "unknown"},
    "building_type":  {"多层民用", "高层民用", "超高层民用", "丙类厂房",
                       "丁戊类厂房", "甲乙类厂房库房", "加油加气加氢站", "其他", "unknown"},
    "fire_rating":    {"一级", "二级", "三级", "四级", "unknown"},
}

# 每种图纸"必现"的类（缺了就报错 / 数量过少就警告）
MANDATORY = {
    # 总平面图：必有 surrounding_building（《详细版》§9 DoD）。
    # 站厅层：必有 ≥2 fire_compartment(含设备区)、safety_exit、gate（《详细版》§9）。
    # public_area 由方案调整：人工标 public_area 多边形 + 程序找最远点不可行（程序无法绕墙算路径），
    # 改为人工沿设计院最远疏散折线标 evac_distance_line，每个公共区分区 ≥1 条 →
    # 因此 public_area 退出必现类、evac_distance_line 进入必现类(≥1)。
    "site": {"surrounding_building": 1},
    "hall": {"fire_compartment": 2, "safety_exit": 1, "gate": 1, "evac_distance_line": 1},
}

# 数量类规范阈值（仅用于"数量异常"提醒，不替代规则引擎）
COUNT_RULES = {"commercial_shop_max": 3}

# 判断 room_title 是否是"设备区房间"的关键词（用于"设备区分区缺失"检查）
EQUIP_ROOM_KW = ["机房", "泵房", "配电", "控制室", "通信", "信号", "环控", "气瓶",
                 "变电", "电缆", "风室", "管理", "票务", "站长", "备品", "工具",
                 "卫生间", "厕所"]

# ORPHAN 距离分级阈值（像素）：
# val_text / room_title 距离最近 fire_compartment 多边形的距离：
#   ≤ NEAR(=0 即在多边形内)            → ✅ 静默通过
#   0 < dist ≤ NEAR_THRESHOLD          → ✅ 静默通过（示意图风格：标题在轮廓上方）
#                                          + 房间在分区边界附近(规范§5"不计面积≠独立成区")
#   NEAR_THRESHOLD < dist ≤ FAR_THRESHOLD → 🔵 INFO（提示性，可能正常也可能漏归属）
#   dist > FAR_THRESHOLD                → 🟠 WARN（大概率框到角落小图/标题栏/示意图上）
ORPHAN_NEAR_THRESHOLD = 300  # ≤300px 视为在分区附近(含示意图风格"标题在轮廓上方"的常见 186-229px 偏移)
ORPHAN_FAR_THRESHOLD  = 500  # 300-500px 提示性，>500 才报警

# ORPHAN 类问题的处理指引（QC 报告末尾汇总打印一次，避免每条都重复）
ORPHAN_GUIDANCE = {
    "VAL_TEXT_ORPHAN": (
        f"val_text 距最近 fire_compartment >{ORPHAN_FAR_THRESHOLD}px，大概率框错位置。常见原因：\n"
        "      ①框在了图角的「防火分区示意图」小图上 → 移到主图实际分区位置\n"
        "      ②框在了标题栏/图例区域 → 移到主图分区位置\n"
        "      ③主图本身没有面积数字、只有示意图有 → 直接删 val_text，改在 fire_compartment\n"
        "         多边形的 area_m2_design 属性里填面积（从示意图抄录）"
    ),
    "VAL_TEXT_NEAR_ORPHAN": (
        f"val_text 距最近 fire_compartment 在 {ORPHAN_NEAR_THRESHOLD}-{ORPHAN_FAR_THRESHOLD}px 之间，"
        "提示性检查。请人工核实是否归属正确分区。"
    ),
    "ROOM_TITLE_ORPHAN": (
        f"room_title 距最近 fire_compartment >{ORPHAN_FAR_THRESHOLD}px，大概率位置错。\n"
        "      可能原因：①框在了示意图小图或标题栏 → 移到主图位置\n"
        "                ②房间确实在站厅层结构外（如出入口附属建筑）→ 不该标 room_title，请删除"
    ),
    "ROOM_TITLE_NEAR_ORPHAN": (
        f"room_title 距最近 fire_compartment 在 {ORPHAN_NEAR_THRESHOLD}-{ORPHAN_FAR_THRESHOLD}px 之间，"
        "提示性检查。常见情况：\n"
        "      ①示意图风格：标题印在分区轮廓上方 → 正常，无需修改\n"
        "      ②规范《详细版》§5：「泵房/厕所『不计面积』≠『独立成区』」→ 该房间属于相邻分区，正常\n"
        "      ③确实定位错 → 请人工核实"
    ),
    "WIDTH_LINE_ORPHAN": (
        "width_dimension_line 两端 80px 内应有 fire_door/stair_escalator/gate/safety_exit（它在标这些实体的宽度）。\n"
        "      处理：找到这条尺寸线在标谁的宽度，修正端点位置贴到对应实体上；或如果是误标就删掉。"
    ),
    "DIM_VAL_ORPHAN": (
        "dimension_val 框 150px 内应有 fire_clearance_line（它在标某条防火间距的数值）。\n"
        "      处理：调整 dimension_val 框的位置使其紧邻对应的防火间距线。"
    ),
}

# QC 报 BAD_LABEL 时给出的拆解指引（标注公司可能误以为"删了就过"，要明确告知"按内容拆"）
LABEL_GUIDANCE = {
    "注释": (
        "千万不要直接删！按内容拆解：\n"
        "      · FM甲1023 等防火门型号 → 移到对应 fire_door box 的 text_content 属性\n"
        "      · 防火间距 13.92m 等数字 → dimension_val + text_content=\"13.92\"\n"
        "      · 环控机房 等房间名 → room_title + text_content=\"环控机房\"\n"
        "      · 防火分区面积 1133.6㎡ → val_text + text_content（整段框）\n"
        "      如该图有文字层（建筑名能 Ctrl+C 复制），直接删即可（程序会从文字层抽）。"
    ),
    "宽度线": "应改为 width_dimension_line（规范名）",
    "楼梯/扶梯": "应改为 stair_escalator（规范名，不要带斜杠）",
    "风亭/风亭组": "应改为 vent_group_ground（规范名，不要带斜杠）",
    "车站地面出入口/疏散口": (
        "应改为 station_exit_ground（规范名）。\n"
        "      若确实要区分'地面出入口'与'紧急疏散口'，请先报项目方批准新增子类。"
    ),
}

# ---------- CVAT 模板导出（建议2：模板锁死） ----------
# 给每个标签分配 CVAT 项目里显示用的颜色（按图纸功能区分）
LABEL_COLORS = {
    # 总平面图
    "station_exit_ground":  "#2E86DE",
    "vent_group_ground":    "#10AC84",
    "surrounding_building": "#8E44AD",
    "building_meta":        "#5F6A6A",
    "vent_meta":            "#1ABC9C",
    "dimension_val":        "#E67E22",
    "fire_clearance_line":  "#E74C3C",
    # 站厅层
    "fire_compartment":     "#FF6B6B",
    "public_area":          "#48CAE4",
    "commercial_shop":      "#F39C12",
    "safety_exit":          "#00B894",
    "stair_escalator":      "#6C5CE7",
    "gate":                 "#FDCB6E",
    "fire_door":            "#D63031",
    "fire_shutter":         "#A29BFE",
    "draft_curtain":        "#74B9FF",
    "evac_distance_line":   "#00CEC9",
    "width_dimension_line": "#FFA502",
    "room_title":           "#7F8C8D",
    "val_text":             "#9B59B6",
}

# 几何类型 -> CVAT type
_CVAT_GEOM_MAP = {"box": "rectangle", "polygon": "polygon", "polyline": "polyline"}

# 属性是否可变（mutable=True 的标好后还能改；如 text 内容）
_MUTABLE_ATTRS = {"text_content", "pair_id", "name", "area_m2", "area_m2_design", "floors", "height_m", "width"}

ALLOWED_LABELS = set(LABELS)
ALLOWED_ATTR_KEYS = {a for v in LABELS.values() for a in v["attrs"]}
ZH = re.compile(r"[一-鿿]")


# =====================================================================
#  公共：解析 CVAT
# =====================================================================
def load_cvat(path):
    tree = ET.parse(path)
    root = tree.getroot()
    imgs = root.findall(".//image")
    return tree, root, imgs

def shape_points(el):
    """返回 [(x,y),...]，box/polygon/polyline 统一处理"""
    if el.tag == "box":
        return [(float(el.get("xtl")), float(el.get("ytl"))),
                (float(el.get("xbr")), float(el.get("ybr")))]
    pts = el.get("points")
    if not pts:
        return []
    return [tuple(map(float, p.split(","))) for p in pts.split(";")]

def guess_group(imgs):
    """根据出现的标签猜这是总平面图(site)还是站厅层(hall)"""
    labs = {el.get("label") for im in imgs for el in list(im)}
    site = sum(1 for l in labs if l in LABELS and LABELS[l]["group"] == "site")
    hall = sum(1 for l in labs if l in LABELS and LABELS[l]["group"] == "hall")
    # 也看中文名
    name = (imgs[0].get("name") if imgs else "") or ""
    if "总平面" in name: return "site"
    if "站厅" in name:   return "hall"
    return "site" if site >= hall else "hall"


# =====================================================================
#  1) CONVERT —— 中文转英文 + 漏转自检
# =====================================================================
def convert(in_path, out_path=None):
    out_path = out_path or in_path.replace(".xml", "") + "_en.xml"
    tree = ET.parse(in_path)
    root = tree.getroot()

    converted = Counter()
    unmapped_labels = Counter()
    unmapped_attrs = Counter()

    for elem in root.iter():
        # label 属性
        if "label" in elem.attrib:
            old = elem.attrib["label"]
            if old in MAPPING:
                elem.attrib["label"] = MAPPING[old]; converted[old] += 1
            elif ZH.search(old):
                unmapped_labels[old] += 1
        # attribute name
        if elem.tag == "attribute" and "name" in elem.attrib:
            old = elem.attrib["name"]
            if old in MAPPING:
                elem.attrib["name"] = MAPPING[old]; converted[old] += 1
            elif ZH.search(old) or old in {".", ""}:
                unmapped_attrs[old] += 1
        # meta <labels><label><name>中文</name>
        if elem.tag == "name" and elem.text in MAPPING:
            elem.text = MAPPING[elem.text]

    tree.write(out_path, encoding="utf-8", xml_declaration=True)

    print(f"[convert] 已写出: {out_path}")
    print(f"[convert] 成功转换 {sum(converted.values())} 处 ({len(converted)} 种名称)")
    ok = True
    if unmapped_labels:
        ok = False
        print("  🔴 漏转的【标签】(MAPPING 里没有，仍是中文)：")
        for k, v in unmapped_labels.items(): print(f"      {k!r}  x{v}")
    if unmapped_attrs:
        ok = False
        print("  🔴 漏转/异常的【属性键】：")
        for k, v in unmapped_attrs.items(): print(f"      {k!r}  x{v}")
    if ok:
        print("  ✅ 没有残留中文/异常键，转换干净。")
    else:
        print("  ⚠️  上面这些没被转换 —— 请在 MAPPING 里补齐后重跑（这正是原脚本会静默漏掉的）。")
    return out_path, ok


# =====================================================================
#  2) QC —— 质检
# =====================================================================
class Report:
    def __init__(self):
        self.items = []  # (level, code, msg)
    def add(self, level, code, msg):
        self.items.append((level, code, msg))
    def counts(self):
        c = Counter(l for l, _, _ in self.items)
        return c
    def dump(self):
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        icon = {"ERROR": "🔴", "WARN": "🟠", "INFO": "🔵"}
        # 聚合完全相同的 (level,code,msg)，避免同一条刷屏
        agg = Counter((l, c, m) for l, c, m in self.items)
        for (lvl, code, msg), n in sorted(agg.items(), key=lambda x: order.get(x[0][0], 9)):
            tail = f"  (x{n})" if n > 1 else ""
            print(f"  {icon.get(lvl,'')} [{lvl}] {code}: {msg}{tail}")

def iou_box(a, b):
    (ax0, ay0), (ax1, ay1) = a; (bx0, by0), (bx1, by1) = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0: return 0.0
    ua = (ax1-ax0)*(ay1-ay0) + (bx1-bx0)*(by1-by0) - inter
    return inter / ua if ua > 0 else 0.0


def _point_in_poly(px, py, poly):
    """射线法判定 (px, py) 是否在 polygon 内部。poly = [(x,y), ...]"""
    n = len(poly)
    if n < 3: return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and \
           (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _box_center(box):
    """box = ((x0,y0),(x1,y1)) → (cx, cy)"""
    return ((box[0][0] + box[1][0]) / 2.0, (box[0][1] + box[1][1]) / 2.0)


def _min_dist_point_to_boxes(p, boxes):
    """点到一组矩形的最小距离（用矩形最近点近似）"""
    px, py = p
    best = float("inf")
    for (x0, y0), (x1, y1) in boxes:
        cx = max(x0, min(px, x1))
        cy = max(y0, min(py, y1))
        d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        if d < best: best = d
    return best


def _min_dist_point_to_polylines(p, polylines):
    """点到一组折线的最小距离（用顶点最近近似，演示足够）"""
    px, py = p
    best = float("inf")
    for line in polylines:
        for x, y in line:
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < best: best = d
    return best

def qc(path, dtype="auto"):
    tree, root, imgs = load_cvat(path)
    rep = Report()
    if not imgs:
        rep.add("ERROR", "NO_IMAGE", "文件里没有 <image> 节点")
        print(f"\n===== QC: {os.path.basename(path)} =====")
        rep.dump(); return rep

    group = guess_group(imgs) if dtype == "auto" else dtype
    per_label = Counter()
    boxes_by_label = defaultdict(list)
    room_titles = []
    zone_types = []
    # 疏散方案：按图收集 evac_distance_line / safety_exit 的 pair_id 与折线长度，做配对一致性检查
    evac_per_image = defaultdict(list)   # img_name -> list of (pair_id, text_content, pts)
    safety_per_image = defaultdict(list) # img_name -> list of pair_id
    # 必填属性按"图×标签×属性"分桶聚合（替代原 per-shape WARN，区分 全空/部分填/全填）
    req_fill = defaultdict(lambda: [0, 0])  # (img_name, label, attr_key) -> [total, filled]
    # P2 几何关系派生检查：按图收集各类对象（含 text_content 用于报错时定位）
    # img -> label -> list of (pts, text_content)
    geom_per_image = defaultdict(lambda: defaultdict(list))

    for im in imgs:
        # 每张图各自的宽高（修复：原来用 imgs[0] 的 W/H 比较所有图，会误报越界）
        W = float(im.get("width") or 0); H = float(im.get("height") or 0)
        img_name = im.get("name") or ""
        for el in list(im):
            lab = el.get("label")
            per_label[lab] += 1

            # --- 标签合法性 ---
            if lab not in ALLOWED_LABELS:
                lvl = "ERROR" if ZH.search(lab or "") else "WARN"
                guidance = LABEL_GUIDANCE.get(lab, "")
                msg = f"非法/未知标签 {lab!r}（不在受控词表）"
                if guidance:
                    msg += f"\n      💡 处理建议：{guidance}"
                rep.add(lvl, "BAD_LABEL", msg)
                continue
            spec = LABELS[lab]

            # --- 几何类型 & 合法性 ---
            pts = shape_points(el)
            geom = el.tag
            if not pts:
                rep.add("ERROR", "EMPTY_GEOM", f"{lab} 没有坐标")
            else:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                if W and H and (min(xs) < -1 or min(ys) < -1 or max(xs) > W+1 or max(ys) > H+1):
                    rep.add("WARN", "OUT_OF_BOUNDS", f"{lab} 坐标超出图像范围")
                if geom == "polygon" and len(pts) < 3:
                    rep.add("ERROR", "BAD_POLYGON", f"{lab} 多边形点数 <3 ({len(pts)})")
                if geom == "box":
                    w = abs(pts[1][0]-pts[0][0]); h = abs(pts[1][1]-pts[0][1])
                    if w < 1 or h < 1:
                        rep.add("ERROR", "DEGENERATE_BOX", f"{lab} 退化框(零面积)")
                    boxes_by_label[lab].append(((min(p[0] for p in pts),min(p[1] for p in pts),),
                                                (max(p[0] for p in pts),max(p[1] for p in pts),)))

            # --- 属性键合法性 + 必填 + 枚举 ---
            present = {}
            for a in el.findall("attribute"):
                k = a.get("name"); v = (a.text or "").strip()
                present[k] = v
                if k not in spec["attrs"]:
                    if k in {".", ""} or ZH.search(k or ""):
                        rep.add("WARN", "JUNK_ATTR", f"{lab} 存在异常属性键 {k!r}（建议清理/改名）")
                    else:
                        rep.add("INFO", "EXTRA_ATTR", f"{lab} 含未声明属性 {k!r}")
                if k in ENUMS and v and v not in ENUMS[k]:
                    rep.add("WARN", "BAD_ENUM", f"{lab}.{k} 取值 {v!r} 不在允许集 {sorted(ENUMS[k])}")
            # 必填属性：先聚合，循环结束后按 全空/部分填/全填 分级报告
            for req in spec["required"]:
                req_fill[(img_name, lab, req)][0] += 1
                if present.get(req):
                    req_fill[(img_name, lab, req)][1] += 1

            if lab == "room_title":
                room_titles.append(present.get("text_content", ""))
            if lab == "fire_compartment":
                zone_types.append(present.get("zone_type", ""))
            if lab == "evac_distance_line":
                evac_per_image[img_name].append((
                    present.get("pair_id", ""),
                    present.get("text_content", ""),
                    pts if pts else []
                ))
            if lab == "safety_exit":
                pid = present.get("pair_id", "")
                if pid:
                    # safety_exit.pair_id 允许逗号分隔多个值（一个出口对应多条疏散路径）
                    # 例如 "01,02" 表示该 safety_exit 同时是 01 和 02 号疏散线的终点
                    for p in re.split(r"[,，;；\s]+", pid):
                        p = p.strip()
                        if p: safety_per_image[img_name].append(p)
            # P2 几何关系派生：按图收集，循环结束后做 ORPHAN 检查（含 text 用于定位）
            # 包含 safety_exit：width_dimension_line 端点可能在标 safety_exit 的宽度
            if lab in ("val_text", "room_title", "fire_compartment", "fire_door",
                       "stair_escalator", "gate", "safety_exit", "width_dimension_line",
                       "dimension_val", "fire_clearance_line"):
                tc = present.get("text_content", "")
                geom_per_image[img_name][lab].append((pts if pts else [], tc))

    # --- 必填属性按"图×标签×属性"分级 ---
    # 该图同标签同属性全部留空 → 推断"有文字层、程序代填"，发 INFO 提示
    # 全部填了 → ✅ 不报
    # 部分填部分空 → 发 WARN（数据质量问题）
    for (im_name, lab, req), (total, filled) in sorted(req_fill.items()):
        base_nm = os.path.basename(im_name)
        if filled == 0:
            rep.add("INFO", "ATTR_ALL_BLANK",
                    f"[{base_nm}] {lab}.{req} {total}/{total} 全部留空 — 如果该图【有文字层】请忽略（程序会代抽）；如果【无文字层】请补齐，看不到填 unknown")
        elif filled < total:
            rep.add("WARN", "ATTR_PARTIAL",
                    f"[{base_nm}] {lab}.{req} 仅填了 {filled}/{total}（部分填部分空），请核实是否有漏填")

    # --- P2 几何关系派生 ORPHAN 检查 ---
    # 阈值（像素）：在 1024² 以上的高清底图上的经验值
    ORPHAN_NEAR_PX = 80     # 尺寸线/数值与对应实体的就近阈值
    ORPHAN_NEAR_PX_FAR = 150
    orphan_codes_seen = set()  # 收集触发过的 ORPHAN 类型，末尾统一打印指引

    def _min_dist_point_to_poly(p, poly):
        """点到 polygon 顶点的最小距离（近似）"""
        px, py = p
        return min(((px - x) ** 2 + (py - y) ** 2) ** 0.5 for x, y in poly)

    for im in imgs:
        nm = im.get("name") or ""
        base_nm = os.path.basename(nm)
        g = geom_per_image.get(nm, {})
        if not g: continue
        # 取出 fire_compartment 多边形（用于"中心点在分区内"判定）
        fc_polys = [shape for shape, _ in g.get("fire_compartment", []) if shape and len(shape) >= 3]
        # box 简化为左上右下两点
        def _to_box(pts):
            if not pts: return None
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            return ((min(xs), min(ys)), (max(xs), max(ys)))
        # 邻接对象（站厅层 width_dimension_line 端点要找的目标）
        # 包含 safety_exit：规范 GB 50016 5.5.18 关心疏散门/疏散口的净宽，
        # 标注员可能用 width_dimension_line 标 safety_exit 的宽度（这是合理用法）
        hall_anchors = []
        for k in ("fire_door", "stair_escalator", "gate", "safety_exit"):
            for pts, _ in g.get(k, []):
                b = _to_box(pts)
                if b: hall_anchors.append(b)
        # 总平面 dimension_val 邻接目标
        site_lines = [pts for pts, _ in g.get("fire_clearance_line", []) if pts]

        # ① val_text 距最近 fire_compartment 多边形的距离分级判断
        # ≤300px 视为正常（含示意图风格"标题在轮廓上方 186-229px"的情况）
        # 300-500px → INFO（提示性，可能正常）
        # >500px → WARN（大概率框到角落小图）
        for pts, tc in g.get("val_text", []):
            b = _to_box(pts)
            if not b or not fc_polys: continue
            cx, cy = _box_center(b)
            if any(_point_in_poly(cx, cy, poly) for poly in fc_polys):
                continue  # 在多边形内，直接通过
            dist = min(_min_dist_point_to_poly((cx, cy), p) for p in fc_polys)
            if dist <= ORPHAN_NEAR_THRESHOLD:
                continue  # 在阈值内，视为正常（示意图标题在轮廓上方）
            tc_show = (tc or "<空>")[:30]
            if dist <= ORPHAN_FAR_THRESHOLD:
                rep.add("INFO", "VAL_TEXT_NEAR_ORPHAN",
                        f"[{base_nm}] val_text 'text_content={tc_show}' 中心 ({cx:.0f},{cy:.0f}) 距最近 fire_compartment {dist:.0f}px — 请人工核实归属（{ORPHAN_NEAR_THRESHOLD}-{ORPHAN_FAR_THRESHOLD}px 提示带）")
                orphan_codes_seen.add("VAL_TEXT_NEAR_ORPHAN")
            else:
                rep.add("WARN", "VAL_TEXT_ORPHAN",
                        f"[{base_nm}] val_text 'text_content={tc_show}' 中心 ({cx:.0f},{cy:.0f}) 距最近 fire_compartment {dist:.0f}px (>{ORPHAN_FAR_THRESHOLD}px) — 大概率位置错")
                orphan_codes_seen.add("VAL_TEXT_ORPHAN")

        # ② room_title 同上的距离分级
        for pts, tc in g.get("room_title", []):
            b = _to_box(pts)
            if not b or not fc_polys: continue
            cx, cy = _box_center(b)
            if any(_point_in_poly(cx, cy, poly) for poly in fc_polys):
                continue
            dist = min(_min_dist_point_to_poly((cx, cy), p) for p in fc_polys)
            if dist <= ORPHAN_NEAR_THRESHOLD:
                continue  # 在阈值内，视为正常（规范§5"不计面积≠独立成区"+示意图风格）
            tc_show = (tc or "<空>")[:20]
            if dist <= ORPHAN_FAR_THRESHOLD:
                rep.add("INFO", "ROOM_TITLE_NEAR_ORPHAN",
                        f"[{base_nm}] room_title '{tc_show}' 中心 ({cx:.0f},{cy:.0f}) 距最近 fire_compartment {dist:.0f}px — 请人工核实归属（{ORPHAN_NEAR_THRESHOLD}-{ORPHAN_FAR_THRESHOLD}px 提示带）")
                orphan_codes_seen.add("ROOM_TITLE_NEAR_ORPHAN")
            else:
                rep.add("WARN", "ROOM_TITLE_ORPHAN",
                        f"[{base_nm}] room_title '{tc_show}' 中心 ({cx:.0f},{cy:.0f}) 距最近 fire_compartment {dist:.0f}px (>{ORPHAN_FAR_THRESHOLD}px) — 大概率位置错")
                orphan_codes_seen.add("ROOM_TITLE_ORPHAN")

        # ③ width_dimension_line 两端点都应有就近实体（fire_door/stair/gate），<80px
        if hall_anchors:
            for pts, _ in g.get("width_dimension_line", []):
                if not pts or len(pts) < 2: continue
                ep = [pts[0], pts[-1]]   # 取折线两个端点
                orphan_ends = sum(1 for p in ep
                                  if _min_dist_point_to_boxes(p, hall_anchors) > ORPHAN_NEAR_PX)
                if orphan_ends == 2:
                    rep.add("WARN", "WIDTH_LINE_ORPHAN",
                            f"[{base_nm}] width_dimension_line 两端 ({pts[0][0]:.0f},{pts[0][1]:.0f})-({pts[-1][0]:.0f},{pts[-1][1]:.0f}) >{ORPHAN_NEAR_PX}px 内都没有 fire_door/stair_escalator/gate/safety_exit — 请核实关联实体")
                    orphan_codes_seen.add("WIDTH_LINE_ORPHAN")

        # ④ dimension_val 框中心 <150px 内应有 fire_clearance_line（总平面）
        if site_lines and g.get("dimension_val"):
            for pts, tc in g.get("dimension_val", []):
                b = _to_box(pts)
                if not b: continue
                cx, cy = _box_center(b)
                if _min_dist_point_to_polylines((cx, cy), site_lines) > ORPHAN_NEAR_PX_FAR:
                    tc_show = (tc or "<空>")[:20]
                    rep.add("WARN", "DIM_VAL_ORPHAN",
                            f"[{base_nm}] dimension_val '{tc_show}' ({cx:.0f},{cy:.0f}) {ORPHAN_NEAR_PX_FAR}px 内没有 fire_clearance_line — 请核实归属")
                    orphan_codes_seen.add("DIM_VAL_ORPHAN")

    # --- 必现类缺失 ---
    for lab, need in MANDATORY.get(group, {}).items():
        got = per_label.get(lab, 0)
        if got == 0:
            rep.add("ERROR", "MISSING_CLASS", f"[{group}图] 必现类 {lab} 一个都没有")
        elif got < need:
            rep.add("WARN", "TOO_FEW", f"[{group}图] {lab} 仅 {got} 个（期望≥{need}）")

    # --- 数量异常 ---（修正：规范《站厅层平面图要点》"商铺≤3" 是【单张图】上限，原 QC 错把跨图汇总当上限）
    shop_per_image = []
    for im in imgs:
        n = sum(1 for el in list(im) if el.get("label") == "commercial_shop")
        if n: shop_per_image.append((im.get("name") or "", n))
    for nm, n in shop_per_image:
        if n > COUNT_RULES["commercial_shop_max"]:
            rep.add("WARN", "SHOP_OVER",
                    f"[{os.path.basename(nm)}] commercial_shop={n} 超过单张图规范上限{COUNT_RULES['commercial_shop_max']}")
    shop = per_label.get("commercial_shop", 0)
    if shop > 5 and shop == per_label.get("room_title", -1):
        rep.add("WARN", "SHOP_EQ_ROOM", f"commercial_shop 与 room_title 数量相同({shop})，疑似把每个房间都标成了商铺")

    # --- 站厅层：疏散方案 per-image 检查 ---
    if group == "hall":
        for im in imgs:
            nm = im.get("name") or ""
            base_nm = os.path.basename(nm)
            evac_list = evac_per_image.get(nm, [])
            if not evac_list:
                rep.add("ERROR", "NO_EVAC_LINE",
                        f"[{base_nm}] 没有 evac_distance_line（每张站厅图至少 1 条最远疏散折线）")
                continue
            # pair_id 重复检查
            pids = [p for p, _, _ in evac_list if p]
            dup = [k for k, v in Counter(pids).items() if v > 1]
            if dup:
                rep.add("WARN", "DUP_PAIR_ID",
                        f"[{base_nm}] evac_distance_line 的 pair_id 重复：{sorted(set(dup))}")
            # pair_id ↔ safety_exit 配对
            sx_pids = set(safety_per_image.get(nm, []))
            for p, _, _ in evac_list:
                if p and sx_pids and p not in sx_pids:
                    rep.add("INFO", "NO_EXIT_PAIR",
                            f"[{base_nm}] evac_distance_line pair_id={p!r} 找不到匹配的 safety_exit.pair_id")
            # text_content 数值合理性（≤100m 区间，>100 像 m 单位写错或 cm）
            for p, tc, _ in evac_list:
                if tc:
                    try:
                        v = float(re.sub(r"[^\d.]", "", tc) or "0")
                        if v <= 0 or v > 100:
                            rep.add("WARN", "EVAC_TEXT_RANGE",
                                    f"[{base_nm}] evac_distance_line text_content={tc!r} 数值不合理（应为米，0–100）")
                    except ValueError:
                        rep.add("WARN", "EVAC_TEXT_NONNUM",
                                f"[{base_nm}] evac_distance_line text_content={tc!r} 不是数字（应填距离米数，如 48.6）")
            # 折线退化检查（点数 < 2 不可能是折线；点数 = 2 是直线，允许但提示）
            for p, _, pts in evac_list:
                if len(pts) < 2:
                    rep.add("ERROR", "EVAC_TOO_SHORT",
                            f"[{base_nm}] evac_distance_line 点数 <2，无法构成折线")
                elif len(pts) == 2:
                    rep.add("INFO", "EVAC_STRAIGHT",
                            f"[{base_nm}] evac_distance_line 只有 2 个点（直线）—— 请确认是否需要沿走道加拐点")

    # --- 设备区分区缺失（站厅层）---
    if group == "hall":
        has_equip_room = any(any(kw in (t or "") for kw in EQUIP_ROOM_KW) for t in room_titles)
        has_equip_zone = any(z in {"无人区", "有人值守区"} for z in zone_types)
        if has_equip_room and not has_equip_zone:
            rep.add("WARN", "NO_EQUIP_ZONE",
                    "图中有设备区房间(机房/泵房/配电等)，但没有一个 fire_compartment 的 zone_type 是无人区/有人值守区 —— 设备区分区可能漏标")

    # --- 重复/重叠 ---
    for lab, bxs in boxes_by_label.items():
        for i in range(len(bxs)):
            for j in range(i+1, len(bxs)):
                if iou_box(bxs[i], bxs[j]) > 0.85:
                    rep.add("INFO", "DUP_SHAPE", f"{lab} 有两个框高度重叠(IoU>0.85)，疑似重复标注")
                    break

    # --- 打印 ---
    print(f"\n===== QC: {os.path.basename(path)}  (判定为 [{group}] 图) =====")
    print("  每类数量:", dict(per_label))
    c = rep.counts()
    print(f"  结果: 🔴ERROR {c.get('ERROR',0)}  🟠WARN {c.get('WARN',0)}  🔵INFO {c.get('INFO',0)}")
    rep.dump()
    # ORPHAN 类指引（汇总打印一次，避免每条 ORPHAN 都重复一遍）
    if orphan_codes_seen:
        print("\n  ──── ORPHAN 问题处理指引 ────")
        for code in sorted(orphan_codes_seen):
            print(f"  💡 {code}:")
            print(f"      {ORPHAN_GUIDANCE.get(code, '请人工核实归属')}")
    verdict = "❌ 不通过(有ERROR)" if c.get("ERROR") else ("⚠️  可用但需修正(有WARN)" if c.get("WARN") else "✅ 通过")
    print("  验收:", verdict)
    return rep


# =====================================================================
#  3) PRELABEL —— 矢量 PDF 自动预标注
# =====================================================================
def prelabel(pdf_path, out_dir=None, page_no=0, dpi=200):
    import fitz, cv2, numpy as np
    out_dir = out_dir or os.path.dirname(os.path.abspath(pdf_path))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]

    doc = fitz.open(pdf_path)
    page = doc[page_no]
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)

    # 渲染底图
    pix = page.get_pixmap(matrix=mat)
    img_name = f"{base}_p{page_no}.jpg"
    img_path = os.path.join(out_dir, img_name)
    pix.save(img_path)
    Wp, Hp = pix.width, pix.height

    draws = page.get_drawings()
    print(f"[prelabel] 页 {page_no}: {Wp}x{Hp}px, 矢量对象 {len(draws)}")

    # ---- A. 文字块候选：把"小尺寸矢量笔画"按邻近聚成块 ----
    small = []
    for d in draws:
        r = d["rect"]
        w = (r.x1 - r.x0) * scale; h = (r.y1 - r.y0) * scale
        if 0 < w < 60 and 0 < h < 60:   # 文字笔画级别
            small.append([r.x0*scale, r.y0*scale, r.x1*scale, r.y1*scale])
    text_boxes = _cluster_boxes(small, gap=12, min_members=4)

    # ---- B. 线候选：长直 H/V 段 ----
    hlines, vlines = [], []
    for d in draws:
        for it in d["items"]:
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                x0*=scale; y0*=scale; x1*=scale; y1*=scale
                L = ((x1-x0)**2 + (y1-y0)**2) ** 0.5
                if L > 120:
                    if abs(y1-y0) < 1.5: hlines.append((x0, y0, x1, y1, L))
                    elif abs(x1-x0) < 1.5: vlines.append((x0, y0, x1, y1, L))
    line_cands = _pick_dim_lines(hlines, vlines, top=40)

    # ---- C. 房间/分区多边形候选：栅格化线 -> 闭合区域 ----
    region_polys = _region_candidates(draws, scale, Wp, Hp, cv2, np)

    # ---- 写 CVAT ----
    out_xml = os.path.join(out_dir, f"{base}_prelabel.xml")
    _write_cvat(out_xml, img_name, Wp, Hp, text_boxes, line_cands, region_polys)

    print(f"[prelabel] 文字块候选 {len(text_boxes)}  线候选 {len(line_cands)}  区域候选 {len(region_polys)}")
    print(f"[prelabel] 底图:  {img_path}")
    print(f"[prelabel] 预标:  {out_xml}")
    print("[prelabel] 注意：以上均为【自动候选, source=auto_review】，需人工确认语义(哪个是分区/房间/尺寸线)后再用。")
    # 叠加预览
    _overlay(img_path, out_dir, base, text_boxes, line_cands, region_polys, cv2, np)
    return out_xml


def _cluster_boxes(boxes, gap=12, min_members=4):
    """简单网格并查集聚类：把邻近的小框聚成文字块"""
    if not boxes: return []
    import math
    n = len(boxes)
    parent = list(range(n))
    def find(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    # 网格加速
    cell = 40
    grid = defaultdict(list)
    for i, b in enumerate(boxes):
        cx = int((b[0]+b[2])/2 // cell); cy = int((b[1]+b[3])/2 // cell)
        grid[(cx, cy)].append(i)
    for (cx, cy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh += grid.get((cx+dx, cy+dy), [])
        for i in idxs:
            for j in neigh:
                if i < j:
                    bi, bj = boxes[i], boxes[j]
                    if (bi[0]-gap < bj[2] and bj[0]-gap < bi[2] and
                        bi[1]-gap < bj[3] and bj[1]-gap < bi[3]):
                        union(i, j)
    groups = defaultdict(list)
    for i in range(n): groups[find(i)].append(i)
    out = []
    for g in groups.values():
        if len(g) < min_members: continue
        xs0 = min(boxes[i][0] for i in g); ys0 = min(boxes[i][1] for i in g)
        xs1 = max(boxes[i][2] for i in g); ys1 = max(boxes[i][3] for i in g)
        if (xs1-xs0) < 8 or (ys1-ys0) < 6: continue
        if (xs1-xs0) > 1200: continue
        out.append((xs0, ys0, xs1, ys1))
    return out


def _pick_dim_lines(hlines, vlines, top=40):
    """挑出最像尺寸线的：取较长、按长度排序的若干条（演示级）"""
    alll = sorted(hlines + vlines, key=lambda t: -t[4])[:top]
    return [(x0, y0, x1, y1) for (x0, y0, x1, y1, L) in alll]


def _region_candidates(draws, scale, Wp, Hp, cv2, np):
    """把所有线画到 mask 上 -> 闭运算 -> 找闭合轮廓 -> 当作房间/分区候选"""
    mask = np.zeros((Hp, Wp), np.uint8)
    for d in draws:
        for it in d["items"]:
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                cv2.line(mask, (int(x0*scale), int(y0*scale)),
                               (int(x1*scale), int(y1*scale)), 255, 1)
            elif it[0] == "re":
                r = it[1]
                cv2.rectangle(mask, (int(r.x0*scale), int(r.y0*scale)),
                                    (int(r.x1*scale), int(r.y1*scale)), 255, 1)
    # 闭合细缝
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    cnts, _ = cv2.findContours(closed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    area_img = Wp * Hp
    for c in cnts:
        a = cv2.contourArea(c)
        if a < area_img * 0.0008 or a > area_img * 0.05:   # 太小=噪声, 太大=整图外框
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.01 * peri, True)
        if len(approx) < 4 or len(approx) > 12:
            continue
        xs=[p[0][0] for p in approx]; ys=[p[0][1] for p in approx]
        if (max(xs)-min(xs)) > Wp*0.75 or (max(ys)-min(ys)) > Hp*0.75:
            continue   # 跳过横跨整张的外框
        polys.append([(int(p[0][0]), int(p[0][1])) for p in approx])
    # 控制数量，取面积较大的前 N
    polys = sorted(polys, key=lambda p: -cv2.contourArea(np.array(p)))[:30]
    return polys


def _write_cvat(out_xml, img_name, W, H, text_boxes, lines, polys):
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"
    meta = ET.SubElement(root, "meta"); job = ET.SubElement(meta, "job")
    labels_el = ET.SubElement(job, "labels")
    for nm in ["room_title", "val_text", "width_dimension_line", "fire_compartment"]:
        le = ET.SubElement(labels_el, "label")
        ET.SubElement(le, "name").text = nm
    img = ET.SubElement(root, "image", id="0", name=img_name, width=str(W), height=str(H))
    for (x0, y0, x1, y1) in text_boxes:
        ET.SubElement(img, "box", label="val_text", source="auto_review", occluded="0",
                      xtl=f"{x0:.1f}", ytl=f"{y0:.1f}", xbr=f"{x1:.1f}", ybr=f"{y1:.1f}")
    for (x0, y0, x1, y1) in lines:
        ET.SubElement(img, "polyline", label="width_dimension_line", source="auto_review",
                      occluded="0", points=f"{x0:.1f},{y0:.1f};{x1:.1f},{y1:.1f}")
    for poly in polys:
        pstr = ";".join(f"{x:.1f},{y:.1f}" for x, y in poly)
        ET.SubElement(img, "polygon", label="fire_compartment", source="auto_review",
                      occluded="0", points=pstr)
    ET.ElementTree(root).write(out_xml, encoding="utf-8", xml_declaration=True)


# =====================================================================
#  4) EXPORT-CVAT-LABELS —— 从 LABELS/ENUMS 生成 CVAT 项目模板（建议2：模板锁死）
# =====================================================================
def export_cvat_labels(out_path=None):
    """生成 CVAT 项目可直接导入的 labels.json，锁死下拉菜单 + 属性枚举。

    标注公司在 CVAT 项目设置 → 'Raw' 模式贴入，标注员前端只能从受控词表里选。
    避免再出现"宽度线" / "楼梯/扶梯" / "注释" 等错名标签或非法属性键。
    """
    out_path = out_path or "rules/cvat_labels.json"
    labels_out = []
    for name, spec in LABELS.items():
        cvat_type = _CVAT_GEOM_MAP.get(spec["geom"], "any")
        attrs_out = []
        for a in spec["attrs"]:
            # 决定 input_type
            if a in ENUMS:
                vals = sorted(ENUMS[a])
                # 枚举值多时用 select，少时用 radio
                input_type = "radio" if len(vals) <= 4 else "select"
                # default_value：有 unknown 的用 unknown 兜底；
                # 没有 unknown 的(zone_type/vent_function/swing_dir)留空，强制标注员主动选(方案A)——
                # 漏选则该值为空，必填项会被 QC 的 MISSING_ATTR 抓出，
                # 避免静默落到一个"看似合法但可能错"的具体默认值。
                default = "unknown" if "unknown" in vals else ""
            elif a in ("area_m2", "area_m2_design", "height_m", "floors", "width"):
                # CVAT number 格式：values=["min;max;step"]
                input_type = "number"
                ranges = {
                    "area_m2": "0;100000;0.01",
                    "area_m2_design": "0;100000;0.01",   # 设计院声称分区面积(㎡)
                    "height_m": "0;300;0.1",
                    "floors": "0;100;1",
                    "width": "0;10;0.01",
                }
                vals = [ranges.get(a, "0;1000;0.01")]
                default = "0"
            else:
                # 文本类（text_content / pair_id / name 等）
                input_type = "text"
                vals = [""]
                default = ""
            attrs_out.append({
                "name": a,
                "input_type": input_type,
                "mutable": a in _MUTABLE_ATTRS,
                "values": vals,
                "default_value": default,
            })
        labels_out.append({
            "name": name,
            "color": LABEL_COLORS.get(name, "#808080"),
            "type": cvat_type,
            "attributes": attrs_out,
        })
    # 按 group 排序：先 site 后 hall（与规范行文一致）
    site_first = [l for l in labels_out if LABELS[l["name"]]["group"] == "site"]
    hall_then  = [l for l in labels_out if LABELS[l["name"]]["group"] == "hall"]
    labels_out = site_first + hall_then

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(labels_out, f, ensure_ascii=False, indent=2)
    print(f"[export-cvat-labels] 已生成: {out_path}")
    print(f"  共 {len(labels_out)} 个标签（site {len(site_first)} + hall {len(hall_then)}），覆盖所有受控词表。")
    print(f"  使用方法：CVAT 项目设置 → Labels → 切到 'Raw' 模式 → 粘贴本文件内容 → Save。")
    print(f"  标注员前端只能从下拉菜单选标签和属性值，无法自创。")
    return out_path


def _overlay(img_path, out_dir, base, text_boxes, lines, polys, cv2, np):
    # OpenCV 在 Windows 读不了中文路径，改用 fromfile+imdecode / imencode+tofile
    im = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        print(f"[prelabel] (叠加预览跳过：底图读取失败 {img_path})"); return
    for (x0, y0, x1, y1) in text_boxes:
        cv2.rectangle(im, (int(x0), int(y0)), (int(x1), int(y1)), (0, 170, 170), 2)
    for (x0, y0, x1, y1) in lines:
        cv2.line(im, (int(x0), int(y0)), (int(x1), int(y1)), (255, 0, 255), 2)
    for poly in polys:
        cv2.polylines(im, [np.array(poly, np.int32)], True, (0, 0, 255), 3)
    p = os.path.join(out_dir, f"{base}_prelabel_overlay.jpg")
    ok, buf = cv2.imencode(".jpg", im)
    if ok:
        buf.tofile(p)
    print(f"[prelabel] 叠加预览: {p}")


# =====================================================================
#  CLI
# =====================================================================
def main():
    ap = argparse.ArgumentParser(description="消防图纸标注 预标注+质检+转换 一体化工具")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("convert"); p1.add_argument("inp"); p1.add_argument("out", nargs="?")
    p2 = sub.add_parser("qc"); p2.add_argument("inp"); p2.add_argument("--type", default="auto", choices=["auto","hall","site"])
    p3 = sub.add_parser("prelabel"); p3.add_argument("pdf"); p3.add_argument("out", nargs="?")
    p3.add_argument("--page", type=int, default=0); p3.add_argument("--dpi", type=int, default=200)
    p4 = sub.add_parser("all"); p4.add_argument("inp"); p4.add_argument("--type", default="auto", choices=["auto","hall","site"])
    p5 = sub.add_parser("export-cvat-labels", help="生成 CVAT 项目可导入的 labels.json，锁死下拉菜单")
    p5.add_argument("out", nargs="?", default="rules/cvat_labels.json")

    a = ap.parse_args()
    if a.cmd == "convert":
        convert(a.inp, a.out)
    elif a.cmd == "qc":
        qc(a.inp, a.type)
    elif a.cmd == "prelabel":
        prelabel(a.pdf, a.out, a.page, a.dpi)
    elif a.cmd == "all":
        out, ok = convert(a.inp)
        qc(out, a.type)
    elif a.cmd == "export-cvat-labels":
        export_cvat_labels(a.out)
    else:
        ap.print_help()

if __name__ == "__main__":
    main()
