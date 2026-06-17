# tools/ 模块索引

命令行脚本与核心库。本文件标注每个模块的**职责**、**被谁引用**、**活跃 / 归档**状态,
便于接手人快速判断哪些能改、哪些是兜底、哪些已废弃。

> 依赖关系由静态扫描 import 得出(2026-06)。"被谁引用"为空且无 CLI 价值的脚本已移入 `legacy/`。

---

## 端到端核心链(`mvp_e2e.py` 驱动)

拖入 PDF/CVAT → 结构化 → 规则比对 → 标注 → Word 报告,一条命令跑完。

| 模块 | 职责 | 被谁引用 |
|---|---|---|
| `mvp_e2e.py` ★ | 端到端编排(0→4 阶段),CVAT 或 YOLO 两种数据源 | `app/page_e2e.py`, `mvp_batch.py` |
| `anno_to_structured.py` | CVAT shapes → 规则引擎消费对象(MVP 中枢) | `mvp_e2e.py` |
| `scale_calibrate.py` | 从 PDF 矢量文字层读 "1:N" → m/px 比例尺 | `mvp_e2e.py` |
| `geom_measure.py` | 几何量测:面积 / 门净宽(洞口−0.15)/ 距离 | `anno_to_structured.py`, `evac_path.py` |
| `evac_path.py` ★2026-06 | 疏散路径:公共区栅格化 + 多源 Dijkstra(任一点→最近出口) | `mvp_e2e.py` `[1.5/4]` |
| `raster_ocr.py` ★2026-06 | 栅格 OCR 兜底:几何匹配补防火分区 `area_m2_design` | `mvp_e2e.py` `[1.6/4]` |
| `vector_extract.py` | 矢量层直读防火分区面积(OCR 之前的首选) | `raster_ocr.py`, `e2e_demo.py` |
| `rule_engine.py` | 规则引擎,37 条 / 8 大类,确定性 if-else | `app/page_rules.py`, `mvp_e2e.py`, `vector_extract.py` |
| `mvp_report_docx.py` ★ | 仿真实审查意见表 Word 报告(可交付) | `mvp_e2e.py` |
| `naming.py` | 产物命名(`站名-图类型`) | `mvp_e2e.py`, `app/page_e2e.py` |
| `tesseract_init.py` | Tesseract 自动探测(免配 PATH) | `raster_ocr.py`, `prelabel_pro.py`, `e2e_demo.py` |

## YOLO 识别

| 模块 | 职责 | 被谁引用 |
|---|---|---|
| `yolo_to_cvat.py` ★2026-06 | YOLO 推理结果 → CVAT XML 适配器(把 YOLO 接进端到端) | `mvp_e2e.py` |
| `sahi_predict.py` | SAHI 风格切片推理(大图小目标) | `yolo_to_cvat.py` |

## 批处理入口 / 标注工具

| 模块 | 职责 | 被谁引用 |
|---|---|---|
| `mvp_batch.py` | 批量端到端:遍历目录,每张图配 PDF 跑 `mvp_e2e` | CLI 入口(无人 import) |
| `fire_anno_tool.py` | 标注五件套:转换 / 质检 / 预标注 | `app/page_prepare.py`, `backend/build_dataset.py`, `backend/prelabel_pro.py`, `yolo_to_cvat.py` |

## 旧版(仍被客户端引用,暂留根目录)

> 这两个不是死代码——`app/page_e2e.py` 的 **④兜底路径**(无 CVAT、无 YOLO 时)仍走它们。
> 待客户端彻底切到 `mvp_e2e` 后可一并归档。

| 模块 | 职责 | 被谁引用 |
|---|---|---|
| `e2e_demo.py` | 旧版纯 PDF 端到端 demo(粗候选 + OCR) | `app/page_e2e.py` |
| `report_docx.py` | 旧版 Word 报告(吃 `e2e_demo` 产物) | `app/page_e2e.py` |

## legacy/ — 已归档(无人引用的早期脚本)

保留供追溯,不在任何活跃流程里;可直接 `python tools/legacy/xxx.py` 单跑(已修正 import 路径)。

| 模块 | 为何归档 |
|---|---|
| `legacy/e2e_steps.py` | 早期为绕开沙箱 45s 限制的分步 e2e,硬编码沙箱路径已失效 |
| `legacy/fangju_demo.py` | 总平面图防火间距比对 demo,功能已被 `rule_engine` 的 `building_clearance` 规则覆盖 |
| `legacy/replace_to_english.py` | 一次性中→英标签迁移脚本(标注规范定稿后不再需要) |
