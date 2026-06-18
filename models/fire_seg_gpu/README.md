# fire_seg_gpu — YOLO11-seg 消防元素识别模型

端到端预审"①识别"环节的权重。CVAT 标注 → YOLO-seg 数据集 → 训练,识别图上 16 类消防元素
(防火分区 / 安全出口 / 楼梯扶梯 / 闸机 / 商铺 / 防火门 / 风亭 / 周边建筑 等)。

## 文件

| 文件 | 说明 |
|---|---|
| `best.pt` (77.7 MB) | **推荐权重**,val mAP 最优轮 |
| `last.pt` (77.7 MB) | 末轮权重(断点续训用) |
| `results.csv` | 逐轮训练指标(loss / P / R / mAP) |
| `args.yaml` | 训练超参(基座 / imgsz / batch / device …) |
| `val/*.png` | 验证评估图表:P/R/F1/PR 曲线(Box & Mask)+ 混淆矩阵 |

> ⚠️ 训练/验证图纸拼图(`train_batch*.jpg` / `val_batch*.jpg`)与数据集本身是**保密图纸**,
> 按 `.gitignore` 不入库;`models/` 下一律禁止 `.jpg` 作双保险。

## 训练配置(`args.yaml`)

- 基座:`yolo11s-seg.pt` · 任务:`segment`(分割,要算防火分区面积必须闭合多边形)
- `imgsz=1024` · `batch=4` · `device=0`(RTX 5060)· `workers=0` · `amp=false`
  (`workers=0` + `amp=false` 是 Windows 中文路径 + RTX 50 系 Blackwell 的必需参数,见 docs/guides/yolo_training_guide.md)
- 设定 50 轮,实际在 **36 轮** 收敛/停止

## 整体指标(`results.csv` 末轮,16 类聚合)

| | mAP50 | mAP50-95 | Precision | Recall |
|---|---|---|---|---|
| Box(检测框) | 0.491 | 0.348 | 0.567 | 0.452 |
| Mask(分割) | 0.481 | 0.321 | 0.578 | 0.438 |

> 聚合 mAP 偏低是被稀有类拖累(训练实例 <20 的类 mAP≈0)。**逐类**差异很大:
> `gate`≈0.99 / `vent`≈0.98 / `fire_door`≈0.92,而 `safety_exit` / `commercial_shop` 因样本太少≈0。
> 逐类细节见 `val/confusion_matrix.png` 与各 `*_curve.png`,精度提升路径见
> [docs/guides/yolo_incremental_training.html](../../docs/guides/yolo_incremental_training.html)。

## 用法

```bash
# 端到端(方式②:YOLO 自动识别)
python tools/mvp_e2e.py "" 底图.pdf out/ --pdf 底图.pdf \
    --yolo-weights models/fire_seg_gpu/best.pt
```

客户端「端到端预审」页"方式② YOLO 模型权重"选 `models/fire_seg_gpu/best.pt` 即可。
