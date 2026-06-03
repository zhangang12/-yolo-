# -*- coding: utf-8 -*-
"""端到端预审页：拖入 PDF → ①识别 ②结构化 ③规范比对 ④原图标注。"""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QSpinBox, QSplitter, QLabel,
)
from ui_common import (card, h1, hint, section, PathRow, ImageViewer, LogConsole, ProcRunner)

TOOL = "tools/e2e_demo.py"

STAGES = ["① 识别 (YOLO占位+OCR)", "② 结构化 (比例尺/面积/距离)",
          "③ 规范比对 (规则引擎)", "④ 原图标注 (回写)"]


class E2EPage(QWidget):
    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd
        self.runner = ProcRunner(cwd)
        self.runner.output.connect(self._on_output)
        self.runner.finished.connect(self._on_finished)
        self._out_dir = None

        root = QHBoxLayout(self); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(16)

        left = card(); lv = QVBoxLayout(left); lv.setContentsMargins(18, 18, 18, 18); lv.setSpacing(12)
        lv.addWidget(h1("端到端预审 Demo"))
        lv.addWidget(hint("矢量 PDF 一条龙跑通五阶段，产出结构化 JSON + 标注版 PNG/PDF。\n"
                          "⚠️ 当前“识别”为矢量粗提取+OCR，含噪声，仅演示流程，不代表审查准确。"))

        self.pdf = PathRow("矢量 PDF", "pdf", "PDF (*.pdf)")
        self.out = PathRow("输出目录", "dir")
        lv.addWidget(self.pdf); lv.addWidget(self.out)

        row = QHBoxLayout()
        row.addWidget(section("页码")); self.page = QSpinBox(); self.page.setRange(0, 999)
        row.addWidget(self.page); row.addSpacing(16)
        row.addWidget(section("DPI")); self.dpi = QSpinBox(); self.dpi.setRange(72, 600); self.dpi.setValue(200)
        row.addWidget(self.dpi); row.addStretch(1)
        lv.addLayout(row)

        # 阶段指示灯
        lv.addWidget(section("流程进度"))
        self.stage_labels = []
        for s in STAGES:
            l = QLabel("○  " + s); l.setObjectName("Hint")
            self.stage_labels.append(l); lv.addWidget(l)

        lv.addStretch(1)
        btns = QHBoxLayout()
        self.run_btn = QPushButton("开始预审"); self.run_btn.setObjectName("Primary"); self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("停止"); self.stop_btn.setObjectName("Danger"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.runner.stop)
        btns.addWidget(self.run_btn, 1); btns.addWidget(self.stop_btn)
        lv.addLayout(btns)
        left.setFixedWidth(400)
        root.addWidget(left)

        right = QSplitter(Qt.Vertical)
        self.viewer = ImageViewer(); self.log = LogConsole()
        right.addWidget(self.viewer); right.addWidget(self.log)
        right.setSizes([480, 240])
        root.addWidget(right, 1)

    def _run(self):
        pdf = self.pdf.text()
        if not pdf:
            self.log.append_text("⚠️ 请先选择矢量 PDF。\n"); return
        out = self.out.text() or os.path.join(os.path.dirname(os.path.abspath(pdf)), "e2e_out")
        self._out_dir = out
        for l, s in zip(self.stage_labels, STAGES):
            l.setText("○  " + s)
        self.log.banner("端到端预审开始")
        self._set_running(True)
        self.runner.run(TOOL, [pdf, out, "--page", self.page.value(), "--dpi", self.dpi.value()])

    def _on_output(self, s):
        self.log.append_text(s)
        # 依据脚本打印的阶段标记点亮指示灯
        marks = ["[①识别]", "[②结构化]", "[③规范比对]", "[④原图标注]"]
        for i, m in enumerate(marks):
            if m in s:
                self.stage_labels[i].setText("●  " + STAGES[i])
                self.stage_labels[i].setStyleSheet("color:#2f6fed;")

    def _on_finished(self, code):
        self._set_running(False)
        self.log.append_text(f"\n[结束] 退出码 {code}\n")
        if code != 0 or not self._out_dir:
            return
        png = os.path.join(self._out_dir, "e2e_annotated.png")
        if os.path.exists(png) and self.viewer.load(png):
            self.log.append_text("[预览] 已加载标注图（绿=合规，红=超限）。\n")
        js = os.path.join(self._out_dir, "e2e_structured.json")
        if os.path.exists(js):
            try:
                with open(js, encoding="utf-8") as f:
                    self.log.append_text("\n[结构化结果 e2e_structured.json]\n" + f.read() + "\n")
            except Exception:
                pass

    def _set_running(self, running):
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
