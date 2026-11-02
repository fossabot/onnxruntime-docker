#!/usr/bin/env python3
"""计算构建矩阵，输出到 stdout（key=value 形式，由 workflow 追加到 $GITHUB_OUTPUT）。

从环境变量读取输入，不读仓库文件。
"""

import json
import os
import sys

default = [p.strip() for p in os.environ["DEFAULT_PLATFORMS"].split(",") if p.strip()]
raw = (os.environ.get("PLATFORM_INPUT") or "").strip()
plats = [p.strip() for p in raw.split(",") if p.strip()] if raw else default

# 可选平台 = 实测通过的 6 个。加新平台前必须先确认两件事：
#   1) 该架构的 alpine 源里有 py3-onnxruntime 与 py3-numpy（或像 loong64 那样换一个
#      同版本的基座仓库）；
#   2) tests/smoke.py 的 ELF_MACHINE 里有它的 e_machine 编码。
allowed = {
    "linux/amd64",
    "linux/arm64",
    "linux/arm/v7",
    "linux/ppc64le",
    "linux/riscv64",
    "linux/loong64",
}
bad = [p for p in plats if p not in allowed]
if bad:
    sys.exit(
        "::error::不支持或未验证的平台: %s（可选: %s）"
        % (",".join(bad), ",".join(sorted(allowed)))
    )
dup = sorted({p for p in plats if plats.count(p) > 1})
if dup:
    sys.exit("::error::平台重复: %s" % ",".join(dup))

# 默认启用原生 arm64 runner，与 pycurl-docker / ddddocr-docker 一致。
# 原来写的是 == "true"，而没有任何地方传 NATIVE_ARM，等于 arm 构建一直跑在 x86_64 上
# 靠 QEMU。要临时退回 QEMU 时设 NATIVE_ARM=false。
arm_runner = (
    "ubuntu-latest"
    if os.environ.get("NATIVE_ARM") == "false"
    else "ubuntu-24.04-arm"
)

# wheel 文件名里的架构串（pack_wheel.py 用 uname -m 的值）。容器 tag 与日志里用它，
# 避免再有地方把 arm/v7 写成 arm-v7 而和 wheel 名对不上。
arch_map = {
    "linux/amd64": "amd64",
    "linux/arm64": "arm64",
    "linux/arm/v7": "armv7l",
    "linux/ppc64le": "ppc64le",
    "linux/riscv64": "riscv64",
    "linux/loong64": "loong64",
}


def base_of(platform: str) -> str:
    """只有 loong64 需要换基座：alpine 官方没有该架构，ghcr.io/loong64/alpine 有同
    Alpine 版本、同 py3-onnxruntime / py3-numpy。空字符串 = 用 Dockerfile 里的默认
    ${BASE_IMAGE}=alpine:${ALPINE_VERSION}。"""
    if platform == "linux/loong64":
        return "%s:%s" % (os.environ["LOONG64_BASE"], os.environ["ALPINE"])
    return ""


def wheel_entry(platform: str) -> dict:
    """第一段：重打包 wheel，跑在最快的 runner 上。

    只有 arm64 放原生 arm64 runner。**armv7 必须留在 x86_64**：wheel 的名字由
    Python 的 sysconfig.get_platform() 决定，而 compat 模式下 Python 自报 aarch64，
    会让 arm32 的 wheel 被标成 linux_aarch64，auditwheel 随即失败（实测）。
    这一点与 pycurl-docker 不同——那边的 wheel 名由 gcc target 决定，compat 下仍正确。
    """
    return {
        "platform": platform,
        "group": platform.replace("linux/", "").replace("/", "-"),
        "arch": arch_map[platform],
        "runner": arm_runner if platform == "linux/arm64" else "ubuntu-latest",
        "base_image": base_of(platform),
    }


def runtime_entry(platform: str) -> dict:
    """第二段：用第一段的产物组装 wheelhouse 与 runtime，统一跑 x86_64。"""
    e = wheel_entry(platform)
    e["runner"] = "ubuntu-latest"
    return e


wheel_matrix = [wheel_entry(p) for p in plats]
runtime_matrix = [runtime_entry(p) for p in plats]

print("wheel_matrix=" + json.dumps({"include": wheel_matrix}))
print("runtime_matrix=" + json.dumps({"include": runtime_matrix}))
print("smoke_matrix=" + json.dumps({"include": wheel_matrix}))
print("expected=" + ",".join(sorted(plats)))
print("full=%s" % str(sorted(plats) == sorted(default)).lower())
