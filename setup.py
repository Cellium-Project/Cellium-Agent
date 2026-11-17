"""构建期预处理：保证 vendor 可执行文件带执行位，并写入 wheel 条目。"""
import glob
import os
import stat
import zipfile

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
from setuptools.command.sdist import sdist as _sdist

ROOT = os.path.dirname(os.path.abspath(__file__))
EXEC_GLOBS = (
    "vendor/ripgrep/**/rg",
    "vendor/ripgrep/**/rg.exe",
)
EXEC_NAMES = {"rg", "rg.exe"}


def chmod_vendor_executables():
    fixed = []
    for pattern in EXEC_GLOBS:
        for path in glob.glob(os.path.join(ROOT, pattern), recursive=True):
            if not os.path.isfile(path):
                continue
            try:
                mode = stat.S_IMODE(os.stat(path).st_mode)
                if mode & 0o111 == 0o111:
                    continue
                os.chmod(path, mode | 0o755)
                fixed.append(path)
            except OSError:
                continue
    if fixed:
        print("[build] chmod +x: %d file(s)" % len(fixed))
    return fixed


def patch_wheel_exec_bits(wheel_path):
    tmp_path = wheel_path + ".tmp"
    patched = 0
    with zipfile.ZipFile(wheel_path, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            info = zipfile.ZipInfo(item.filename, item.date_time)
            info.external_attr = item.external_attr
            info.compress_type = item.compress_type
            base = item.filename.rsplit("/", 1)[-1]
            if base in EXEC_NAMES:
                info.external_attr = (0o755 & 0xFFFF) << 16
                patched += 1
            dst.writestr(info, data)
    os.replace(tmp_path, wheel_path)
    if patched:
        print("[build] wheel exec bits: %d entr(ies) in %s" % (patched, os.path.basename(wheel_path)))
    return patched


def patch_sdist_exec_bits(sdist_path):
    import tarfile

    tmp_path = sdist_path + ".tmp"
    patched = 0
    with tarfile.open(sdist_path, "r:gz") as src, tarfile.open(tmp_path, "w:gz") as dst:
        for member in src.getmembers():
            base = member.name.rsplit("/", 1)[-1]
            if base in EXEC_NAMES:
                member.mode = 0o755
                patched += 1
            handle = src.extractfile(member) if member.isfile() else None
            dst.addfile(member, handle)
    os.replace(tmp_path, sdist_path)
    if patched:
        print("[build] sdist exec bits: %d entr(ies) in %s" % (patched, os.path.basename(sdist_path)))
    return patched


class Sdist(_sdist):
    def run(self):
        chmod_vendor_executables()
        super().run()
        archives = sorted(glob.glob(os.path.join(self.dist_dir, "*.tar.gz")), key=os.path.getmtime)
        if archives:
            patch_sdist_exec_bits(archives[-1])


class BdistWheel(_bdist_wheel):
    def run(self):
        chmod_vendor_executables()
        super().run()
        wheels = sorted(glob.glob(os.path.join(self.dist_dir, "*.whl")), key=os.path.getmtime)
        if wheels:
            patch_wheel_exec_bits(wheels[-1])


setup(cmdclass={"sdist": Sdist, "bdist_wheel": BdistWheel})
