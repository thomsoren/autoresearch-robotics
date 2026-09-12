"""Read visible surface geometry from synchronized calibrated depth observations.

No object identities, task geometry, simulator access, or physics actions enter
this module. The sensor producer marks background depth invalid before saving.
"""

from pathlib import Path

import numpy as np

LOCATE_PIXELS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "locate_pixels",
        "description": (
            "Read calibrated depth at 1 to 8 pixels chosen from a displayed camera image. "
            "u is the integer column, v the integer row, with origin at the top left. "
            "Returns measured visible surface points in world meters, without moving "
            "the robot. These are not object centers or verified grasp goals. Choose "
            "pixels on visible surfaces, avoiding silhouettes and occlusions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "Exact displayed camera name."},
                "pixels": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {"u": {"type": "integer"}, "v": {"type": "integer"}},
                        "required": ["u", "v"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["camera", "pixels"],
            "additionalProperties": False,
        },
    },
}


def _matrix(value, shape, label):
    try:
        matrix = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid camera {label}") from exc
    if matrix.shape != shape or not np.isfinite(matrix).all():
        raise ValueError(f"Invalid camera {label}")
    return matrix


def _coordinate(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Pixel coordinates must be finite integers")
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise ValueError("Pixel coordinates must be finite integers") from exc
    if not np.isfinite(numeric) or not numeric.is_integer():
        raise ValueError("Pixel coordinates must be finite integers")
    return int(value)


def locate_pixels(observation, camera, pixels):
    """Return indexed sensor samples; invalid pixels do not create world targets."""
    if not isinstance(pixels, list) or not 1 <= len(pixels) <= 8:
        raise ValueError("pixels must contain 1 to 8 coordinate objects")
    geometry = observation.extra.get("camera_geometry", {})
    if (
        not isinstance(camera, str)
        or camera not in observation.images
        or not isinstance(geometry, dict)
        or camera not in geometry
    ):
        raise ValueError("Requested camera has no displayed image and calibrated depth")
    entry = geometry[camera]
    if not isinstance(entry, dict):
        raise ValueError("Invalid camera geometry record")
    step = entry.get("step")
    if type(step) is not int or step < 0:
        raise ValueError("Camera observation step must be a nonnegative integer")
    if "physics_steps" in observation.state:
        observed_step = np.asarray(observation.state["physics_steps"])
        if observed_step.shape != (1,) or observed_step[0] != step:
            raise ValueError("Camera geometry is stale relative to the observation")
    intrinsics = _matrix(entry.get("intrinsics"), (3, 3), "intrinsics")
    if intrinsics[0, 0] <= 0 or intrinsics[1, 1] <= 0 or not np.allclose(intrinsics[2], [0, 0, 1]):
        raise ValueError("Invalid camera intrinsics")
    try:
        inverse = np.linalg.inv(intrinsics)
    except np.linalg.LinAlgError as exc:
        raise ValueError("Invalid camera intrinsics") from exc
    transform = _matrix(entry.get("camera_to_world"), (4, 4), "camera_to_world")
    rotation = transform[:3, :3]
    if (
        not np.allclose(transform[3], [0, 0, 0, 1])
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
        or not np.isclose(np.linalg.det(rotation), 1, atol=1e-5)
    ):
        raise ValueError("Invalid camera_to_world rigid transform")
    source = entry.get("depth_path")
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise ValueError("Camera depth must be an absolute NPZ path")
    try:
        with np.load(source, allow_pickle=False) as archive:
            depth = np.asarray(archive["depth"], dtype=float)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Camera depth snapshot is unavailable or invalid") from exc
    image = np.asarray(observation.images[camera])
    if depth.ndim != 2 or image.ndim < 2 or depth.shape != image.shape[:2]:
        raise ValueError("Depth dimensions do not match the displayed camera image")
    height, width = depth.shape
    points = []
    for index, requested in enumerate(pixels):
        point = {"index": index, "valid": False}
        points.append(point)
        if not isinstance(requested, dict) or set(requested) != {"u", "v"}:
            point["error"] = "Each pixel must contain only u and v"
            continue
        try:
            u, v = _coordinate(requested["u"]), _coordinate(requested["v"])
        except ValueError as exc:
            point["error"] = str(exc)
            continue
        point["pixel"] = {"u": u, "v": v}
        if not (0 <= u < width and 0 <= v < height):
            point["error"] = "Pixel is outside the displayed image"
            continue
        z = float(depth[v, u])
        if not np.isfinite(z) or z <= 0:
            point["error"] = "invalid_or_background_depth"
            continue
        with np.errstate(over="ignore", invalid="ignore"):
            camera_point = z * (inverse @ [u, v, 1])
            world = transform @ np.r_[camera_point, 1]
        if not np.isfinite(world).all():
            point["error"] = "Invalid projected point"
            continue
        point.update(valid=True, depth_m=z, world_xyz=world[:3].tolist())
    return {
        "camera": camera,
        "observation_step": step,
        "image_dimensions": {"width": width, "height": height},
        "pixel_convention": "u=column, v=row, top-left origin",
        "meaning": "Visible surface points, not object centers or verified grasp goals.",
        "points": points,
    }
