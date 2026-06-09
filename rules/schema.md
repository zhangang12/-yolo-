# 规则表 Schema

## 一、文件位置

- 规则数据：`rules/rules.json`
- 引擎：`tools/rule_engine.py`

引擎读 `rules.json`，对一份"结构化数据"逐条评估，输出违规清单。

## 二、规则字段（每条规则）

| 字段 | 必填 | 说明 |
|---|---|---|
| `rule_id` | ✅ | 全局唯一 ID，如 `AREA-EQUIP-UG-001`，命名 `<大类>-<子类>-<序号>` |
| `name` | ✅ | 中文名（一句话） |
| `category` | ✅ | 分类：`防火分区面积` / `安全疏散` / `疏散宽度` / `安全出口` / `商铺` / `防排烟` / `防火门窗卷帘` 等 |
| `target` | ✅ | 作用对象类型，见下文"对象类型表" |
| `applies_when` | ✅ | 适用条件列表，按 AND 连接；空数组表示无条件适用。条目结构 `{path, op, value}` |
| `check` | ✅ | 判据，详见"判据类型" |
| `mandatory` | ✅ | true=强条（"不应/不得/严禁"），false=非强条（"不宜/宜"） |
| `severity` | ✅ | `critical` / `warning` / `info`。规范惯例：mandatory=true → critical；mandatory=false → warning |
| `source` | ✅ | 规范出处，如 `GB 51298-2018 4.3.2` |
| `message` | ✅ | 违规描述模板，占位符 `{target.<field>}` `{value}` `{threshold}` |

## 三、对象类型表（`target`）

引擎从结构化数据按对象类型取数。每个对象都应有唯一的 `id` 字段。

| target | 含义 | 必备字段 |
|---|---|---|
| `fire_compartment` | 防火分区 | `id`, `area_m2`, `zone_type`(`public`/`equipment_unmanned`/`equipment_staffed`/`platform`), `is_shared_concourse`, `exits`(数组), `effective_exits`(派生) |
| `evac_distance_line` | 疏散距离线 | `id`, `length_m`, `kind`(`any_to_exit`/`door_to_exit_between`/`door_to_exit_deadend`) |
| `exit` | 安全出口 | `id`, `leads_to`(`ground`/`other`), `direction_group`, `zone`(`public`/`equipment_unmanned`/`equipment_staffed`/`platform`) |
| `exit_pair` | 出口两两组合（由上游准备） | `id`, `exit_a`, `exit_b`, `relation`(`same_direction`/`adjacent`), `distance_m` |
| `door` | 门 | `id`, `clear_width_m`(=洞口宽-0.15), `swing_direction`(`evacuation`/`reverse`), `is_evacuation`, `position`(`between_exits`/`dead_end`/`other`), `zone`, `distance_to_nearest_exit_m`, `class`(`甲级`/`乙级`/`unknown`), `is_on_firewall`, `is_always_open` |
| `corridor` | 走道/楼梯 | `id`, `kind`(`evac_corridor`/`evac_stair`/`equip_corridor_single`/`equip_corridor_double`/`entrance_passage`/`ground_stair_landing`), `clear_width_m`, `length_m` |
| `shop` | 商铺 | `id`, `area_m2` |
| `shop_pair` | 商铺两两组合 | `id`, `shop_a`, `shop_b`, `opening_distance_m` |
| `fire_shutter` | 防火卷帘 | `id`, `opening_width_m`(洞口宽), `shutter_width_m`(卷帘宽) |
| `vent_pair` | 风口两两组合 | `id`, `kind`(`fresh_vs_exhaust_or_piston` 等), `distance_m`, `is_exhaust_higher`(派生：排风/活塞口是否高于新风口) |
| `building_clearance` | 出入口/风亭→周边建筑 防火间距 | `id`, `building_name`, `building_category`(`多层民用`/`高层民用`/`加油加气加氢站`), `distance_m`, `nearest_kind` |
| `station` | 站点全局对象 | `type`(`underground`/`at_grade`/`elevated`), `height_m`, `transfer_lines`, `public_zone`(含 `exits`, `commercial_shops`, `area_m2`), `equipment_zone`, `project_all_doors_class_A`(布尔：本工程是否承诺所有防火门均甲级) |

### 三 ✻ 标注源对象 → 规则消费对象的映射

标注团队在 CVAT 里产出的是**源对象**（多边形/框 + 属性），由预处理脚本派生成上表中的**规则消费对象**。两层 schema 不一致是正常的：源对象保留人工标注口径，消费对象贴近规则判据。

| 标注源对象（CVAT） | → 规则消费对象 | 字段映射说明 |
|---|---|---|
| `surrounding_building` polygon + `name` / `building_type` / `fire_rating` / `floors` / `height_m` 属性 | `building_clearance`（每条防火间距一条） | `building_name ← name`；`building_category ← building_type`；`distance_m` 由几何就近计算；每栋建筑可能对站点出入口/风亭分别派生多条 |
| `vent_group_ground` polygon + `vent_function` / `discharge_type` / `name` / `area_m2` 属性 | `vent_pair`（两两风亭组合，仅当一方=新风、另一方=排风/活塞风时生成） | `kind ← "fresh_vs_exhaust_or_piston"`（基于 vent_function 配对）；`distance_m` 由几何最近距离计算；`is_exhaust_higher ← discharge_type` 推断（排风/活塞=`高风亭` 且新风=`侧出/敞口` → `true`；同高度 → `false`；信息不足 → `null` 触发 review） |
| `fire_door` box + `class` / `swing_dir` 属性 | `door` | `class` 直接透传；`swing_direction ← swing_dir`（"顺着疏散方向"→`evacuation`，"逆着疏散方向"→`reverse`）；`clear_width_m` 由洞口宽 − 0.15 算出（洞口宽来自就近尺寸数字）；`is_on_firewall` 由几何派生：fire_door 中心点是否落在 `fire_compartment` 多边形边界或 `fire_shutter` 上；`is_always_open` 当前无源标注，需项目方在配置补 |
| `fire_compartment` polygon + `zone_type` 属性 | `fire_compartment` | `area_m2` 由几何面积 + 比例尺算出；`zone_type` 透传（公共区→`public`，**无人区→`equipment_unmanned`**，**有人值守区→`equipment_staffed`**）；`exits ←` 落在该多边形边界附近（< 2m）的 `safety_exit` 集合；`effective_exits ← exits ∪` 邻接分区共用防火墙上的"常开甲级防火门"（per GB 50157 § 28.2.3-3 注） |
| `evac_distance_line` polyline + `text_content` / `pair_id` 属性 | `evac_distance_line` | `length_m` ← 折线像素累计长度 × 图纸比例尺；`kind` 派生：终点最近的实体 = `safety_exit` → `any_to_exit`；终点是房间门 + 位置 = `between_exits` → `door_to_exit_between`；终点是房间门 + `dead_end` → `door_to_exit_deadend` |
| `safety_exit` box + `pair_id` 属性 | `exit` | `leads_to`：终点在地面图（站点出入口所在层）则 `ground`，否则 `other`；`zone` ← 该 safety_exit 所在 `fire_compartment.zone_type`；`direction_group` 用方位角聚类（同 ±15° 方向算一组） |

标注团队**只关心源对象层**，详见 [`docs/标注规范说明_详细版.md`](../docs/标注规范说明_详细版.md)。规则编辑者**只关心消费对象层**（上面那张主表）。

## 四、`applies_when` 条目

每条 `{path, op, value}`，AND 连接。`path` 支持：

- `target.<field>`：当前对象字段
- `station.<field>`：站点全局字段（如 `station.type`、`station.transfer_lines`）

支持的 `op`：

| op | 含义 |
|---|---|
| `eq` / `ne` | 等于 / 不等于 |
| `lt` / `le` / `gt` / `ge` | 数值比较 |
| `in` / `not_in` | 在列表中 / 不在列表中 |

例：
```json
"applies_when": [
  {"path": "station.type", "op": "eq", "value": "underground"},
  {"path": "target.zone_type", "op": "eq", "value": "equipment"}
]
```

## 五、`check` 判据

三种类型：

### 5.1 `compare`：单字段对比
```json
"check": {
  "type": "compare",
  "path": "target.area_m2",
  "op": "le",
  "threshold": 1500
}
```

**动态阈值**（阈值由数据字段算出，如卷帘"≤洞口宽/3 且 ≤20m"）：用
`threshold_path` 取阈值来源字段，可选 `threshold_scale`(乘系数) / `threshold_divisor`(除以) / `threshold_cap`(封顶)。
```json
"check": {
  "type": "compare", "path": "target.shutter_width_m", "op": "le",
  "threshold_path": "target.opening_width_m", "threshold_divisor": 3, "threshold_cap": 20
}
```

### 5.2 `count`：集合计数对比
```json
"check": {
  "type": "count",
  "collection_path": "target.exits",
  "filter": [{"path": "item.leads_to", "op": "eq", "value": "ground"}],
  "op": "ge",
  "threshold": 2
}
```

`filter` 内用 `item.<field>` 引用集合元素字段。`filter` 可省略（全计数）。

### 5.3 `sum_aggregate`：集合字段求和对比
```json
"check": {
  "type": "sum_aggregate",
  "collection_path": "target.public_zone.commercial_shops",
  "field": "area_m2",
  "op": "le",
  "threshold": 100
}
```

对集合内每个元素的 `field` 求和后比阈值（如商铺总面积 ≤100㎡）。可选 `filter`（同 count）。

## 六、评估结果

引擎对每条规则返回：

```json
{
  "rule_id": "AREA-EQUIP-UG-001",
  "target_id": "FC-EQ-03",
  "passed": false,                   // true/false/null(数据不足→待人工复核)
  "review_required": false,          // 关键字段缺失/不可计算时为 true
  "value": 1820.0,
  "threshold": 1500,
  "severity": "critical",
  "mandatory": true,
  "source": "GB 50157-2013 28.2.2-1 / GB 51298-2018 4.2.2",
  "message": "地下设备区 FC-EQ-03 面积 1820.0㎡，超规范上限 1500㎡"
}
```

## 七、关键口径（"不冲突"，按适用条件分支）

| 规则 | 地下 underground | 地上 at_grade | 高架 elevated(>24m) |
|---|---|---|---|
| 设备管理区分区面积 | ≤1500㎡ | ≤2500㎡ | ≤1500㎡ |

| 规则 | 同方向 same_direction | 相邻 adjacent |
|---|---|---|
| 两个安全出口净距 | ≥10m | ≥20m |

> **`exit_pair.relation` 派生口径**（预处理）：
> - `same_direction`：两个 exit 的 `direction_group` 相同（方位角 ±15° 内）
> - `adjacent`：两个 exit 不同方向、且欧氏距离 ≤ 50m
> - 其他：不生成 exit_pair（不触发该类规则）

> **`fire_compartment.is_shared_concourse` 数据来源**：
> 项目级配置字段（站点是否换乘共用站厅），不从 CVAT 标注派生。
> 由项目方在 `station.transfer_lines` 同时手填于 `examples/<station>_meta.json`，
> 由预处理脚本注入到对应公共区 fire_compartment。

> **`fire_compartment.zone_type` 四类语义**：
> | 标注源 zone_type | 消费层 zone_type | 含义 |
> |---|---|---|
> | 公共区 | `public` | 站厅公共活动区（付费区+非付费区） |
> | 无人区 | `equipment_unmanned` | 设备管理区，**无**长期值守人员（如变电、风室） |
> | 有人值守区 | `equipment_staffed` | 设备管理区，**有**长期值守人员（如车控室、票务用房）—— **必须 ≥1 出口直通地面** |
> | （站台层） | `platform` | 仅站台层平面图用，本批次无 |
>
> 跨"设备区"两子类的规则用 `op: in` 写并集：
> `{"path": "target.zone_type", "op": "in", "value": ["equipment_unmanned", "equipment_staffed"]}`

| 规则 | 单线 | 两线共用站厅 | 三线共用站厅 |
|---|---|---|---|
| 站厅公共区面积 | ≤5000㎡ (不宜) | ≤10000㎡ | ≤15000㎡，>10000 时应设自喷 |

> 这些不是"阈值冲突"，是不同适用条件下的不同分支——`applies_when` 把它们隔开，每条规则只在自己的场景里触发。

## 八、扩展规则的步骤

1. 在 `rules.json` 末尾追加一条，字段齐全。
2. 若引入新的 `target` 对象类型，需在结构化数据里产出该类型的数组。
3. 跑 `python tools/rule_engine.py --dry-run rules.json` 看新规则能不能被引擎解析。
4. 跑端到端 demo 验证。
