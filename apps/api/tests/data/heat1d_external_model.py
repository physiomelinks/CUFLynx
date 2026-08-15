"""A minimal ``external_python`` model, for the tests only.

Deliberately self-contained: it must not import circulatory_autogen, and it must
not be circulatory_autogen's own ``funcs_user/example_model_external`` example —
a fixture that reaches into a sibling repository stops being a fixture and starts
being an integration with whatever that repository last changed.

What it is: explicit-Euler diffusion on a five-node 1-D rod, with a Dirichlet
value ``u_D`` at both ends and conductivity ``k``. Three interior nodes are the
observable outputs. Small enough to run in milliseconds, real enough that the
outputs actually depend on the parameters (so a slider move is visible and a
calibration would have something to do).

It also implements the optional ``extra_plots`` hook, because the extra-figure
pipeline — helper -> PNG -> ``solver_plots`` URL — has no other way to be tested
end to end.
"""

import numpy as np


class Heat1D:
    # LITERAL class attributes: CUFLynx reads these by AST, without importing
    # this file, to build the parameter table before any simulation exists.
    parameters = {"heat/k": 0.5, "heat/u_D": 0.0}
    output_names = ["heat/T_p1", "heat/T_p2", "heat/T_p3"]

    #: Interior nodes of the rod. Five nodes, three of them observed.
    N_NODES = 5

    def __init__(self):
        self.k = self.parameters["heat/k"]
        self.u_D = self.parameters["heat/u_D"]
        self.dt = 0.01
        self.n_samples = 1
        self.history = None

    # -- contract ------------------------------------------------------
    def init_solver(self, config):
        """One-off setup. Everything expensive would belong here."""
        self.dx = 1.0 / (self.N_NODES - 1)
        user_config = (config or {}).get("user_config") or {}
        # Exercises the one solver_info field this backend has: a free-form dict
        # handed over untouched.
        self.initial_peak = float(user_config.get("initial_peak", 1.0))

    def update_times(self, dt, start_time, sim_time, pre_time):
        self.dt = float(dt)
        self.n_samples = int(pre_time / dt) + int(sim_time / dt) + 1

    def set_param_vals(self, param_dict):
        for name, value in (param_dict or {}).items():
            if name == "heat/k":
                self.k = float(value)
            elif name == "heat/u_D":
                self.u_D = float(value)

    def run(self):
        """Step the whole grid, pre_time included, from the initial condition."""
        u = np.full(self.N_NODES, self.u_D, dtype=float)
        u[self.N_NODES // 2] = self.initial_peak
        # Explicit Euler is only stable for k*dt/dx^2 <= 1/2; sub-step rather
        # than diverge, so a large k is a slower run and not a wrong answer.
        stable = 0.5 * self.dx**2 / max(self.k, 1e-12)
        sub_steps = max(1, int(np.ceil(self.dt / stable)))
        h = self.dt / sub_steps

        history = np.empty((self.n_samples, self.N_NODES), dtype=float)
        history[0] = u
        for sample in range(1, self.n_samples):
            for _ in range(sub_steps):
                lap = np.zeros_like(u)
                lap[1:-1] = (u[:-2] - 2.0 * u[1:-1] + u[2:]) / self.dx**2
                u = u + h * self.k * lap
                u[0] = u[-1] = self.u_D
            history[sample] = u
        self.history = history
        return bool(np.all(np.isfinite(history)))

    def get_results(self):
        return {
            "heat/T_p1": self.history[:, 1],
            "heat/T_p2": self.history[:, 2],
            "heat/T_p3": self.history[:, 3],
        }

    # -- optional ------------------------------------------------------
    def get_init_param_vals(self, names):
        return [self.parameters[n] for n in names]

    def reset(self):
        self.history = None

    def extra_plots(self):
        """A space-time view of the rod — the sort of thing a generic trace plot
        cannot show, which is what the hook exists for."""
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots()
        if self.history is not None:
            ax.imshow(self.history.T, aspect="auto", origin="lower")
        ax.set_title("Rod temperature over time")
        return [fig]


SIM_HELPER = Heat1D
