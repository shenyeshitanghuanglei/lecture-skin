import torch

def circular_write(new_data: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    offset = new_data.shape[0]
    target[: -offset] = target[offset :].detach().clone()
    target[-offset :] = new_data
    return target


def sync_for_onnx(t: torch.Tensor) -> None:
    """voice-translater: wait for torch's pending GPU work before ONNX Runtime reads `t`.

    io_binding hands ORT a raw device pointer, but ORT runs on its own CUDA stream and
    does not wait for torch's stream. Without this it can read a half-written buffer,
    which shows up as random NaN pitch/features -> ~250 ms dropouts in the output,
    more often when the GPU is busy (measured: 12 bad blocks -> 0 on an 18 s clip).
    """
    if t.is_cuda:
        torch.cuda.current_stream(t.device).synchronize()
