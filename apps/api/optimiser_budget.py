"""The method and evaluation budget a calibration gets when the user has not set them.

The method is CA's ``DEFAULT_PARAM_ID_METHOD`` (CMA-ES from libcuflynx 0.8.0),
or the genetic algorithm on a CA that declares none.

CA's genetic algorithm exits when ``num_calls_to_function`` is below one
generation's population, so its fallback is exactly one population -- the
smallest budget it accepts. The population is computed the way CA's
``GeneticAlgorithmOptimiser`` computes it, from the option values the run
passes and, for any it leaves out, the schema's ``default`` (``debug_default``
under DEBUG) in ``PARAM_ID_METHODS['genetic_algorithm']``.

Other methods take the schema's own ``num_calls_to_function`` default; only when
CA declares none (or cannot be read) does the old fixed fallback apply.

Ships into ``runners/`` beside ``ca_imports``: the runners, the defaults routes
and the pipeline export all use it, so they cannot disagree on the method or budget.
"""
from __future__ import annotations

from ca_imports import ca_from, ensure_ca_path

GENETIC_ALGORITHM = "genetic_algorithm"
#: What both paths used before CA's schema was read; kept for a CA without one.
LEGACY_FALLBACK = 100
#: The options CA's GA sizes one generation from.
POPULATION_KEYS = {"num_survivors", "num_mutations_per_survivor", "num_cross_breed"}


def _schema_value(option: dict, debug: bool):
    if debug and option.get("debug_default") is not None:
        return option["debug_default"]
    return option.get("default")


def ga_population(options_schema: list[dict], user_options: dict | None = None,
                  debug: bool = False) -> int:
    """One GA generation: ``num_survivors * (1 + num_mutations_per_survivor) + num_cross_breed``."""
    user_options = user_options or {}
    by_name = {o["name"]: o for o in options_schema}

    def size(key):
        val = user_options.get(key)
        return int(val if val is not None else _schema_value(by_name[key], debug))

    survivors = size("num_survivors")
    return survivors + survivors * size("num_mutations_per_survivor") + size("num_cross_breed")


def default_method() -> str:
    """CA's default calibration method, or the genetic algorithm when this CA declares none."""
    ensure_ca_path()
    try:
        return ca_from("parsers.PrimitiveParsers", "DEFAULT_PARAM_ID_METHOD") or GENETIC_ALGORITHM
    except ImportError:
        return GENETIC_ALGORITHM


def _method_options(param_id_method: str):
    """CA's option schema for ``param_id_method``, or None when this CA has none."""
    ensure_ca_path()
    try:
        options_for = ca_from("parsers.PrimitiveParsers", "param_id_method_options")
    except ImportError:
        return None
    return options_for(param_id_method) or None


def default_num_calls(param_id_method: str | None, user_options: dict | None = None,
                      debug: bool = False) -> int:
    """The ``num_calls_to_function`` to use when the user left it unset.

    A method whose schema sizes a population (CA's genetic algorithm) gets one
    population; any other gets its schema default for ``num_calls_to_function``.
    """
    schema = _method_options(param_id_method or default_method())
    if not schema:
        return LEGACY_FALLBACK
    names = {o["name"] for o in schema}
    if POPULATION_KEYS <= names:
        return ga_population(schema, user_options, debug)
    for option in schema:
        if option["name"] == "num_calls_to_function":
            val = _schema_value(option, debug)
            if val is not None:
                return int(val)
    return LEGACY_FALLBACK
