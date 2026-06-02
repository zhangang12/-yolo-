# 预标注使用指南

预标注 = 让机器先把"候选标注"铺到图上，人工只需 **核对 + 修正**，而不是从零画。
它解决的是体力活（画、防漏、统一画法、提速），**判断对错仍需人工**（见文末"人工确认流程"）。

本项目有两个版本：

| 版本 | 脚本 | 特点 |
|---|---|---|
| **基础版** | `tools/fire_anno_tool.py prelabel` | 文字/线/区域三类候选，**不分子类**（都叫 val_text / width_dimension_line / fire_compartment） |
| **增强版** ⭐ | `app/backend/prelabel_pro.py` | 语义细分 + 内容填充(OCR可选) + 置信度分级 + 确认清单 + 接质检 |

> 推荐用增强版。客户端「数据准备 → 预标注」默认就是增强版。

---

## 一、它是怎么做到的（原理）

矢量 PDF 里墙、线、文字笔画都是**真实几何对象**（不是像素），用 `PyMuPDF` 的 `get_drawings()` 全部读出来，再用规则聚成候选：

1. **文字块**：文字在矢量里是一堆小笔画 → 用并查集把邻近小笔画聚成"一行字"的框。
2. **线**：挑出长的水平/垂直直线段（尺寸线、墙线），并记录描边**颜色**。
3. **区域**：把所有线画到一张 mask 上 → 形态学闭运算补缝 → 找闭合轮廓 = 一个个房间/分区。

> ⚠️ **重要前提**：本项目的图纸**没有文字层**（文字是用曲线"画"出来的，`get_text` 取不到）。
> 所以文字框只能定位"这里有字"，**内容必须靠 OCR**。装了 tesseract 才能读出内容并据此分类。

---

## 二、增强版相比基础版多了什么

1. **语义细分**（按 几何 / 颜色 / 图类型 / OCR内容）
   - 文字：数字→`dimension_val`/`val_text`；"机房/泵房/厕所"→`room_title`；"敞口/侧出"→`vent_meta`；"高层/耐火等级"→`building_meta`。
   - 线：站厅图按长度分 `evac_distance_line`(长) / `width_dimension_line`(短)；总平面图→`fire_clearance_line`；蓝色线置信度更高。
   - 区域：总平面图按面积分 `surrounding_building` / `station_exit_ground`；站厅图→`fire_compartment` 并**预填 `zone_type`**（内含"机房/泵房"等词→无人区）。
2. **内容填充**：OCR 读文字框内容写入 `text_content`（需 tesseract）。
3. **辅助人工确认**：
   - **置信度分级**：高置信标 `source=auto`（信任）、低置信标 `source=auto_review`（重点审）。
   - **颜色编码预览**：橙/红 = 待审，青/绿 = 较确定。
   - **确认清单 `_review.txt`**：主动列出"疑似漏分区""分区类型是猜的""未启用OCR"等待办。
   - **`--qc` 自检**：预标完自动跑质检。

---

## 三、怎么用

### A. 客户端（推荐）
1. `python app/main.py` → 左侧「数据准备」→ 顶部「预标注」。
2. 选矢量 PDF、输出目录；勾选「增强预标注」（默认开）。
3. 选**图类型**（关键！见下）、按需勾「OCR」「完成后自动质检」。
4. 点「运行」。右侧上方看叠加预览，下方看日志与确认清单。

### B. 命令行
```bash
# 增强版（总平面图，自动质检）
python app/backend/prelabel_pro.py docs/图纸.pdf out_dir --type site --qc

# 站厅图 + OCR 读内容（需先装 tesseract）
python app/backend/prelabel_pro.py docs/图纸.pdf out_dir --type hall --ocr --qc

# 多页一次跑
python app/backend/prelabel_pro.py 图纸.pdf out_dir --page all

# 基础版
python tools/fire_anno_tool.py prelabel docs/图纸.pdf out_dir --dpi 200
```

### 参数说明（增强版）
| 参数 | 默认 | 说明 |
|---|---|---|
| `--page` | `0` | 页码，或 `all` 跑全部页 |
| `--dpi` | `200` | 渲染底图分辨率，目标小就调高 |
| `--type` | `auto` | `site`(总平面) / `hall`(站厅) / `auto`(按文件名猜) |
| `--ocr` | 关 | 启用 OCR 读文字内容（需 tesseract） |
| `--qc` | 关 | 预标完自动跑质检 |

> **`--type` 很关键**：它决定用哪套标签和分类规则。`auto` 仅按文件名里的"总平面/站厅"判断，
> 文件名不含这些词时默认 `site`。**图纸是站厅/站台图时务必手动选 `hall`**，否则区域会被分成出入口而不是防火分区。

---

## 四、输出物
跑完在输出目录得到（`xxx` = PDF 名，`pN` = 页号）：
| 文件 | 用途 |
|---|---|
| `xxx_pN.jpg` | 渲染底图（CVAT 里给人看的图） |
| `xxx_pN_prelabel.xml` | **CVAT 预标文件**，导入 CVAT 即可审核 |
| `xxx_pN_overlay.jpg` | 叠加预览（橙/红=待审，青/绿=较确定，品红=线） |
| `xxx_pN_review.txt` | **人工确认清单**，列出待办与疑点 |

---

## 五、装 OCR（tesseract）
不装也能用（文字内容留空、统一待审），但装了才能自动填内容、分文字类型。
- **Windows**：装 [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) 安装包，勾选中文语言包，加入 PATH。
- **macOS**：`brew install tesseract tesseract-lang`
- **Ubuntu**：`sudo apt install tesseract-ocr tesseract-ocr-chi-sim`

装好后命令行加 `--ocr` 或客户端勾「OCR」重跑即可。

---

## 六、人工确认流程（预标注省不掉的一步）
预标注是**辅助**，进训练集前必须人工确认：
1. 把 `xxx_pN_prelabel.xml` 导入 CVAT，对照底图。
2. 先看 `_review.txt` 确认清单，按它逐条处理疑点。
3. 重点审 `source=auto_review`（预览里橙/红）的候选：删错的、改语义、修边界。
4. 补全文字内容（没 OCR 时）、确认每个 `fire_compartment` 的 `zone_type`。
5. 结合规范补"该有但图上没画"的（如设备区分区漏画）——这类机器变不出来。
6. 改完用 `python tools/fire_anno_tool.py qc 改好的.xml` 自检，通过后再进训练集。

---

## 七、当前局限（已知，待迭代）
- 文字框靠笔画聚类，密集处可能粘连或漏聚；区域靠"补缝"，缝太大会漏、补过头会粘连。
- 线/区域的子类是**启发式猜测**，置信度普遍偏低（设计上就让人重点审）。
- 颜色信息有限（本批图纸主要黑/灰，蓝色少量），颜色线索作用有限。
- 没装 OCR 时文字内容全空——这是图纸无文字层导致的硬限制，非脚本缺陷。
