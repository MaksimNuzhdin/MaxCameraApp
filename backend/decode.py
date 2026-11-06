"""Convert GVSP payloads to BGR images for preview and snapshots."""
import cv2
import numpy as np

MONO8, BG8, BG10, BG10P = 0x01080001, 0x0108000B, 0x0110000F, 0x010A0052
RGB8, BGR8, RGB565P, YUV444 = 0x02180014, 0x02180015, 0x02100035, 0x02180020
UYVY, YUYV, YVYU, VYUY = 0x0210001F, 0x02100032, 0x82100004, 0x82100005
RGB555BE, RGB444BE, YUV420 = 0x82100001, 0x82100002, 0x820C0003

_BAYER_BG_TO_BGR = cv2.COLOR_BayerBGGR2BGR


def _expand(values, bits):
    return ((values << (8 - bits)) | (values >> (2 * bits - 8))).astype(np.uint8)


def to_bgr(data, width, height, fmt):
    """Returns a BGR array; raises ValueError for an unknown format or short payload."""
    buf = np.frombuffer(data, dtype=np.uint8)
    if fmt == MONO8:
        return cv2.cvtColor(buf[:width * height].reshape(height, width), cv2.COLOR_GRAY2BGR)
    if fmt == BG8:
        return cv2.cvtColor(buf[:width * height].reshape(height, width), _BAYER_BG_TO_BGR)
    if fmt == BG10:
        raw = np.frombuffer(data, dtype="<u2", count=width * height).reshape(height, width)
        return cv2.cvtColor((raw >> 2).astype(np.uint8), _BAYER_BG_TO_BGR)
    if fmt == BG10P:
        # Continuous little-endian 10-bit stream, five bytes per four pixels.
        groups = buf[:width * height * 5 // 4].reshape(-1, 5).astype(np.uint16)
        out = np.empty((groups.shape[0], 4), dtype=np.uint8)
        out[:, 0] = ((groups[:, 0] | (groups[:, 1] << 8)) >> 2) & 255
        out[:, 1] = ((groups[:, 1] >> 2) | (groups[:, 2] << 6)) >> 2 & 255
        out[:, 2] = ((groups[:, 2] >> 4) | (groups[:, 3] << 4)) >> 2 & 255
        out[:, 3] = groups[:, 4]
        return cv2.cvtColor(out.reshape(height, width), _BAYER_BG_TO_BGR)
    if fmt == RGB8:
        return cv2.cvtColor(buf[:width * height * 3].reshape(height, width, 3), cv2.COLOR_RGB2BGR)
    if fmt == BGR8:
        return buf[:width * height * 3].reshape(height, width, 3).copy()
    if fmt == YUV444:
        # Wire order U,Y,V.
        uyv = buf[:width * height * 3].reshape(height, width, 3)
        return cv2.cvtColor(uyv[:, :, [1, 0, 2]], cv2.COLOR_YUV2BGR)
    if fmt in (UYVY, YUYV, YVYU, VYUY):
        code = {UYVY: cv2.COLOR_YUV2BGR_UYVY, YUYV: cv2.COLOR_YUV2BGR_YUYV,
                YVYU: cv2.COLOR_YUV2BGR_YVYU}.get(fmt)
        image = buf[:width * height * 2].reshape(height, width, 2)
        if code is not None:
            return cv2.cvtColor(image, code)
        # VYUY has no OpenCV code: swapping U and V bytes yields UYVY.
        swapped = buf[:width * height * 2].copy().reshape(-1, 4)[:, [2, 1, 0, 3]]
        return cv2.cvtColor(swapped.reshape(height, width, 2), cv2.COLOR_YUV2BGR_UYVY)
    if fmt == RGB565P:
        v = np.frombuffer(data, dtype="<u2", count=width * height).reshape(height, width)
        return np.dstack([_expand(v >> 11 & 31, 5), _expand(v >> 5 & 63, 6), _expand(v & 31, 5)])
    if fmt == RGB555BE:
        v = np.frombuffer(data, dtype=">u2", count=width * height).reshape(height, width)
        return np.dstack([_expand(v & 31, 5), _expand(v >> 5 & 31, 5), _expand(v >> 10 & 31, 5)])
    if fmt == RGB444BE:
        v = np.frombuffer(data, dtype=">u2", count=width * height).reshape(height, width)
        return np.dstack([(v & 15).astype(np.uint8) * 17, (v >> 4 & 15).astype(np.uint8) * 17,
                          (v >> 8 & 15).astype(np.uint8) * 17])
    if fmt == YUV420:
        return _yuv420_legacy(buf, width, height)
    raise ValueError(f"Pixel format 0x{fmt:08x} cannot be previewed")


def _yuv420_legacy(buf, width, height):
    # Project-specific packing: triplets C Y Y; C is U on even rows and V on odd rows,
    # giving 2x2 chroma (same layout as the project's gige_frame_previews tool).
    v = buf[:width * 3 // 2 * height].reshape(height, width // 2, 3)
    luma = v[:, :, 1:].reshape(height, width)
    chroma_u = np.repeat(np.repeat(v[::2, :, 0], 2, axis=0), 2, axis=1)[:height, :width]
    chroma_v = np.repeat(np.repeat(v[1::2, :, 0], 2, axis=0), 2, axis=1)[:height, :width]
    return cv2.cvtColor(np.dstack([luma, chroma_v, chroma_u]), cv2.COLOR_YCrCb2BGR)


def preview_jpeg(bgr, max_width, quality):
    h, w = bgr.shape[:2]
    if max_width and w > max_width:
        bgr = cv2.resize(bgr, (max_width, max(2, h * max_width // w)), interpolation=cv2.INTER_AREA)
    ok, out = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return out.tobytes(), bgr.shape[1], bgr.shape[0]
