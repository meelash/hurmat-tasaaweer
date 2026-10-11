import os

# ncnn's CPU thread pool (OpenMP) busy-waits between tasks by default. The
# model runs on the GPU, so that spinning burned ~2 cores for nothing. This
# has to be set before ncnn is first imported.
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
