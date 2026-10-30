#!/usr/bin/env python3
"""把 onnxruntime 变成「自包含 wheel」。两条入口共用同一套组装逻辑：

* ``--wheel-dir``（源码路径，Dockerfile 用）：吃 setup.py ``bdist_wheel`` 的产物，
  补齐 capi 里的库、解软链，再交给 auditwheel 修复后重新打包。
* ``--from-apk``（重打包路径，Dockerfile.repackage 用）：直接吃 Alpine 官方
  ``py3-onnxruntime`` 装进 site-packages 的目录树，组装成 wheel 树后修复打包。

依赖收集（DT_NEEDED 递归闭包）与 RPATH 全部交给 ``auditwheel repair``：
它会把 abseil / re2 / protobuf / icu 这类非系统库改名塞进 ``onnxruntime.libs/``，
把每个 ELF 的 RPATH 设成 ``$ORIGIN:$ORIGIN/../../onnxruntime.libs``，并把标签
从 ``linux_<arch>`` 改成 ``musllinux_1_2_<arch>``。因此本脚本只保留三件它做不到
或不该由它做的事：

1. 解引用软链。alpine 的包是这样装的：``libonnxruntime.so.1.24.4`` 和
   ``libonnxruntime_providers_shared.so`` 放在 ``/usr/lib``，python 目录里的
   ``libonnxruntime.so.1`` / ``libonnxruntime.so.1.24.4`` /
   ``libonnxruntime_providers_shared.so`` 是指过去的符号链接。软链不能进 wheel
   （zip 会按每个链接各存一份真实内容，15MB 的主库直接翻倍），这里解引用成真实
   文件。

2. 按 SONAME 对齐名字。cmake 只把 ``libonnxruntime.so.1.24.4``（真实文件）拷进
   ``onnxruntime/capi/``，而 ``libonnxruntime.so.1`` 这个名字是运行时按名字加载
   的 —— wheel 里已有同名库时 auditwheel 不会重复打包，也不会替你改名，所以
   必须先在 capi 里放成 SONAME 文件名，同时避免 ``.so`` / ``.so.1`` / ``.so.1.24.4``
   三份重复。

3. 把 ``libonnxruntime_providers_shared.so`` 一起放进 capi。它是 ``dlopen`` 的
   （没有 DT_NEEDED，也没有任何 DT_NEEDED 指向它），auditwheel 不会把它「收集」
   进来；但它自己也没有任何非系统依赖，原样放进去即可，不需要再加工。

auditwheel 的 policy 不靠 ``--plat`` 硬编码：它从 ELF 的 interpreter 认出 musl，
自动给出 ``musllinux_1_2_<arch>``，所以本脚本对 amd64 / arm64 / armv7 等一视同仁。

最后 ``auditwheel repair`` 自己会重写 RECORD 与 WHEEL 元数据，不必再 ``wheel pack``。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

# 查找依赖库的位置（musl 没有 ldconfig）
SEARCH_DIRS = ("/usr/lib", "/lib", "/usr/local/lib")


def readelf_dyn(path: Path) -> tuple[list[str], str | None]:
    out = subprocess.run(
        ["readelf", "-d", str(path)], capture_output=True, text=True, check=True
    ).stdout
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", out)
    soname = re.search(r"\(SONAME\)\s+Library soname: \[(.+?)\]", out)
    return needed, (soname.group(1) if soname else None)


def find_library(name: str, extra_dirs: list[Path]) -> Path | None:
    for d in (*extra_dirs, *(Path(x) for x in SEARCH_DIRS)):
        candidate = d / name
        if candidate.exists():
            return candidate
    return None


def shared_objects(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_file() and ".so" in p.name)


def die(message: str) -> int:
    print(f"错误: {message}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# 组装 capi：解软链 + 按 SONAME 对齐（两条入口共用）
# --------------------------------------------------------------------------- #
def materialize_symlinks(capi: Path, extra_dirs: list[Path]) -> None:
    """把 capi 里指向 ``/usr/lib`` 的符号链接换成真实文件（按 SONAME 命名）。"""
    # 分两趟：先解出所有 (链接, 真实文件, SONAME)，再统一删链接、再复制。
    # 不能边删边复制 —— 目标名常常与链接同名（libonnxruntime_providers_shared.so
    # 的 SONAME 就是它自己），dest.exists() 会看到链接本身而跳过复制，接着
    # unlink 就把唯一一份删掉了。
    pending = []
    for link in sorted(capi.iterdir()):
        if not link.is_symlink():
            continue
        src = find_library(link.name, extra_dirs)
        if src is None:
            raise SystemExit(f"错误: {link.name} 是符号链接，但系统里找不到真实文件")
        real = Path(os.path.realpath(src))
        pending.append((link, real, readelf_dyn(real)[1] or real.name))

    for link, _, _ in pending:
        link.unlink()
    for _, real, soname in pending:
        dest = capi / soname
        if dest.exists():
            continue
        print(f"解引用 {real.name} -> {soname}")
        shutil.copyfile(real, dest)
        shutil.copymode(real, dest)


def align_sonames(capi: Path) -> None:
    """按 SONAME 重命名 capi 里的库，避免 .so / .so.1 / .so.1.24.4 三份重复。"""
    for lib in shared_objects(capi):
        _, soname = readelf_dyn(lib)
        if not soname or lib.name == soname:
            continue
        target = capi / soname
        if target.exists():
            lib.unlink()
        else:
            print(f"按 SONAME 重命名 {lib.name} -> {soname}")
            lib.rename(target)


def prepare_capi(capi: Path, extra_dirs: list[Path]) -> None:
    materialize_symlinks(capi, extra_dirs)
    align_sonames(capi)


# --------------------------------------------------------------------------- #
# 打包 + 修复
# --------------------------------------------------------------------------- #
def auditwheel_repair(raw_wheel: Path, out_dir: Path) -> None:
    """让 auditwheel 收集 DT_NEEDED 闭包、改名入 onnxruntime.libs/、改 RPATH、重打标签。

    必须在**装了这些库的构建镜像**里跑：auditwheel 靠遍历 musl 的搜索路径
    （/lib、/usr/lib、LD_LIBRARY_PATH）去定位每个 NEEDED，找不到就直接
    ``ValueError: Cannot repair wheel, because required library "..." could not be
    located``。不加 ``--plat`` 让它自己从 ELF interpreter 认出 musllinux_1_2_<arch>。
    """
    subprocess.run(
        [
            sys.executable, "-m", "auditwheel", "repair",
            "--wheel-dir", str(out_dir),
            str(raw_wheel),
        ],
        check=True,
    )


def report(wheel: Path) -> None:
    """打印产物与各目录的库清单（CI 日志里用来快速核对，不做断言）。"""
    print(f"\n产物: {wheel.name}")
    print(f"大小: {wheel.stat().st_size / 1024 / 1024:.1f} MiB")
    with zipfile.ZipFile(wheel) as zf:
        entries = [(i.filename, i.file_size) for i in zf.infolist() if ".so" in i.filename]
    for prefix in ("onnxruntime/capi/", "onnxruntime.libs/"):
        libs = sorted((e for e in entries if e[0].startswith(prefix)), key=lambda e: -e[1])
        print(f"{prefix}库数量: {len(libs)}")
        for name, size in libs:
            if size >= 1024 * 1024:
                print(f"  {name[len(prefix):]:48s} {size / 1024 / 1024:8.2f} MiB")


def pack_tree(unroot: Path, out_dir: Path) -> Path:
    """把组装好的 wheel 树打成 wheel，再交给 auditwheel 修复，返回最终产物。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    # 中间产物必须落在 out_dir 之外：CI 用 --output type=local 把整个目录取走，
    # 两个 wheel 混在一起会让下游的 *.whl 通配符装错文件。
    staging = Path(tempfile.mkdtemp(prefix="ortraw-"))
    try:
        subprocess.run(
            [sys.executable, "-m", "wheel", "pack", str(unroot), "--dest-dir", str(staging)],
            check=True,
        )
        raw_wheel = next(staging.glob("*.whl"))
        auditwheel_repair(raw_wheel, out_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    wheels = sorted(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"错误: 期望 {out_dir} 里恰好 1 个 wheel, 实际 {wheels}")
    report(wheels[0])
    return wheels[0]


def from_source_wheel(wheel_dir: Path, out_dir: Path, extra_dirs: list[Path]) -> int:
    """源码路径：解包 setup.py 的 wheel -> 组装 capi -> 修复打包。"""
    wheels = sorted(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        return die(f"期望 {wheel_dir} 里恰好 1 个 wheel, 实际 {len(wheels)}: {wheels}")

    work = Path(tempfile.mkdtemp(prefix="ortwheel-"))
    try:
        subprocess.run(
            [sys.executable, "-m", "wheel", "unpack", str(wheels[0]), "-d", str(work)],
            check=True,
        )
        unroot = next(p for p in work.iterdir() if p.is_dir())
        capi = unroot / "onnxruntime" / "capi"
        if not capi.is_dir():
            return die(f"{capi} 不存在")

        # setup.py 的 package_data 有时漏掉 /usr/lib 里的 onnxruntime 库
        for pattern in ("libonnxruntime.so*", "libonnxruntime_providers_shared.so"):
            if list(capi.glob(pattern)):
                continue
            for d in extra_dirs:
                for src in sorted(d.glob(pattern)):
                    print(f"补充 {src.name} -> onnxruntime/capi/")
                    shutil.copyfile(src, capi / src.name)

        prepare_capi(capi, extra_dirs)
        pack_tree(unroot, out_dir)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 重打包路径：Alpine 官方 apk -> wheel
# --------------------------------------------------------------------------- #
def copy_package(src: Path, dst: Path) -> None:
    """复制 python 包目录，跳过 __pycache__ / *.pyc（apk 预编译的用不上）。"""
    shutil.copytree(
        src,
        dst,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def write_dist_info(unroot: Path, egg_info: Path, version: str, plat_name: str) -> Path:
    """用 apk 里的 egg-info 元数据生成 wheel 的 dist-info 目录。"""
    major, minor = sys.version_info[:2]
    tag = f"cp{major}{minor}-cp{major}{minor}-{plat_name}"
    dist_info = unroot / f"onnxruntime-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)

    # egg-info 的 PKG-INFO 就是 PEP 566 METADATA，直接改名即可
    shutil.copyfile(egg_info / "PKG-INFO", dist_info / "METADATA")
    entries = egg_info / "entry_points.txt"
    if entries.exists():
        shutil.copyfile(entries, dist_info / "entry_points.txt")
    # 这里的 plat_name 只是「打包机的平台」，进过 auditwheel 后会被改成
    # musllinux_1_2_<arch>，两头一致才有意义，所以照旧写 sysconfig 的值。
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\n"
        "Generator: onnxruntime-docker pack_wheel.py (apk repackage)\n"
        "Root-Is-Purelib: false\n"
        f"Tag: {tag}\n"
    )
    return dist_info


def metadata_version(pkg_info: Path) -> str:
    for line in pkg_info.read_text(errors="replace").splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
        if not line.strip():
            break
    raise SystemExit(f"错误: {pkg_info} 里没有 Version:")


def from_apk(site_packages: Path, out_dir: Path, expect_version: str) -> int:
    """重打包路径：site-packages 里的 onnxruntime -> 自包含 wheel。"""
    pkg = site_packages / "onnxruntime"
    if not pkg.is_dir():
        return die(f"{pkg} 不存在（apk 没装 py3-onnxruntime？）")
    egg_infos = sorted(site_packages.glob("onnxruntime-*.egg-info"))
    if len(egg_infos) != 1:
        return die(f"期望 {site_packages} 里恰好 1 个 onnxruntime egg-info, 实际 {egg_infos}")

    version = metadata_version(egg_infos[0] / "PKG-INFO")
    if expect_version and version != expect_version:
        return die(
            f"apk 里的 onnxruntime 是 {version}，但期望 {expect_version}"
            f"（ONNX_VERSION 与 ALPINE_VERSION 不匹配？）"
        )

    plat_name = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    work = Path(tempfile.mkdtemp(prefix="ortapk-"))
    try:
        unroot = work / "tree"
        unroot.mkdir()
        print(f"从 {pkg} 复制 onnxruntime {version}")
        copy_package(pkg, unroot / "onnxruntime")

        capi = unroot / "onnxruntime" / "capi"
        prepare_capi(capi, [])
        write_dist_info(unroot, egg_infos[0], version, plat_name)
        pack_tree(unroot, out_dir)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheel-dir", help="含单个 setup.py 产物 wheel 的目录（源码路径）")
    ap.add_argument(
        "--from-apk",
        metavar="SITE_PACKAGES",
        help="apk 装好的 site-packages 目录（重打包路径），如 /usr/lib/python3.14/site-packages",
    )
    ap.add_argument("--out-dir", required=True, help="最终 wheel 输出目录")
    ap.add_argument(
        "--lib-dir",
        default="",
        help="cmake 构建目录下的 onnxruntime/capi，作为依赖查找的补充路径（源码路径）",
    )
    ap.add_argument(
        "--expect-version",
        default="",
        help="断言产出的版本，防止 alpine 升级后与 ONNX_VERSION 不一致",
    )
    args = ap.parse_args()

    if bool(args.wheel_dir) == bool(args.from_apk):
        return die("--wheel-dir 与 --from-apk 必须且只能给一个")

    out_dir = Path(args.out_dir).resolve()
    if args.from_apk:
        return from_apk(Path(args.from_apk).resolve(), out_dir, args.expect_version)

    extra_dirs = [Path(args.lib_dir).resolve()] if args.lib_dir else []
    return from_source_wheel(Path(args.wheel_dir).resolve(), out_dir, extra_dirs)


if __name__ == "__main__":
    sys.exit(main())
