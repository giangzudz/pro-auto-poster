#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PRO AUTO POSTER - Phần mềm tự động đăng bài & bình luận Facebook.
Điểm khởi chạy ứng dụng.
"""
import tkinter as tk

from ui.main_window import MainWindow


def main() -> None:
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
