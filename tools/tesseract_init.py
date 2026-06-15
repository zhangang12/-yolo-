#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tesseract 自动探测初始化  tesseract_init.py
=================================================
PATH 中找不到 tesseract 时,自动探测 Windows / macOS / Linux 常见安装目录,
显式设置 pytesseract.pytesseract.tesseract_cmd 让脚本无需手工配置 PATH 也能跑。

任何用到 pytesseract 的脚本顶部加一行:
    from tesseract_init import ensure_tesseract
    ensure_tesseract()       # 静默返回 True/False;True 即可调 pytesseract.image_to_*
"""
import os, shutil, subprocess

# Windows / Linux / macOS 常见安装路径(按优先级)
COMMON_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
    r"C:\tools\tesseract\tesseract.exe",
    "/opt/homebrew/bin/tesseract",       # macOS Apple Silicon
    "/usr/local/bin/tesseract",          # macOS Intel / Linux 手装
    "/usr/bin/tesseract",                # Linux apt
]


def ensure_tesseract(verbose=False):
    """确保 pytesseract 能找到 tesseract;返回 (ok, cmd_path or None)。

    1. 若 PATH 中 'tesseract' 可执行,直接用,不改 pytesseract 配置
    2. 否则在 COMMON_PATHS 找第一个存在的,设到 pytesseract.tesseract_cmd
    3. 都找不到返回 (False, None);调用方应给出安装指引
    """
    try:
        import pytesseract
    except ImportError:
        if verbose:
            print("⚠️ pytesseract 未安装,跳过 tesseract 探测。")
        return False, None

    # ① PATH 检查
    on_path = shutil.which("tesseract")
    if on_path:
        if verbose:
            print(f"✅ tesseract 在 PATH: {on_path}")
        return True, on_path

    # ② 常见路径
    for p in COMMON_PATHS:
        if os.path.isfile(p):
            pytesseract.pytesseract.tesseract_cmd = p
            if verbose:
                # 顺便跑 --version 验证
                try:
                    ver = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=5)
                    first = (ver.stdout or ver.stderr).splitlines()[0] if (ver.stdout or ver.stderr) else "?"
                    print(f"✅ tesseract 在 {p} ({first})")
                except Exception:
                    print(f"✅ tesseract 探测到: {p}")
            return True, p

    # ③ 找不到 → 给指引
    if verbose:
        print("❌ 未找到 tesseract。安装:")
        print("   Windows: 从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装,")
        print("            装到 C:\\Program Files\\Tesseract-OCR\\(默认即可,中文勾 Chinese Simplified)。")
        print("   macOS  : brew install tesseract tesseract-lang")
        print("   Ubuntu : sudo apt install tesseract-ocr tesseract-ocr-chi-sim")
    return False, None


if __name__ == "__main__":
    ok, cmd = ensure_tesseract(verbose=True)
    print(f"\nensure_tesseract() → ok={ok}, cmd={cmd!r}")
