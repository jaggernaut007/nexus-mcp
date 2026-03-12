# ADR-003: ONNX Runtime over PyTorch for Inference

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Shreyas Jagannath

## Context
CodeGrok used PyTorch via SentenceTransformers for embedding inference, consuming 300-500MB RAM just for the runtime. This was the single largest contributor to memory usage and made it impossible to meet the <350MB total RAM target.

## Decision
Use ONNX Runtime as the inference backend. SentenceTransformers supports ONNX export natively. ONNX Runtime uses ~50MB RAM vs PyTorch's ~300-500MB, with 2.5x faster CPU inference.

## Consequences
- **Easier:** Meets RAM target, faster cold start, smaller install size, INT8 quantization available for further savings
- **Harder:** Model must be exported to ONNX format first, some exotic model architectures may not export cleanly, debugging inference issues is harder than with PyTorch

## Alternatives Considered
- **PyTorch with inference_mode():** Rejected — even with optimizations, runtime baseline is ~300MB
- **TensorFlow Lite:** Rejected — less ecosystem support for SentenceTransformers models
- **Custom C++ inference:** Rejected — overkill for this use case, ONNX Runtime already provides the Rust/C++ performance
