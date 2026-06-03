# -*- coding: utf-8 -*-
"""客户端公共件：主题样式、文件选择行、可缩放图片查看器、日志台、子进程执行器。"""
import os, sys
from PySide6.QtCore import Qt, QObject, Signal, QProcess, QProcessEnvironment
from PySide6.QtGui import QPixmap, QPainter, QFont, QColor, QIcon, QPixmap as _Px
from PySide6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QFileDialog, QGraphicsView, QGraphicsScene, QPlainTextEdit, QFrame, QSizePolicy,
)

# ====================== 主题（深色，扁平卡片） ======================
THEME = """
* { font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; font-size: 13px; color: #e6e9ef; }
QMainWindow, QWidget#Root { background: #14171f; }

/* 侧边导航 */
QWidget#Sidebar { background: #0f1219; border-right: 1px solid #232838; }
QLabel#Brand { font-size: 16px; font-weight: 700; color: #ffffff; padding: 18px 16px 4px 18px; }
QLabel#BrandSub { font-size: 11px; color: #6b7280; padding: 0 18px 14px 18px; }
QPushButton#NavBtn {
    text-align: left; padding: 11px 18px; border: none; border-radius: 0px;
    background: transparent; color: #aab2c5; font-size: 14px;
}
QPushButton#NavBtn:hover { background: #1a1f2b; color: #ffffff; }
QPushButton#NavBtn:checked { background: #1d2433; color: #ffffff; border-left: 3px solid #4f8cff; }

/* 卡片 */
QFrame#Card { background: #1a1e29; border: 1px solid #262c3b; border-radius: 12px; }
QLabel#H1 { font-size: 20px; font-weight: 700; color: #ffffff; }
QLabel#Hint { color: #8b93a7; font-size: 12px; }
QLabel#Section { font-size: 13px; font-weight: 600; color: #cfd6e6; }

/* 输入框 */
QLineEdit {
    background: #11141c; border: 1px solid #2b3243; border-radius: 8px;
    padding: 8px 10px; color: #e6e9ef; selection-background-color: #4f8cff;
}
QLineEdit:focus { border: 1px solid #4f8cff; }

/* 按钮 */
QPushButton {
    background: #232a3a; border: 1px solid #313a4f; border-radius: 8px;
    padding: 8px 16px; color: #e6e9ef;
}
QPushButton:hover { background: #2b3447; }
QPushButton:disabled { color: #5a6376; background: #1b2030; }
QPushButton#Primary { background: #4f8cff; border: none; color: #ffffff; font-weight: 600; }
QPushButton#Primary:hover { background: #6c9eff; }
QPushButton#Primary:disabled { background: #2f3a52; color: #8b93a7; }
QPushButton#Danger { background: #2a1d22; border: 1px solid #5a2b35; color: #ff8a9b; }
QPushButton#Danger:hover { background: #3a2229; }

/* 日志台 */
QPlainTextEdit#Log {
    background: #0c0f16; border: 1px solid #232838; border-radius: 10px;
    font-family: 'Cascadia Mono','Consolas','monospace'; font-size: 12px; color: #c7d0e0;
    padding: 8px;
}
/* 图查看器 */
QGraphicsView#Viewer { background: #0c0f16; border: 1px solid #232838; border-radius: 10px; }
QLabel#Drop { color: #6b7280; }

QComboBox, QSpinBox {
    background: #11141c; border: 1px solid #2b3243; border-radius: 8px; padding: 6px 8px; color: #e6e9ef;
}
QComboBox:focus, QSpinBox:focus { border: 1px solid #4f8cff; }
QComboBox QAbstractItemView { background: #1a1e29; selection-background-color: #4f8cff; border: 1px solid #313a4f; }
/* 表格（规则库） */
QTableWidget {
    background: #11141c; alternate-background-color: #161a24; gridline-color: #232838;
    border: 1px solid #232838; border-radius: 10px; color: #d7dcea;
    selection-background-color: #21314e; selection-color: #ffffff;
}
QTableWidget::item { padding: 5px 8px; }
QHeaderView::section {
    background: #1b2030; color: #aab2c5; padding: 7px 8px; border: none;
    border-right: 1px solid #232838; border-bottom: 1px solid #2b3243; font-weight: 600;
}

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2f3a52; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


def card():
    f = QFrame(); f.setObjectName("Card"); return f


def h1(text):
    l = QLabel(text); l.setObjectName("H1"); return l


def hint(text):
    l = QLabel(text); l.setObjectName("Hint"); l.setWordWrap(True); return l


def section(text):
    l = QLabel(text); l.setObjectName("Section"); return l


# ====================== 文件选择行（支持拖拽） ======================
class PathRow(QWidget):
    """一行：[标签] [可拖拽的路径框] [浏览]。mode: 'file' | 'pdf' | 'dir' | 'save'"""
    changed = Signal(str)

    def __init__(self, label, mode="file", filt="所有文件 (*.*)", placeholder=""):
        super().__init__()
        self.mode = mode; self.filt = filt
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)
        lab = QLabel(label); lab.setMinimumWidth(96); lab.setObjectName("Section")
        self.edit = QLineEdit(); self.edit.setPlaceholderText(placeholder or "拖入此处，或点右侧浏览…")
        self.edit.setAcceptDrops(True)
        self.edit.dragEnterEvent = self._drag
        self.edit.dropEvent = self._drop
        self.edit.textChanged.connect(self.changed)
        btn = QPushButton("浏览"); btn.clicked.connect(self._browse)
        lay.addWidget(lab); lay.addWidget(self.edit, 1); lay.addWidget(btn)

    def _drag(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def _drop(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.edit.setText(urls[0].toLocalFile())

    def _browse(self):
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, "选择目录")
        elif self.mode == "save":
            p, _ = QFileDialog.getSaveFileName(self, "保存为", "", self.filt)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "选择文件", "", self.filt)
        if p:
            self.edit.setText(p)

    def text(self):
        return self.edit.text().strip()

    def set_text(self, t):
        self.edit.setText(t)


# ====================== 可缩放/拖动 图片查看器 ======================
class ImageViewer(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setObjectName("Viewer")
        self._scene = QGraphicsScene(self); self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._item = None
        self._empty = QLabel("（运行后在此预览结果图）", self)
        self._empty.setObjectName("Drop"); self._empty.setAlignment(Qt.AlignCenter)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._empty.setGeometry(self.rect())

    def load(self, path):
        if not path or not os.path.exists(path):
            return False
        pm = QPixmap(path)
        if pm.isNull():
            return False
        self._scene.clear()
        self._item = self._scene.addPixmap(pm)
        self._scene.setSceneRect(pm.rect())
        self._empty.hide()
        self.fitInView(self._item, Qt.KeepAspectRatio)
        return True

    def wheelEvent(self, e):
        if self._item is None:
            return
        factor = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


# ====================== 日志台 ======================
class LogConsole(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setObjectName("Log"); self.setReadOnly(True)
        self.setMaximumBlockCount(5000)

    def append_text(self, s):
        self.moveCursor(self.textCursor().End)
        self.insertPlainText(s)
        self.moveCursor(self.textCursor().End)

    def banner(self, s):
        self.append_text(f"\n{'─'*60}\n{s}\n{'─'*60}\n")


# ====================== 子进程执行器（流式日志，不卡 UI） ======================
class ProcRunner(QObject):
    output = Signal(str)
    finished = Signal(int)
    started = Signal()

    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd
        self.proc = None

    def is_running(self):
        return self.proc is not None and self.proc.state() != QProcess.NotRunning

    def run(self, script_rel, args):
        if self.is_running():
            self.output.emit("⚠️ 已有任务在运行，请先停止。\n"); return
        self.proc = QProcess()
        self.proc.setWorkingDirectory(self.cwd)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._on_out)
        self.proc.finished.connect(self._on_fin)
        full = [script_rel] + [str(a) for a in args]
        self.output.emit(f"$ python {' '.join(full)}\n")
        self.proc.start(sys.executable, full)
        self.started.emit()

    def stop(self):
        if self.is_running():
            self.proc.kill()
            self.output.emit("\n⏹ 已请求停止。\n")

    def _on_out(self):
        data = bytes(self.proc.readAllStandardOutput())
        self.output.emit(data.decode("utf-8", errors="replace"))

    def _on_fin(self, code, _status):
        self.finished.emit(int(code))
