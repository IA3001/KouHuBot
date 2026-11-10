"""题目源集合。

目前只有一个源：Codeforces。

为什么换掉了原来的 LeetCode：
  1. LeetCode 没有官方难度分值，做不了「按难度给分」的积分制；
  2. CF 每道题都有官方 rating（800~3500），分档天然可用；
  3. CF 题库是一份静态 JSON（problemset.problems），一次拿全，不用一题一请求。

`codeforces.py` 负责：题目总表、按难度抽题、抓题面 HTML、翻成中文、落缓存。
题面缓存是独立的 `cf_cache.db`，和积分库分开存，升级表结构不会把题一起清掉。
"""

from .codeforces import CfCache, CodeforcesSource  # noqa: F401

__all__ = ["CfCache", "CodeforcesSource"]
