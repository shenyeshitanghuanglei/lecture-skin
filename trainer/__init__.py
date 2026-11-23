"""Voice cloning: train an RVC v2 (40k, f0) model from user-supplied audio.

Runs as its own process (``python -m trainer.run ...``) so a crash or OOM never takes
the real-time voice changer down. Training code is a streamlined port of
RVC-Project/Retrieval-based-Voice-Conversion-WebUI (MIT); the model classes are the
same ones the real-time server uses for inference.
"""
