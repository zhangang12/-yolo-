# YOLO 训练流程指南

## 总览
```
定类别 → 标注(切片) → 转YOLO格式 → 划分8:1:1 → 配置训练 → 训练 → 评估 → 找错例补标 → 重训(循环)
```

## 1. 类别设计
- **检测类（矩形框）**：safety_exit / stair_escalator / gate(闸机) / commercial_shop / fire_door / val_text / room_title
- **分割类（多边形，要算面积）**：fire_compartment / 各区域类
- 一个 **YOLO-seg** 模型可同时输出检测框和分割多边形，不必训两个。
- 完整词表见 `../reference/标注标签说明.md`。

## 2. 标注要点
- 工具:本项目用 **CVAT**(产出 CVAT 1.1 XML,`build_dataset.py` 直接读)。标注规范见 `../reference/标注标签说明.md`。
- **必须切片**：站厅图是超长条(如 8228×3339)，整图缩小后闸机/尺寸数字会糊。`build_dataset.py` 会自动切成 ~1024×1024 再训练(见 §3)。
- 每类 **≥150–300 实例**起步；**一致性 > 数量**（同类画法全队统一，写进标注手册）。

## 3. 建数据集(`build_dataset.py`)

把【CVAT 标注 XML + 对应底图】一键转成 YOLO-seg 训练集。客户端「① 建数据集」页是它的图形封装。

### 3.1 输入要求
- 一个**标注目录**,里面 **CVAT 的 .xml 与底图成对存放**。底图按 CVAT 里 `<image name=...>` 去找,支持放在:目录顶层、`images/` 子目录、或目录内递归同名。
- 标签**中文自动转英文**(复用 `fire_anno_tool` 的映射表),所以原始未转英文的 CVAT 也能直接喂。
- 进数据集前先过质检:`python tools/fire_anno_tool.py qc 标注.xml`,**ERROR 必须为 0**,否则脏标注会污染整批。

### 3.2 运行
```bash
python app/backend/build_dataset.py  标注目录  dataset  --tile 1024 --overlap 128 --seed 0
```
- `--tile 1024`:切片边长。站厅图是超长条(如 8228×3339),不切片缩小后小目标会糊。
- `--overlap 128`:相邻切片重叠像素,避免目标正好被切断。
- `--seed 0`:固定它,每次 train/val/test 划分一致,便于复现。填 `--tile 0` = 不切片。

### 3.3 它内部做了什么(理解输出)
- **box 与 polygon 统一成多边形**:检测框转成 4 点多边形,一个 YOLO-seg 模型同时出框和分割轮廓。
- **尺寸线/疏散线(polyline)不参与训练**:它们没有面积,自动跳过(`width_dimension_line` / `evac_distance_line` / `fire_clearance_line`)。这些指标靠几何量测,不靠模型。
- **切片**:大图按 tile 切,标注随之裁剪到瓦片局部坐标;**没有任何标注的纯背景瓦片会被丢弃**。
- **划分** train:val:test = 8:1:1。
- 产出:`images/{train,val,test}/*.png`、`labels/{train,val,test}/*.txt`(每行 `类别id 归一化多边形点…`)、`data.yaml`(类名 + 路径,直接喂训练)。

### 3.4 ⚠️ 类别顺序只能追加,不能重排(增量训练命脉)
训练类别写在 `build_dataset.py` 顶部的 `CLASS_NAMES`(当前 **16 类**),**列表下标就是类别 id**。
新增类别**只能往列表末尾追加**——一旦插入或重排,所有类别 id 全部错位,**旧的 `best.pt` 直接报废、增量训练全乱**。

### 3.5 看产出报告——判断数据够不够的第一道关
建完会打印**各类实例数**,这是数据质量最重要的体检:
```
[建数据集] 源图 18 张 -> 样本(切片后) 349 个
[建数据集] 划分  train 281 / val 34 / test 34
[建数据集] 各类实例数:
    fire_door              450
    safety_exit              4  ⚠️不足(建议≥150)
    commercial_shop          0  ❌为0
```
- **≥150**:较稳,mAP 可用。
- **<150(⚠️)**:能训但效果一般。
- **=0(❌)**:这类压根没标注,训出来必然认不出 → 要么补标,要么接受它识别不了。

样本特别少的类(如 `safety_exit` 只有几个实例),评估 mAP 大概率接近 0——这是**数据量问题,不是训练参数问题**,调 epochs/batch 救不了,只能补标注。

## 4. 训练
推荐用项目封装的命令(已带 Windows/RTX 50 系安全默认):
```bash
python app/backend/train_yolo.py train  dataset/data.yaml  \
    --model yolo11s-seg.pt  --epochs 100 --imgsz 1024  \
    --batch 4 --patience 20 --workers 0 --no-amp --device 0  \
    --project runs --name exp
```
- `--batch 4`:适配 8G 显存 @imgsz1024(报 CUDA out of memory 再调小)。
- `--workers 0`:数据加载单进程,规避 Windows + 中文路径多进程崩溃。
- `--no-amp`:关闭混合精度,规避 RTX 50 系(Blackwell)偶发 cudaErrorUnknown。
- 客户端「YOLO 训练」页这些已是默认值(AMP 默认关,可勾选开启)。

等价的原生 ultralytics 写法:
```python
from ultralytics import YOLO
model = YOLO('yolo11s-seg.pt')        # 预训练权重迁移学习，别从零训
model.train(data='dataset/data.yaml', epochs=100, imgsz=1024,
            batch=4, patience=20, workers=0, amp=False)
```

## 5. 评估(`val`)

### 5.1 跑评估
```bash
python app/backend/train_yolo.py val  dataset/data.yaml  \
    --weights runs/segment/runs/exp/weights/best.pt  --imgsz 1024
```
客户端「③ 评估」页等价:选 `data.yaml` + 训练产出的 `best.pt`,混淆矩阵图自动显示在右上方。

### 5.2 指标含义(先搞懂再看数)
- **mAP50**:IoU(预测框与真值框的重叠度)阈值取 0.5 时的平均精度,取值 0–1,**整体准确度主指标,越高越好**。
- **mAP50-95**:IoU 从 0.5 到 0.95 逐档平均,更严格(要求框/轮廓更贴合);一般比 mAP50 低。
- **P(Precision 查准率)**:模型判为某类的里头,真正是的比例。高 = 误报少。
- **R(Recall 查全率)**:实际存在的某类里,被模型找出来的比例。高 = 漏检少。
- **Box vs Mask**:Box 指标看检测框,Mask 指标看分割轮廓;**要算面积的类(防火分区)重点看 Mask**。

### 5.3 别只看整体 mAP,务必逐类看
整体 mAP 会被**类别不均衡**带偏:常见类(`fire_door`/`gate`)拉高、稀有类(`safety_exit`)拉低,平均下来看不出问题在哪。
**逐类看 P/R/mAP**:哪类低就知道该补哪类数据。本项目实测:`gate`≈0.99 / `vent`≈0.98 / `fire_door`≈0.92 良好;`safety_exit`/`commercial_shop` 因样本太少≈0。

### 5.4 看混淆矩阵 + 目视抽查
- **`confusion_matrix.png`**:对角线越亮越好;**非对角线发亮 = 两类互相认错**(比如把扶梯认成楼梯);**最后一行/列 = 与背景混淆**(漏检 / 凭空误检)。
- **`val_batch*_pred.jpg`**:模型在验证图上的真实预测图,**人眼扫一遍最直观**——框歪没歪、该框的漏没漏、标签贴对没。比单看数字更能发现问题。

### 5.5 合格判断与下一步
- 数据多的类 mAP 应稳定较高(0.8+);
- 若**普遍偏低** → 多半是数据量不足 / 标注不一致,不是训练参数,调 epochs/batch 救不了;
- 想提升某一类 → 回 §3「建数据集」把该类样本补到 ≥150,重新训练。

## 6. 迭代（涨点关键）
拿训好的模型跑新图 → 挑漏检/误检 → **重点补标这些难例** → 加进训练集重训。这个"模型找错→人工补难例→重训"的主动学习循环，比一次性堆数据有效得多。

## 7. 与本项目的衔接
- YOLO 只负责"识别"一环：把元素框出来/分割出来。
- 数字内容靠 OCR；面积/距离/净宽靠几何计算；疏散路径靠图算法；判违规靠规则引擎。
- 训练数据用 `fire_anno_tool.py qc` 自检通过后再进训练。

## 8. 常见报错与处理
本项目在 Windows + RTX 50 系(Blackwell)上踩过的坑,GUI/CLI 安全默认已规避;手动调参或换机时可能复现:

| 报错信息(关键字) | 原因 | 处理 |
|---|---|---|
| `CUDA out of memory` / `Reducing to batch=N and retrying` | 一次喂的图太多,显存(8G)装不下 | 调小 `--batch`(8G@1024 用 **4**);别同时把 imgsz 调大 |
| `[WinError 1455] 页面文件太小` / 加载 `cublas64_*.dll` 失败 | 多进程数据加载,每个 worker 子进程都加载 torch/CUDA,撑爆虚拟内存 | `--workers 0`(单进程,已是默认);并把 Windows 虚拟内存(页面文件)调到 ≥16G 或设"系统管理" |
| `cudaErrorUnknown` / 训练到一半 NaN 或崩 | RTX 50 系 + PyTorch 自动混合精度(AMP)偶发不兼容 | `--no-amp` 关闭 AMP(GUI 训练页默认就关) |
| `Download failure ... yolo26n.pt` / `schannel ... CRYPT_E_REVOCATION_OFFLINE` | 离线/证书吊销检查失败,AMP 自检下载不到小模型 | **无害**,会自动跳过 AMP 自检继续训;彻底关掉用 `--no-amp` |
| `Couldn't open shared file mapping` | 同上多进程共享内存问题(Windows + 中文路径) | `--workers 0` |

> 一句话:在这台机器/类似配置上,**`--batch 4 --workers 0 --no-amp` 三件套**能避开以上绝大多数坑。客户端「YOLO 训练」页已内置这套默认。
