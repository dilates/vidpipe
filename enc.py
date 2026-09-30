"""Final-encode backend: h264_nvenc when the GPU accepts it, libx264 otherwise.

The NVENC settings are measured: spatial+temporal AQ keeps the moving speaker
sharp instead of spending bits on the static background (+3.2 dB PSNR over
p6/cq21 vs the near-lossless intermediate). VIDPIPE_ENCODER=nvenc|libx264
overrides the probe.
"""
import os, subprocess

_choice = None


def encoder():
    global _choice
    if _choice is None:
        c = os.environ.get("VIDPIPE_ENCODER")
        if c not in ("nvenc", "libx264"):
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=black:s=128x128:d=0.3:r=10", "-frames:v", "3",
                 "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True)
            c = "nvenc" if r.returncode == 0 else "libx264"
        _choice = c
    return _choice


def video_args(quality=17, maxrate="40M", bufsize="80M"):
    """Video codec args for the dress/final encode, matching measured settings."""
    if encoder() == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr",
                "-cq", str(quality), "-b:v", "0", "-maxrate", maxrate,
                "-bufsize", bufsize, "-profile:v", "high",
                "-rc-lookahead", "32", "-spatial-aq", "1", "-aq-strength", "8",
                "-temporal-aq", "1", "-bf", "3"]
    # cq and crf aren't the same scale; +3 keeps the visual target roughly equal
    return ["-c:v", "libx264", "-preset", "slow", "-crf", str(quality + 3),
            "-profile:v", "high"]


def _selftest():
    assert encoder() in ("nvenc", "libx264")
    args = video_args()
    i = args.index("-c:v")
    assert args[i + 1] in ("h264_nvenc", "libx264"), args
    print("enc.py selftest ok")


if __name__ == "__main__":
    _selftest()