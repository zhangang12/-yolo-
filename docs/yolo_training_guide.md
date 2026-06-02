# YOLO 训练流程指南

## 总览
```
定类别 → 标注(切片) → 转YOLO格式 → 划分8:1:1 → 配置训练 → 训练 → 评估 → 找错例补标 → 重训(循环)
```

## 1. 类别设计
- **检测类（矩形框）**：safety_exit / stair_escalator / gate(闸机) / commercial_shop / fire_door / val_text / room_title
- **分割类（多边形，要算面积）**：fire_compartment / 各区域类
- 一个 **YOLO-seg** 模型可同时输出检测框和分割多边形，不必训两个。
- 完整词表见 `label_schema.md`。

## 2. 标注要点
- 工具：Roboflow（框+多边形+自动导出 YOLO 格式最省事）/ LabelImg / Labelme。
- **必须切片**：站厅图是超长条(如 8228×3399)，整图缩小后闸机/尺寸数字会糊。切成 ~1024×1024 再标注、训练。
- 每类 **≥150–300 实例**起步；**一致性 > 数量**（同类画法全队统一，写进标注手册）。

## 3. 数据集结构
```
dataset/
  images/{train,val,test}/*.png
  labels/{train,val,test}/*.txt   # 检测: class cx cy w h(归一化); 分割: class x1 y1 x2 y2...
  data.yaml                        # names + 路径
```
划分 train:val:test ≈ 8:1:1。

## 4. 训练
```python
from ultralytics import YOLO
model = YOLO('yolo11s-seg.pt')        # 预训练权重迁移学习，别从零训
model.train(
    data='dataset/data.yaml',
    epochs=100, imgsz=1024,           # 目标小，imgsz 要大
    batch=8, patience=20,             # 20轮不涨早停
)
```

## 5. 评估
- 看整体 **mAP50**，更要看**每类单独 P/R**（出口/商铺通常好，闸机/尺寸数字常差）。
- `model.val()` 看混淆矩阵，定位"哪两类混淆""哪类漏检多"。

## 6. 迭代（涨点关键）
拿训好的模型跑新图 → 挑漏检/误检 → **重点补标这些难例** → 加进训练集重训。这个"模型找错→人工补难例→重训"的主动学习循环，比一次性堆数据有效得多。

## 7. 与本项目的衔接
- YOLO 只负责"识别"一环：把元素框出来/分割出来。
- 数字内容靠 OCR；面积/距离/净宽靠几何计算；疏散路径靠图算法；判违规靠规则引擎。
- 训练数据用 `fire_anno_tool.py qc` 自检通过后再进训练。
