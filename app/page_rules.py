# -*- coding: utf-8 -*-
"""规则库页：展示规则引擎已结构化的全部消防检查规则，可筛选/搜索/试跑。"""
import os, json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QComboBox, QLineEdit, QLabel,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PySide6.QtGui import QColor
from ui_common import card, h1, hint, section, LogConsole, ProcRunner

ENGINE = "tools/rule_engine.py"
RULES_JSON = "rules/rules.json"
SAMPLE = "examples/sample_structured.json"


def _check_str(check):
    """把 check 判据转成人类可读的判据文字。"""
    op = {"le": "≤", "lt": "<", "ge": "≥", "gt": ">", "eq": "=", "ne": "≠",
          "in": "∈", "not_in": "∉"}.get(check.get("op"), check.get("op", ""))
    t = check.get("type", "compare")
    if t == "compare":
        path = (check.get("path") or "").replace("target.", "")
        if "threshold_path" in check:
            tp = check["threshold_path"].replace("target.", "")
            extra = ""
            if "threshold_divisor" in check: extra += f"/{check['threshold_divisor']}"
            if "threshold_cap" in check: extra += f" 且≤{check['threshold_cap']}"
            return f"{path} {op} {tp}{extra}"
        return f"{path} {op} {check.get('threshold')}"
    if t == "count":
        coll = (check.get("collection_path") or "").split(".")[-1]
        return f"count({coll}) {op} {check.get('threshold')}"
    if t == "sum_aggregate":
        coll = (check.get("collection_path") or "").split(".")[-1]
        return f"Σ {coll}.{check.get('field')} {op} {check.get('threshold')}"
    return t


def _applies_str(applies):
    if not applies:
        return "无条件"
    parts = []
    for c in applies:
        p = (c.get("path") or "").replace("target.", "").replace("station.", "站.")
        parts.append(f"{p}={c.get('value')}")
    return " 且 ".join(parts)


class RulesPage(QWidget):
    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd
        self.rules = []
        self.runner = ProcRunner(cwd)
        self.runner.output.connect(lambda s: self.log.append_text(s))
        self.runner.finished.connect(lambda c: self.log.append_text(f"\n[结束] 退出码 {c}\n"))

        root = QHBoxLayout(self); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(16)

        # ---------- 左：说明 + 筛选 ----------
        left = card(); lv = QVBoxLayout(left); lv.setContentsMargins(18, 18, 18, 18); lv.setSpacing(12)
        lv.addWidget(h1("规则库"))
        lv.addWidget(hint(
            "系统内置的消防检查规则，会自动逐条比对图纸数据。\n\n"
            "• 按固定标准判定，每条都标明规范出处，结论可查可追溯。\n"
            "• 图纸信息不足时标“待人工复核”，不乱判。"))

        self.stat = section("加载中…"); lv.addWidget(self.stat)

        f = QHBoxLayout()
        f.addWidget(section("类别"))
        self.cat = QComboBox(); self.cat.currentTextChanged.connect(self._refilter)
        f.addWidget(self.cat, 1); lv.addLayout(f)

        self.search = QLineEdit(); self.search.setPlaceholderText("搜索 规则ID / 名称 / 出处…")
        self.search.textChanged.connect(self._refilter)
        lv.addWidget(self.search)

        lv.addWidget(section("两类条文"))
        lv.addWidget(hint("🔴 强制条文：必须满足，不满足即不通过。\n🟠 建议条文：宜满足，不满足给提醒。"))

        lv.addStretch(1)
        self.try_btn = QPushButton("试运行（用示例数据演示）"); self.try_btn.setObjectName("Primary")
        self.try_btn.clicked.connect(self._try_run)
        lv.addWidget(self.try_btn)
        left.setFixedWidth(360)
        root.addWidget(left)

        # ---------- 右：规则表 + 日志 ----------
        right = QSplitter(Qt.Vertical)
        self.table = QTableWidget()
        cols = ["规则ID", "类别", "名称", "判据", "适用条件", "条文", "出处"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)            # 长文本换行
        hdr = self.table.horizontalHeader()
        # ID/类别/强条 按内容；名称/判据/出处 固定可拖；适用条件占剩余并换行
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 规则ID
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # 类别
        hdr.setSectionResizeMode(2, QHeaderView.Interactive)       # 名称
        hdr.setSectionResizeMode(3, QHeaderView.Interactive)       # 判据
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)           # 适用条件
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 强条
        hdr.setSectionResizeMode(6, QHeaderView.Interactive)       # 出处
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 190)
        self.table.setColumnWidth(6, 200)
        right.addWidget(self.table)
        self.log = LogConsole(); right.addWidget(self.log)
        right.setSizes([560, 180])
        root.addWidget(right, 1)

        self._load()

    def _load(self):
        path = os.path.join(self.cwd, RULES_JSON)
        try:
            spec = json.load(open(path, encoding="utf-8"))
            self.rules = spec.get("rules", spec)
        except Exception as e:
            self.stat.setText(f"读取 rules.json 失败：{e}"); return
        cats = sorted({r.get("category", "?") for r in self.rules})
        self.cat.blockSignals(True)
        self.cat.addItem("全部")
        for c in cats: self.cat.addItem(c)
        self.cat.blockSignals(False)
        n_crit = sum(1 for r in self.rules if r.get("mandatory"))
        self.stat.setText(f"共 {len(self.rules)} 条规则 · {len(cats)} 大类 · "
                          f"强制 {n_crit} 条 / 建议 {len(self.rules)-n_crit} 条")
        self._refilter()

    def _refilter(self):
        cat = self.cat.currentText()
        kw = self.search.text().strip().lower()
        rows = []
        for r in self.rules:
            if cat and cat != "全部" and r.get("category") != cat:
                continue
            if kw and kw not in (r.get("rule_id", "") + r.get("name", "") +
                                 r.get("source", "")).lower():
                continue
            rows.append(r)
        self._fill(rows)

    def _fill(self, rows):
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            mand = r.get("mandatory")
            cells = [
                r.get("rule_id", ""), r.get("category", ""), r.get("name", ""),
                _check_str(r.get("check", {})), _applies_str(r.get("applies_when", [])),
                "🔴强制" if mand else "🟠建议", r.get("source", ""),
            ]
            for j, txt in enumerate(cells):
                it = QTableWidgetItem(str(txt))
                it.setToolTip(r.get("message", "") if j == 2 else str(txt))
                if j == 5:
                    it.setForeground(QColor("#e03131") if mand else QColor("#e8590c"))
                self.table.setItem(i, j, it)
        self.table.resizeRowsToContents()       # 换行后按内容调整行高

    def _try_run(self):
        self.log.banner("试运行：用示例数据演示规则判定")
        self.runner.run(ENGINE, [RULES_JSON, "--data", SAMPLE])
