# 消防建筑图 AI 预审系统（MVP）

拖入 PDF 图纸 → 自动识别 → 结构化数据 → 与消防规范比对 → 在原图标注不合规位置。

> 📄 **新成员先读 [`DELIVERY.md`](DELIVERY.md)** —— 交付总览、架构、5 项任务完成度、已知限制、路线图全在那里。

## 目录结构

```
fire-review-mvp/
├── DELIVERY.md             交付文档（先读这个）
├── README.md              本文件（安装与用法）
├── requirements.txt
├── app/                    🖥️ 桌面客户端（PySide6）
│   ├── main.py             入口：python app/main.py
│   ├── ui_common.py        公共件（主题/文件行/图查看器/日志/子进程执行器）
│   ├── page_prepare.py     数据准备页（预标注/质检/转换）
│   ├── page_e2e.py         端到端预审页
│   ├── page_train.py       YOLO 训练页（建数据集/训练/评估）
│   └── backend/
│       ├── prelabel_pro.py   增强预标注（语义细分+内容+可审核）
│       ├── build_dataset.py  CVAT 标注 → YOLO-seg 数据集
│       └── train_yolo.py     ultralytics 训练/评估封装
├── tools/                  命令行脚本(模块索引见 tools/README.md)
│   ├── mvp_e2e.py          ★ 端到端一条命令(CVAT 或 YOLO)
│   ├── mvp_report_docx.py  ★ 仿真实审查表 Word 报告
│   ├── rule_engine.py        规则引擎(37 条)
│   ├── evac_path.py          疏散路径(多源 Dijkstra)
│   ├── fire_anno_tool.py     标注三件套：转换 / 质检 / 预标注
│   └── legacy/               已归档的早期脚本(e2e_steps / fangju_demo …)
└── docs/                   文档(已分类归档)
    ├── guides/                操作指南(client/scripts/prelabel/yolo 训练)
    ├── reference/             规范参考(label_schema / rule_engine_notes / 标注规范)
    └── archive/               历史过程文档(任务分工 / annotation_issues_整改单)
```

## 安装

```bash
pip install -r requirements.txt
# OCR 需系统级 tesseract:
#   Ubuntu:  sudo apt install tesseract-ocr            (中文再加 tesseract-ocr-chi-sim)
#   macOS :  brew install tesseract tesseract-lang
#   Windows: 装 UB-Mannheim/tesseract 安装包并加入 PATH
```

## 🖥️ 桌面客户端（推荐）

把下面三块命令行能力包成了一个 PySide6 图形客户端，拖文件即可用：

```bash
pip install -r requirements.txt
python app/main.py
```

四个标签页：

| 页面 | 功能 | 对应脚本 |
|---|---|---|
| **数据准备** | 矢量 PDF 预标注 / 标注质检(QC) / 中英标签转换 | `tools/fire_anno_tool.py` |
| **端到端预审** | 拖入 PDF 跑通五阶段，看标注图 + 结构化 JSON | `tools/e2e_demo.py` |
| **规则库** | 展示已结构化的 32 条消防检查规则，可按类别筛选/搜索/用样例数据试跑 | `tools/rule_engine.py` + `rules/rules.json` |
| **YOLO 训练** | ① CVAT 标注→YOLO-seg 数据集 ② 训练 ③ 评估 | `app/backend/{build_dataset,train_yolo}.py` |

特性：拖拽选文件、结果图可滚轮缩放、子进程跑任务+实时日志（不卡界面）、训练完自动展示
`results.png`/混淆矩阵、建完数据集自动把 `data.yaml` 衔接到训练页。客户端只是 GUI 外壳，
底层仍调用 `tools/` 与 `app/backend/` 的脚本，命令行用法不受影响。

> 训练页需要 `ultralytics`（已加入 requirements）；有 GPU 更快，CPU 也能跑（慢）。首次训练会自动下载预训练权重。

## 用法（命令行）

### 标注三件套 `fire_anno_tool.py`

```bash
# ① 中文标签转英文（带"漏转自检"，没映射到的会报出来而非静默跳过）
python tools/fire_anno_tool.py convert  输入.xml  输出.xml

# ② 质检：按 schema 查格式/几何/完整性/数量异常
python tools/fire_anno_tool.py qc  标注.xml            # 自动判断总平面/站厅图

# ③ 矢量预标注(基础版)：从矢量PDF自动抽 文字块/线/区域候选 → CVAT预标 + 叠加预览
python tools/fire_anno_tool.py prelabel  矢量图纸.pdf  输出目录  --dpi 200

# ③+ 增强预标注：语义细分 + 内容(OCR可选) + 置信度分级 + 确认清单 + 自检
python app/backend/prelabel_pro.py  矢量图纸.pdf  输出目录  --type hall --ocr --qc

# ④ 一条龙：convert 完接着 qc
python tools/fire_anno_tool.py all  标注.xml
```

> 预标注详细原理、参数、OCR 安装、人工确认流程见 **[docs/guides/prelabel_guide.md](docs/guides/prelabel_guide.md)**。

质检能自动报出的典型问题：非法/中文标签、`.`等垃圾属性键、必填空值、枚举越界、必现类缺失（如周边建筑=0）、商铺超规范上限、设备区分区漏标等。

### 矢量文字层直读 `vector_extract.py`

```bash
# 直读图纸文字层里的防火分区(名称+区域类型+面积) → 结构化数据，可顺带跑规则引擎
python tools/vector_extract.py  站厅层.pdf  --rules rules/rules.json
```

设计院常把"防火分区一（公共区）：面积3861.71㎡"直接写在矢量 PDF 文字层。本工具直读这些文字，
**比 OCR 准、不依赖标注/模型**，主攻防火分区面积。⚠️ 是设计院**声称值**，会标注来源并做自洽检查；
真伪的几何复核留待后续（几何精提取作校验器）。已兼容欧洲城/嘉宾/东莞等多种制图格式。

### 导出 Word 审查报告 `report_docx.py`

```bash
# 把 e2e 的产出汇编成可交付的 Word 报告（封面/结论/逐条检查表/标注图/免责）
python tools/report_docx.py  e2e输出目录
```

客户端「端到端预审」页跑完后，点 **导出 Word 报告** 按钮即可一键生成 `审查报告.docx`。依赖 `python-docx`。

### 端到端流程 `e2e_demo.py`

```bash
python tools/e2e_demo.py  图纸.pdf  输出目录  --page 0 --dpi 200
```

产出：
- `e2e_structured.json` —— 结构化数据（分区面积/类型、比对结论）
- `e2e_annotated.png/.pdf` —— 原图标注版（绿框=合规，红框=超限，黄框=待复核）

> **①②阶段优先走"文字层直读"**：图纸已写明面积时，防火分区判定精确、带规范出处、无噪声；
> 图纸未写明时回退 OCR 识别（含噪声，仅演示）。其余指标（出口个数/净宽/门方向等）仍需 YOLO+几何（见 DELIVERY.md 第三、五节）。

## 已实现的规范检查（rule_engine 驱动）

| 检查项 | 阈值 | 依据 |
|---|---|---|
| 站厅公共区防火分区面积 | ≤ 5000㎡ | GB 51298-2018 4.2.1 |
| 设备管理区每防火分区面积 | ≤ 1500㎡ | GB 51298-2018 4.2.2 |
| 任一点至安全出口疏散距离 | ≤ 50m | GB 50157-2013 28.2.7 |
| 出入口/风亭→周边多层民用建筑(一二级)防火间距 | ≥ 6m | GB 50016-2014 表5.2.2 |
| 出入口/风亭→周边高层民用建筑防火间距 | ≥ 9m | GB 50016-2014 表5.2.2 |
| 出入口→加油加气加氢站安全间距 | ≥ 50m | GB 50156-2021 4.0.4 |

三项检查（防火分区面积 / 疏散距离 / 防火间距）均由 `tools/rule_engine.py` + `rules/rules.json` 统一驱动。完整规则与冲突处理见 `docs/reference/rule_engine_notes.md`。

## 依赖与环境

Python 3.9+；`pymupdf` / `opencv-python` / `numpy` / `pytesseract` / `Pillow`。训练阶段另需 `ultralytics`。



