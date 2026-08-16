

def test_a_trace_on_a_backend_that_cannot_drive_one_points_at_the_solver():
    """circulatory_autogen refuses a time-varying protocol input on CasADi, AADC
    and solve_ivp. The generic advice -- tighten tolerances -- cannot possibly
    help there, so it must not be what the user is told."""
    from engine import _failure_hint

    message = _failure_hint(
        "'engine/pace' was given the protocol trace name 'engine_pace', but the "
        "CasADi backend cannot drive a variable from a time series."
    )
    assert "CVODE_myokit" in message
    assert "rtol" not in message


def test_a_solver_plugin_that_will_not_load_is_not_a_tolerance_problem():
    """CasADi ships its integrators as separate shared libraries, so a build
    without libcasadi_integrator_CVODE.so fails whatever the tolerances are.
    Telling the user to lower MaximumStep sends them round a loop with no exit."""
    from engine import _failure_hint

    message = _failure_hint(
        'Assertion "handle!=nullptr" failed: PluginInterface::load_plugin: Cannot '
        "load shared library 'libcasadi_integrator_CVODE.so'"
    )
    assert "cellml" in message
    assert "MaximumStep" not in message and "rtol" not in message
