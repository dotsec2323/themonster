"""
draw.py
-------
Shared rendering helpers for overlaying detections onto frames.
Used by image test mode now; reused unchanged by video/camera modes later
so the visual language stays consistent across every stage.
"""

import cv2

COLOR_GOOD = (60, 200, 90)      # BGR - green
COLOR_REJECT = (40, 40, 230)    # BGR - red
COLOR_UNKNOWN = (0, 200, 230)   # BGR - amber
COLOR_TEXT_BG = (20, 20, 20)


def color_for_role(role: str):
    if role == "GOOD":
        return COLOR_GOOD
    if role == "REJECT":
        return COLOR_REJECT
    return COLOR_UNKNOWN


def draw_detections(frame, detections, show_track_id=False):
    """Draws boxes + labels in-place on a BGR frame (numpy array). Returns frame."""
    for det in detections:
        x1, y1, x2, y2 = [int(round(v)) for v in det.xyxy]
        color = color_for_role(det.role)
        thickness = max(2, round(frame.shape[1] / 500))
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

        label = f"{det.role}_CAN  {det.confidence * 100:.0f}%"
        if show_track_id and det.track_id is not None:
            label = f"ID {det.track_id}  " + label

        font_scale = max(0.5, frame.shape[1] / 1400)
        font_thick = max(1, round(font_scale * 2))
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thick)

        label_y1 = max(0, y1 - th - baseline - 6)
        cv2.rectangle(frame, (x1, label_y1), (x1 + tw + 8, y1), COLOR_TEXT_BG, -1)
        cv2.putText(frame, label, (x1 + 4, y1 - baseline - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), font_thick, cv2.LINE_AA)
    return frame
