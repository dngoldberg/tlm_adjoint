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
from tlm_adjoint_firedrake import manager as _manager

from test_base import *

import petsc4py.PETSc as PETSc
import pytest


@pytest.mark.firedrake
def test_long_range(setup_test, test_leaks):
    n_steps = 200
    configure_checkpointing("multistage",
                            {"blocks": n_steps, "snaps_on_disk": 0,
                             "snaps_in_ram": 2, "verbose": True})

    mesh = UnitIntervalMesh(20)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)

    def forward(F, x_ref=None):
        x_old = Function(space, name="x_old")
        x = Function(space, name="x")
        AssignmentSolver(F, x_old).solve()
        J = Functional(name="J")
        gather_ref = x_ref is None
        if gather_ref:
            x_ref = {}
        for n in range(n_steps):
            terms = [(1.0, x_old)]
            if n % 11 == 0:
                terms.append((1.0, F))
            LinearCombinationSolver(x, *terms).solve()
            if n % 17 == 0:
                if gather_ref:
                    x_ref[n] = function_copy(x, name=f"x_ref_{n:d}")
                J.addto(inner(x * x * x, x_ref[n]) * dx)
            AssignmentSolver(x, x_old).solve()
            if n < n_steps - 1:
                new_block()

        return x_ref, J

    F = Function(space, name="F", static=True)
    interpolate_expression(F, sin(pi * X[0]))

    start_manager()
    x_ref, J = forward(F)
    stop_manager()

    J_val = J.value()

    dJ = compute_gradient(J, F)

    def forward_J(F):
        return forward(F, x_ref=x_ref)[1]

    min_order = taylor_test(forward_J, F, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward_J)
    min_order = taylor_test(forward_J, F, J_val=J_val, ddJ=ddJ)
    assert min_order > 2.99

    min_order = taylor_test_tlm(forward_J, F, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, F, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward_J, F, adjoint_order=2)
    assert min_order > 1.99


@pytest.mark.firedrake
def test_EmptySolver(setup_test, test_leaks):
    class EmptySolver(Equation):
        def __init__(self):
            super().__init__([], [], nl_deps=[], ic=False, adj_ic=False)

        def forward_solve(self, X, deps=None):
            pass

    mesh = UnitIntervalMesh(100)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)

    def forward(F):
        EmptySolver().solve()

        F_norm_sq = Constant(name="F_norm_sq")
        NormSqSolver(F, F_norm_sq).solve()

        J = Functional(name="J")
        NormSqSolver(F_norm_sq, J.fn()).solve()
        return J

    F = Function(space, name="F")
    interpolate_expression(F, sin(pi * X[0]) * exp(X[0]))

    start_manager()
    J = forward(F)
    stop_manager()

    manager = _manager()
    manager.finalize()
    manager.info()
    assert len(manager._blocks) == 1
    assert len(manager._blocks[0]) == 3
    assert len(manager._blocks[0][0].X()) == 0

    J_val = J.value()
    with F.dat.vec_ro as F_v:
        J_ref = F_v.norm(norm_type=PETSc.NormType.NORM_2) ** 4
    assert abs(J_val - J_ref) < 1.0e-11

    dJ = compute_gradient(J, F)

    min_order = taylor_test(forward, F, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, F, J_val=J_val, ddJ=ddJ)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward, F, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, F, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, F, adjoint_order=2)
    assert min_order > 2.00


@pytest.mark.firedrake
def test_empty(setup_test, test_leaks):
    def forward(m):
        return Functional(name="J")

    m = Constant(name="m", static=True)

    start_manager()
    J = forward(m)
    stop_manager()

    dJ = compute_gradient(J, m)
    assert float(dJ) == 0.0


@pytest.mark.firedrake
def test_adjoint_graph_pruning(setup_test, test_leaks):
    mesh = UnitIntervalMesh(10)
    X = SpatialCoordinate(mesh)
    space = FunctionSpace(mesh, "Lagrange", 1)

    def forward(y):
        x = Function(space, name="x")

        NullSolver(x).solve()

        AssignmentSolver(y, x).solve()

        J_0 = Functional(name="J_0")
        J_0.assign(inner(dot(x, x), dot(x, x)) * dx)

        J_1 = Functional(name="J_1")
        J_1.assign(x * dx)

        J_0_val = J_0.value()
        NullSolver(x).solve()
        assert function_linf_norm(x) == 0.0
        J_0.addto(inner(x, y) * dx)
        assert J_0.value() == J_0_val

        J_2 = Functional(name="J_2")
        J_2.assign(x * dx)

        return J_0

    y = Function(space, name="y", static=True)
    interpolate_expression(y, exp(X[0]))

    start_manager()
    J = forward(y)
    stop_manager()

    eqs = {(0, 0, i) for i in range(8)}
    active_eqs = {(0, 0, 1), (0, 0, 2), (0, 0, 5), (0, 0, 6)}

    def callback(J_i, n, i, eq, adj_X):
        eqs.remove((J_i, n, i))
        assert adj_X is None or (J_i, n, i) in active_eqs

    dJ = compute_gradient(J, y, callback=callback)
    assert len(eqs) == 0

    J_val = J.value()

    min_order = taylor_test(forward, y, J_val=J_val, dJ=dJ)
    assert min_order > 2.00

    ddJ = Hessian(forward)
    min_order = taylor_test(forward, y, J_val=J_val, ddJ=ddJ)
    assert min_order > 3.00

    min_order = taylor_test_tlm(forward, y, tlm_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, y, adjoint_order=1)
    assert min_order > 2.00

    min_order = taylor_test_tlm_adjoint(forward, y, adjoint_order=2)
    assert min_order > 2.00
