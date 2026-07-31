

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
