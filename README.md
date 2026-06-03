# 消防建筑图 AI 预审系统（MVP）

拖入 PDF 图纸 → 自动识别 → 结构化数据 → 与消防规范比对 → 在原图标注不合规位置。

> 📄 **新成员先读 [`HANDOFF.md`](HANDOFF.md)** —— 项目背景、架构、进度、技术决策、待办全在那里。

## 目录结构

```
fire-review-mvp/
├── HANDOFF.md              交接文档（先读这个）
├── README.md              本文件（安装与用法）
├── requirements.txt
├── tools/
│   ├── fire_anno_tool.py   标注三件套：转换 / 质检 / 预标注
│   ├── e2e_demo.py         端到端流程 demo（接 rule_engine）
│   ├── fangju_demo.py      防火间距比对 demo（出入口/风亭→周边建筑）
│   └── e2e_steps.py        分步执行版（受限环境用）
└── docs/
    ├── label_schema.md             标注标签 schema（类别/属性/枚举）
    ├── annotation_issues_整改单.md  标注数据问题清单（发给标注单位）
    ├── rule_engine_notes.md        《消防检查规则》评审与规则引擎设计
    ├── yolo_training_guide.md      YOLO 训练流程
    ├── 任务分工_标注vs矢量直抽.md    哪些标注团队标、哪些程序直抽
    └── 标注规范说明_详细版.md        给标注团队的详细标注规范(另有docx)
```

## 安装

```bash
pip install -r requirements.txt
# OCR 需系统级 tesseract:
#   Ubuntu:  sudo apt install tesseract-ocr            (中文再加 tesseract-ocr-chi-sim)
#   macOS :  brew install tesseract tesseract-lang
#   Windows: 装 UB-Mannheim/tesseract 安装包并加入 PATH
```

## 用法

### 标注三件套 `fire_anno_tool.py`

```bash
# ① 中文标签转英文（带"漏转自检"，没映射到的会报出来而非静默跳过）
python tools/fire_anno_tool.py convert  输入.xml  输出.xml

# ② 质检：按 schema 查格式/几何/完整性/数量异常
python tools/fire_anno_tool.py qc  标注.xml            # 自动判断总平面/站厅图

# ③ 矢量预标注：从矢量PDF自动抽 文字块/线/区域候选 → CVAT预标 + 叠加预览
python tools/fire_anno_tool.py prelabel  矢量图纸.pdf  输出目录  --dpi 200

# ④ 一条龙：convert 完接着 qc
python tools/fire_anno_tool.py all  标注.xml
```

质检能自动报出的典型问题：非法/中文标签、`.`等垃圾属性键、必填空值、枚举越界、必现类缺失（如周边建筑=0）、商铺超规范上限、设备区分区漏标等。

### 端到端流程 `e2e_demo.py`

```bash
python tools/e2e_demo.py  图纸.pdf  输出目录  --page 0 --dpi 200
```

产出：
- `e2e_structured.json` —— 结构化数据（比例尺、各分区面积、疏散距离、比对结论）
- `e2e_annotated.png/.pdf` —— 原图标注版（绿框=合规，红框=超限）

> ⚠️ 当前"识别"无训练模型、靠 OCR，**结果含噪声，仅演示流程，不代表审查准确**。精度靠后续 YOLO 训练 + 几何精提取补强（见 HANDOFF 第 3、9 节）。

## 已实现的规范检查（rule_engine 驱动）

| 检查项 | 阈值 | 依据 |
|---|---|---|
| 站厅公共区防火分区面积 | ≤ 5000㎡ | GB 51298-2018 4.2.1 |
| 设备管理区每防火分区面积 | ≤ 1500㎡ | GB 51298-2018 4.2.2 |
| 任一点至安全出口疏散距离 | ≤ 50m | GB 50157-2013 28.2.7 |
| 出入口/风亭→周边多层民用建筑(一二级)防火间距 | ≥ 6m | GB 50016-2014 表5.2.2 |
| 出入口/风亭→周边高层民用建筑防火间距 | ≥ 9m | GB 50016-2014 表5.2.2 |
| 出入口→加油加气加氢站安全间距 | ≥ 50m | GB 50156-2021 4.0.4 |

三项检查（防火分区面积 / 疏散距离 / 防火间距）均由 `tools/rule_engine.py` + `rules/rules.json` 统一驱动。完整规则与冲突处理见 `docs/rule_engine_notes.md`。

## 依赖与环境

Python 3.9+；`pymupdf` / `opencv-python` / `numpy` / `pytesseract` / `Pillow`。训练阶段另需 `ultralytics`。

## 数据

图纸、标注数据均为保密业务数据，**不入库**（见 `.gitignore`）。请向项目负责人索取。
