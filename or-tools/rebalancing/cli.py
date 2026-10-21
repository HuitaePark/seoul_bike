"""Shared validated arguments for local road diagnostics and measurements."""
import argparse

from .travel_time import LatLon, validate_point


def depot_coordinate(value: str) -> LatLon:
    try:
        lat, lon = (float(part) for part in value.split(","))
        point = (lat, lon)
        validate_point("--depot", point)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--depot must be finite LAT,LON (latitude -90..90, longitude -180..180)"
        ) from error
    return point
