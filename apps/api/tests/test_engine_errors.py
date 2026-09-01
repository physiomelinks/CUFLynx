

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


def test_a_failed_model_compile_is_not_a_tolerance_problem():
    """CVODE_myokit builds each model into a native extension when it runs, so a
    failed build is a toolchain problem on the user's machine. Before this the
    generic tail told them to lower MaximumStep and loosen rtol/atol -- advice for
    a different failure, and the only advice a Mac user with no working clang got.

    The message quoted here is the real one, from the packaged app on a Mac whose
    /usr/bin/clang has no toolchain behind it."""
    from engine import _failure_hint

    message = _failure_hint(
        "Simulation failed: CompilationError: Unable to compile.\n"
        "Error message:\n"
        "error: command '/usr/bin/clang' failed with exit code 1\n"
        '  File "setuptools/_distutils/spawn.py", line 70, in spawn\n'
        "    raise DistutilsExecError(\n"
        "distutils.errors.DistutilsExecError: command '/usr/bin/clang' failed "
        "with exit code 1"
    )
    assert "MaximumStep" not in message and "rtol" not in message
    # What the user can actually do: a backend that needs no compiler...
    assert "casadi_python" in message
    # ...or repair the toolchain, in this platform's own words.
    assert "compiler" in message.lower()


def test_the_other_confirmed_compile_failure_routes_the_same_way():
    """The second cause found on macOS: the bundle pointed clang at the *build*
    machine's Python headers, so the compile died on Python.h with a perfectly
    healthy Xcode. The runtime hook fixes the cause; this pins that the message a
    user sees is still the toolchain one and not the tolerance one."""
    from engine import _failure_hint

    message = _failure_hint(
        "CompilationError: Unable to compile.\nCompiler output:\n"
        "    fatal error: 'Python.h' file not found"
    )
    assert "MaximumStep" not in message and "rtol" not in message
    assert "casadi_python" in message
