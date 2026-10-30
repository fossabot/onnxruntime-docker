#!/usr/bin/env python3
"""onnxruntime wheel 的冒烟入口。

在**干净的 alpine 镜像**里跑（只有 `apk add python3 uv` 与 `uv pip install` 装进去的
那个 wheel，没有编译期依赖、没有构建工具），因此这里能跑通就等价于「任意 alpine 用户
`pip install` 后可用」：

    docker run --rm --platform <platform> \
        -v $PWD/dist/wheels:/wheels:ro -v $PWD/tests:/tests:ro alpine:3.24 \
        sh -c 'apk add --no-cache python3 uv &&
               uv pip install --system --break-system-packages /wheels/*.whl &&
               python3 /tests/smoke.py <platform> <version>'

    argv1: 平台名，如 linux/arm64（用于 ELF 架构校验）
    argv2: 期望的 onnxruntime 版本，如 1.24.4

三件事必须验，各自防一类回归：

1. import + 版本 + provider —— wheel 能不能被加载、CPU EP 有没有被编进去；
2. 真实推理 —— 只 import 不推理的话，`libonnxruntime.so.1` 用 dlopen 懒加载，
   很多自包含化问题（RPATH、DT_NEEDED 缺失）要到建 Session 时才炸；
3. ELF e_machine —— 产物是不是目标架构的机器码。QEMU 下构建时最容易踩的坑是
   cmake 的架构探测被 uname 骗了（在原生 arm64 runner 上跑 armv7 容器，uname
   仍报 aarch64），编译能过、产出的却是宿主架构的 .so。

numpy 从 `Dockerfile.repackage` 的 wheelhouse 来时是「apk 重打包 + auditwheel 把
libopenblas 收进 numpy.libs/」，所以额外做一次**线性代数**：`numpy.linalg.solve`
是 LAPACK 调用，只有 libopenblas / libgfortran 真被 RPATH 找到并加载才可能算对；
光 `import numpy` 只能证明 `.so` 被 mmap 了，证明不了 BLAS 可用。
"""

import glob
import os
import platform
import struct
import sys

# ELF e_machine 编码（/usr/include/elf.h）
ELF_MACHINE = {
    "linux/amd64": 62, "linux/386": 3, "linux/arm64": 183,
    "linux/arm/v7": 40, "linux/arm/v6": 40, "linux/ppc64le": 21,
    "linux/riscv64": 243, "linux/s390x": 22, "linux/loong64": 258,
}


def elf_machine(path):
    """读 ELF 头的 e_machine（不依赖 readelf/file，镜像里未必有）。"""
    with open(path, "rb") as handle:
        header = handle.read(20)
    assert header[:4] == b"\x7fELF", "%s 不是 ELF 文件" % path
    # EI_DATA: 1=小端 2=大端（s390x 大端，e_machine 也按大端存）
    endian = "<" if header[5] == 1 else ">"
    return struct.unpack(endian + "H", header[18:20])[0]


def check_arch(platform_name, patterns):
    """断言 wheel 里的原生产物确实是目标平台的机器码。"""
    expected = ELF_MACHINE.get(platform_name)
    if expected is None:
        print("跳过 ELF 架构校验（未知平台 %r）" % platform_name)
        return

    checked = 0
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            got = elf_machine(path)
            assert got == expected, (
                "%s 的 ELF machine=%d，期望 %d（%s）—— 构建时架构探测错了"
                % (path, got, expected, platform_name)
            )
            checked += 1
    assert checked, "没有找到可校验的原生产物（检查 glob 模式是否写错）"
    print("ELF 架构校验通过：%d 个产物 = %s（e_machine=%d）"
          % (checked, platform_name, expected))


# --------------------------------------------------------------------------- #
# 极小 ONNX 模型：不需要 onnx 包，手写 protobuf 编码
#
#   C = A + B          A/B 是 initializer（1.0 / 2.0），无图输入
#
# 为什么手搓而不下载：冒烟是发布闸门，不能把通过与否交给第三方可用性；也不
# 能依赖 `onnx` 包（wheel 的 Requires-Dist 里没有它，装了就等于在验别的包）。
# 编码只用 protobuf 的 varint 与 length-delimited 两种 wire type，二十行就够。
# --------------------------------------------------------------------------- #
def _varint(value):
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _f_int64(field, value):
    return _varint(field << 3) + _varint(value)


def _f_bytes(field, payload):
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _f_string(field, text):
    return _f_bytes(field, text.encode())


def _f_float(field, value):
    # wire type 5 = 32-bit little-endian
    return _varint((field << 3) | 5) + struct.pack("<f", value)


FLOAT = 1  # TensorProto.DataType.FLOAT


def _tensor(name, dims, values):
    """TensorProto：name=8 / dims=1 / data_type=2 / float_data=4。"""
    body = b"".join(_f_int64(1, d) for d in dims)
    body += _f_int64(2, FLOAT)
    body += b"".join(_f_float(4, v) for v in values)
    body += _f_string(8, name)
    return body


def _node(op_type, inputs, outputs, name=""):
    """NodeProto：input=1 / output=2 / name=3 / op_type=4。"""
    body = b"".join(_f_string(1, i) for i in inputs)
    body += b"".join(_f_string(2, o) for o in outputs)
    body += _f_string(3, name)
    body += _f_string(4, op_type)
    return body


def _value_info(name, shape):
    """ValueInfoProto{name=1, type=2} + TypeProto{tensor_type=1} + Tensor{elem_type=1, shape=2}。"""
    shape_body = b"".join(
        _f_bytes(1, _f_int64(1, d)) for d in shape       # Dimension.dim_value=1
    )
    tensor_type = _f_int64(1, FLOAT) + _f_bytes(2, shape_body)
    return _f_string(1, name) + _f_bytes(2, _f_bytes(1, tensor_type))


def build_add_model():
    graph = _f_bytes(1, _node("Add", ["A", "B"], ["C"], "add"))
    graph += _f_string(2, "smoke")                        # graph.name
    graph += _f_bytes(5, _tensor("A", [1], [1.0]))        # initializer
    graph += _f_bytes(5, _tensor("B", [1], [2.0]))
    graph += _f_bytes(12, _value_info("C", [1]))          # graph.output

    model = _f_int64(1, 4)                                # ir_version
    model += _f_bytes(7, graph)                           # graph
    model += _f_bytes(8, _f_int64(2, 13))                 # opset_import.version=13
    return model


# --------------------------------------------------------------------------- #
# 三项检查
# --------------------------------------------------------------------------- #
def check_import(expected_version):
    import onnxruntime

    print("onnxruntime %s | 加载自 %s"
          % (onnxruntime.__version__, os.path.dirname(onnxruntime.__file__)))
    assert onnxruntime.__version__ == expected_version, (
        "版本不符：%s != %s（构建参数与冒烟参数不一致？）"
        % (onnxruntime.__version__, expected_version)
    )
    providers = onnxruntime.get_available_providers()
    print("providers: %s" % (providers,))
    assert "CPUExecutionProvider" in providers, (
        "缺少 CPUExecutionProvider（providers=%s）—— 这个 wheel 没有可用的执行后端" % (providers,)
    )
    return onnxruntime


def check_inference(onnxruntime):
    """真实跑一次推理：懒加载的 libonnxruntime.so.1 到这一步才被 dlopen。"""
    data = build_add_model()
    print("模型: 手搓 Add，%d 字节" % len(data))

    session = onnxruntime.InferenceSession(data, providers=["CPUExecutionProvider"])
    outputs = session.run(None, {})
    value = outputs[0].tolist()
    print("推理结果: 1.0 + 2.0 = %r" % (value,))
    # 输出 shape 是 [1]（不是标量），tolist 后是长度 1 的列表
    assert value == [3.0], "推理结果错误：期望 [3.0]，实际 %r" % (value,)


def check_numpy():
    """numpy 的三层检查：import、数组运算、LAPACK（libopenblas 是否真被加载）。"""
    import numpy

    print("numpy %s | 加载自 %s"
          % (numpy.__version__, os.path.dirname(numpy.__file__)))

    # 普通 ufunc 走 SIMD kernel，不碰 BLAS
    assert float((numpy.arange(4.0) * 2).sum()) == 12.0, "numpy 基础运算错误"

    # solve / svd 都落到 LAPACK（_umath_linalg -> libopenblas）。
    # 自包含化做坏了（numpy.libs 没打进去、RPATH 不对）这里才会炸：
    # ImportError: libopenblas.so.3: No such file or directory
    a = numpy.array([[4.0, 1.0], [1.0, 3.0]])
    b = numpy.array([1.0, 2.0])
    x = numpy.linalg.solve(a, b)
    assert numpy.allclose(a @ x, b), "numpy.linalg.solve 结果错误：%r" % (x,)

    sv = numpy.linalg.svd(a, compute_uv=False)
    print("线性代数: solve=%s | svd=%s | norm=%.6f"
          % (numpy.round(x, 6).tolist(), numpy.round(sv, 6).tolist(),
             numpy.linalg.norm(a)))
    # 2x2 对称正定矩阵的奇异值可解析验证，防止「算出来是垃圾但没报错」
    assert numpy.allclose(sv, [4.618033988749895, 2.381966011250105]), (
        "numpy.linalg.svd 结果错误：%r（BLAS/LAPACK 有问题）" % (sv,)
    )


def main(argv):
    if len(argv) < 3:
        raise SystemExit("用法: python3 tests/smoke.py <平台> <期望版本>，"
                         "例如 python3 tests/smoke.py linux/arm64 1.24.4")
    platform_name, expected_version = argv[1], argv[2]

    print("== 环境 ==")
    print("python %s | %s | %s"
          % (platform.python_version(), platform.machine(), sys.platform))

    print("== numpy / 线性代数 ==")
    check_numpy()

    print("== import / 版本 / provider ==")
    onnxruntime = check_import(expected_version)

    print("== wheel 内容 ==")
    capi = os.path.join(os.path.dirname(onnxruntime.__file__), "capi")
    libs = sorted(glob.glob(os.path.join(capi, "*.so*")))
    # capi 里有几十个 abseil 碎片库，全打出来会淹没日志，只列 >1MiB 的
    for path in libs:
        size = os.path.getsize(path) / 1024 / 1024
        if size >= 1:
            print("  %-40s %6.2f MiB" % (os.path.basename(path), size))
    print("  （共 %d 个 .so，其余 <1MiB 的依赖库略）" % len(libs))

    print("== 产物架构 ==")
    # 只校验 pybind 扩展与主库：这两个是编译产物，架构错了必然跑不起来
    import numpy
    np_dir = os.path.dirname(numpy.__file__)
    check_arch(platform_name, [
        os.path.join(capi, "onnxruntime_pybind11_state.so"),
        os.path.join(capi, "libonnxruntime.so*"),
        # numpy 的扩展也是编译产物；重打包时若误用了宿主架构的包，这里会抓到
        os.path.join(np_dir, "_core", "_multiarray_umath*.so"),
        os.path.join(np_dir, "linalg", "_umath_linalg*.so"),
    ])

    print("== 真实推理 ==")
    check_inference(onnxruntime)

    print("smoke(%s/%s): PASS" % (platform_name, expected_version))


if __name__ == "__main__":
    main(sys.argv)
