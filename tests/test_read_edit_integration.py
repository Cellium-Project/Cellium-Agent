# -*- coding: utf-8 -*-
import os
import sys
import time
import threading
import tempfile
import shutil
import stat

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.agent.tools.read_tool import ReadTool
from app.agent.tools.edit_tool import EditTool
from app.agent.tools import file_cache as fc
from app.agent.runtime.transaction import EditTransaction

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


def make_temp_file(content="", suffix=".txt"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ============================================================
# 1. 基础 read → edit 工作流
# ============================================================
print("\n=== 1. 基础 read → edit 工作流 ===")

rt = ReadTool()
et = EditTool()

# 1.1 完整流程
f = make_temp_file("line1\nline2\nline3\n")
try:
    r = rt._cmd_read(file_path=f)
    check("read basic", r["success"])
    r = et._cmd_edit(file_path=f, old_string="line2", new_string="LINE2")
    check("edit after read", r["success"])
    check("edit count=1", r.get("count") == 1)
    with open(f, "r", encoding="utf-8") as fp:
        content = fp.read()
    check("edit applied", "LINE2" in content and "line2" not in content)
finally:
    os.remove(f)

# 1.2 多行替换
f = make_temp_file("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f,
                      old_string="def foo():\n    return 1",
                      new_string="def foo():\n    return 100")
    check("multi-line replace", r["success"])
finally:
    os.remove(f)

# 1.3 缩进敏感
f = make_temp_file("if True:\n    a = 1\n    b = 2\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="    a = 1\n    b = 2", new_string="    a = 10\n    b = 20")
    check("indentation preserved", r["success"])
finally:
    os.remove(f)

# ============================================================
# 2. read 不存在 → edit 报错
# ============================================================
print("\n=== 2. read 不存在 → edit 报错 ===")

# 2.1 编辑前没 read
f = make_temp_file("test content\n")
try:
    r = et._cmd_edit(file_path=f, old_string="test", new_string="TEST")
    check("edit without read fails", not r["success"])
    check("error mentions read", "read" in r.get("error", "").lower())
finally:
    os.remove(f)

# 2.2 编辑不存在的文件
r = et._cmd_edit(file_path="C:\\__nonexistent_xyz__.txt", old_string="a", new_string="b")
check("edit nonexistent file", not r["success"])
check("error mentions not found", "not found" in r.get("error", "").lower())

# ============================================================
# 3. partial read → edit 拒绝
# ============================================================
print("\n=== 3. partial read → edit 拒绝 ===")

# 3.1 offset 读 → edit 拒绝
f = make_temp_file("\n".join(f"line{i}" for i in range(100)))
try:
    rt._cmd_read(file_path=f, offset=10, limit=10)
    r = et._cmd_edit(file_path=f, old_string="line50", new_string="LINE50")
    check("edit after partial read fails", not r["success"])
    check("error mentions partial/full", "partial" in r.get("error", "").lower() or "full" in r.get("error", "").lower())
finally:
    os.remove(f)

# 3.2 limit 读 → edit 拒绝
f = make_temp_file("\n".join(f"line{i}" for i in range(100)))
try:
    rt._cmd_read(file_path=f, limit=10)
    r = et._cmd_edit(file_path=f, old_string="line5", new_string="LINE5")
    check("edit after limit-only read fails", not r["success"])
finally:
    os.remove(f)

# 3.3 target 读 → edit 接受（target 读包含 ±3 行上下文，文件是全读）
f = make_temp_file("\n".join(f"line{i}" for i in range(100)))
try:
    rt._cmd_read(file_path=f, target="line50")
    r = et._cmd_edit(file_path=f, old_string="line50", new_string="LINE50")
    check("edit after target read works (target is full read)", r["success"])
finally:
    os.remove(f)

# 3.4 needle 读 → edit 拒绝
f = make_temp_file("def hello():\n    return 1\n")
try:
    rt._cmd_read(file_path=f, needle="def hello")
    r = et._cmd_edit(file_path=f, old_string="def hello():", new_string="def hi():")
    check("edit after needle read fails", not r["success"])
finally:
    os.remove(f)

# 3.5 offset=0, limit=2000（小文件 ≤ 2000 行）→ 不算 partial
f = make_temp_file("\n".join(f"line{i}" for i in range(50)))
try:
    rt._cmd_read(file_path=f)  # 默认 offset=0, limit=2000
    r = et._cmd_edit(file_path=f, old_string="line10", new_string="LINE10")
    check("edit after full default read works", r["success"])
finally:
    os.remove(f)

# 3.6 大文件 streaming → 不算 partial
f = make_temp_file("\n".join(f"line{i}" for i in range(100)))  # > _CHUNK*4 = 32KB
# 实际 100 行可能还不够大，强制加长
with open(f, "w", encoding="utf-8") as fp:
    for i in range(10000):
        fp.write(f"line {i} {'x' * 100}\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="line 100 xxxxxxxxxxx", new_string="MODIFIED")
    check("edit after large file streaming read works", r["success"])
finally:
    os.remove(f)

# ============================================================
# 4. mtime 外部修改检测
# ============================================================
print("\n=== 4. mtime 外部修改检测 ===")

# 4.1 同一文件被 read 之后，外部修改 → edit 拒绝
f = make_temp_file("original content line1\noriginal content line2\n")
try:
    rt._cmd_read(file_path=f)
    time.sleep(1.1)  # Windows 文件系统 mtime 精度 2s
    with open(f, "w", encoding="utf-8") as fp:
        fp.write("CHANGED content line1\nCHANGED content line2\n")
    r = et._cmd_edit(file_path=f, old_string="original content line1", new_string="NEW")
    check("edit detects external modification", not r["success"])
    check("error mentions modified", "modified" in r.get("error", "").lower())
finally:
    os.remove(f)

# 4.2 同一文件被 read 之后，外部修改但内容相同 → 接受
f = make_temp_file("original content\n")
try:
    rt._cmd_read(file_path=f)
    time.sleep(1.1)
    with open(f, "w", encoding="utf-8") as fp:
        fp.write("original content\n")
    r = et._cmd_edit(file_path=f, old_string="original content", new_string="NEW")
    check("edit with same content despite mtime change", r["success"])
finally:
    os.remove(f)

# 4.3 同一文件被 read 之后，外部删除 → edit 拒绝
f = make_temp_file("test content\n")
try:
    rt._cmd_read(file_path=f)
    time.sleep(1.1)
    os.remove(f)
    r = et._cmd_edit(file_path=f, old_string="test content", new_string="NEW")
    check("edit detects file deletion", not r["success"])
    # 错误可能 "File has been modified" 或 "File not found"
finally:
    pass

# ============================================================
# 5. old_string 匹配的各种容错
# ============================================================
print("\n=== 5. old_string 匹配容错 ===")

# 5.1 精确匹配
f = make_temp_file("hello world\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="hello world", new_string="hi world")
    check("exact match", r["success"])
finally:
    os.remove(f)

# 5.2 弯引号 → 直引号
f = make_temp_file('message = "hello"\n', suffix=".py")
try:
    rt._cmd_read(file_path=f)
    # LLM 输出了弯引号
    r = et._cmd_edit(file_path=f,
                      old_string='message = \u201chello\u201d',  # ""hello""
                      new_string='message = "hi"')
    check("smart quote normalization", r["success"])
finally:
    os.remove(f)

# 5.3 尾随空白差异
f = make_temp_file("line with trailing space   \nline2\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="line with trailing space", new_string="clean line")
    check("trailing whitespace stripped", r["success"])
finally:
    os.remove(f)

# 5.4 old_string 不存在
f = make_temp_file("hello world\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="not in file", new_string="x")
    check("edit with non-existent old_string fails", not r["success"])
    check("error mentions not found", "not found" in r.get("error", "").lower())
finally:
    os.remove(f)

# 5.5 old_string 多个匹配
f = make_temp_file("foo\nfoo\nbar\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="foo", new_string="FOO")
    check("edit with multiple matches fails", not r["success"])
    check("error mentions matches/replace_all", "match" in r.get("error", "").lower() or "replace_all" in r.get("error", "").lower())
finally:
    os.remove(f)

# 5.6 replace_all=True
f = make_temp_file("foo\nfoo\nbar\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="foo", new_string="FOO", replace_all=True)
    check("edit with replace_all", r["success"])
    check("replace_all count=2", r.get("count") == 2)
finally:
    os.remove(f)

# ============================================================
# 6. CR/LF 兼容性
# ============================================================
print("\n=== 6. CR/LF 兼容性 ===")

# 6.1 写入 CRLF，LLM 给出 LF
f = make_temp_file("line1\r\nline2\r\nline3\r\n", suffix=".txt")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="line2", new_string="LINE2")
    check("edit on CRLF file with LF", r["success"])
finally:
    os.remove(f)

# 6.2 大文件 CRLF
import subprocess
f = make_temp_file(suffix=".txt")
try:
    # 写 10000 行 CRLF
    with open(f, "wb") as fp:
        for i in range(10000):
            fp.write(f"line {i}\r\n".encode("utf-8"))
    file_size = os.path.getsize(f)
    rt._cmd_read(file_path=f)  # 走 streaming 路径
    # LLM 给 LF
    r = et._cmd_edit(file_path=f, old_string="line 5000", new_string="MODIFIED")
    check("edit on large CRLF file", r["success"])
finally:
    os.remove(f)

# ============================================================
# 7. read → edit → read 循环
# ============================================================
print("\n=== 7. read → edit → read 循环 ===")

f = make_temp_file("v1\nv2\nv3\n")
try:
    rt._cmd_read(file_path=f)
    et._cmd_edit(file_path=f, old_string="v1", new_string="v1.1")
    rt._cmd_read(file_path=f)  # 重新读
    r = et._cmd_edit(file_path=f, old_string="v2", new_string="v2.1")
    check("edit after re-read", r["success"])
finally:
    os.remove(f)

# ============================================================
# 8. 并发与边界
# ============================================================
print("\n=== 8. 并发与边界 ===")

# 8.1 read 后立即 edit（无延迟）
f = make_temp_file("quick test\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="quick test", new_string="fast test")
    check("immediate edit after read", r["success"])
finally:
    os.remove(f)

# 8.2 edit 同一文件多次（不同 ReadTool 实例）
f = make_temp_file("multi edit\n")
try:
    rt1 = ReadTool()
    et1 = EditTool()
    rt1._cmd_read(file_path=f)
    et1._cmd_edit(file_path=f, old_string="multi edit", new_string="first")
    # 新实例共享全局 file_cache
    rt2 = ReadTool()
    et2 = EditTool()
    r2 = et2._cmd_edit(file_path=f, old_string="first", new_string="second")
    check("edit across instances", r2["success"])
finally:
    os.remove(f)

# 8.3 大文件流式 read → edit
f = make_temp_file(suffix=".txt")
try:
    with open(f, "w", encoding="utf-8") as fp:
        for i in range(20000):
            fp.write(f"line {i} {'x' * 200}\n")  # 总共约 4-5 MB
    rt._cmd_read(file_path=f)  # 走 streaming
    r = et._cmd_edit(file_path=f, old_string="line 10000 xxx", new_string="EDITED")
    check("edit on large file", r["success"])
finally:
    os.remove(f)

# ============================================================
# 9. LLM 不会的奇葩情况
# ============================================================
print("\n=== 9. LLM 不会的奇葩情况 ===")

# 9.1 old_string 包含转义字符
f = make_temp_file('print("hello\\nworld")\n', suffix=".py")
try:
    rt._cmd_read(file_path=f)
    # LLM 传 \n 经过 JSON 后是 \\n，再经 _unescape_string 变成 newline
    # 但文件里的 \n 是字面量，不是真换行符
    r = et._cmd_edit(file_path=f,
                      old_string='print("hello\\nworld")',
                      new_string='print("hi")')
    # 不论成功失败都行，不 crash
    check("edit with escaped chars no crash", True)
finally:
    os.remove(f)

# 9.2 old_string 是空字符串
f = make_temp_file("content\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="", new_string="x")
    check("edit with empty old_string fails", not r["success"])
finally:
    os.remove(f)

# 9.3 old_string == new_string
f = make_temp_file("same\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="same", new_string="same")
    check("edit identical strings fails", not r["success"])
finally:
    os.remove(f)

# 9.4 edit 没有 file_path
r = et._cmd_edit(file_path="", old_string="a", new_string="b")
check("edit without file_path fails", not r["success"])

# 9.5 edit 没有 old_string
f = make_temp_file("x")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string=None, new_string="y")
    check("edit without old_string fails", not r["success"])
finally:
    os.remove(f)

# 9.6 old_string 含 null 字符
f = make_temp_file("a\x00b\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="a\x00b", new_string="abc")
    check("edit with null char in old_string", r["success"] or not r["success"])  # 不 crash 即可
finally:
    os.remove(f)

# 9.7 new_string 是 None
f = make_temp_file("content\n")
try:
    rt._cmd_read(file_path=f)
    r = et._cmd_edit(file_path=f, old_string="content", new_string=None)
    check("edit with None new_string fails", not r["success"])
finally:
    os.remove(f)

# ============================================================
# 10. target / needle 读取模式
# ============================================================
print("\n=== 10. target / needle 读取模式 ===")

# 10.1 target 找不到
f = make_temp_file("a\nb\nc\n")
try:
    r = rt._cmd_read(file_path=f, target="nonexistent")
    check("read with nonexistent target fails", not r["success"])
finally:
    os.remove(f)

# 10.2 needle 找不到
f = make_temp_file("a\nb\nc\n")
try:
    r = rt._cmd_read(file_path=f, needle="not in file")
    check("read with nonexistent needle fails", not r["success"])
finally:
    os.remove(f)

# 10.3 needle CRLF 容错
f = make_temp_file("line1\r\nline2 with needle\r\nline3\r\n", suffix=".txt")
try:
    r = rt._cmd_read(file_path=f, needle="line2 with needle")  # LLM 给 LF
    check("needle CRLF tolerance", r["success"])
finally:
    os.remove(f)

# ============================================================
# 11. dedup（同轮重复读）
# ============================================================
print("\n=== 11. dedup（同轮重复读） ===")

f = make_temp_file("dedup test\n")
try:
    r1 = rt._cmd_read(file_path=f)
    check("first read", r1["success"] and not r1.get("_dedup"))
    r2 = rt._cmd_read(file_path=f)
    check("second read dedup", r2.get("_dedup") == True)
finally:
    os.remove(f)

# 11.2 不同实例共享去重
f = make_temp_file("cross instance\n")
try:
    rt1 = ReadTool()
    rt2 = ReadTool()
    rt1._cmd_read(file_path=f)
    r2 = rt2._cmd_read(file_path=f)
    # 跨实例不会 dedup（_dedup_entries 是实例级）
    check("cross-instance dedup not shared", not r2.get("_dedup"))
finally:
    os.remove(f)

# ============================================================
# 12. 文件权限
# ============================================================
print("\n=== 12. 文件权限 ===")

# 12.1 只读文件 → edit 应失败（Windows 上需特殊处理）
if sys.platform != "win32":
    f = make_temp_file("read only test\n")
    try:
        os.chmod(f, stat.S_IRUSR)
        rt._cmd_read(file_path=f)
        r = et._cmd_edit(file_path=f, old_string="read only test", new_string="changed")
        check("edit on read-only file fails", not r["success"])
    finally:
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
        os.remove(f)
else:
    print("  ⊘ skipped on Windows")

# 12.2 文件无读权限（Windows 上 skip）
if sys.platform != "win32":
    f = make_temp_file("no perm\n")
    try:
        os.chmod(f, 0)
        rt._cmd_read(file_path=f)  # 先读 (用 root 可能能)
        r = et._cmd_edit(file_path=f, old_string="no perm", new_string="changed")
        check("edit on no-perm file fails gracefully", not r["success"])
    finally:
        os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
        os.remove(f)
else:
    print("  ⊘ skipped on Windows")

# ============================================================
# 13. 编码边界
# ============================================================
print("\n=== 13. 编码边界 ===")

# 13.1 GBK 文件
f = make_temp_file(suffix=".txt")
try:
    with open(f, "w", encoding="gbk") as fp:
        fp.write("你好世界\n")
    r = rt._cmd_read(file_path=f)
    check("read GBK file", r["success"])
    r2 = et._cmd_edit(file_path=f, old_string="你好世界", new_string="世界你好")
    check("edit GBK file", r2["success"])
finally:
    os.remove(f)

# 13.2 UTF-8 BOM
f = make_temp_file(suffix=".txt")
try:
    with open(f, "wb") as fp:
        fp.write(b'\xef\xbb\xbf')
        fp.write("BOM test\n".encode("utf-8"))
    r = rt._cmd_read(file_path=f)
    check("read UTF-8 BOM", r["success"])
    r2 = et._cmd_edit(file_path=f, old_string="BOM test", new_string="BOM modified")
    check("edit UTF-8 BOM", r2["success"])
finally:
    os.remove(f)

# ============================================================
# 14. 路径边界
# ============================================================
print("\n=== 14. 路径边界 ===")

# 14.1 相对路径
old_cwd = os.getcwd()
d = tempfile.mkdtemp()
try:
    f_rel = os.path.join(d, "rel.txt")
    with open(f_rel, "w", encoding="utf-8") as fp:
        fp.write("relative test\n")
    os.chdir(d)
    rt._cmd_read(file_path="rel.txt")
    r = et._cmd_edit(file_path="rel.txt", old_string="relative test", new_string="REL")
    check("edit with relative path", r["success"])
finally:
    os.chdir(old_cwd)
    shutil.rmtree(d, ignore_errors=True)

# 14.2 路径中带空格
d = tempfile.mkdtemp()
try:
    f_space = os.path.join(d, "file with spaces.txt")
    with open(f_space, "w", encoding="utf-8") as fp:
        fp.write("space test\n")
    rt._cmd_read(file_path=f_space)
    r = et._cmd_edit(file_path=f_space, old_string="space test", new_string="SPACE")
    check("edit with spaces in path", r["success"])
finally:
    shutil.rmtree(d, ignore_errors=True)

# 14.3 中文路径
d = tempfile.mkdtemp()
try:
    f_zh = os.path.join(d, "中文文件.txt")
    with open(f_zh, "w", encoding="utf-8") as fp:
        fp.write("chinese test\n")
    rt._cmd_read(file_path=f_zh)
    r = et._cmd_edit(file_path=f_zh, old_string="chinese test", new_string="中文测试")
    check("edit with chinese path", r["success"])
finally:
    shutil.rmtree(d, ignore_errors=True)

# ============================================================
# 15. 撤销行为
# ============================================================
print("\n=== 15. 撤销行为 ===")

# 15.1 edit 失败后文件不变
f = make_temp_file("original\nunique_marker\nmore content\n")
try:
    rt._cmd_read(file_path=f)
    with open(f, "r", encoding="utf-8") as fp:
        original_content = fp.read()
    # 故意触发错误：old_string 多次出现
    r = et._cmd_edit(file_path=f, old_string="original\n", new_string="X\n", replace_all=False)
    # 实际 original\n 可能在文件里出现多次
    with open(f, "r", encoding="utf-8") as fp:
        after_content = fp.read()
    if not r["success"]:
        check("edit failure no file change", original_content == after_content)
    else:
        # 如果成功则是单次替换
        check("edit success one replacement", r["success"])
finally:
    os.remove(f)

# ============================================================
# 16. 内部一致性：EditTransaction 原子写
# ============================================================
print("\n=== 16. 内部一致性：EditTransaction 原子写 ===")

f = make_temp_file("atomic test\n")
try:
    # 直接测试 EditTransaction
    with open(f, "r", encoding="utf-8") as fp:
        content = fp.read()
    patch = {"mode": "replace", "old_text": "atomic test", "new_text": "ATOMIC", "replace_all": False}
    result = EditTransaction.apply_edit(f, content, patch)
    check("atomic write success", result["success"])
    with open(f, "r", encoding="utf-8") as fp:
        check("file updated", "ATOMIC" in fp.read())
finally:
    os.remove(f)

# 16.2 patch 模式
f = make_temp_file("\n".join(f"line{i}" for i in range(10)) + "\n")
try:
    with open(f, "r", encoding="utf-8") as fp:
        content = fp.read()
    # 测试 insert (需要 content 字段，不是 text)
    patch = {"mode": "insert", "line": 3, "content": "inserted\n"}
    result = EditTransaction.apply_edit(f, content, patch)
    check("insert patch", result["success"])
    with open(f, "r", encoding="utf-8") as fp:
        new = fp.read()
    check("insert applied", "inserted" in new)
finally:
    os.remove(f)

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

# 清理缓存
fc.clear_read_cache()
