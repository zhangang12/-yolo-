# -*- coding: utf-8 -*-
"""端到端预审页：拖入 PDF → ①识别 ②结构化 ③规范比对 ④原图标注。"""
import os, sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QSpinBox, QSplitter, QLabel,
)
from ui_common import (card, h1, hint, section, PathRow, ImageViewer, LogConsole, ProcRunner)

# 复用 tools/naming.py 的产物命名(必须与 mvp_e2e 一致,否则找不到产物文件)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import naming

TOOL = "tools/e2e_demo.py"          # 旧:PDF only(无 CVAT 时兜底)
TOOL_MVP = "tools/mvp_e2e.py"       # 新:CVAT XML + PDF/jpg → 真值审查
REPORT = "tools/report_docx.py"

STAGES = ["① 识别图纸内容（文字 / 尺寸）", "② 计算面积、类型、疏散距离",
          "③ 对照消防规范逐条比对", "④ 在原图标出问题位置"]


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
        lv.addWidget(h1("端到端预审"))
        lv.addWidget(hint("拖入一张图纸 PDF，自动跑完「识别 → 计算 → 对照规范 → 标注」全流程，"
                          "最后给出一张标好问题的图和一份结论。\n\n"
                          "▸ 推荐：同时选上 CVAT 标注 XML → 走真值审查，标注图清晰只圈违规位置 + 中文规则编号 + 走 37 条规则。\n"
                          "▸ 仅 PDF：兜底走文字层直读 + OCR，只能查防火分区面积；噪声多。\n\n"
                          "比例尺自动从 PDF 矢量文字层读 1:N（如 1:200），无需手填。"))

        self.pdf = PathRow("图纸 PDF", "pdf", "PDF (*.pdf)")
        self.xml = PathRow("CVAT 标注（可选）", "file", "XML (*.xml)",
                           placeholder="如有真值标注 → 走真值审查，结果最准；留空走旧 OCR 路径")
        self.out = PathRow("结果存到", "dir", placeholder="留空＝在 PDF 旁新建 e2e_out 文件夹")
        lv.addWidget(self.pdf); lv.addWidget(self.xml); lv.addWidget(self.out)

        row = QHBoxLayout()
        row.addWidget(section("第几页")); self.page = QSpinBox(); self.page.setRange(0, 999)
        row.addWidget(self.page); row.addSpacing(16)
        row.addWidget(section("清晰度")); self.dpi = QSpinBox(); self.dpi.setRange(72, 600); self.dpi.setValue(200)
        row.addWidget(self.dpi); row.addStretch(1)
        lv.addLayout(row)
        lv.addWidget(hint("需用矢量 PDF（能用鼠标选中文字的那种）；页码从 0 开始数，清晰度默认 200 够用。"))

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
        xml = self.xml.text()
        if not pdf:
            self.log.append_text("⚠️ 请先选择矢量 PDF。\n"); return

        # ★ 关键改动:用户没手填 CVAT XML 时,自动在仓库 data/anno_*/ 下找匹配的标注。
        #   命中即走 mvp_e2e 真值路径,UI 提示已自动匹配;找不到才退回旧路径。
        #   这是用户痛点修复:之前不填 XML 默认走旧路径 → 产物文件名是旧的 e2e_annotated.xxx。
        if not xml:
            xml = self._auto_find_xml(pdf)
            if xml:
                self.log.append_text(f"[自动匹配] 找到 CVAT 标注: {xml}\n  走真值路径(mvp_e2e)\n")
                self.xml.set_text(xml)        # 同步到 UI,让用户看到
            else:
                self.log.append_text("[自动匹配] 未在 data/anno_*/ 下找到匹配的 CVAT 标注\n"
                                     "  → 走兜底路径(e2e_demo),只能查防火分区面积\n")

        out = self.out.text() or os.path.join(os.path.dirname(os.path.abspath(pdf)), "e2e_out")
        self._out_dir = out
        self._use_mvp = bool(xml)
        self._pdf_stem = naming.product_stem(pdf)
        self.report_btn.setEnabled(False)
        for l, s in zip(self.stage_labels, STAGES):
            l.setText("○  " + s)

        if xml:
            self.log.banner("端到端预审开始 [真值路径 mvp_e2e]")
            self._set_running(True)
            self.runner.run(TOOL_MVP, [xml, pdf, out, "--pdf", pdf])
        else:
            self.log.banner("端到端预审开始 [兜底路径 e2e_demo,无真值标注]")
            self._set_running(True)
            self.runner.run(TOOL, [pdf, out, "--page", self.page.value(), "--dpi", self.dpi.value()])

    def _auto_find_xml(self, pdf_path):
        """根据 PDF 文件名 stem,在仓库 data/anno_hall/ / anno_site/ 等目录下找匹配的 CVAT XML。

        匹配逻辑: 打开候选 XML 的 image name,看是否含 PDF 的关键词 (站名 / 站厅层 / 总平面)。
        命中即返回路径;不命中返回 None。
        """
        import glob, xml.etree.ElementTree as ET
        from naming import extract_station, extract_kind
        st = extract_station(os.path.basename(pdf_path))
        kind = extract_kind(os.path.basename(pdf_path))
        if not st:
            return None
        # 在项目 data/ 下递归找 annotations.xml
        roots = [
            os.path.join(self.cwd, "data"),
            os.path.dirname(os.path.dirname(os.path.abspath(pdf_path))),  # 兼容用户拷贝整套数据到别处
        ]
        candidates = []
        for root in roots:
            if root and os.path.isdir(root):
                candidates += glob.glob(os.path.join(root, "**", "annotations.xml"), recursive=True)
                candidates += glob.glob(os.path.join(root, "**", "*.xml"), recursive=True)
        # 按 image name 匹配。站名带"站"和不带"站"都试,兼容 CVAT 里命名不带"站"的情况(如"明海")
        st_short = st.rstrip("站")
        for xml_path in sorted(set(candidates)):
            try:
                root = ET.parse(xml_path).getroot()
            except Exception:
                continue
            for im in root.findall(".//image"):
                name = im.get("name") or ""
                station_hit = (st in name) or (st_short and st_short in name)
                kind_hit = (not kind) or (kind in name) or \
                           ("站厅" in name and kind == "站厅层") or \
                           ("平面图" in name and kind == "总平面图")
                if station_hit and kind_hit:
                    return xml_path
        return None

    def _light(self, i):
        self.stage_labels[i].setText("●  " + STAGES[i])
        self.stage_labels[i].setStyleSheet("color:#2f6fed;")

    def _on_output(self, s):
        self.log.append_text(s)
        # 旧路径(e2e_demo)的阶段标记
        if "[①②文字层直读]" in s:        # 文字层模式一次点亮 ①②
            self._light(0); self._light(1)
        for i, m in enumerate(["[①识别]", "[②结构化]", "[③规范比对]", "[④原图标注]"]):
            if m in s:
                self._light(i)
        # 新路径(mvp_e2e)的阶段标记 [0/4] [1/4] [2/4] [3/4]
        # 0=scale 标定;1=adapter;2=规则引擎;3=底图标注;4=写报告。映射到 4 个指示灯。
        for i, m in enumerate(["[0/4]", "[1/4]", "[2/4]", "[3/4]"]):
            if m in s:
                self._light(i)

    def _on_finished(self, code):
        self._set_running(False)
        self.log.append_text(f"\n[结束] 退出码 {code}\n")
        if code != 0 or not self._out_dir:
            return
        # 找产物图:mvp_e2e 出 <stem>_e2e_fail.jpg;e2e_demo 出 e2e_annotated.png
        if getattr(self, "_use_mvp", False):
            stem = getattr(self, "_pdf_stem", "")
            png = os.path.join(self._out_dir, f"{stem}_e2e_fail.jpg")
            js = os.path.join(self._out_dir, f"{stem}_findings.json")
            md = os.path.join(self._out_dir, f"{stem}_report.md")
        else:
            png = os.path.join(self._out_dir, "e2e_annotated.png")
            js = os.path.join(self._out_dir, "e2e_structured.json")
            md = None
        if os.path.exists(png) and self.viewer.load(png):
            tip = "[预览] 已加载标注图（红框=违规位置，旁附规则编号）。\n" \
                  if getattr(self, "_use_mvp", False) \
                  else "[预览] 已加载标注图（绿=合规，红=超限）。\n"
            self.log.append_text(tip)
        if os.path.exists(js):
            try:
                with open(js, encoding="utf-8") as f:
                    self.log.append_text(f"\n[结构化结果 {os.path.basename(js)}]\n" + f.read()[:8000] + "\n")
            except Exception:
                pass
            self.report_btn.setEnabled(True)
        if md and os.path.exists(md):
            try:
                with open(md, encoding="utf-8") as f:
                    self.log.append_text(f"\n[报告 {os.path.basename(md)}]\n" + f.read() + "\n")
            except Exception:
                pass

    def _export_report(self):
        if not self._out_dir:
            self.log.append_text("⚠️ 请先跑一次端到端预审。\n"); return
        # mvp 路径下 docx 已在 mvp_e2e 跑完时自动生成,直接打开
        if getattr(self, "_use_mvp", False):
            stem = getattr(self, "_pdf_stem", "")
            docx = os.path.join(self._out_dir, f"{stem}_审查意见表.docx")
            if os.path.exists(docx):
                self.log.append_text(f"[报告] Word 审查意见表已生成: {docx}\n")
                try:
                    os.startfile(docx)   # Windows 用关联程序(Word)打开
                except Exception as e:
                    self.log.append_text(f"  (自动打开失败,请手动打开: {e})\n")
            else:
                self.log.append_text("⚠️ docx 未生成,可能 mvp_e2e 失败,检查日志。\n")
            return
        # 旧路径:跑 report_docx 脚本
        self.log.banner("导出 Word 审查报告 (旧路径)")
        self.report_runner.run(REPORT, [self._out_dir])

    def _set_running(self, running):
        self.run_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
