"""
Project Pulse V2.5 — Universal Unit Conversion Engine
High-precision unit conversion using Python's decimal library.

Supports:
    - Mass family: g, kg, oz, lb
    - Volume family: ml, l, cup, fl_oz, tbsp, tsp, pt, qt, gal
    - Cross-family (volume → mass): requires density parameter
"""

from decimal import Decimal, ROUND_HALF_UP

# Mass units → grams
MASS_MAP: dict[str, Decimal] = {
    "g": Decimal("1.0"),
    "kg": Decimal("1000.0"),
    "oz": Decimal("28.3495"),
    "lb": Decimal("453.592"),
}

# Volume units → milliliters
VOLUME_MAP: dict[str, Decimal] = {
    "ml": Decimal("1.0"),
    "l": Decimal("1000.0"),
    "cup": Decimal("240.0"),
    "fl_oz": Decimal("29.5735"),
    "tbsp": Decimal("15.0"),
    "tsp": Decimal("5.0"),
    "pt": Decimal("473.176"),
    "qt": Decimal("946.353"),
    "gal": Decimal("3785.41"),
}

TWO_PLACES = Decimal("0.01")


def _get_unit_family(unit: str) -> str | None:
    """Returns 'mass' or 'volume' or None."""
    if unit in MASS_MAP:
        return "mass"
    if unit in VOLUME_MAP:
        return "volume"
    return None


def convert_units_decimal(
    value: Decimal,
    from_unit: str,
    to_unit: str,
    density: Decimal = Decimal("1.0"),
) -> Decimal:
    """
    Convert a numeric value between units.

    Same-family conversions are direct mathematical scaling.
    Cross-family (volume ↔ mass) requires physical density (g/ml).

    Args:
        value: Numeric quantity to convert.
        from_unit: Source unit string (e.g., 'oz', 'cup', 'g').
        to_unit: Target unit string.
        density: Density in g/ml for cross-family conversions.

    Returns:
        Decimal: Converted value quantized to 2 decimal places.

    Raises:
        ValueError: If units are unknown or conversion is impossible.
    """
    from_unit = from_unit.lower().strip()
    to_unit = to_unit.lower().strip()

    # Same unit — identity
    if from_unit == to_unit:
        return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    from_family = _get_unit_family(from_unit)
    to_family = _get_unit_family(to_unit)

    if from_family is None:
        raise ValueError(f"Unknown source unit: '{from_unit}'")
    if to_family is None:
        raise ValueError(f"Unknown target unit: '{to_unit}'")

    # Same family — direct scale
    if from_family == to_family:
        if from_family == "mass":
            # Convert to grams, then to target
            grams = value * MASS_MAP[from_unit]
            result = grams / MASS_MAP[to_unit]
        else:
            # Convert to ml, then to target
            ml = value * VOLUME_MAP[from_unit]
            result = ml / VOLUME_MAP[to_unit]
        return result.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # Cross-family conversion
    if from_family == "volume" and to_family == "mass":
        # volume → ml → grams (via density) → target mass unit
        ml = value * VOLUME_MAP[from_unit]
        grams = ml * density
        result = grams / MASS_MAP[to_unit]
        return result.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    if from_family == "mass" and to_family == "volume":
        # mass → grams → ml (via density) → target volume unit
        grams = value * MASS_MAP[from_unit]
        ml = grams / density
        result = ml / VOLUME_MAP[to_unit]
        return result.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    raise ValueError(
        f"Cannot convert from '{from_unit}' ({from_family}) "
        f"to '{to_unit}' ({to_family})"
    )
