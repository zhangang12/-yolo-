#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练 / 评估  train_yolo.py
=================================================
封装 ultralytics YOLO-seg 的训练与评估，供客户端以子进程方式调用（流式日志）。

用法：
  python train_yolo.py train  <data.yaml>  [--model yolo11s-seg.pt] [--epochs 100]
        [--imgsz 1024] [--batch 8] [--patience 20] [--project runs] [--name exp] [--device ""]
  python train_yolo.py val    <data.yaml>  --weights runs/exp/weights/best.pt [--imgsz 1024]

要点（与 docs/guides/yolo_training_guide.md 一致）：
  - 从预训练权重迁移学习，别从零训。
  - 目标小，imgsz 要大(1024)，配合切片。
  - patience 早停；评估看每类 P/R 与 mAP50。
"""
import sys, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _need_ultralytics():
    try:
        from ultralytics import YOLO  # noqa
        return YOLO
    except ImportError:
        print("❌ 未安装 ultralytics。请先：pip install ultralytics", flush=True)
        sys.exit(2)


def train(args):
    YOLO = _need_ultralytics()
    print(f"[训练] 模型={args.model}  数据={args.data}", flush=True)
    print(f"[训练] epochs={args.epochs} imgsz={args.imgsz} batch={args.batch} "
          f"patience={args.patience} device={args.device or 'auto'}", flush=True)
    model = YOLO(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        patience=args.patience,
        project=args.project,
        name=args.name,
        device=(args.device or None),
        workers=args.workers,   # Windows + 中文路径下 workers>0 易触发 'Couldn't open shared file mapping'
        amp=args.amp,           # 部分新卡(如 RTX 50系 Blackwell)+ PyTorch AMP 偶发 cudaErrorUnknown，可关
        exist_ok=True,
    )
    import os
    save_dir = str(model.trainer.save_dir)  # ultralytics 真实输出目录
    print(f"[训练] 完成。权重: {os.path.join(save_dir, 'weights', 'best.pt')}", flush=True)
    print(f"[训练] 训练曲线: {os.path.join(save_dir, 'results.png')}", flush=True)
    # 机器可解析行：供客户端定位要预览的产物图
    print(f"[ARTIFACT] {os.path.join(save_dir, 'results.png')}", flush=True)


def val(args):
    YOLO = _need_ultralytics()
    print(f"[评估] 权重={args.weights}  数据={args.data}", flush=True)
    import os
    model = YOLO(args.weights)
    metrics = model.val(data=args.data, imgsz=args.imgsz,
                        project=args.project, name=args.name, exist_ok=True)
    try:
        print(f"[评估] mAP50={metrics.box.map50:.4f}  mAP50-95={metrics.box.map:.4f}", flush=True)
    except Exception:
        pass
    save_dir = str(metrics.save_dir)
    print(f"[评估] 报表(混淆矩阵/PR曲线): {save_dir}", flush=True)
    cm = os.path.join(save_dir, "confusion_matrix.png")
    print(f"[ARTIFACT] {cm}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="YOLO-seg 训练/评估")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("train")
    t.add_argument("data")
    t.add_argument("--model", default="yolo11s-seg.pt")
    t.add_argument("--epochs", type=int, default=100)
    t.add_argument("--imgsz", type=int, default=1024)
    t.add_argument("--batch", type=int, default=8)
    t.add_argument("--patience", type=int, default=20)
    t.add_argument("--project", default="runs")
    t.add_argument("--name", default="exp")
    t.add_argument("--device", default="")
    t.add_argument("--workers", type=int, default=8,
                   help="DataLoader workers；Windows + 中文路径建议 0")
    t.add_argument("--no-amp", dest="amp", action="store_false",
                   help="关闭自动混合精度(AMP)；RTX 50系/Blackwell 等新卡偶发 cudaErrorUnknown 时使用")
    t.set_defaults(amp=True)

    v = sub.add_parser("val")
    v.add_argument("data")
    v.add_argument("--weights", required=True)
    v.add_argument("--imgsz", type=int, default=1024)
    v.add_argument("--project", default="runs")
    v.add_argument("--name", default="val")

    a = ap.parse_args()
    if a.cmd == "train":
        train(a)
    elif a.cmd == "val":
        val(a)


if __name__ == "__main__":
    main()
