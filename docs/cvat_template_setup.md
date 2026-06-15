# CVAT 项目模板导入指引

锁死下拉菜单，从源头杜绝错名标签 / 非法属性键 / 拼写错误。

---

## 总结

`rules/cvat_labels.json` 这个文件，在 CVAT **新建项目**时一次性贴进去，
之后标注员的标签和属性值就**只能从下拉菜单选**，连"注释""宽度线""楼梯/扶梯"
这些规范里没有的标签都创建不出来。

---

## 操作步骤（5 分钟）

### 1. 拿到最新的 labels.json

从代码仓库取最新的 `rules/cvat_labels.json`（共 20 个标签，覆盖站厅层+总平面图所有受控词表）。
也可以本地用工具生成：

```bash
python tools/fire_anno_tool.py export-cvat-labels rules/cvat_labels.json
```

### 2. 在 CVAT 后台新建项目

CVAT 主界面 → 左上 `+` 按钮 → `Create new project`，填项目名（如"消防图纸标注_v2"）。

### 3. 切到 Raw 模式贴模板

在项目创建页面或项目设置页 → `Labels` 区域 → 右上角小开关切到 **"Raw"** 模式
（默认是 "Constructor" 可视化模式）→ 把 `rules/cvat_labels.json` 整个文件内容**复制粘贴**进去 → 点 **Save**。

CVAT 会自动解析，左侧出现 20 个标签：

- 总平面图 7 个：`station_exit_ground`、`vent_group_ground`、`surrounding_building`、
  `building_meta`、`vent_meta`、`dimension_val`、`fire_clearance_line`
- 站厅层 13 个：`fire_compartment`、`public_area`、`commercial_shop`、`safety_exit`、
  `stair_escalator`、`gate`、`fire_door`、`fire_shutter`、`draft_curtain`、
  `evac_distance_line`、`width_dimension_line`、`room_title`、`val_text`

### 4. 在该项目下建任务、分配图纸

之后所有任务都从这个项目派生，标签集自动继承，标注员开干。

---

## 导入后效果

✅ **下拉菜单锁死** —— 标注员只能从 20 个标签里选，输不进"注释""宽度线"等
✅ **属性枚举锁死** —— `fire_door.class` 只能选 甲级/乙级/unknown；
   `fire_compartment.zone_type` 只能选 公共区/无人区/有人值守区
✅ **类型锁死** —— `fire_door` 只能画矩形，`fire_compartment` 只能画多边形，
   `evac_distance_line` 只能画折线
✅ **颜色区分** —— 每类标签自带不同颜色，CVAT 标注视图一眼分清楚
✅ **必填属性默认值** —— 有 `unknown` 选项的（`fire_rating`/`class`/`building_type`/`discharge_type`）漏填兜底为 `unknown`；
   `zone_type`/`vent_function`/`swing_dir` 无 `unknown`、**默认留空强制主动选**，漏选会被 QC 报"缺必填"，不会静默落错值

---

## 工具命令一览（开发组维护用）

```bash
# 生成最新 CVAT 模板（每次 SCHEMA 改动后跑一次）
python tools/fire_anno_tool.py export-cvat-labels rules/cvat_labels.json

# 验证模板格式（JSON + 字段齐全）
python -c "import json; print(len(json.load(open('rules/cvat_labels.json',encoding='utf-8'))), '个标签')"

# 转换 CVAT 导出的 XML（中文→英文，已基本不需要，因为模板锁死后标签直接是英文）
python tools/fire_anno_tool.py convert annotations.xml annotations_en.xml

# 质检 CVAT 导出的 XML
python tools/fire_anno_tool.py qc annotations_en.xml [--type hall|site]
```



【建议 2：CVAT 模板锁死】的落地。
另两条建议同步落地：

- 建议 1（关系标注校验）：QC 加了 4 条 ORPHAN 检查（`VAL_TEXT_ORPHAN` / `ROOM_TITLE_ORPHAN` /
  `WIDTH_LINE_ORPHAN` / `DIM_VAL_ORPHAN`），跑 `qc` 时自动校验
- 建议 3（无文字层兜底）：QC 报 `BAD_LABEL: '注释'` 时**附拆解指引**，明确告知
  "不要直接删，按内容拆到 fire_door.text_content / room_title / val_text / dimension_val"
