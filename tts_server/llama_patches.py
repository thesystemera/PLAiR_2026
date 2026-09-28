import logging
import os
import sys

import config

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ["CUDA_VISIBLE_DEVICES"] = config.GPU
os.environ.setdefault("GGML_CUDA_FORCE_MMQ", "1")

_site_packages = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", "Lib", "site-packages"))
_nvidia_root = os.path.join(_site_packages, "nvidia")

if os.name == "nt" and os.path.isdir(_nvidia_root):
    _dll_dirs = [
        os.path.join(_nvidia_root, name, "bin")
        for name in os.listdir(_nvidia_root)
        if os.path.isdir(os.path.join(_nvidia_root, name, "bin"))
    ]
    os.environ["PATH"] = os.pathsep.join(_dll_dirs + [os.environ.get("PATH", "")])
    for _dll_dir in _dll_dirs:
        os.add_dll_directory(_dll_dir)

import onnxruntime as _onnxruntime

if os.name == "nt":
    _onnxruntime.preload_dlls(cuda=True, cudnn=True, msvc=True, directory="")

_OrigInferenceSession = _onnxruntime.InferenceSession


def _snac_inference_session(path, *args, providers=None, **kwargs):
    if config.SNAC_DEVICE == "cpu":
        providers = ["CPUExecutionProvider"]
    return _OrigInferenceSession(path, *args, providers=providers, **kwargs)


_onnxruntime.InferenceSession = _snac_inference_session

import llama_cpp as _llama_cpp

log = logging.getLogger("tts_server")

if not _llama_cpp.llama_supports_gpu_offload():
    raise RuntimeError(
        "llama-cpp-python was installed WITHOUT CUDA support. Rebuild it with "
        "CMAKE_ARGS='-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=61;75' (see tts_server/README.md)."
    )

_orig_llama_init = _llama_cpp.Llama.__init__


def _patched_llama_init(self, *args, **kwargs):
    kwargs.setdefault("flash_attn", config.FLASH_ATTN)
    kwargs.setdefault("n_gpu_layers", -1)
    if kwargs.get("n_ctx") == 0:
        kwargs["n_ctx"] = config.N_CTX
    kwargs.setdefault("n_batch", config.N_BATCH)
    kwargs.setdefault("n_ubatch", config.N_UBATCH)
    log.info("Loading model %s | n_ctx=%s n_batch=%s flash_attn=%s",
             kwargs.get("model_path", "?"), kwargs.get("n_ctx"), kwargs.get("n_batch"), kwargs.get("flash_attn"))
    return _orig_llama_init(self, *args, **kwargs)


_llama_cpp.Llama.__init__ = _patched_llama_init

_orig_llama_call = _llama_cpp.Llama.__call__


def _patched_llama_call(self, *args, **kwargs):
    kwargs.setdefault("repeat_penalty", config.REPEAT_PENALTY)
    if config.FREQUENCY_PENALTY != 0.0:
        kwargs.setdefault("frequency_penalty", config.FREQUENCY_PENALTY)
    return _orig_llama_call(self, *args, **kwargs)


_llama_cpp.Llama.__call__ = _patched_llama_call
