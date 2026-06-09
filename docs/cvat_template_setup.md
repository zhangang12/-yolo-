# CVAT 项目模板导入指引（给标注公司）

锁死下拉菜单，从源头杜绝错名标签 / 非法属性键 / 拼写错误。

---

## 一句话

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
✅ **必填属性默认值** —— 漏填会用 `unknown` 兜底（如 `fire_rating`、`class`）

---

## 注意事项

### 历史项目要迁移吗？

**不强求**。已经标完的项目（6.8、6.9 批次）维持现状，本次模板从下批次开始用。

如果想把老项目也升级：
- 在老项目设置里同样贴 Raw 模板覆盖
- CVAT 会把已标对象的非法标签变成 `unknown` —— 需要人工修正
- 工作量较大，**不推荐**，除非项目方明确要求

### 想再加新标签怎么办？

**不要标注员自己改 Raw 模板**。流程：

1. 标注员告诉项目负责人："需要新增 XX 标签，理由是 ……"
2. 项目方评估后让开发组在 `tools/fire_anno_tool.py` 的 `LABELS` 里加这一条
3. 重新跑 `export-cvat-labels` 生成新 JSON
4. 项目负责人在 CVAT 项目设置里更新 Raw 模板
5. 通知标注员

这样**改动只有一个出口**，避免不同标注员各自加各自的版本。

### 标注员发现已经标了的对象，CVAT 报"unknown label"怎么办？

通常是项目模板更新后老对象没跟着升级。处理：

- 在 CVAT 任务页面右键该对象 → `Change label` → 选正确的标签
- 同时检查属性是否需要补填（按新模板的必填项）

### 如果项目方坚持不用 CVAT 后台模板？

**强烈不建议**。但如果一定要用旧的标注协议：

- 每次标完，**必须**先跑 `python tools/fire_anno_tool.py convert <xml>` 看有没有
  "🔴 漏转的标签"，有就回头改
- 然后 **必须**跑 `python tools/fire_anno_tool.py qc <xml>` 看有没有 ERROR
- ERROR 不归零绝对不能交付

没有模板锁死的话，每次都会出现新的命名变体（"注释""宽度线""楼梯/扶梯"……），
质检和后续规则引擎工作量翻倍。

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

---

## 跟之前的关系

这是建议 docx 里的【建议 2：CVAT 模板锁死】的落地。
另两条建议同步落地：

- 建议 1（关系标注校验）：QC 加了 4 条 ORPHAN 检查（`VAL_TEXT_ORPHAN` / `ROOM_TITLE_ORPHAN` /
  `WIDTH_LINE_ORPHAN` / `DIM_VAL_ORPHAN`），跑 `qc` 时自动校验
- 建议 3（无文字层兜底）：QC 报 `BAD_LABEL: '注释'` 时**附拆解指引**，明确告知
  "不要直接删，按内容拆到 fire_door.text_content / room_title / val_text / dimension_val"
