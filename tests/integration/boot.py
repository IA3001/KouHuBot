"""用真实 AstrBot 启动，但先注册测试平台适配器。

直接 runpy 跑 .local/astrbot/main.py，等价于用户执行 python main.py，
区别只是多 import 了一个 fake_platform，把 kouhu_test 注册进平台表。
"""

import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ASTRBOT = REPO / ".local" / "astrbot"

if not (ASTRBOT / "main.py").is_file():
    sys.exit(f"找不到 AstrBot 源码：{ASTRBOT}（先跑 ./run.sh setup）")

sys.path.insert(0, str(ASTRBOT))
sys.path.insert(0, str(HERE))
os.chdir(ASTRBOT)

import fake_platform  # noqa: F401,E402  注册 kouhu_test 适配器

runpy.run_path(str(ASTRBOT / "main.py"), run_name="__main__")
