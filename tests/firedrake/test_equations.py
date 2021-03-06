#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# For tlm_adjoint copyright information see ACKNOWLEDGEMENTS in the tlm_adjoint
# root directory

# This file is part of tlm_adjoint.
#
# tlm_adjoint is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# tlm_adjoint is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with tlm_adjoint.  If not, see <https://www.gnu.org/licenses/>.

from firedrake import *
from tlm_adjoint_firedrake import *
from tlm_adjoint_firedrake.backend_code_generator_interface import \
    assemble_system

from test_base import *

import numpy as np
import os
import pytest


@pytest.mark.firedrake
def test_AssignmentSolver(setup_test, test_leaks):
    x = Constant(16.0, name="x", static=True)

    def forward(x):
        y = [Constant(name=f"y_{i:d}") for i in range(9)]
        z = Constant(name="z")

        AssignmentSolver(x, y[0]).solve()
        for i in range(len(y) - 1):
            AssignmentSolver(y[i], y[i + 1]).solve()
        NormSqSolver(y[-1], z).solve()

        x_norm_sq = Constant(name="x_norm_sq")
        NormSqSolver(x, x_norm_sq).solve()

        z_norm_sq = Constant(name="z_norm_sq")
        NormSqSolver(z, z_norm_sq).solve()

        J = Functional(name="J")
        AxpySolver(z_norm_sq, 2.0, x_norm_sq, J.fn()).solve()

        K = Functional(name="K")
        AssignmentSolver(z_norm_sq, K.fn()).solve()

        return J, K

    start_manager()
    J, K = forward(x)
    stop_manager()

    assert abs(J.value() - 66048.0) == 0.0
    assert abs(K.value() - 65536.0) == 0.0

    dJs = compute_gradient([J, K], x)

    dm = Constant(1.0, name="dm", static=True)

    for forward_J, J_val, dJ in [(lambda x: forward(x)[0], J.value(), dJs[0]),
                                 (lambda x: forward(x)[1], K.value(), dJs[1])]:
        min_order = taylor_test(forward_J, x, J_val=J_val, dJ=dJ, dM=dm)
        assert min_order > 2.00

        ddJ = Hessian(forward_J)
        min_order = taylor_test(forward_J, x, J_val=J_val, ddJ=ddJ, dM=dm)
        assert min_order > 3.00

        min_order = taylor_test_tlm(forward_J, x, tlm_order=1, dMs=(dm,))
        assert min_order > 2.00

        min_order = taylor_test_tlm_adjoint(forward_J, x, adjoint_order=1,
                                            dMs=(dm,))
        assert min_order > 2.00

        min_order = taylor_test_tlm_adjoint(forward_J, x, adjoint_order=2,
                                            dMs=(dm, dm))
        assert min_order > 2.00


@pytest.mark.firedrake
def test_AxpySolver(setup_test, test_leaks):
    x = Constant(1.0, name="x", static=True)

    def forward(x):
        y = [Constant(name=f"y_{i:d}") for i in range(5)]
        z = [Constant(name=f"z_{i:d}") for i in range(2)]
        z[0].assign(7.0)

        AssignmentSolver(x, y[0]).solve()
        for i in range(len(y) - 1):
            AxpySolver(y[i], i + 1, z[0], y[i + 1]).solve()
        NormSqSolver(y[-1], z[1]).solve()

        J = Functional(name="J")
        NormSqSolver(z[1], J.fn()).solve()
        return J

    start_manager()
    J = forward(x)
    stop_manager()

    J_val = J.value()
    assert abs(J_val - 25411681.0) == 0.0

    dJ = compute_gradient(J, x)

    dm = Constant(1.0, name="dm", static=True)

    min_order = taylor_test(forward, x, J_val=J_val, dJ=dJ, dM=dm)
    assert min_order > 2.00

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, x, J_val=J_val, ddJ=ddJ, dM=dm,
                            seed=2.0e-2)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward, x, tlm_order=1, dMs=(dm,))
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, x, adjoint_order=1, dMs=(dm,))
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, x, adjoint_order=2,
                                        dMs=(dm, dm))
    assert min_order > 2.00


@pytest.mark.firedrake
def test_DirichletBCSolver(setup_test, test_leaks, test_configurations):
    mesh = UnitSquareMesh(20, 20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)
    test, trial = TestFunction(space), TrialFunction(space)

    F = Function(space, name="F", static=True)
    interpolate_expression(F, sin(pi * X[0]) * sin(3.0 * pi * X[1]))

    def forward(bc):
        x_0 = Function(space, name="x_0")
        x_1 = Function(space, name="x_1")
        x = Function(space, name="x")

        DirichletBCSolver(bc, x_1, "on_boundary").solve()

        EquationSolver(
            inner(grad(test), grad(trial)) * dx
            == inner(test, F) * dx - inner(grad(test), grad(x_1)) * dx,
            x_0, HomogeneousDirichletBC(space, "on_boundary"),
            solver_parameters=ls_parameters_cg).solve()

        AxpySolver(x_0, 1.0, x_1, x).solve()

        J = Functional(name="J")
        J.assign(inner(x * x, x * x) * dx)
        return x, J

    bc = Function(space, name="bc", static=True)
    function_assign(bc, 1.0)

    start_manager()
    x, J = forward(bc)
    stop_manager()

    x_ref = Function(space, name="x_ref")
    solve(inner(grad(test), grad(trial)) * dx == inner(test, F) * dx,
          x_ref,
          DirichletBC(space, 1.0, "on_boundary"),
          solver_parameters=ls_parameters_cg)
    error = Function(space, name="error")
    function_assign(error, x_ref)
    function_axpy(error, -1.0, x)
    assert function_linf_norm(error) < 1.0e-14

    J_val = J.value()

    dJ = compute_gradient(J, bc)

    def forward_J(bc):
        return forward(bc)[1]

    min_order = taylor_test(forward_J, bc, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, bc, J_val=J_val, ddJ=ddJ)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward_J, bc, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, bc, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, bc, adjoint_order=2)
    assert min_order > 2.00


@pytest.mark.firedrake
def test_FixedPointSolver(setup_test, test_leaks):
    x = Constant(name="x")
    z = Constant(name="z")

    a = Constant(2.0, name="a", static=True)
    b = Constant(3.0, name="b", static=True)

    def forward(a, b):
        eqs = [LinearCombinationSolver(z, (1.0, x), (1.0, b)),
               ExprEvaluationSolver(a / sqrt(z), x)]

        fp_parameters = {"absolute_tolerance": 0.0,
                         "relative_tolerance": 1.0e-14}
        FixedPointSolver(eqs, solver_parameters=fp_parameters).solve()

        J = Functional(name="J")
        J.assign(x)
        return J

    start_manager()
    J = forward(a, b)
    stop_manager()

    x_val = float(x)
    a_val = float(a)
    b_val = float(b)
    assert abs(x_val * np.sqrt(x_val + b_val) - a_val) < 1.0e-14

    J_val = J.value()

    dJda, dJdb = compute_gradient(J, [a, b])

    dm = Constant(1.0, name="dm", static=True)

    for M, dM, forward_J, dJ in \
            [(a, dm, lambda a: forward(a, b), dJda),
             (b, dm, lambda b: forward(a, b), dJdb),
             ((a, b), (dm, dm), forward, (dJda, dJdb))]:
        min_order = taylor_test(forward_J, M, J_val=J_val, dJ=dJ, dM=dM)
        assert min_order > 1.99

        ddJ = Hessian(forward_J)
        min_order = taylor_test(forward_J, M, J_val=J_val, ddJ=ddJ, dM=dM)
        assert min_order > 2.99

        min_order = taylor_test_tlm(forward_J, M, tlm_order=1, dMs=(dM,))
        assert min_order > 1.99

        min_order = taylor_test_tlm_adjoint(forward_J, M, adjoint_order=1,
                                            dMs=(dM,))
        assert min_order > 1.99

        min_order = taylor_test_tlm_adjoint(forward_J, M, adjoint_order=2,
                                            dMs=(dM, dM))
        assert min_order > 1.99


@pytest.mark.firedrake
def test_PointInterpolationSolver(setup_test, test_leaks):
    mesh = UnitCubeMesh(5, 5, 5)
    X = SpatialCoordinate(mesh)
    y_space = FunctionSpace(mesh, "Lagrange", 3)
    X_coords = np.array([[0.1, 0.1, 0.1],
                         [0.2, 0.3, 0.4],
                         [0.9, 0.8, 0.7],
                         [0.4, 0.2, 0.3]], dtype=np.float64)

    # Test optimization: Use to cache the interpolation matrix
    P = [None]

    def forward(y):
        X_vals = [Constant(name=f"x_{i:d}") for i in range(X_coords.shape[0])]
        eq = PointInterpolationSolver(y, X_vals, X_coords, P=P[0])
        eq.solve()
        P[0] = eq._P

        J = Functional(name="J")
        for x in X_vals:
            term = space_new(J.space())
            ExprEvaluationSolver(x ** 3, term).solve()
            J.addto(term)
        return X_vals, J

    y = Function(y_space, name="y", static=True)
    interpolate_expression(y, pow(X[0], 3) - 1.5 * X[0] * X[1] + 1.5)

    start_manager()
    X_vals, J = forward(y)
    stop_manager()

    def x_ref(x):
        return x[0] ** 3 - 1.5 * x[0] * x[1] + 1.5

    x_error_norm = 0.0
    for x, x_coord in zip(X_vals, X_coords):
        x_error_norm = max(x_error_norm,
                           abs(real_function_value(x) - x_ref(x_coord)))
    info(f"Error norm = {x_error_norm:.16e}")
    assert x_error_norm < 1.0e-13

    J_val = J.value()

    dJ = compute_gradient(J, y)

    def forward_J(y):
        return forward(y)[1]

    min_order = taylor_test(forward_J, y, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, y, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward_J, y, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, y, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, y, adjoint_order=2)
    assert min_order > 1.99


@pytest.mark.firedrake
def test_ExprEvaluationSolver(setup_test, test_leaks):
    mesh = UnitIntervalMesh(20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)

    def test_expression(y, y_int):
        return (y_int * y * (sin if is_function(y) else np.sin)(y)
                + 2.0 + (y ** 2) + y / (1.0 + (y ** 2)))

    def forward(y):
        x = Function(space, name="x")
        y_int = Constant(name="y_int")
        AssembleSolver(y * dx, y_int).solve()
        ExprEvaluationSolver(test_expression(y, y_int), x).solve()

        J = Functional(name="J")
        J.assign(x * x * x * dx)
        return x, J

    y = Function(space, name="y", static=True)
    interpolate_expression(y, cos(3.0 * pi * X[0]))
    start_manager()
    x, J = forward(y)
    stop_manager()

    error_norm = abs(function_get_values(x)
                     - test_expression(function_get_values(y),
                                       assemble(y * dx))).max()
    info(f"Error norm = {error_norm:.16e}")
    assert error_norm == 0.0

    J_val = J.value()

    dJ = compute_gradient(J, y)

    def forward_J(y):
        return forward(y)[1]

    min_order = taylor_test(forward_J, y, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, y, J_val=J_val, ddJ=ddJ)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward_J, y, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, y, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, y, adjoint_order=2)
    assert min_order > 2.00


@pytest.mark.firedrake
def test_LocalProjectionSolver(setup_test, test_leaks):
    mesh = UnitSquareMesh(10, 10)
    X = SpatialCoordinate(mesh)
    space_1 = FunctionSpace(mesh, "Discontinuous Lagrange", 1)
    space_2 = FunctionSpace(mesh, "Lagrange", 2)
    test_1, trial_1 = TestFunction(space_1), TrialFunction(space_1)

    def forward(G):
        F = Function(space_1, name="F")
        LocalProjectionSolver(G, F).solve()

        J = Functional(name="J")
        J.assign((F ** 2 + F ** 3) * dx)
        return F, J

    G = Function(space_2, name="G", static=True)
    interpolate_expression(G, sin(pi * X[0]) * sin(2.0 * pi * X[1]))

    start_manager()
    F, J = forward(G)
    stop_manager()

    F_ref = Function(space_1, name="F_ref")
    solve(inner(test_1, trial_1) * dx == inner(test_1, G) * dx, F_ref,
          solver_parameters=ls_parameters_cg)
    F_error = Function(space_1, name="F_error")
    function_assign(F_error, F_ref)
    function_axpy(F_error, -1.0, F)

    F_error_norm = function_linf_norm(F_error)
    info(f"Error norm = {F_error_norm:.16e}")
    assert F_error_norm < 1.0e-14

    J_val = J.value()

    dJ = compute_gradient(J, G)

    def forward_J(G):
        return forward(G)[1]

    min_order = taylor_test(forward_J, G, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, G, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward_J, G, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, G, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, G, adjoint_order=2)
    assert min_order > 1.99


@pytest.mark.firedrake
def test_AssembleSolver(setup_test, test_leaks):
    mesh = UnitSquareMesh(20, 20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)
    test = TestFunction(space)

    def forward(F):
        x = Function(space, name="x")
        y = Constant(name="y")

        AssembleSolver(inner(test, F * F) * dx
                       + inner(test, F) * dx, x).solve()
        AssembleSolver(inner(F, x) * dx, y).solve()

        J = Functional(name="J", fn=y)
        return J

    F = Function(space, name="F", static=True)
    interpolate_expression(F, X[0] * sin(pi * X[1]))

    start_manager()
    J = forward(F)
    stop_manager()

    J_val = J.value()

    dJ = compute_gradient(J, F)

    min_order = taylor_test(forward, F, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, F, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward, F, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, F, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, F, adjoint_order=2)
    assert min_order > 1.99


@pytest.mark.firedrake
def test_Storage(setup_test, test_leaks):
    comm = manager().comm()
    if comm.rank == 0:
        if not os.path.exists("checkpoints~"):
            os.mkdir("checkpoints~")
    comm.barrier()

    mesh = UnitSquareMesh(20, 20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)

    def forward(x, d=None, h=None):
        y = Function(space, name="y")
        x_s = Function(space, name="x_s")
        y_s = Function(space, name="y_s")

        if d is None:
            function_assign(x_s, x)
            d = {}
        MemoryStorage(x_s, d, function_name(x_s), save=True).solve()

        ExprEvaluationSolver(x * x * x * x_s, y).solve()

        if h is None:
            function_assign(y_s, y)
            import h5py
            if comm.size > 1:
                h = h5py.File(os.path.join("checkpoints~", "storage.hdf5"),
                              "w", driver="mpio", comm=comm)
            else:
                h = h5py.File(os.path.join("checkpoints~", "storage.hdf5"),
                              "w")
        HDF5Storage(y_s, h, function_name(y_s), save=True).solve()

        J = Functional(name="J")
        InnerProductSolver(y, y_s, J.fn()).solve()
        return y, x_s, y_s, d, h, J

    x = Function(space, name="x", static=True)
    interpolate_expression(x, cos(pi * X[0]) * exp(X[1]))

    start_manager()
    y, x_s, y_s, d, h, J = forward(x)
    stop_manager()

    assert len(manager()._cp._refs) == 1
    assert tuple(manager()._cp._refs.keys()) == (function_id(x),)
    assert len(manager()._cp._cp) == 0
    assert len(manager()._cp._data) == 4
    assert tuple(manager()._cp._data.keys()) \
        == ((function_id(x), 0),
            (function_id(x_s), 1),
            (function_id(y), 1),
            (function_id(y_s), 1))

    J_val = J.value()

    def forward_J(x):
        return forward(x, d=d, h=h)[5]

    dJ = compute_gradient(J, x)

    min_order = taylor_test(forward_J, x, J_val=J_val, dJ=dJ)
    assert min_order > 1.99

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, x, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward_J, x, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, x, adjoint_order=1)
    assert min_order > 1.99

    min_order = taylor_test_tlm_adjoint(forward_J, x, adjoint_order=2)
    assert min_order > 1.99

    h.close()


@pytest.mark.firedrake
def test_SumSolver(setup_test, test_leaks):
    mesh = UnitIntervalMesh(10)
    space = FunctionSpace(mesh, "Discontinuous Lagrange", 0)

    def forward(F):
        G = Function(space, name="G")
        AssignmentSolver(F, G).solve()

        J = Functional(name="J")
        SumSolver(G, J.fn()).solve()
        return J

    F = Function(space, name="F", static=True)
    function_set_values(F, np.random.random(function_local_size(F)))

    start_manager()
    J = forward(F)
    stop_manager()

    assert J.value() == function_sum(F)

    dJ = compute_gradient(J, F)
    assert abs(function_get_values(dJ) - 1.0).max() == 0.0


@pytest.mark.firedrake
def test_InnerProductSolver(setup_test, test_leaks):
    mesh = UnitIntervalMesh(10)
    space = FunctionSpace(mesh, "Discontinuous Lagrange", 0)

    def forward(F):
        G = Function(space, name="G")
        AssignmentSolver(F, G).solve()

        J = Functional(name="J")
        InnerProductSolver(F, G, J.fn()).solve()
        return J

    F = Function(space, name="F", static=True)
    function_set_values(F, np.random.random(function_local_size(F)))

    start_manager()
    J = forward(F)
    stop_manager()

    dJ = compute_gradient(J, F)
    min_order = taylor_test(forward, F, J_val=J.value(), dJ=dJ)
    assert min_order > 1.99


@pytest.mark.firedrake
def test_initial_guess(setup_test, test_leaks):
    mesh = UnitSquareMesh(20, 20)
    X = SpatialCoordinate(mesh)
    space_1 = FunctionSpace(mesh, "Lagrange", 1)
    test_1, trial_1 = TestFunction(space_1), TrialFunction(space_1)
    space_2 = FunctionSpace(mesh, "Lagrange", 2)

    zero = Constant(0.0, static=True)

    def forward(y, x_0=None):
        if x_0 is None:
            x_0 = project(y, space_1, name="x_0",
                          solver_parameters=ls_parameters_cg)
        x = Function(space_1, name="x")

        class TestSolver(ProjectionSolver):
            def __init__(self, y, x, form_compiler_parameters={},
                         solver_parameters={}):
                assert is_function(y)
                super().__init__(
                    inner(TestFunction(x.function_space()), y) * dx, x,
                    form_compiler_parameters=form_compiler_parameters,
                    solver_parameters=solver_parameters,
                    cache_jacobian=False, cache_rhs_assembly=False)

            def forward_solve(self, x, deps=None):
                rhs = self._rhs
                if deps is not None:
                    rhs = ufl.replace(rhs,
                                      dict(zip(self.dependencies(), deps)))
                J, b = assemble_system(
                    self._J, rhs,
                    form_compiler_parameters=self._form_compiler_parameters)
                solver = linear_solver(J, self._linear_solver_parameters)
                solver.solve(x, b)
                assert solver.ksp.getIterationNumber() == 0

            def adjoint_jacobian_solve(self, adj_x, nl_deps, b):
                assert adj_x is not None
                J = assemble(
                    self._J,
                    form_compiler_parameters=self._form_compiler_parameters)
                solver = linear_solver(J, self._linear_solver_parameters)
                solver.solve(adj_x, b)
                # test_adj_ic defined in test scope below
                assert not test_adj_ic or solver.ksp.getIterationNumber() == 0
                return adj_x

            def tangent_linear(self, M, dM, tlm_map):
                x, y = self.dependencies()
                tau_y = get_tangent_linear(y, M, dM, tlm_map)
                if tau_y is None:
                    return NullSolver(tlm_map[x])
                else:
                    return TestSolver(
                        tau_y, tlm_map[x],
                        form_compiler_parameters=self._form_compiler_parameters,  # noqa: E501
                        solver_parameters=self._solver_parameters)

        AssignmentSolver(x_0, x).solve()
        TestSolver(
            y, x,
            solver_parameters={"ksp_type": "cg",
                               "pc_type": "sor",
                               "ksp_rtol": 1.0e-10,
                               "ksp_atol": 1.0e-16,
                               "ksp_initial_guess_nonzero": True}).solve()

        J = Functional(name="J")
        J.assign(inner(dot(x, x), dot(x, x)) * dx)
        J_val = J.value()

        # test_adj_ic defined in test scope below
        if test_adj_ic:
            adj_x_0 = Function(space_1, name="adj_x_0", static=True)
            solve(
                inner(test_1, trial_1) * dx
                == derivative(inner(dot(x, x), dot(x, x)) * dx, x, du=test_1),
                adj_x_0, solver_parameters=ls_parameters_cg,
                annotate=False, tlm=False)
            NullSolver(x).solve()
            J_term = space_new(J.space())
            InnerProductSolver(x, adj_x_0, J_term).solve()
            J.addto(J_term)
        else:
            adj_x_0 = None

        # Active equation which requires no adjoint initial condition, but
        # for which one will be supplied
        z = Function(space_1, name="z")
        ProjectionSolver(
            zero * x, z,
            solver_parameters=ls_parameters_cg).solve()
        J.addto(inner(z, z) * dx)

        assert abs(J.value() - J_val) == 0.0

        return x, adj_x_0, z, J

    y = Function(space_2, name="y", static=True)
    interpolate_expression(y, exp(X[0]) * (1.0 + X[1] * X[1]))

    test_adj_ic = True
    start_manager()
    x_0 = Function(space_1, name="x_0")
    solve(inner(test_1, trial_1) * dx == inner(test_1, y) * dx,
          x_0, solver_parameters=ls_parameters_cg)
    x, adj_x_0, z, J = forward(y, x_0=x_0)
    stop_manager()

    assert len(manager()._cp._refs) == 3
    assert tuple(manager()._cp._refs.keys()) == (function_id(y),
                                                 function_id(adj_x_0),
                                                 function_id(zero))
    assert len(manager()._cp._cp) == 0
    assert len(manager()._cp._data) == 6
    assert tuple(manager()._cp._data.keys()) == ((function_id(y), 0),
                                                 (function_id(x), 2),
                                                 (function_id(adj_x_0), 0),
                                                 (function_id(x), 3),
                                                 (function_id(zero), 0),
                                                 (function_id(z), 1))

    dJdx_0, dJdy = compute_gradient(
        J, [x_0, y], adj_ics={z: ZeroFunction(space_1)})
    test_adj_ic = False
    assert function_linf_norm(dJdx_0) == 0.0

    J_val = J.value()

    def forward_J(y):
        return forward(y)[3]

    min_order = taylor_test(forward_J, y, J_val=J_val, dJ=dJdy)
    assert min_order > 2.00

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, y, J_val=J_val, ddJ=ddJ)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward_J, y, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, y, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, y, adjoint_order=2)
    assert min_order > 2.00


@pytest.mark.firedrake
@pytest.mark.parametrize("cache_rhs_assembly", [True, False])
def test_EquationSolver_form_binding_bc(setup_test, test_leaks,
                                        cache_rhs_assembly):
    mesh = UnitSquareMesh(20, 20)
    space = FunctionSpace(mesh, "Lagrange", 1)
    test, trial = TestFunction(space), TrialFunction(space)

    def forward(m):
        class CustomEquationSolver(EquationSolver):
            def forward_solve(self, x, deps=None):
                # Force into form binding code paths
                super().forward_solve(x, deps=self.dependencies())

        x = Function(space, name="x")
        CustomEquationSolver(
            inner(test, m * trial) * dx == inner(test, Constant(0.0)) * dx,
            x, DirichletBC(space, 1.0, "on_boundary"),
            solver_parameters=ls_parameters_cg,
            cache_jacobian=False,
            cache_rhs_assembly=cache_rhs_assembly).solve()

        J = Functional(name="J")
        J.assign(inner(dot(x, x), dot(x, x)) * dx)
        return J

    # m should not be static for this test
    m = Function(space, name="m")
    function_assign(m, 1.0)

    start_manager()
    J = forward(m)
    stop_manager()

    J_val = J.value()

    dJ = compute_gradient(J, m)

    min_order = taylor_test(forward, m, J_val=J_val, dJ=dJ)
    assert min_order > 1.99

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, m, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward, m, tlm_order=1)
    assert min_order > 1.99

    min_order = taylor_test_tlm_adjoint(forward, m, adjoint_order=1)
    assert min_order > 1.98

    min_order = taylor_test_tlm_adjoint(forward, m, adjoint_order=2)
    assert min_order > 1.99


@pytest.mark.firedrake
def test_ZeroFunction(setup_test, test_leaks, test_configurations):
    mesh = UnitIntervalMesh(10)
    space = FunctionSpace(mesh, "Lagrange", 1)

    def forward(m):
        X = [Function(space, name=f"x_{i:d}") for i in range(4)]

        AssignmentSolver(m, X[0]).solve()
        ScaleSolver(1.0, X[0], X[1]).solve()
        ExprEvaluationSolver(m + X[1], X[2]).solve()
        ProjectionSolver(m + X[2], X[3],
                         solver_parameters=ls_parameters_cg).solve()

        J = Functional(name="J")
        J.assign(inner(dot(X[-1] + 1.0, X[-1] + 1.0),
                       dot(X[-1] + 1.0, X[-1] + 1.0)) * dx
                 + inner(dot(m + 2.0, m + 2.0),
                         dot(m + 2.0, m + 2.0)) * dx)
        return J

    m = ZeroFunction(space, name="m")

    start_manager()
    J = forward(m)
    stop_manager()

    dJ = compute_gradient(J, m)

    J_val = J.value()

    min_order = taylor_test(forward, m, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, m, J_val=J_val, ddJ=ddJ)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward, m, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, m, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, m, adjoint_order=2)
    assert min_order > 2.00
