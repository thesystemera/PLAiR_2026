import torch
from clearvoice import ClearVoice
from clearvoice.networks import SpeechModel

SpeechModel.get_free_gpu = lambda self: torch.cuda.current_device()

__all__ = ["ClearVoice"]
