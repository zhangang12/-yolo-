# -*- coding: utf-8 -*-
"""端到端预审页：拖入 PDF → ①识别 ②结构化 ③规范比对 ④原图标注。"""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QSpinBox, QSplitter, QLabel,
)
from ui_common import (card, h1, hint, section, PathRow, ImageViewer, LogConsole, ProcRunner)

TOOL = "tools/e2e_demo.py"
REPORT = "tools/report_docx.py"

STAGES = ["① 识别 (文字层直读 / OCR)", "② 结构化 (面积/类型/距离)",
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
                          "优先读图纸文字层：防火分区面积精确、带规范出处；图纸未写明面积时回退 OCR 识别（含噪声）。"))

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

        btns = QHBoxLayout()
        self.run_btn = QPushButton("开始预审"); self.run_btn.setObjectName("Primary"); self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("停止"); self.stop_btn.setObjectName("Danger"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.runner.stop)
        btns.addWidget(self.run_btn, 1); btns.addWidget(self.stop_btn)
        lv.addLayout(btns)
        self.report_btn = QPushButton("导出 Word 报告")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self._export_report)
        lv.addWidget(self.report_btn)
        left.setFixedWidth(400)
        leftcol = QVBoxLayout(); leftcol.setContentsMargins(0, 0, 0, 0); leftcol.setSpacing(0)
        leftcol.addWidget(left); leftcol.addStretch(1)
        root.addLayout(leftcol)

        # 报告生成用独立子进程（避免与预审任务冲突）
        self.report_runner = ProcRunner(cwd)
        self.report_runner.output.connect(lambda s: self.log.append_text(s))
        self.report_runner.finished.connect(
            lambda c: self.log.append_text(
                f"[报告] 已导出到预审输出目录的“审查报告.docx”。\n" if c == 0
                else f"[报告] 生成失败（退出码 {c}）。\n"))

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
        self.report_btn.setEnabled(False)
        for l, s in zip(self.stage_labels, STAGES):
            l.setText("○  " + s)
        self.log.banner("端到端预审开始")
        self._set_running(True)
        self.runner.run(TOOL, [pdf, out, "--page", self.page.value(), "--dpi", self.dpi.value()])

    def _light(self, i):
        self.stage_labels[i].setText("●  " + STAGES[i])
        self.stage_labels[i].setStyleSheet("color:#2f6fed;")

    def _on_output(self, s):
        self.log.append_text(s)
        # 依据脚本打印的阶段标记点亮指示灯
        if "[①②文字层直读]" in s:        # 文字层模式一次点亮 ①②
            self._light(0); self._light(1)
        marks = ["[①识别]", "[②结构化]", "[③规范比对]", "[④原图标注]"]
        for i, m in enumerate(marks):
            if m in s:
                self._light(i)

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
            self.report_btn.setEnabled(True)   # 有结果了，允许导出报告

    def _export_report(self):
        if not self._out_dir:
            self.log.append_text("⚠️ 请先跑一次端到端预审。\n"); return
        self.log.banner("导出 Word 审查报告")
        self.report_runner.run(REPORT, [self._out_dir])

    def _set_running(self, running):
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
