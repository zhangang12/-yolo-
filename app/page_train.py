# -*- coding: utf-8 -*-
"""YOLO 训练页：① 建数据集(CVAT→YOLO-seg) ② 训练 ③ 评估。"""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QButtonGroup, QStackedWidget,
    QSpinBox, QComboBox, QLabel, QSplitter, QGridLayout,
)
from ui_common import (card, h1, hint, section, PathRow, ImageViewer, LogConsole, ProcRunner)

BUILD = "app/backend/build_dataset.py"
TRAIN = "app/backend/train_yolo.py"


class TrainPage(QWidget):
    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd
        self.runner = ProcRunner(cwd)
        self.runner.output.connect(self._on_output)
        self.runner.finished.connect(self._on_finished)
        self._cur = "build"
        self._preview_path = None
        self._artifact = None  # 脚本通过 [ARTIFACT] 行回报的产物图

        root = QHBoxLayout(self); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(16)

        left = card(); lv = QVBoxLayout(left); lv.setContentsMargins(18, 18, 18, 18); lv.setSpacing(12)
        lv.addWidget(h1("YOLO 训练"))
        lv.addWidget(hint("训练 AI 的「眼睛」——让它学会认出图纸上的元素。三步：① 把标注好的图整理成训练集 → "
                          "② 训练 → ③ 评估认得准不准。\n面向会调模型的人：保留 epochs/imgsz 等术语，旁边都配了说明。"))

        seg = QHBoxLayout(); seg.setSpacing(6)
        self.bg = QButtonGroup(self); self.bg.setExclusive(True)
        for i, (k, n) in enumerate([("build", "① 建数据集"), ("train", "② 训练"), ("val", "③ 评估")]):
            b = QPushButton(n); b.setObjectName("Seg"); b.setCheckable(True)
            if i == 0: b.setChecked(True)
            b.clicked.connect(lambda _, kk=k: self._switch(kk))
            self.bg.addButton(b); seg.addWidget(b)
        lv.addLayout(seg)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_build())
        self.stack.addWidget(self._page_train())
        self.stack.addWidget(self._page_val())
        lv.addWidget(self.stack)

        btns = QHBoxLayout()
        self.run_btn = QPushButton("开始建数据集"); self.run_btn.setObjectName("Primary"); self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("停止"); self.stop_btn.setObjectName("Danger"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.runner.stop)
        btns.addWidget(self.run_btn, 1); btns.addWidget(self.stop_btn)
        lv.addLayout(btns)
        left.setFixedWidth(420)
        leftcol = QVBoxLayout(); leftcol.setContentsMargins(0, 0, 0, 0); leftcol.setSpacing(0)
        leftcol.addWidget(left); leftcol.addStretch(1)
        root.addLayout(leftcol)

        right = QSplitter(Qt.Vertical)
        self.viewer = ImageViewer(); self.log = LogConsole()
        right.addWidget(self.viewer); right.addWidget(self.log)
        right.setSizes([460, 260])
        root.addWidget(right, 1)

    @staticmethod
    def _grid(items):
        """把 [(标签,控件)...] 排成两列网格，整齐对齐、控件列等宽填充。"""
        g = QGridLayout(); g.setHorizontalSpacing(10); g.setVerticalSpacing(8)
        g.setContentsMargins(0, 0, 0, 0)
        for i, (lab, wid) in enumerate(items):
            r, c = divmod(i, 2)
            g.addWidget(section(lab), r, c * 2)
            g.addWidget(wid, r, c * 2 + 1)
        g.setColumnStretch(1, 1); g.setColumnStretch(3, 1)
        return g

    # ---------- ① 建数据集 ----------
    def _page_build(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.b_anno = PathRow("标注目录", "dir")
        self.b_out = PathRow("数据集存到", "dir")
        self.b_tile = QSpinBox(); self.b_tile.setRange(0, 4096); self.b_tile.setValue(1024)
        self.b_ov = QSpinBox(); self.b_ov.setRange(0, 1024); self.b_ov.setValue(128)
        self.b_seed = QSpinBox(); self.b_seed.setRange(0, 9999)
        v.addWidget(self.b_anno); v.addWidget(self.b_out)
        v.addLayout(self._grid([("切片大小", self.b_tile), ("切片重叠", self.b_ov), ("随机种子", self.b_seed)]))
        v.addWidget(hint("把标注好的数据整理成 AI 能训练的格式。\n"
                         "• 标注目录里要有标注文件(.xml)和对应底图，成对存放。\n"
                         "• 图大、目标小，需切成小块训练：切片大小默认 1024，块之间留点重叠（默认 128）"
                         "免得目标被切断；填 0 ＝ 不切。\n"
                         "• 随机种子：固定它，每次划分训练 / 验证集的结果都一样，便于复现。\n"
                         "• 完成后自动生成配置文件，并填到「训练」「评估」页。"))
        return w

    # ---------- ② 训练 ----------
    def _page_train(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.t_data = PathRow("数据集配置", "file", "YAML (*.yaml *.yml)")
        row1 = QHBoxLayout()
        row1.addWidget(section("预训练权重"))
        self.t_model = QComboBox(); self.t_model.setEditable(True)
        self.t_model.addItems(["yolo11n-seg.pt", "yolo11s-seg.pt", "yolo11m-seg.pt"])
        self.t_model.setCurrentText("yolo11s-seg.pt")
        row1.addWidget(self.t_model, 1)
        v.addWidget(self.t_data); v.addLayout(row1)

        self.t_ep = QSpinBox(); self.t_ep.setRange(1, 2000); self.t_ep.setValue(100)
        self.t_imgsz = QSpinBox(); self.t_imgsz.setRange(320, 2048); self.t_imgsz.setSingleStep(64); self.t_imgsz.setValue(1024)
        self.t_batch = QSpinBox(); self.t_batch.setRange(1, 128); self.t_batch.setValue(8)
        self.t_pat = QSpinBox(); self.t_pat.setRange(0, 500); self.t_pat.setValue(20)
        self.t_dev = QComboBox(); self.t_dev.setEditable(True)
        self.t_dev.addItems(["", "0", "cpu"]); self.t_dev.setCurrentText("")
        v.addLayout(self._grid([("epochs", self.t_ep), ("imgsz", self.t_imgsz),
                                ("batch", self.t_batch), ("patience", self.t_pat),
                                ("device", self.t_dev)]))
        v.addWidget(hint("从预训练模型接着学（别从零训，省时省数据）。各参数：\n"
                         "• epochs 训练轮数，越多越久，先 100 轮试；patience 连续多少轮没进步就提前停（默认 20）。\n"
                         "• imgsz 训练用的图片边长，目标小就用大图 1024（和切片对齐）；batch 一次喂几张，显存不够就调小。\n"
                         "• device 留空 ＝ 自动选 GPU/CPU，也可填 0（第一块显卡）或 cpu。\n"
                         "• 「数据集配置」就是上一步「建数据集」生成的那个文件；首次训练会联网下载预训练权重，请稍等。"))
        return w

    # ---------- ③ 评估 ----------
    def _page_val(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.v_data = PathRow("数据集配置", "file", "YAML (*.yaml *.yml)")
        self.v_weights = PathRow("训练好的权重", "file", "权重 (*.pt)")
        row = QHBoxLayout()
        row.addWidget(section("imgsz")); self.v_imgsz = QSpinBox(); self.v_imgsz.setRange(320, 2048); self.v_imgsz.setSingleStep(64); self.v_imgsz.setValue(1024)
        row.addWidget(self.v_imgsz); row.addStretch(1)
        v.addWidget(self.v_data); v.addWidget(self.v_weights); v.addLayout(row)
        v.addWidget(hint("检验训练出的模型认得准不准。\n"
                         "• 权重选训练产出的 best.pt（一般在 runs/exp/weights/ 下）。\n"
                         "• 主要看两个数：mAP50 ＝ 整体准确度（越高越好）；每类的查准率 / 查全率（P/R）。\n"
                         "• 混淆矩阵图会显示在右上方，能看出哪类元素容易认错。"))
        return w

    def _switch(self, k):
        self._cur = k
        self.stack.setCurrentIndex({"build": 0, "train": 1, "val": 2}[k])
        self.run_btn.setText({"build": "开始建数据集", "train": "开始训练", "val": "开始评估"}[k])

    def _on_output(self, s):
        self.log.append_text(s)
        for line in s.splitlines():
            if "[ARTIFACT]" in line:
                self._artifact = line.split("[ARTIFACT]", 1)[1].strip()

    def _run(self):
        self._preview_path = None
        self._artifact = None
        k = self._cur
        if k == "build":
            anno = self.b_anno.text(); out = self.b_out.text()
            if not anno or not out:
                self.log.append_text("⚠️ 请填写标注目录与输出数据集目录。\n"); return
            self._pending_yaml = os.path.join(out, "data.yaml")
            args = [anno, out, "--tile", self.b_tile.value(),
                    "--overlap", self.b_ov.value(), "--seed", self.b_seed.value()]
            self.log.banner("建数据集"); self._set_running(True)
            self.runner.run(BUILD, args)
        elif k == "train":
            data = self.t_data.text()
            if not data:
                self.log.append_text("⚠️ 请选择 data.yaml（可先在①建数据集生成）。\n"); return
            args = ["train", data, "--model", self.t_model.currentText(),
                    "--epochs", self.t_ep.value(), "--imgsz", self.t_imgsz.value(),
                    "--batch", self.t_batch.value(), "--patience", self.t_pat.value(),
                    "--project", "runs", "--name", "exp"]
            dev = self.t_dev.currentText().strip()
            if dev:
                args += ["--device", dev]
            self.log.banner("训练（首次会自动下载预训练权重，请耐心等待）"); self._set_running(True)
            self.runner.run(TRAIN, args)
        else:  # val
            data = self.v_data.text(); wts = self.v_weights.text()
            if not data or not wts:
                self.log.append_text("⚠️ 请选择 data.yaml 与权重 .pt。\n"); return
            args = ["val", data, "--weights", wts, "--imgsz", self.v_imgsz.value(),
                    "--project", "runs", "--name", "val"]
            self.log.banner("评估"); self._set_running(True)
            self.runner.run(TRAIN, args)

    def _on_finished(self, code):
        self._set_running(False)
        self.log.append_text(f"\n[结束] 退出码 {code}\n")
        if code != 0:
            return
        # 建数据集完成 → 把 data.yaml 衔接到训练/评估页
        if self._cur == "build" and getattr(self, "_pending_yaml", None) and os.path.exists(self._pending_yaml):
            self.t_data.set_text(self._pending_yaml)
            self.v_data.set_text(self._pending_yaml)
            self.log.append_text(f"[衔接] data.yaml 已自动填入“训练/评估”页。\n")
        # 加载结果图：优先用脚本回报的真实产物路径
        target = self._artifact or self._preview_path
        if target and os.path.exists(target):
            if self.viewer.load(target):
                self.log.append_text(f"[预览] {os.path.basename(target)}\n")

    def _set_running(self, running):
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
