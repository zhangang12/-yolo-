# 消防建筑图 AI 预审系统 — 交付包

> 拖入 PDF 图纸 → 自动识别 → 结构化数据 → 与消防规范比对 → 在原图标注不合规位置 → 输出可交付的 Word 审查意见表。

**MVP 状态**:✅ 端到端全流程跑通(CVAT 真值路径 + YOLO 自动识别路径双通路) · ✅ 客户端 4 页可用 · ✅ 规则引擎 37 条 8 大类 · ✅ 5 个产物自动生成。

**交付时间**:2026-06 · **接手人先读本文档**(30 分钟上手),细节再翻 [HANDOFF.md](HANDOFF.md) 和 [docs/](docs/) 下分类文档。

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
2. 步骤 4 选 `C:\Users\Administrator\yolo_work\runs\segment\runs\fire_seg_gpu\weights\best.pt`(已训好的权重 77 MB)
3. 其余同上

---

## 三、系统架构(四个大脑)

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

tools/                              核心命令行
├── mvp_e2e.py             ★       端到端一条命令(CVAT 或 YOLO)
├── mvp_batch.py                   批量端到端(整目录)
├── mvp_report_docx.py    ★       仿真实审查表 Word 报告
├── yolo_to_cvat.py       ★ 2026-06  YOLO 推理 → CVAT XML 适配器
├── sahi_predict.py                切片推理(SAHI 风格)
├── scale_calibrate.py             PDF 矢量层读 "1:N" → m/px
├── geom_measure.py                几何量测(面积/净宽/距离)
├── anno_to_structured.py          MVP 中枢:CVAT → 规则引擎对象
├── rule_engine.py                 规则引擎(37 条)
├── naming.py                      产物命名(站名-图类型)
├── tesseract_init.py              Tesseract 自动探测
├── fire_anno_tool.py              标注五件套
├── vector_extract.py              矢量层直读防火分区面积
├── fangju_demo.py                 防火间距比对
├── e2e_demo.py                    旧版 PDF 端到端(向后兼容)
└── report_docx.py                 旧版报告(向后兼容)

rules/                              规则知识库
├── rules.json                     37 条规则 / 8 大类
├── schema.md                      规则 schema 文档
└── cvat_labels.json               CVAT 项目模板(锁死下拉)

docs/                               文档
├── client_guide.html       ★      客户端使用指南
├── scripts_guide.html      ★      脚本使用指南
├── yolo_incremental_training.html ★  YOLO 增量训练指南
├── cvat_template_setup.md         CVAT 模板导入指引(给标注公司)
├── label_schema.md                标注标签 schema
├── rule_engine_notes.md           规则引擎评审与设计
├── 标注规范说明_详细版.md         标注规范 v3(给标注团队)
└── ...

examples/
└── sample_structured.json         覆盖全规则分支的样例数据
```

### 4.2 模型权重(在 `C:\Users\Administrator\yolo_work\`,不入 git)

| 文件 | 大小 | 用途 |
|---|---|---|
| `runs/segment/runs/fire_seg_gpu/weights/best.pt` | 77 MB | YOLO-seg 训练产物,16 类(fire_door/safety_exit/gate/fire_compartment 等) |
| `dataset/data.yaml` | — | 训练数据集配置 |
| `runs/segment/runs/fire_seg_gpu_val/` | — | 验证报告(mAP / 混淆矩阵 / PR 曲线) |

### 4.3 数据(保密,不入 git;向项目负责人索取)

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
| 3 | 提取防火分区编号 + 面积(面+OCR) | 🟡 **2/3** | `vector_extract.py` 矢量层直读 ✅;栅格 OCR 仅在 e2e_demo 有,主流程没接 |
| 4 | 楼梯/走道/门宽(线+OCR) | ✅ | `geom_measure.door_net_width_m`(洞口宽−0.15)+ width_line 关联实现 |
| 5 | 公共区→安全出口疏散路径(点+面+图算法) | 🔴 **缺失** | **0 处 A\* / Dijkstra 实现**,目前只走人工标的 `evac_distance_line` 折线 |

---

## 六、已知限制(交付时必须告知)

### 6.1 模型精度

- YOLO 训练数据仅 18 张图(9 站 × 站厅+总平面),稀有类(`safety_exit` 训练实例 = 4)mAP = 0
- 客户对照表:`gate` 99% / `vent` 98% / `fire_door` 92% / `surrounding_building` 60% / `safety_exit` 0%
- **解决路径**:按 [docs/yolo_incremental_training.html](docs/yolo_incremental_training.html) 增量补 8-10 个新站

### 6.2 流程缺口

- **疏散路径自动算法未实现** — 任务原文第 5 项,目前靠人工标 `evac_distance_line` 折线代偿
- **付费区/非付费区未拆** — 任务原文第 1 项 4 类只识别了 3 类
- **栅格 PDF 兜底** — 明海/贝尔路等无矢量文字层的 PDF,scale 自动标定失败,需用户手填 `--scale`

### 6.3 客户端 UX

- **每次跑同 PDF 会覆盖 `e2e_out/` 旧产物**(同一 stem)— 想保留对比要手动改 "结果存到" 路径
- **方式② YOLO 路径首次运行下载预训练权重**(GitHub,可能慢)
- 文件名带全角斜杠(╱)的图,在文件资源管理器和邮件附件里可能有兼容性问题(已用 naming.py 化解)

---

## 七、接手人路线图(下个迭代建议优先级)

| 优先级 | 工作 | 工作量 | 价值 |
|---|---|---|---|
| 🔴 高 | 疏散路径 A\* 算法 — 公共区栅格化 + 寻路 | 2-3 天 | 补齐 MVP 任务第 5 项,核心规则可跑 |
| 🔴 高 | 标注规范扩 付费/非付费区 + 标注公司补标 | 1 周(含人工) | 补齐 MVP 任务第 1 项 4 类 |
| 🟡 中 | YOLO 增量训练补稀有类 — 8-10 个新站 | 1 周(标注 + 训练) | safety_exit / commercial_shop 从 mAP 0 → 0.5+ |
| 🟡 中 | 客户端 "e2e_out/" 子目录化避免覆盖 | 2 小时 | UX 改善 |
| 🟢 低 | 栅格 OCR 完整接入 mvp_e2e 主流程 | 1 天 | tesseract 已就绪,只缺接入分支 |
| 🟢 低 | 客户端"端到端预审"加 "选 best.pt" 默认推荐路径提示 | 30 分钟 | 引导用户 |

---

## 八、关键命令速查(一行级)

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

## 九、深入文档索引

| 内容 | 文档 |
|---|---|
| **客户端 4 页用法 + 截图说明** | [docs/client_guide.html](docs/client_guide.html) |
| **16 个脚本命令行 + 组合工作流** | [docs/scripts_guide.html](docs/scripts_guide.html) |
| **YOLO 增量训练 + 基线指标** | [docs/yolo_incremental_training.html](docs/yolo_incremental_training.html) |
| **CVAT 项目模板导入(给标注公司)** | [docs/cvat_template_setup.md](docs/cvat_template_setup.md) |
| **标注规范 v3(给标注团队)** | [docs/标注规范说明_详细版.md](docs/标注规范说明_详细版.md) |
| **规则引擎评审与设计** | [docs/rule_engine_notes.md](docs/rule_engine_notes.md) |
| **规则 schema** | [rules/schema.md](rules/schema.md) |
| **项目背景 + 进度 + 待办** | [HANDOFF.md](HANDOFF.md) |
| **本文档** | [DELIVERY.md](DELIVERY.md) |

---

## 十、联系

数据/权重保密,需向项目负责人索取。代码已 push 到 GitHub origin/main,clone 即可上手。

*本文档由项目交付审计自动汇总,真实反映 2026-06 交付时的代码状态。最新功能见 git log。*
