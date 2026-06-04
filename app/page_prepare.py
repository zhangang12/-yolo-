# -*- coding: utf-8 -*-
"""数据准备页：矢量预标注 / 质检(QC) / 中英转换 —— 标注三件套。"""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QButtonGroup, QStackedWidget,
    QSpinBox, QComboBox, QLabel, QSplitter, QCheckBox,
)
from ui_common import (card, h1, hint, section, PathRow, ImageViewer, LogConsole, ProcRunner)

TOOL = "tools/fire_anno_tool.py"
PRO = "app/backend/prelabel_pro.py"


class PreparePage(QWidget):
    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd
        self.runner = ProcRunner(cwd)
        self.runner.output.connect(lambda s: self.log.append_text(s))
        self.runner.finished.connect(self._on_finished)
        self._cur_op = "prelabel"
        self._preview_path = None

        root = QHBoxLayout(self); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(16)

        # ---------- 左：控制区 ----------
        left = card(); lv = QVBoxLayout(left); lv.setContentsMargins(18, 18, 18, 18); lv.setSpacing(12)
        lv.addWidget(h1("数据准备"))
        lv.addWidget(hint("标注三件套：从矢量 PDF 自动预标注、按 schema 质检、中文标签转英文。"))

        # 操作切换（segmented）
        seg = QHBoxLayout(); seg.setSpacing(6)
        self.bg = QButtonGroup(self); self.bg.setExclusive(True)
        for i, (key, name) in enumerate([("prelabel", "预标注"), ("qc", "质检 QC"), ("convert", "转换")]):
            b = QPushButton(name); b.setCheckable(True); b.setProperty("op", key)
            if i == 0: b.setChecked(True)
            b.clicked.connect(lambda _, k=key: self._switch(k))
            self.bg.addButton(b); seg.addWidget(b)
        lv.addLayout(seg)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_prelabel())
        self.stack.addWidget(self._page_qc())
        self.stack.addWidget(self._page_convert())
        lv.addWidget(self.stack)

        btns = QHBoxLayout()
        self.run_btn = QPushButton("运行"); self.run_btn.setObjectName("Primary")
        self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("停止"); self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(self.runner.stop); self.stop_btn.setEnabled(False)
        btns.addWidget(self.run_btn, 1); btns.addWidget(self.stop_btn)
        lv.addLayout(btns)
        left.setFixedWidth(400)
        leftcol = QVBoxLayout(); leftcol.setContentsMargins(0, 0, 0, 0); leftcol.setSpacing(0)
        leftcol.addWidget(left); leftcol.addStretch(1)
        root.addLayout(leftcol)

        # ---------- 右：预览 + 日志 ----------
        right = QSplitter(Qt.Vertical)
        self.viewer = ImageViewer()
        self.log = LogConsole()
        right.addWidget(self.viewer); right.addWidget(self.log)
        right.setSizes([460, 240])
        root.addWidget(right, 1)

    # ---- 三套表单 ----
    def _page_prelabel(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.pl_pdf = PathRow("矢量 PDF", "pdf", "PDF (*.pdf)")
        self.pl_out = PathRow("输出目录", "dir")
        row = QHBoxLayout()
        row.addWidget(section("页码")); self.pl_page = QSpinBox(); self.pl_page.setRange(0, 999)
        row.addWidget(self.pl_page)
        row.addSpacing(16)
        row.addWidget(section("DPI")); self.pl_dpi = QSpinBox(); self.pl_dpi.setRange(72, 600); self.pl_dpi.setValue(200)
        row.addWidget(self.pl_dpi); row.addStretch(1)
        v.addWidget(self.pl_pdf); v.addWidget(self.pl_out); v.addLayout(row)

        # 增强选项
        self.pl_pro = QCheckBox("增强预标注（语义细分 + 可审核）"); self.pl_pro.setChecked(True)
        self.pl_pro.toggled.connect(self._toggle_pro)
        v.addWidget(self.pl_pro)
        self.pro_box = QWidget(); pb = QHBoxLayout(self.pro_box); pb.setContentsMargins(0, 0, 0, 0)
        pb.addWidget(section("图类型")); self.pl_type = QComboBox(); self.pl_type.addItems(["auto", "site", "hall"])
        pb.addWidget(self.pl_type)
        self.pl_ocr = QCheckBox("OCR 读内容(需 tesseract)"); pb.addWidget(self.pl_ocr)
        self.pl_qc = QCheckBox("完成后自动质检"); self.pl_qc.setChecked(True); pb.addWidget(self.pl_qc)
        pb.addStretch(1)
        v.addWidget(self.pro_box)
        v.addWidget(hint("增强版：文字/线/区域按语义细分，橙/红=待审、青/绿=较确定，另出确认清单 _review.txt。\n"
                         "OCR 关闭时文字内容留空、统一待审（装 tesseract 勾上 OCR 可自动填内容并分类）。\n"
                         "基础版（取消勾选）：黄=文字块、紫=尺寸线、红=区域候选，不分子类。"))
        return w

    def _toggle_pro(self, on):
        self.pro_box.setVisible(on)

    def _page_qc(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.qc_xml = PathRow("标注 XML", "file", "CVAT XML (*.xml)")
        row = QHBoxLayout(); row.addWidget(section("图纸类型"))
        self.qc_type = QComboBox(); self.qc_type.addItems(["auto", "hall", "site"])
        row.addWidget(self.qc_type); row.addStretch(1)
        v.addWidget(self.qc_xml); v.addLayout(row)
        v.addWidget(hint("按 schema 检查标签合法性/几何/必填/枚举/必现类缺失/数量异常，结果见下方日志。"))
        return w

    def _page_convert(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        self.cv_in = PathRow("输入 XML", "file", "CVAT XML (*.xml)")
        self.cv_out = PathRow("输出 XML", "save", "CVAT XML (*.xml)")
        v.addWidget(self.cv_in); v.addWidget(self.cv_out)
        v.addWidget(hint("中文标签/属性键转英文 snake_case，带“漏转自检”（没映射到的会报出来）。输出可留空，默认 *_en.xml。"))
        return w

    def _switch(self, key):
        self._cur_op = key
        self.stack.setCurrentIndex({"prelabel": 0, "qc": 1, "convert": 2}[key])

    # ---- 运行 ----
    def _run(self):
        op = self._cur_op
        self._preview_path = None
        if op == "prelabel":
            pdf = self.pl_pdf.text(); out = self.pl_out.text()
            if not pdf:
                self.log.append_text("⚠️ 请先选择矢量 PDF。\n"); return
            out_dir = out or os.path.dirname(os.path.abspath(pdf))
            base = os.path.splitext(os.path.basename(pdf))[0]
            page = self.pl_page.value()
            if self.pl_pro.isChecked():
                # 增强版：app/backend/prelabel_pro.py
                args = [pdf, out_dir, "--page", page, "--dpi", self.pl_dpi.value(),
                        "--type", self.pl_type.currentText()]
                if self.pl_ocr.isChecked(): args.append("--ocr")
                if self.pl_qc.isChecked(): args.append("--qc")
                self._preview_path = os.path.join(out_dir, f"{base}_p{page}_overlay.jpg")
                self.log.banner("增强预标注"); self._set_running(True)
                self.runner.run(PRO, args); return
            # 基础版：fire_anno_tool prelabel
            args = ["prelabel", pdf]
            if out: args.append(out)
            args += ["--page", page, "--dpi", self.pl_dpi.value()]
            self._preview_path = os.path.join(out_dir, f"{base}_prelabel_overlay.jpg")
        elif op == "qc":
            xml = self.qc_xml.text()
            if not xml:
                self.log.append_text("⚠️ 请先选择标注 XML。\n"); return
            args = ["qc", xml, "--type", self.qc_type.currentText()]
        else:  # convert
            inp = self.cv_in.text()
            if not inp:
                self.log.append_text("⚠️ 请先选择输入 XML。\n"); return
            args = ["convert", inp]
            if self.cv_out.text(): args.append(self.cv_out.text())

        self.log.banner(f"运行：{op}")
        self._set_running(True)
        self.runner.run(TOOL, args)

    def _on_finished(self, code):
        self._set_running(False)
        self.log.append_text(f"\n[结束] 退出码 {code}\n")
        if code == 0 and self._preview_path and os.path.exists(self._preview_path):
            if self.viewer.load(self._preview_path):
                self.log.append_text(f"[预览] 已加载 {os.path.basename(self._preview_path)}\n")

    def _set_running(self, running):
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
