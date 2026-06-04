# -*- coding: utf-8 -*-
"""客户端公共件：主题样式、文件选择行、可缩放图片查看器、日志台、子进程执行器。"""
import os, sys
from PySide6.QtCore import Qt, QObject, Signal, QProcess, QProcessEnvironment
from PySide6.QtGui import QPixmap, QPainter, QFont, QColor, QIcon, QTextCursor, QPixmap as _Px
from PySide6.QtWidgets import (
    QWidget, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QFileDialog, QGraphicsView, QGraphicsScene, QPlainTextEdit, QFrame, QSizePolicy,
)

# ====================== 主题（浅色，扁平卡片） ======================
THEME = """
* { font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; font-size: 13px; color: #1f2430; }
QMainWindow, QWidget#Root { background: #f3f5f8; }

/* 侧边导航 */
QWidget#Sidebar { background: #ffffff; border-right: 1px solid #e3e6ea; }
QLabel#Brand { font-size: 16px; font-weight: 700; color: #1f2430; padding: 18px 16px 4px 18px; }
QLabel#BrandSub { font-size: 11px; color: #9aa1ad; padding: 0 18px 14px 18px; }
QPushButton#NavBtn {
    text-align: left; padding: 11px 18px; border: none; border-radius: 0px;
    background: transparent; color: #4b5563; font-size: 14px;
}
QPushButton#NavBtn:hover { background: #f0f3f8; color: #1f2430; }
QPushButton#NavBtn:checked { background: #eaf1ff; color: #2f6fed; border-left: 3px solid #2f6fed; font-weight: 600; }

/* 卡片 */
QFrame#Card { background: #ffffff; border: 1px solid #e3e6ea; border-radius: 12px; }
QLabel#H1 { font-size: 20px; font-weight: 700; color: #1f2430; }
QLabel#Hint { color: #6b7280; font-size: 12px; }
QLabel#Section { font-size: 13px; font-weight: 600; color: #374151; }

/* 输入框 */
QLineEdit {
    background: #ffffff; border: 1px solid #d4d9e0; border-radius: 8px;
    padding: 8px 10px; color: #1f2430; selection-background-color: #2f6fed; selection-color: #ffffff;
}
QLineEdit:focus { border: 1px solid #2f6fed; }

/* 按钮 */
QPushButton {
    background: #ffffff; border: 1px solid #d4d9e0; border-radius: 8px;
    padding: 8px 16px; color: #374151;
}
QPushButton:hover { background: #f0f3f8; border: 1px solid #b9c1cc; }
QPushButton:disabled { color: #aab0bb; background: #f4f5f7; border: 1px solid #e3e6ea; }
QPushButton#Primary { background: #2f6fed; border: none; color: #ffffff; font-weight: 600; }
QPushButton#Primary:hover { background: #195fe6; }
QPushButton#Primary:disabled { background: #aac3f6; color: #ffffff; }
/* 分段切换按钮：选中态高亮，让用户看清当前在哪个工具 */
QPushButton#Seg { padding: 7px 10px; }
QPushButton#Seg:checked { background: #eaf1ff; border: 1px solid #2f6fed; color: #2f6fed; font-weight: 600; }
QPushButton#Danger { background: #fdecef; border: 1px solid #f3c2cb; color: #d6336c; }
QPushButton#Danger:hover { background: #fbdce2; }

/* 日志台 */
QPlainTextEdit#Log {
    background: #fafbfc; border: 1px solid #e3e6ea; border-radius: 10px;
    font-family: 'Cascadia Mono','Consolas','monospace'; font-size: 12px; color: #2b3340;
    padding: 8px;
}
/* 图查看器 */
QGraphicsView#Viewer { background: #f7f8fa; border: 1px solid #e3e6ea; border-radius: 10px; }
QLabel#Drop { color: #9aa1ad; }

QComboBox, QSpinBox {
    background: #ffffff; border: 1px solid #d4d9e0; border-radius: 8px; padding: 6px 8px; color: #1f2430;
    min-width: 64px; min-height: 22px;
}
QComboBox:focus, QSpinBox:focus { border: 1px solid #2f6fed; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #eaf1ff; selection-color: #2f6fed; border: 1px solid #d4d9e0; }

/* 数字框上下按钮：给足可点区域 + 浅灰底分隔，箭头用系统原生（Windows 下是清晰三角） */
QSpinBox { padding-right: 22px; }
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border; width: 20px; background: #eef1f5; border-left: 1px solid #d9dee5;
}
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 7px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 7px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #dfe6ef; }
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed { background: #cfd8e3; }
/* up/down 箭头图标由 full_theme() 运行时生成并注入（见文件末尾 _spin_arrow_css） */
/* 表格（规则库） */
QTableWidget {
    background: #ffffff; alternate-background-color: #f7f8fa; gridline-color: #e8ebef;
    border: 1px solid #e3e6ea; border-radius: 10px; color: #2b3340;
    selection-background-color: #eaf1ff; selection-color: #1f2430;
}
QTableWidget::item { padding: 5px 8px; }
QHeaderView::section {
    background: #eef1f5; color: #4b5563; padding: 7px 8px; border: none;
    border-right: 1px solid #e3e6ea; border-bottom: 1px solid #d4d9e0; font-weight: 600;
}

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c7ccd4; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #aab0bb; }
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
        self._empty = QLabel("🖼\n\n运行后，结果图在此预览\n滚轮缩放 · 拖动平移", self)
        self._empty.setObjectName("Drop"); self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color:#aab0bb; font-size:14px; line-height:1.6;")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._empty.setGeometry(self.rect())

    def show_empty(self, text=None):
        if text:
            self._empty.setText(text)
        self._empty.show()

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
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertPlainText(s)
        self.moveCursor(QTextCursor.MoveOperation.End)
        sb = self.verticalScrollBar(); sb.setValue(sb.maximum())

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


# ====================== 数字框三角箭头（运行时生成图标） ======================
def _spin_arrow_css():
    """生成上/下三角箭头 PNG 并返回引用它们的 QSS。

    Qt 样式表的 ::up-arrow 只认 image，不认 CSS 边框三角；不画 image 时各平台
    原生箭头表现不一（Fusion 下甚至不可见）。这里用 QPainter 现画两个三角存成
    PNG，再 image: url() 引用——与平台/风格无关，所见即所得。需 QApplication 已建。
    """
    import tempfile
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPolygonF

    cache = os.path.join(tempfile.gettempdir(), "fireapp_assets")
    try:
        os.makedirs(cache, exist_ok=True)
    except OSError:
        return ""  # 退化：不注入箭头图标，保留原生箭头，不影响功能

    def _draw(path, up):
        S = 28  # 高分辨率绘制，downscale 后更锐利
        pm = QPixmap(S, S); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen); p.setBrush(QColor("#5b6675"))
        if up:
            tri = QPolygonF([QPointF(S*0.22, S*0.64), QPointF(S*0.78, S*0.64), QPointF(S*0.50, S*0.34)])
        else:
            tri = QPolygonF([QPointF(S*0.22, S*0.36), QPointF(S*0.78, S*0.36), QPointF(S*0.50, S*0.66)])
        p.drawPolygon(tri); p.end()
        pm.save(path, "PNG")

    up_p = os.path.join(cache, "spin_up.png"); dn_p = os.path.join(cache, "spin_down.png")
    try:
        _draw(up_p, True); _draw(dn_p, False)
    except Exception:
        return ""
    up_url = up_p.replace("\\", "/"); dn_url = dn_p.replace("\\", "/")
    return ("\nQSpinBox::up-arrow { image: url('%s'); width: 11px; height: 11px; }\n"
            "QSpinBox::down-arrow { image: url('%s'); width: 11px; height: 11px; }\n"
            % (up_url, dn_url))


def full_theme():
    """主题 = 基础 QSS + 运行时生成的数字框箭头图标。在 QApplication 创建后调用。"""
    return THEME + _spin_arrow_css()
