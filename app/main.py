#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
消防图纸 AI 预审 —— 桌面客户端
=================================================
三大功能：数据准备(预标注/质检/转换) · 端到端预审 Demo · YOLO 训练。

运行：
  pip install -r requirements.txt        # 需含 PySide6（已加入）
  python app/main.py

说明：客户端以子进程方式调用 tools/ 与 app/backend/ 下的脚本，流式回显日志，
不会卡界面；工作目录固定为项目根（-yolo-），脚本用相对路径。
"""
import os, sys

# 让 import ui_common / page_* 生效
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP_DIR)            # -yolo- 项目根
sys.path.insert(0, APP_DIR)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QButtonGroup, QStackedWidget, QLabel,
)
from ui_common import full_theme
from page_prepare import PreparePage
from page_e2e import E2EPage
from page_train import TrainPage
from page_rules import RulesPage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("消防图纸 AI 预审 · 客户端")
        self.resize(1280, 800)
        self.setMinimumSize(1000, 640)   # 防止窗口拖太窄时右侧预览/日志被挤没
        central = QWidget(); central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ---------- 侧边导航 ----------
        side = QWidget(); side.setObjectName("Sidebar"); side.setFixedWidth(220)
        sv = QVBoxLayout(side); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(0)
        brand = QLabel("🔥 消防预审"); brand.setObjectName("Brand")
        sub = QLabel("AI 预审 MVP 客户端"); sub.setObjectName("BrandSub")
        sv.addWidget(brand); sv.addWidget(sub)

        self.stack = QStackedWidget()
        pages = [
            ("数据准备", PreparePage(ROOT)),
            ("端到端预审", E2EPage(ROOT)),
            ("规则库", RulesPage(ROOT)),
            ("YOLO 训练", TrainPage(ROOT)),
        ]
        self.bg = QButtonGroup(self); self.bg.setExclusive(True)
        for i, (name, page) in enumerate(pages):
            self.stack.addWidget(page)
            b = QPushButton(name); b.setObjectName("NavBtn"); b.setCheckable(True)
            if i == 0: b.setChecked(True)
            b.clicked.connect(lambda _, idx=i: self.stack.setCurrentIndex(idx))
            self.bg.addButton(b); sv.addWidget(b)
        sv.addStretch(1)
        foot = QLabel("识别·结构化·规范比对·标注"); foot.setObjectName("BrandSub")
        sv.addWidget(foot)

        root.addWidget(side); root.addWidget(self.stack, 1)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(full_theme())
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
