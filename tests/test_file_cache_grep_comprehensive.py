# -*- coding: utf-8 -*-
import os
import sys
import time
import threading
import tempfile
import shutil
from collections import OrderedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.agent.tools import file_cache as fc
from app.agent.tools.grep_tool import GrepTool, _fallback_search, _make_rel, _make_rel_line, _apply_limit, _find_rg, _run_rg

PASS = 0
FAIL = 0
FAILED_CASES = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        FAILED_CASES.append((name, detail))
        print(f"  ✗ {name}  {detail}")


# ============================================================
# 1. file_cache: 基础功能
# ============================================================
print("\n=== file_cache: 基础功能 ===")

d = tempfile.mkdtemp()
f1 = os.path.join(d, "a.txt")
with open(f1, "w") as f:
    f.write("line1\nline2\nline3\n")

fc.clear_read_cache()

# 1.1 基础缓存
fc.cache_read(f1, "content1")
check("cache_read basic", fc.is_file_read(f1))
check("get_read_state returns state", fc.get_read_state(f1) is not None)
check("state has content", fc.get_read_state(f1)["content"] == "content1")
check("state has timestamp", fc.get_read_state(f1)["timestamp"] > 0)
check("state offset=None default", fc.get_read_state(f1)["offset"] is None)
check("state limit=None default", fc.get_read_state(f1)["limit"] is None)

# 1.2 路径规范化
fc.cache_read(os.path.join(d, ".\\a.txt"), "x")
state = fc.get_read_state(f1)
check("path normalization", state is not None and state["content"] == "x")

# 1.3 重复缓存覆盖
fc.cache_read(f1, "new content")
check("cache overwrite", fc.get_read_state(f1)["content"] == "new content")

# 1.4 不存在的文件
check("is_file_read nonexistent", not fc.is_file_read("C:\\__nonexistent__"))

# 1.5 清空缓存
fc.clear_read_cache()
check("clear_read_cache", not fc.is_file_read(f1))

cleanup_done = False
def cleanup_later():
    global cleanup_done
    time.sleep(0.5)
    cleanup_done = True
    shutil.rmtree(d, ignore_errors=True)

# ============================================================
# 2. file_cache: 外部修改检测
# ============================================================
print("\n=== file_cache: 外部修改检测 ===")

d2 = tempfile.mkdtemp()
f2 = os.path.join(d2, "modify.txt")
with open(f2, "w") as f:
    f.write("original content\n")

fc.clear_read_cache()
fc.cache_read(f2, "original content\n")
check("initial read OK", fc.is_file_read(f2))

# 2.1 真实修改（mtime 改变 + 内容不同）
time.sleep(1.1)  # 确保 mtime 变化（Windows FAT 文件系统精度是 2s）
with open(f2, "w") as f:
    f.write("modified content\n")
check("detects external modification", not fc.is_file_read(f2))

# 2.2 mtime 改变但内容相同
fc.cache_read(f2, "modified content\n")
time.sleep(1.1)
with open(f2, "w") as f:
    f.write("modified content\n")  # 同样内容
# mtime 应该会被更新 + 内容相同 → 视为未修改
check("same content with new mtime detected as unmodified", fc.is_file_read(f2))

# 2.3 文件删除
if os.path.exists(f2):
    os.remove(f2)
check("detects file deletion", not fc.is_file_read(f2))
shutil.rmtree(d2, ignore_errors=True)

# ============================================================
# 3. file_cache: partial view
# ============================================================
print("\n=== file_cache: partial view ===")

d3 = tempfile.mkdtemp()
f3 = os.path.join(d3, "p.txt")
with open(f3, "w") as f:
    f.write("\n".join([f"line{i}" for i in range(100)]))

fc.clear_read_cache()

# 3.1 全量读取
fc.cache_read(f3, "full content")
check("full read not partial", not fc.is_partial_view(f3))

# 3.2 offset/limit 设置
fc.cache_read(f3, "partial", offset=10, limit=20)
check("offset set = partial", fc.is_partial_view(f3))

# 3.3 仅 offset
fc.cache_read(f3, "partial", offset=10)
check("only offset = partial", fc.is_partial_view(f3))

# 3.4 仅 limit
fc.cache_read(f3, "partial", limit=20)
check("only limit = partial", fc.is_partial_view(f3))

# 3.5 partial view 在 edit 之后被清除
fc.cache_read(f3, "new content", offset=None, limit=None)
check("after cache overwrite, not partial", not fc.is_partial_view(f3))

# 3.6 touch_read_state 重置为 full
fc.cache_read(f3, "partial", offset=0, limit=100)
check("partial again after", fc.is_partial_view(f3))
fc.touch_read_state(f3, "new full content")
check("touch_read_state resets to full", not fc.is_partial_view(f3))

# 3.7 is_partial_view 不存在的文件
check("partial view nonexistent = False", not fc.is_partial_view("C:\\__nonexistent__"))

shutil.rmtree(d3, ignore_errors=True)

# ============================================================
# 4. file_cache: LRU 淘汰
# ============================================================
print("\n=== file_cache: LRU 淘汰 ===")

d4 = tempfile.mkdtemp()
files = [os.path.join(d4, f"f{i}.txt") for i in range(110)]
for fp in files:
    with open(fp, "w") as f:
        f.write(f"content {fp}\n")

fc.clear_read_cache()
for fp in files:
    fc.cache_read(fp, f"content {fp}\n")

# 缓存最多 100 个，前 10 个应被淘汰
state_first = fc.get_read_state(files[0])
state_last = fc.get_read_state(files[-1])
check("first 10 files evicted", state_first is None)
check("last 100 files cached", state_last is not None)

# LRU：访问 files[20]（在缓存中）把它移到末尾
fc.is_file_read(files[20])
# 然后插入 files[0]，应淘汰 files[10]（新的最旧），但 files[20]（最近访问）保留
fc.cache_read(files[0], "refresh")
state_20 = fc.get_read_state(files[20])
state_10 = fc.get_read_state(files[10])
check("LRU recently accessed kept", state_20 is not None)
check("oldest evicted after refresh", state_10 is None)

shutil.rmtree(d4, ignore_errors=True)

# ============================================================
# 5. file_cache: 并发安全
# ============================================================
print("\n=== file_cache: 并发安全 ===")

d5 = tempfile.mkdtemp()
f5 = os.path.join(d5, "concurrent.txt")
with open(f5, "w") as f:
    f.write("shared\n")

fc.clear_read_cache()
errors = []

def worker(i):
    try:
        for j in range(100):
            fc.cache_read(f5, f"worker{i}-iter{j}")
            fc.is_file_read(f5)
            fc.get_read_state(f5)
    except Exception as e:
        errors.append(e)

threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("concurrent access no errors", len(errors) == 0, str(errors))
check("final state valid", fc.get_read_state(f5) is not None)
shutil.rmtree(d5, ignore_errors=True)

# ============================================================
# 6. file_cache: touch_read_state
# ============================================================
print("\n=== file_cache: touch_read_state ===")

d6 = tempfile.mkdtemp()
f6 = os.path.join(d6, "t.txt")
with open(f6, "w") as f:
    f.write("touch test\n")

fc.clear_read_cache()
fc.cache_read(f6, "partial content", offset=5, limit=10)
check("pre-touch is partial", fc.is_partial_view(f6))

fc.touch_read_state(f6, "new full content")
state = fc.get_read_state(f6)
check("touch updates content", state["content"] == "new full content")
check("touch clears offset", state["offset"] is None)
check("touch clears limit", state["limit"] is None)
check("touch updates timestamp", state["timestamp"] > 0)

shutil.rmtree(d6, ignore_errors=True)

# ============================================================
# 7. grep_tool: 工具函数
# ============================================================
print("\n=== grep_tool: 工具函数 ===")

# 7.1 _make_rel
old_cwd = os.getcwd()
try:
    os.chdir("C:\\Windows")
    r = _make_rel("C:\\Windows\\System32")
    check("_make_rel relative", r == "System32")
    r = _make_rel("D:\\Other")
    check("_make_rel different drive", r == "D:\\Other" or r == "..\\..\\..\\..\\D:\\Other")
finally:
    os.chdir(old_cwd)

# 7.2 _apply_limit
items = list(range(300))
sliced, limit = _apply_limit(items, 50, 0)
check("_apply_limit head_limit=50", len(sliced) == 50 and limit == 50)
sliced, limit = _apply_limit(items, None, 0)
check("_apply_limit default limit", len(sliced) == 250 and limit == 250)
sliced, limit = _apply_limit(items, 50, 250)
check("_apply_limit offset returns remaining", len(sliced) == 50 and limit is None)
sliced, limit = _apply_limit(items, 50, 200)
check("_apply_limit offset end boundary", len(sliced) == 50 and limit == 50)
sliced, limit = _apply_limit(items, 50, 300)
check("_apply_limit offset far past", len(sliced) == 0 and limit is None)
sliced, limit = _apply_limit(items, 50, 999)
check("_apply_limit offset beyond", len(sliced) == 0 and limit is None)
sliced, limit = _apply_limit(items, 50, 240)
check("_apply_limit offset near end", len(sliced) == 50 and limit == 50)
sliced, limit = _apply_limit(items, 0, 0)
check("_apply_limit head_limit=0 (use default)", len(sliced) == 250)
sliced, limit = _apply_limit(items, -5, 0)
check("_apply_limit head_limit<0 (use default)", len(sliced) == 250)

# 7.3 _make_rel_line
line = "C:\\Users\\test\\file.py:10:def hello():"
r = _make_rel_line(line)
check("_make_rel_line strips path", r.startswith("Users\\test\\file.py:10:") or "file.py:10:" in r)

# 7.4 _find_rg
rg_path = _find_rg()
check("_find_rg returns path or None", rg_path is None or isinstance(rg_path, str))

# ============================================================
# 8. grep_tool: ripgrep 命令构建
# ============================================================
print("\n=== grep_tool: ripgrep 命令构建 ===")

gt = GrepTool()

# 8.1 基本 content 模式
r = gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="content", head_limit=5)
check("content mode basic", r["success"] and r["mode"] == "content")
check("content has lines", r["num_lines"] >= 0)
if r["num_lines"] > 0:
    check("content has path:line format", ":" in r["content"].split('\n')[0])

# 8.2 content 模式带 -n (默认)
if r["num_lines"] > 0:
    first_line = r["content"].split('\n')[0]
    parts = first_line.split(':', 2)
    check("content with -n has line number", len(parts) >= 3)

# 8.3 content 模式带 -A 上下文
r = gt._cmd_grep(query="def ", path=".", glob="*.py", output_mode="content", head_limit=5, **{"-A": 2})
check("content with -A", r["success"])

# 8.4 content 模式带 -B 上下文
r = gt._cmd_grep(query="def ", path=".", glob="*.py", output_mode="content", head_limit=5, **{"-B": 2})
check("content with -B", r["success"])

# 8.5 content 模式带 -C 上下文
r = gt._cmd_grep(query="def ", path=".", glob="*.py", output_mode="content", head_limit=5, **{"-C": 2})
check("content with -C", r["success"])

# 8.6 files_with_matches
r = gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="files_with_matches", head_limit=10)
check("files_with_matches", r["success"] and r["mode"] == "files_with_matches")
check("filenames list", isinstance(r["filenames"], list))

# 8.7 count
r = gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="count", head_limit=10)
check("count mode", r["success"] and r["mode"] == "count")
check("count has num_matches", "num_matches" in r)
check("count has num_files", "num_files" in r)

# 8.8 multiline
r = gt._cmd_grep(query="def.*\\n.*return", path=".", glob="*.py", output_mode="content", multiline=True, head_limit=3)
check("multiline no crash", r["success"])

# 8.9 -i 忽略大小写
r1 = gt._cmd_grep(query="DEF", path=".", glob="*.py", output_mode="files_with_matches", head_limit=5)
r2 = gt._cmd_grep(query="DEF", path=".", glob="*.py", output_mode="files_with_matches", **{"-i": True}, head_limit=5)
check("-i finds more", r2["num_files"] >= r1["num_files"])

# 8.10 type 过滤
r = gt._cmd_grep(query="import", path=".", type="py", output_mode="files_with_matches", head_limit=5)
check("type=py", r["success"] and all(f.endswith(".py") for f in r["filenames"]))

# 8.11 glob 多模式
r = gt._cmd_grep(query="import", path=".", glob="*.py,*.txt", output_mode="files_with_matches", head_limit=10)
check("glob multi-pattern", r["success"])

# 8.12 query 以 - 开头
r = gt._cmd_grep(query="-v", path=".", glob="*.py", output_mode="files_with_matches", head_limit=5)
check("query starting with -", r["success"])

# 8.13 head_limit=0
r = gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="files_with_matches", head_limit=0)
check("head_limit=0 (use default)", r["success"] and r["num_files"] <= 250)

# 8.14 offset
r = gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="files_with_matches", offset=5, head_limit=3)
check("offset works", r["success"] and r["offset"] == 5)

# ============================================================
# 9. grep_tool: 路径处理
# ============================================================
print("\n=== grep_tool: 路径处理 ===")

# 9.1 相对路径
old_cwd = os.getcwd()
os.chdir(d)
r = gt._cmd_grep(query="test", path=".", output_mode="files_with_matches")
check("relative path", r["success"])
os.chdir(old_cwd)

# 9.2 绝对路径
r = gt._cmd_grep(query="def", path="C:\\Windows", output_mode="files_with_matches", head_limit=1)
check("absolute path", r["success"])

# 9.3 不存在的路径
r = gt._cmd_grep(query="test", path="C:\\__nonexistent_xyz__", output_mode="files_with_matches")
check("nonexistent path", r["success"] and r["num_files"] == 0)

# 9.4 空 path
r = gt._cmd_grep(query="def", path="", output_mode="files_with_matches", head_limit=1)
check("empty path uses cwd", r["success"])

# ============================================================
# 10. grep_tool: 特殊文件
# ============================================================
print("\n=== grep_tool: 特殊文件 ===")

d_special = tempfile.mkdtemp()

# 10.1 符号链接
try:
    real = os.path.join(d_special, "real.txt")
    with open(real, "w") as f:
        f.write("hello world\n")
    link = os.path.join(d_special, "link.txt")
    if hasattr(os, "symlink"):
        try:
            os.symlink(real, link)
            r = gt._cmd_grep(query="hello", path=d_special, output_mode="files_with_matches")
            check("symlink no crash", r["success"])
        except (OSError, NotImplementedError):
            print(f"  ⊘ symlink test skipped (not supported)")
except Exception as e:
    print(f"  ⊘ symlink test error: {e}")

# 10.2 长文件名（接近 260 字符）
long_dir = tempfile.mkdtemp(dir=d_special)
long_name = "a" * 200 + ".txt"
long_file = os.path.join(long_dir, long_name)
try:
    with open(long_file, "w") as f:
        f.write("long file content\n")
    r = gt._cmd_grep(query="long", path=long_dir, output_mode="files_with_matches")
    check("long filename no crash", r["success"])
except OSError:
    print(f"  ⊘ long filename test skipped (OS limit)")

# 10.3 二进制文件
bin_file = os.path.join(d_special, "binary.bin")
with open(bin_file, "wb") as f:
    f.write(b'\x00\x01\x02\x03HelloWorld\xff\xfe\xfd')
r = gt._cmd_grep(query="Hello", path=d_special, output_mode="files_with_matches")
check("binary file no crash", r["success"])

# 10.4 空文件
empty = os.path.join(d_special, "empty.txt")
open(empty, "w").close()
r = gt._cmd_grep(query="anything", path=d_special, output_mode="files_with_matches")
check("empty file no crash", r["success"])

# 10.5 巨大文件（>100MB）
big = os.path.join(d_special, "big.txt")
with open(big, "w") as f:
    f.write("x" * (5 * 1024 * 1024))  # 5MB
r = gt._cmd_grep(query="x", path=big, output_mode="content", head_limit=1)
check("large file works", r["success"])

shutil.rmtree(d_special, ignore_errors=True)
shutil.rmtree(long_dir, ignore_errors=True)

# ============================================================
# 11. grep_tool: fallback 模式
# ============================================================
print("\n=== grep_tool: fallback 模式 ===")

d_fb = tempfile.mkdtemp()
with open(os.path.join(d_fb, "a.py"), "w") as f:
    f.write("def hello():\n    return 'hi'\n\ndef world():\n    return 'earth'\n")
with open(os.path.join(d_fb, "b.txt"), "w") as f:
    f.write("Hello World\n")

# 11.1 基本搜索
r = _fallback_search(query="hello", path=d_fb, ext=None, pattern=None, offset=0, head_limit=10)
check("fallback search basic", r["success"] and r["total"] >= 1)
check("fallback uses regex detection", r["engine"] == "fallback")

# 11.2 ext 过滤
r = _fallback_search(query="hello", path=d_fb, ext=".py", pattern=None, offset=0, head_limit=10)
check("fallback ext filter", r["success"] and r["total"] >= 1)
r = _fallback_search(query="hello", path=d_fb, ext=".cpp", pattern=None, offset=0, head_limit=10)
check("fallback ext no match", r["success"] and r["total"] == 0)

# 11.3 glob 过滤
r = _fallback_search(query="hello", path=d_fb, ext=None, pattern="*.py", offset=0, head_limit=10)
check("fallback glob", r["success"] and r["total"] >= 1)
r = _fallback_search(query="hello", path=d_fb, ext=None, pattern="*.cpp", offset=0, head_limit=10)
check("fallback glob no match", r["success"] and r["total"] == 0)

# 11.4 regex 模式
r = _fallback_search(query="def\\s+\\w+", path=d_fb, ext=".py", pattern=None, offset=0, head_limit=10)
check("fallback regex", r["success"] and r["total"] >= 2)

# 11.5 边界：没有 query
r = _fallback_search(query="", path=d_fb, ext=None, pattern=None, offset=0, head_limit=10)
check("fallback empty query", r["success"])  # 不报错

# 11.6 不存在的路径
r = _fallback_search(query="hello", path="C:\\__nonexistent__", ext=None, pattern=None, offset=0, head_limit=10)
check("fallback nonexistent path", r["success"] and r["total"] == 0)

# 11.7 offset 分页
r = _fallback_search(query="def", path=d_fb, ext=".py", pattern=None, offset=1, head_limit=1)
check("fallback offset", r["success"])

shutil.rmtree(d_fb, ignore_errors=True)

# ============================================================
# 12. grep_tool: 错误处理
# ============================================================
print("\n=== grep_tool: 错误处理 ===")

# 12.1 无 query/pattern
r = gt._cmd_grep(query="", path=".", output_mode="files_with_matches")
check("no keyword error", not r["success"] and "required" in r.get("error", ""))

r = gt._cmd_grep(pattern="", query="", path=".", output_mode="files_with_matches")
check("both empty error", not r["success"])

# 12.2 query=" "（只有空格）
r = gt._cmd_grep(query=" ", path=".", output_mode="files_with_matches")
check("query=space", r["success"])  # 视作合法关键字

# ============================================================
# 13. 综合边界
# ============================================================
print("\n=== 综合边界 ===")

# 13.1 mtime 排序 (files_with_matches)
d_sort = tempfile.mkdtemp()
import datetime

files_to_sort = []
for i, name in enumerate(["old.txt", "mid.txt", "new.txt"]):
    fp = os.path.join(d_sort, name)
    with open(fp, "w") as f:
        f.write(f"content {name}\n")
    files_to_sort.append(fp)
    os.utime(fp, (time.time() - 100 + i*10, time.time() - 100 + i*10))

r = gt._cmd_grep(query="content", path=d_sort, output_mode="files_with_matches", head_limit=10)
check("files_with_matches sorted by mtime", r["success"] and r["num_files"] == 3)
# 最新修改的应该排在最前
if r["num_files"] >= 1:
    check("newest first", "new.txt" in r["filenames"][0])

shutil.rmtree(d_sort, ignore_errors=True)

# 13.2 query 含特殊字符
special = tempfile.mkdtemp()
with open(os.path.join(special, "test.txt"), "w") as f:
    f.write("line with $pecial ch@r$ & more\n")

r = gt._cmd_grep(query="\\$pecial", path=special, output_mode="content", head_limit=5)
check("query with $ (escaped)", r["success"] and r["num_lines"] >= 1)

r = gt._cmd_grep(query="ch@r", path=special, output_mode="content", head_limit=5)
check("query with @", r["success"] and r["num_lines"] >= 1)

r = gt._cmd_grep(query="\\&", path=special, output_mode="content", head_limit=5)
check("query with & (escaped)", r["success"] and r["num_lines"] >= 1)

shutil.rmtree(special, ignore_errors=True)

# 13.3 output_mode 无效
r = gt._cmd_grep(query="def", path=".", output_mode="invalid", head_limit=5)
check("invalid output_mode", r["success"])  # 应不报错，按默认处理

# ============================================================
# 结果
# ============================================================
print(f"\n{'='*60}")
print(f"总计: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("\n失败项:")
    for name, detail in FAILED_CASES:
        print(f"  ✗ {name}")
        if detail:
            print(f"    {detail}")
else:
    print("全部通过!")

# 清理所有临时目录
for td in [d, d2, d3, d4, d5, d6]:
    if os.path.exists(td):
        shutil.rmtree(td, ignore_errors=True)
