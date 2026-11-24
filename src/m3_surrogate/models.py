"""Three surrogate types with the same small interface:

    model.fit(X, y)                     -> returns model
    model.predict(X)                    -> mean FM
    model.predict(X, return_std=True)   -> (mean, std)   [GP only gives a real std]

X is the (n, 2) array from make_features: [scaled D, scaled T].
"""
import warnings
import numpy as np
from scipy.interpolate import RBFInterpolator
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.exceptions import ConvergenceWarning


class QuadraticSurrogate:
    """Least-squares quadratic response surface (the baseline).

    Terms: 1, x1, x2, x1^2, x1*x2, x2^2   (6 coefficients),  x1 = scaled D, x2 = scaled T
    """
    name = "quadratic"

    def _design(self, X):
        x1, x2 = X[:, 0], X[:, 1]
        return np.column_stack([np.ones(len(X)), x1, x2, x1**2, x1 * x2, x2**2])

    def fit(self, X, y):
        self.beta_ = np.linalg.lstsq(self._design(X), y, rcond=None)[0]
        return self

    def predict(self, X, return_std=False):
        mu = self._design(X) @ self.beta_
        return (mu, np.full(len(mu), np.nan)) if return_std else mu


class RBFSurrogate:
    """Radial basis function interpolant (scipy RBFInterpolator).

    kernel   : 'thin_plate_spline' (default), 'cubic', 'gaussian', ...
    smoothing: 0 -> passes exactly through every point; > 0 -> allowed to miss points (handles noise)
    A linear polynomial tail (degree=1) is added, so far from data it falls back to a plane.
    """

    def __init__(self, kernel="thin_plate_spline", smoothing=0.0, epsilon=None):
        self.kernel, self.smoothing, self.epsilon = kernel, smoothing, epsilon
        self.name = f"RBF {kernel}, s={smoothing:g}"

    def fit(self, X, y):
        kw = {} if self.epsilon is None else {"epsilon": self.epsilon}
        self.rbf_ = RBFInterpolator(X, y, kernel=self.kernel, smoothing=self.smoothing,
                                    degree=1, **kw)
        return self

    def predict(self, X, return_std=False):
        mu = self.rbf_(X)
        return (mu, np.full(len(mu), np.nan)) if return_std else mu


class GPSurrogate:
    """Kriging / Gaussian process with a nugget and ARD length scales.

    Kernel:  s^2 * RBF(l1, l2)  +  WhiteKernel(nugget)
      s^2      signal variance (how much FM varies around its mean)
      l1, l2   one length scale per input (ARD): small l = FM changes fast along that input
      nugget   noise variance; fitted from the data, so the model may choose NOT to pass
               through every point. nugget << s^2 suggests a nearly noise-free oracle.
    y is centred on its mean before fitting (normalize_y=False, constant mean = data mean).
    Hyperparameters are set by maximising the log marginal likelihood with several restarts.
    """

    def __init__(self, nugget_bounds=(1e-8, 1e-1), length_bounds=(0.03, 10.0),
                 n_restarts=20, seed=0):
        self.nugget_bounds = nugget_bounds
        self.length_bounds = length_bounds
        self.n_restarts = n_restarts
        self.seed = seed
        self.name = "GP (ARD + nugget)"

    def fit(self, X, y):
        y = np.asarray(y, dtype=float)
        self.y_mean_ = y.mean()
        var0 = max(y.var(), 1e-6)
        kernel = (ConstantKernel(var0, (1e-4, 1.0))
                  * RBF(length_scale=[0.3, 0.3], length_scale_bounds=self.length_bounds)
                  + WhiteKernel(1e-4, self.nugget_bounds))
        self.gp_ = GaussianProcessRegressor(kernel=kernel, normalize_y=False,
                                            n_restarts_optimizer=self.n_restarts,
                                            random_state=self.seed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            self.gp_.fit(X, y - self.y_mean_)
        return self

    def predict(self, X, return_std=False, include_noise=False):
        """include_noise=False: std of the smooth trend (what M1 should use).
        include_noise=True : std of a new oracle observation (trend + nugget); use for LOO checks."""
        # sklearn's std already contains the nugget (WhiteKernel is part of the kernel),
        # i.e. it is the std of a new noisy oracle value. Remove it for the trend std.
        mu, sd = self.gp_.predict(X, return_std=True)
        mu = mu + self.y_mean_
        if not include_noise:
            sd = np.sqrt(np.maximum(sd**2 - self.hyperparameters()["nugget"], 0.0))
        return (mu, sd) if return_std else mu

    def hyperparameters(self):
        """Fitted values in plain numbers."""
        k = self.gp_.kernel_
        signal_var = k.k1.k1.constant_value
        ls = np.atleast_1d(k.k1.k2.length_scale)
        nugget = k.k2.noise_level
        return {"signal_var": signal_var, "signal_std": np.sqrt(signal_var),
                "length_D": ls[0], "length_T": ls[1],
                "nugget": nugget, "noise_std": np.sqrt(nugget),
                "nugget_to_signal": nugget / signal_var,
                "log_marginal_likelihood": self.gp_.log_marginal_likelihood_value_}
