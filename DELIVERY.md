# 消防建筑图 AI 预审系统 — 交付包

> 拖入 PDF 图纸 → 自动识别 → 结构化数据 → 与消防规范比对 → 在原图标注不合规位置 → 输出可交付的 Word 审查意见表。

---

## 一、30 秒了解项目

| 问题 | 答案 |
|---|---|
| 解决什么 | 把消防设计审查从"人工逐张图纸看"自动化成"AI 跑一遍 + 人工复核" |
| 输入 | 矢量 PDF 图纸(站厅层 / 总平面图) |
| 输出 | 5 个产物:标注图 jpg、规则评估 JSON、结构化 JSON、Markdown 简报、**Word 审查意见表 docx**(可交付) |
| 怎么用 | 桌面客户端(PySide6),拖入 PDF + 选 CVAT 标注 / YOLO 权重 → 一键预审 → 自动出报告 |
| 凭什么"AI" | YOLO-seg 模型(`best.pt` 77 MB)识别图上元素 + 规则引擎 37 条规则比对 + 几何量测把像素转米 |
| 关键资产 | `rules/rules.json`(37 规则) · `runs/.../best.pt`(YOLO 权重) · `data/anno_*/`(CVAT 标注真值) |

---

## 二、5 分钟快速开始

### 2.1 装依赖

```bash
# 项目根目录
pip install -r requirements.txt

# Tesseract OCR(仅栅格 PDF 才需要,矢量 PDF 无需装)
# Windows: 下载 UB-Mannheim/tesseract 安装到默认路径 C:\Program Files\Tesseract-OCR\
#          项目会自动探测,无需配 PATH(见 tools/tesseract_init.py)
```

### 2.2 启动客户端

```bash
python app/main.py
```

### 2.3 跑一份审查

1. 切到 **"端到端预审"** 标签
2. **图纸 PDF** 选你要审的 PDF
3. **方式①  CVAT 真值标注** 选 `data/anno_hall/站厅层平面图标注6.10/annotations.xml`(或留空让客户端自动匹配)
4. **方式②  YOLO 模型权重** 留空(同时填以 ① 为准)
5. **结果存到** 留空(自动在 PDF 旁建 `e2e_out/`)
6. 点 **开始预审**
7. 跑完点 **导出 Word 报告** → 用 Word 打开生成的 `<站名>-<图类型>_审查意见表.docx`

### 2.4 没 CVAT 标注?用 YOLO

1. 步骤 3 留空
2. 步骤 4 选 `models/fire_seg_gpu/best.pt`(随仓库交付的已训权重 77 MB)
3. 其余同上

---

## 三、系统架构(四个模块)

```
拖入 PDF → ①识别 ────────→ ②结构化 → ③规范比对 → ④原图标注
          (眼睛)            (算尺子)    (judge)      (手)
          YOLO + OCR        几何 + scale 规则引擎      标注图 + 徽章
          或 CVAT 真值
```

| 模块 | 干什么 | 核心文件 |
|---|---|---|
| ①识别 | 找元素、读文字 | `best.pt` + `tools/sahi_predict.py` + `tools/yolo_to_cvat.py`(YOLO 路径)<br>或 CVAT 标注员预先画好 |
| ②结构化 | 像素→米,标注→规则消费对象 | `tools/scale_calibrate.py`(从 PDF 读 1:N)+ `tools/geom_measure.py` + `tools/anno_to_structured.py` |
| ③规范比对 | 撞阈值、判违规、分类 | `tools/rule_engine.py` + `rules/rules.json`(37 条 / 8 大类) |
| ④标注 + 报告 | 红框画回 + Word 报告 | `tools/mvp_e2e.py` + `tools/mvp_report_docx.py` |

---

## 四、可交付物清单

### 4.1 代码(已入库 `main` 分支,与 origin/main 同步)

```
app/                                桌面客户端(PySide6)
├── main.py                         入口
├── page_prepare.py                 数据准备(预标注/质检/转换)
├── page_e2e.py                     端到端预审(★ 4 路径分流)
├── page_rules.py                   规则库(37 条可筛选)
├── page_train.py                   YOLO 训练
└── backend/
    ├── build_dataset.py            CVAT → YOLO-seg 数据集
    ├── train_yolo.py               训练/评估封装
    └── prelabel_pro.py             增强预标注

tools/                              核心命令行(模块索引见 tools/README.md)
├── mvp_e2e.py             ★       端到端一条命令(CVAT 或 YOLO)
├── mvp_batch.py                   批量端到端(整目录)
├── mvp_report_docx.py    ★       仿真实审查表 Word 报告
├── yolo_to_cvat.py       ★ 2026-06  YOLO 推理 → CVAT XML 适配器
├── sahi_predict.py                切片推理(SAHI 风格)
├── scale_calibrate.py             PDF 矢量层读 "1:N" → m/px
├── geom_measure.py                几何量测(面积/净宽/距离)
├── anno_to_structured.py          MVP 中枢:CVAT → 规则引擎对象
├── evac_path.py          ★ 2026-06  疏散路径(多源 Dijkstra)
├── raster_ocr.py         ★ 2026-06  栅格 OCR 兜底(防火分区面积)
├── vector_extract.py              矢量层直读防火分区面积
├── rule_engine.py                 规则引擎(37 条)
├── naming.py                      产物命名(站名-图类型)
├── tesseract_init.py              Tesseract 自动探测
├── fire_anno_tool.py              标注五件套
├── e2e_demo.py                    旧版 PDF 端到端(客户端 ④兜底仍引用)
├── report_docx.py                 旧版报告(客户端仍引用)
└── legacy/                        已归档(无人引用的早期脚本)
    ├── e2e_steps.py               早期分步 e2e(沙箱限制产物)
    ├── fangju_demo.py             防火间距比对 demo
    └── replace_to_english.py      一次性中→英标签迁移脚本

rules/                              规则知识库
├── rules.json                     37 条规则 / 8 大类
├── schema.md                      规则 schema 文档
└── cvat_labels.json               CVAT 项目模板(锁死下拉)

docs/                               文档(已分类归档)
├── guides/                         操作指南(怎么用)
│   ├── 客户端使用指南.html   ★       客户端使用指南
│   ├── 脚本使用指南.html  ★       脚本使用指南
│   ├── YOLO增量训练指南.html ★  YOLO 增量训练指南
│   ├── YOLO训练指南.md      YOLO 训练流程
│   ├── 预标注指南.md           预标注原理与流程
│   └── CVAT模板导入指南.md      CVAT 模板导入(给标注公司)
├── reference/                      规范/参考(是什么)
│   ├── 标注标签说明.md             标注标签 schema
│   ├── 规则引擎说明.md        规则引擎评审与设计
│   ├── 标注规范说明_详细版.md/.docx  标注规范 v3(给标注团队)
│   ├── 消防检查规则.docx           规则知识库来源(保密,不入 git)
│   └── Mvp任务.pdf                 需求说明(保密,不入 git)
└── archive/                        历史/过程文档
    ├── 任务分工_标注vs矢量直抽.md   分工:哪些标注、哪些直抽
    └── annotation_issues_整改单.md  标注数据问题清单(发标注单位)

examples/
└── sample_structured.json         覆盖全规则分支的样例数据
```

### 4.2 模型权重(已入库 `models/fire_seg_gpu/`,说明见 [models/fire_seg_gpu/README.md](models/fire_seg_gpu/README.md))

| 文件 | 大小 | 用途 |
|---|---|---|
| `models/fire_seg_gpu/best.pt` | 77.7 MB | YOLO11s-seg 训练产物(★推荐),16 类(fire_door/safety_exit/gate/fire_compartment 等) |
| `models/fire_seg_gpu/last.pt` | 77.7 MB | 末轮权重(断点续训) |
| `models/fire_seg_gpu/results.csv` · `args.yaml` | — | 训练指标(36 轮)+ 超参 |
| `models/fire_seg_gpu/val/*.png` | — | 验证报告(Box/Mask 的 P/R/F1/PR 曲线 + 混淆矩阵) |

> 整体 Box mAP50=0.49(被稀有类拖累),逐类 gate≈0.99 / vent≈0.98 / fire_door≈0.92。
> 客户端"方式② YOLO 权重"或 `mvp_e2e --yolo-weights models/fire_seg_gpu/best.pt` 直接调用。

### 4.3 数据

- `data/02 欧洲城站/` — 9 个站的矢量 PDF(嘉宾、欧洲城、明海、贝尔路、黄君山、石龙、上油松、侨社、珠光)
- `data/anno_hall/站厅层平面图标注6.10/` — 站厅层 9 张图 CVAT 标注 + 底图
- `data/anno_site/总平面图标注6.10/` — 总平面图 9 张图 CVAT 标注 + 底图

### 4.4 实测产物(嘉宾站站厅层验证)

5 个产物已实际跑出可对照:

```
e2e_out/
├── 嘉宾站-站厅层_e2e_fail.jpg      3.6 MB  标注图 + 红框 + 全局徽章
├── 嘉宾站-站厅层_findings.json     62 KB   147 规则触发 / 30 FAIL / 39 REVIEW / 78 PASS
├── 嘉宾站-站厅层_structured.json   23 KB   3 fc / 44 door / 3 exit / 0 shop / 4 evac
├── 嘉宾站-站厅层_report.md         2.4 KB  Markdown 简报
└── 嘉宾站-站厅层_审查意见表.docx   2.8 MB  ★ 可交付 Word(30 条不合规明细 + 嵌入标注图)
```

---

## 五、MVP 任务原文 5 项真实完成度

| # | 任务原文 | 状态 | 备注 |
|---|---|---|---|
| 1 | 识别站厅层 公共/设备/付费/非付费区(面) | 🟡 **3/4** | `fire_compartment` + `public_area` ✅;**付费区/非付费区从未拆**(schema 缺细分) |
| 2 | 识别安全出口/楼梯/扶梯/闸机/商铺(点) | ✅ **5/5** | YOLO 训出来,gate mAP=0.99 / fire_door=0.92;safety_exit/commercial_shop 训练实例<20 mAP 偏低 |
| 3 | 提取防火分区编号 + 面积(面+OCR) | ✅ **3/3** | `vector_extract.py` 矢量层直读 ✅;`raster_ocr.py` 栅格 OCR 兜底已接入 `mvp_e2e [1.6/4]`(矢量层缺失时按几何匹配补 `area_m2_design`) |
| 4 | 楼梯/走道/门宽(线+OCR) | 🟡 | `geom_measure.door_net_width_m`(洞口宽−0.15)+ width_line 关联实现;⚠️ 写死的 −0.15 扣减会让标准 1000mm 门集体误判 <0.9m(本版未修,门宽 FAIL 需人工复核),正解(读门型号代码)待跟进。详见[交付边界与已知限制](docs/reference/交付边界与已知限制.md) §3.1 |
| 5 | 公共区→安全出口疏散路径(点+面+图算法) | ✅ | `evac_path.py` 多源 Dijkstra(8 邻接 + 对角线 √2),公共区栅格化 + 寻路,接入 `mvp_e2e [1.5/4]`,派生 `EVAC-DERIVED-01` 喂规则引擎(嘉宾站实测 43.63m) |

---

## 六、关键命令速查(一行级)

```bash
# 启动客户端
python app/main.py

# 端到端 - 方式①(CVAT 真值)
python tools/mvp_e2e.py annotations.xml 底图.pdf out/ --pdf 底图.pdf

# 端到端 - 方式②(YOLO 自动识别)
python tools/mvp_e2e.py "" 底图.pdf out/ --pdf 底图.pdf --yolo-weights best.pt

# 批量整个站点目录
python tools/mvp_batch.py 底图目录 xml目录 pdf根目录 out/

# YOLO 训练(RTX 50 系参数)
python app/backend/train_yolo.py train data.yaml --model yolo11s-seg.pt \
    --epochs 50 --imgsz 1024 --batch 4 --workers 0 --no-amp --device 0

# 规则引擎 dry-run
python tools/rule_engine.py rules/rules.json --dry-run

# 标注质检
python tools/fire_anno_tool.py qc 标注.xml
```

---

## 七、深入文档索引

| 内容 | 文档 |
|---|---|
| **交付边界 / 已知限制 / 待跟进**(支持什么·不支持什么·依赖什么) | [docs/reference/交付边界与已知限制.md](docs/reference/交付边界与已知限制.md) |
| **tools/ 模块索引(活跃 / 归档一览)** | [tools/README.md](tools/README.md) |
| **客户端 4 页用法 + 截图说明** | [docs/guides/客户端使用指南.html](docs/guides/客户端使用指南.html) |
| **各脚本命令行 + 组合工作流** | [docs/guides/脚本使用指南.html](docs/guides/脚本使用指南.html) |
| **YOLO 增量训练 + 基线指标** | [docs/guides/YOLO增量训练指南.html](docs/guides/YOLO增量训练指南.html) |
| **CVAT 项目模板导入(给标注公司)** | [docs/guides/CVAT模板导入指南.md](docs/guides/CVAT模板导入指南.md) |
| **标注规范 v3(给标注团队)** | [docs/reference/标注规范说明_详细版.md](docs/reference/标注规范说明_详细版.md) |
| **规则引擎评审与设计** | [docs/reference/规则引擎说明.md](docs/reference/规则引擎说明.md) |
| **规则 schema** | [rules/schema.md](rules/schema.md) |
| **本文档(交付总览 / 进度 / 待办)** | [DELIVERY.md](DELIVERY.md) |
