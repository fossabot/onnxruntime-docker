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

arm_runner = (
    "ubuntu-24.04-arm" if os.environ.get("NATIVE_ARM") == "true" else "ubuntu-latest"
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


def entry(platform: str) -> dict:
    """一个平台一个 job，一个 job 里连着 build 两个 target：

    * 重打包是秒级，两个平台并进同一个 buildx 调用没有必要，反而放大内存峰值；
    * 两个 target 共用 `repack` 阶段，同一 builder 里第二次直接命中本地缓存，拆成两个
      job 反而会把它做两遍；
    * 单平台 push-by-digest 得到的是单平台 manifest digest，发布时按平台逐一合并，
      冒烟也能按 digest 取回同一份字节。
    """
    return {
        "platform": platform,
        "group": platform.replace("linux/", "").replace("/", "-"),
        "arch": arch_map[platform],
        "runner": arm_runner if platform == "linux/arm64" else "ubuntu-latest",
        "base_image": base_of(platform),
    }


include = [entry(p) for p in plats]

print("matrix=" + json.dumps({"include": include}))
print("smoke_matrix=" + json.dumps({"include": include}))
print("expected=" + ",".join(sorted(plats)))
print("full=%s" % str(sorted(plats) == sorted(default)).lower())
