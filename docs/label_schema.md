# 标注 Schema（标签词表）

英文 snake_case，**输出标签名必须 100% 与本表一致**，不得拼错、不得混用中文。质检工具 `fire_anno_tool.py` 内置此 schema，可直接校验。

## 总平面图（site）

| 标签 | 几何 | 属性 | 说明 |
|---|---|---|---|
| `station_exit_ground` | polygon | — | 出入口/地面垂直电梯/紧急疏散出口的地面投影 |
| `vent_group_ground` | polygon | — | 风亭(新风/排风/活塞)地面实体；高风亭标底座投影 |
| `surrounding_building` | polygon | text_content | 周边 50m/100m 建筑外墙投影 |
| `fire_clearance_line` | polyline | text_content | 防火间距尺寸线 |
| `building_meta` | box | text_content | 周边建筑属性文字（"二类高层住宅""耐火等级一级""8F"） |
| `vent_meta` | box | text_content | 风亭属性文字（"敞口""侧出""排风亭"） |
| `dimension_val` | box | text_content | 防火间距尺寸数字（如 15.02m） |

## 站厅层（hall）

| 标签 | 几何 | 属性 | 说明 |
|---|---|---|---|
| `fire_compartment` | polygon | **zone_type** | 沿防火墙/特级防火卷帘画闭合多边形 |
| `commercial_shop` | polygon | — | 非付费区商铺边界（注意规范≤3个，勿误标） |
| `fire_door` | box | **class, swing_dir** | 防火门实体符号 |
| `stair_escalator` | box | — | 扶梯/疏散楼梯实体符号 |
| `draft_curtain` | box | — | 挡烟垂壁示意符号 |
| `evac_distance_line` | polyline | text_content | 公共区最远疏散距离线（如 48.6m） |
| `width_dimension_line` | polyline | width* | 通道/楼梯/门净宽尺寸线（*宽度值建议走几何，见下） |
| `room_title` | box | text_content | 房间名称（环控机房、水泵房、厕所…） |
| `val_text` | box | text_content | 数值/字符（1800、1.4m、869.2㎡…） |

## 属性枚举（取值必须在集合内）

| 属性 | 允许取值 |
|---|---|
| `zone_type` | 公共区 / 无人区 / 有人值守区 |
| `class` | 甲级 / 乙级 |
| `swing_dir` | 顺着疏散方向 / 逆着疏散方向 |

## 关键约定

1. **宽度值优先走几何**：`width_dimension_line` 的宽度由"线长 × 比例尺"算，不必逐个手填属性；图上数字另由 `val_text`+OCR 提供，两者交叉校验。**门净宽 = 洞口宽 − 150mm**。
2. **多边形闭合**：`fire_compartment` 拐角必须贴墙内中线、不留缝；用防火卷帘分隔处，边界点须与卷帘线重合。
3. **房间名标全**：尤其水泵房/厕所——规范规定其面积"不计入防火分区面积"，需据此扣除。
4. **设备区分区别漏**：机房/泵房/配电室等设备区房间，必须有对应的 `fire_compartment`(zone_type=无人区/有人值守区)。

## YOLO class id 映射（训练时）

```yaml
# data.yaml 示例
names:
  0: safety_exit          # 注：站厅层"安全出口"若作独立对象需新增
  1: stair_escalator
  2: gate                 # 闸机（当前数据缺失，需补）
  3: commercial_shop
  4: fire_door
  5: val_text
  6: room_title
  # 分割类
  7: fire_compartment
  8: public_area / equip_area ...   # 若区分区域类型
```
> 检测类用矩形框，分割类用多边形；可用同一个 YOLO-seg 模型同时输出。
