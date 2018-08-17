#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright(c) 2018 The University of Edinburgh
#
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

from fenics import *
from tlm_adjoint import *
from tlm_adjoint.hessian_optimization import *
stop_manager()

parameters["form_compiler"]["cpp_optimize"] = True
parameters["form_compiler"]["cpp_optimize_flags"] = "-O3 -march=native"
parameters["form_compiler"]["optimize"] = True

from collections import OrderedDict
import h5py
import numpy;  numpy.random.seed(12143432)
import petsc4py.PETSc
import slepc4py.SLEPc
import ufl

PETScOptions().set("citations", "petsc.bib")

# References:
# GHS09 D. Golgberg, D. M. Holland, and C. Schoof, "Grounding line movement and
#       ice shelf buttressing in marine ice sheets", Journal of Geophysical
#       Research: Earth Surface, 114(F4), F04026, 2009
# G11   D. N. Goldberg, "A variationally derived, depth-integrated approximation
#       to a higher-order glaciological flow model", Journal of Glaciology,
#       57(201), pp. 157--170, 2011
# GH13  D. N. Golberg and P. Heimbach, "Parameter and state estimation with a
#       time-dependent adjoint marine ice sheet model", The Cyrosphere, 7(6),
#       pp. 1659--1678, 2013

L_x, L_y = 40.0e3, 40.0e3  # GH13 experiment 3
H_0 = Constant(1.0e3, static =  True)  # GH13 experiment 3
theta = -0.5 * 2.0 * numpy.pi / 360.0  # GH13 experiment 3
#N_x, N_y = 40, 40  # GH13 experiment 3
N_x, N_y = 20, 20
timesteps = 120  # Close to (identical to?) GH13 experiment 3
debug = False
## Debug configuration
#N_x, N_y, timesteps, debug = 3, 3, 10, True
assert(timesteps % 10 == 0)

# Observe the flow and surface elevation at the end of these timesteps
timestep_obs = [(timesteps // 2 - 1) + i * (timesteps // 10) for i in range(6)]  # Similar to GH13 experiment 3

T = 10.0  # GH13 experiment 3
dt = Constant(T / timesteps, static = True)
B = Constant(2.1544e5, static = True)  # G11 table 1
g = Constant(9.81, static = True)  # G11 table 1
rho = Constant(910.0, static = True)  # G11 table 1
n = Constant(3.0, static = True)  # G11 table 1
sigma_u = Constant(1.0, static = True)  # GH13 experiment 3
sigma_h = Constant(0.02, static = True)  # GH13 experiment 3

mesh = RectangleMesh(Point(0.0, 0.0), Point(L_x, L_y), N_x, N_y, "crossed")

class PeriodicBoundaryCondition(SubDomain):
  # Following FEniCS 2017.1.0 API
  def inside(self, x, on_boundary):
    return (abs(x[0]) < 1.0e-8 or abs(x[1]) < 1.0e-8) and abs(x[0] - L_x) > 1.0e-8 and abs(x[1] - L_y) > 1.0e-8
  def map(self, x, y):
    if abs(x[0] - L_x) < 1.0e-8:
      if abs(x[1] - L_y) < 1.0e-8:
        y[0] = 0.0
        y[1] = 0.0
      else:
        y[0] = 0.0
        y[1] = x[1]
    elif abs(x[1] - L_y) < 1.0e-8:
      y[0] = x[0]
      y[1] = 0.0
    else:
      y[:] = -1.0e10
periodic_bc = PeriodicBoundaryCondition()

space = FunctionSpace(mesh, "Lagrange", 1, constrained_domain = periodic_bc)
test, trial = TestFunction(space), TrialFunction(space)
# Velocity space
space_u_f = FunctionSpace(mesh, "Lagrange", 2)
space_u = FunctionSpace(mesh, "Lagrange", 2, constrained_domain = periodic_bc)
space_U = FunctionSpace(mesh, space_u.ufl_element() * space_u.ufl_element(),
  constrained_domain = periodic_bc)
# Surface elevation space
space_h = FunctionSpace(mesh, "Lagrange", 1, constrained_domain = periodic_bc)
test_h, trial_h = TestFunction(space_h), TrialFunction(space_h)

beta_sq_ref = Function(space, name = "beta_sq_ref", static = True)
beta_sq_ref.interpolate(Expression("1000.0 - 750.0 * exp(-(pow(x[0] - (L_x / 2.0), 2.0) + pow(x[1] - (L_y / 2.0), 2.0)) / pow(5.0e3, 2.0))",
  L_x = L_x, L_y = L_y, element = space.ufl_element()))  # GH13 eqn (16)
File("beta_sq_ref.pvd", "compressed") << beta_sq_ref

forward_calls = [0]
def forward(beta_sq, ref = None, h_filename = None, speed_filename = None):
  forward_calls[0] += 1
  clear_caches()

  class VectorNormSolver(Equation):
    def __init__(self, U, U_norm):
      # Assumes compatible spaces
      Equation.__init__(self, U_norm, [U_norm, U], nl_deps = [U])
    
    def forward_solve(self, x, deps = None):
      _, U = self.dependencies() if deps is None else deps
      u_split = U.split(deepcopy = True)
      U_norm_arr = numpy.zeros(function_local_size(x), dtype = numpy.float64)
      for u in u_split:
        assert(function_local_size(u) == function_local_size(x))
        U_norm_arr[:] += function_get_values(u) ** 2
      function_set_values(x, numpy.sqrt(U_norm_arr))
  
  class MomentumEquation(EquationSolver):
    def __init__(self, U, h, initial_guess = None):
      spaces = U.function_space()
      tests, trials = TestFunction(spaces), TrialFunction(spaces)
      test_u, test_v = split(tests)
      test_u_x, test_u_y = test_u.dx(0), test_u.dx(1)
      test_v_x, test_v_y = test_v.dx(0), test_v.dx(1)

      def nu(U):
        u, v = split(U)
        u_x, u_y = u.dx(0), u.dx(1)
        v_x, v_y = v.dx(0), v.dx(1)
        eps = Constant(1.0e-12, static = True)
        return 0.5 * B * (((u_x ** 2) + (v_y ** 2) + u_x * v_y +
                          0.25 * ((u_y + v_x) ** 2) + eps) ** ((1.0 - n) / (2.0 * n)))  # G11 eqn (3)
      
      marker = ufl.classes.Coefficient(spaces)
      nu_marker = nu(marker)

      # GHS09 eqns (1)--(2)
      u, v = split(U)
      u_x, u_y = u.dx(0), u.dx(1)
      v_x, v_y = v.dx(0), v.dx(1)
      F = (- inner(tests, -beta_sq * U) * dx
           + inner(test_u_x, nu_marker * h * (4.0 * u_x + 2.0 * v_y)) * dx
           + inner(test_u_y, nu_marker * h * (u_y + v_x)) * dx
           + inner(test_v_y, nu_marker * h * (4.0 * v_y + 2.0 * u_x)) * dx
           + inner(test_v_x, nu_marker * h * (u_y + v_x)) * dx
           + inner(test_u, rho * g * h * (Constant(numpy.tan(theta), static = True))) * dx
           + inner(tests, rho * g * h * grad(h)) * dx)

      J_1 = replace(derivative(F, U, du = trials), {marker:U})
      F = replace(F, {marker:U})

      EquationSolver.__init__(self, F == 0, U, initial_guess = initial_guess,
        solver_parameters = {"nonlinear_solver":"newton",
                             "newton_solver":{"linear_solver":"cg", "preconditioner":"amg",
                                              "krylov_solver":{"relative_tolerance":1.0e-12, "absolute_tolerance":1.0e-16},
                                              "maximum_iterations":0,
                                              "relative_tolerance":0.0,
                                              "absolute_tolerance":0.0,
                                              "error_on_nonconvergence":True}},
        match_quadrature = True,  # Required for Jacobian discrete consistency
        defer_adjoint_assembly = False)
      self._J_1 = J_1

#      info("%s F form compiler parameters: %s" % (self.x().name(), extract_form_compiler_parameters(self._F, parameters["form_compiler"])))
#      info("%s J form compiler parameters: %s" % (self.x().name(), extract_form_compiler_parameters(self._J, parameters["form_compiler"])))
#      info("%s J_1 form compiler parameters: %s" % (self.x().name(), extract_form_compiler_parameters(self._J_1, parameters["form_compiler"])))
      
    def _replace(self, replace_map):
      EquationSolver._replace(self, replace_map)
      self._J_1 = replace(self._J_1, replace_map)
    
    def forward_solve(self, x, deps = None):
      U = x
      if deps is None:
        F = self._F
        J_1 = self._J_1
        J_2 = self._J
      else:
        replace_map = OrderedDict(zip(self.dependencies(), deps))
        F = replace(self._F, replace_map)
        J_1 = replace(self._J_1, replace_map)
        J_2 = replace(self._J, replace_map)
    
      function_zero(U)
      r = assemble(F, form_compiler_parameters = self._form_compiler_parameters)
      r_norm = numpy.sqrt(r.inner(r))
      if not self._initial_guess_index is None:
        function_assign(U, (self.dependencies() if deps is None else deps)[self._initial_guess_index])
      solve(F == 0, U, J = J_1, solver_parameters = {"nonlinear_solver":"newton",
                                                     "newton_solver":{"linear_solver":"cg", "preconditioner":"amg",
                                                                      "krylov_solver":{"relative_tolerance":1.0e-12, "absolute_tolerance":1.0e-16},
                                                                      "maximum_iterations":100,
                                                                      "relative_tolerance":0.0,
                                                                      "absolute_tolerance":max(1.0e-16, 1.0e-4 * r_norm),
                                                                      "error_on_nonconvergence":False}},
                                form_compiler_parameters = self._form_compiler_parameters)
      solve(F == 0, U, J = J_2, solver_parameters = {"nonlinear_solver":"newton",
                                                     "newton_solver":{"linear_solver":"cg", "preconditioner":"amg",
                                                                      "krylov_solver":{"relative_tolerance":1.0e-12, "absolute_tolerance":1.0e-16},
                                                                      "maximum_iterations":10,
                                                                      "relative_tolerance":0.0,
                                                                      "absolute_tolerance":max(1.0e-16, min(1.0e3, 1.0e-11 * r_norm)),
                                                                      "error_on_nonconvergence":True}},
                                form_compiler_parameters = self._form_compiler_parameters)
    
    def reset_forward_solve(self):
      pass
  
  h = [Function(space_h, name = "h_n"),
       Function(space_h, name = "h_np1")]
  
  F_h = [Function(space_h, name = "F_h_nm2"),
         Function(space_h, name = "F_h_nm1"),
         Function(space_h, name = "F_h_n")]
  
  U = [Function(space_U, name = "U_n"),
       Function(space_U, name = "U_np1")]
  
  def momentum(U, h, initial_guess = None):
    return MomentumEquation(U, h + H_0, initial_guess = initial_guess)
  def solve_momentum(U, h, initial_guess = None):
    momentum(U, h, initial_guess = initial_guess).solve(replace = True)
    
  def elevation_rhs(U, h, F_h):  
    return EquationSolver(inner(test_h, trial_h) * dx ==
      - dt * inner(test_h, div(U * (h + H_0))) * dx,
      F_h, solver_parameters = {"linear_solver":"cg", "preconditioner":"sor",
                                "krylov_solver":{"relative_tolerance":1.0e-12, "absolute_tolerance":1.0e-16}})  # GHS09 eqn (11) right-hand-side (times timestep size)
  def solve_elevation_rhs(U, h, F_h):
    elevation_rhs(U, h, F_h).solve(replace = True)
    
  def axpy(x, *args):
    return LinearCombinationSolver(x, *args)
  def solve_axpy(x, *args):
    axpy(x, *args).solve(replace = True)
    
  def cycle(x_np1, x_n):
    return AssignmentSolver(x_np1, x_n)
  def solve_cycle(x_np1, x_n):
    cycle(x_np1, x_n).solve(replace = True)
  
  if not h_filename is None:
    h_file = File(h_filename, "compressed")
  if not speed_filename is None:
    speed_file = File(speed_filename, "compressed")
    speed_n = Function(space_u_f, name = "speed_n")
    speed_eq = VectorNormSolver(U[0], speed_n)
  def output(t):
    if not h_filename is None:
      h_file << (h[0], t)
    if not speed_filename is None:
      speed_eq.solve(annotate = False, tlm = False)
      speed_file << (speed_n, t)
    
  # Initialisation
  solve_momentum(U[0], h[0])

  output(t = 0.0)
  
  # RK2
  # Stage 1
  solve_elevation_rhs(U[0], h[0], F_h[2])
  solve_axpy(h[1], (1.0, h[0]), (0.5, F_h[2]))
  solve_momentum(U[1], h[1], initial_guess = U[0])
  solve_cycle(F_h[2], F_h[1])
  # Stage 2
  solve_elevation_rhs(U[1], h[1], F_h[2])
  solve_axpy(h[1], (1.0, h[0]), (1.0, F_h[2]))
  solve_momentum(U[1], h[1], initial_guess = U[0])

  solve_cycle(h[1], h[0])
  solve_cycle(U[1], U[0])
  output(t = float(dt))
  
  # AB2
  solve_elevation_rhs(U[0], h[0], F_h[2])
  solve_axpy(h[1], (1.0, h[0]), (3.0 / 2.0, F_h[2]), (-1.0 / 2.0, F_h[1]))
  solve_momentum(U[1], h[1], initial_guess = U[0])
  solve_cycle(F_h[1], F_h[0])
  solve_cycle(F_h[2], F_h[1])
  solve_cycle(h[1], h[0])
  solve_cycle(U[1], U[0])
  output(t = 2 * float(dt))

  # AB3
  eqs = [elevation_rhs(U[0], h[0], F_h[2]),
         axpy(h[1], (1.0, h[0]), (23.0 / 12.0, F_h[2]), (-4.0 / 3.0, F_h[1]), (5.0 / 12.0, F_h[0])),
         momentum(U[1], h[1], initial_guess = U[0]),
         cycle(F_h[1], F_h[0]),
         cycle(F_h[2], F_h[1]),
         cycle(h[1], h[0]),
         cycle(U[1], U[0])]
  
  gather_ref = ref is None
  if gather_ref:
    ref = OrderedDict()  
  J = Functional()
  
  for timestep in range(2, timesteps):
    for eq in eqs:
      eq.solve()
    if timestep in timestep_obs:
      if gather_ref:
        ref[timestep] = function_copy(U[0], static = True, name = "U_ref_%i" % (timestep + 1)), function_copy(h[0], static = True, name = "h_ref_%i" % (timestep + 1))
      J.addto((1.0 / (sigma_u ** 2)) * inner(U[0] - ref[timestep][0], U[0] - ref[timestep][0]) * dx
            + (1.0 / (sigma_h ** 2)) * inner(h[0] - ref[timestep][1], h[0] - ref[timestep][1]) * dx)  # Similar to GH13 equation (17)
    else:
      J.addto()
    output(t = (timestep + 1) * float(dt))
  for eq in eqs:
    eq.replace()
  if not speed_filename is None:
    speed_eq.replace()
  
  info("forward call %i, J = %.16e" % (forward_calls[0], J.value()))
  return ref, J

start_manager()
ref, J = forward(beta_sq_ref, h_filename = "h.pvd", speed_filename = "speed.pvd")
stop_manager()

ddJ = SingleBlockHessian(J)
M_solver = KrylovSolver("cg", "sor")
M_solver.parameters.update({"relative_tolerance":1.0e-12, "absolute_tolerance":1.0e-16})
M_solver.set_operator(assemble(inner(test, trial) * dx))
A_action_calls = [0]
def A_action(x):
  A_action_calls[0] += 1
  info("A_action call %i" % A_action_calls[0])
  _, _, H_action = ddJ.action(beta_sq_ref, x)
  A_action = Function(space)
  M_solver.solve(A_action.vector(), H_action.vector())
  return function_get_values(A_action)
def eigendecompose_configure(esolver):
  esolver.setType(slepc4py.SLEPc.EPS.Type.KRYLOVSCHUR)
  esolver.setProblemType(slepc4py.SLEPc.EPS.ProblemType.NHEP)
  esolver.setWhichEigenpairs(slepc4py.SLEPc.EPS.Which.LARGEST_MAGNITUDE)
  esolver.setDimensions(nev = function_local_size(beta_sq_ref),
                        ncv = slepc4py.SLEPc.DECIDE, mpd = slepc4py.SLEPc.DECIDE)
  esolver.setConvergenceTest(slepc4py.SLEPc.EPS.Conv.REL)
  esolver.setTolerances(tol = 1.0e-8, max_it = slepc4py.SLEPc.DECIDE)
lam, (V, _) = eigendecompose(space, A_action, configure = eigendecompose_configure)
lam = lam.real
pack = sorted([(lam_val, v) for lam_val, v in zip(lam, V)], key = lambda p : p[0], reverse = True)
lam = [p[0] for p in pack]
V = [p[1] for p in pack]

h = h5py.File("eigenvalues.hdf5", "w")
h.create_dataset("lam", data = lam, compression = True, fletcher32 = True, shuffle = True)
h.close()

for i, lam_val in enumerate(lam):
  info("Eigenvalue %i = %.16e" % (i + 1, lam_val))

v_file = File("eigenvectors.pvd", "compressed")
for i, v in enumerate(V):
  function_set_values(v, function_get_values(v) / numpy.sqrt(assemble(inner(v, v) * dx)))
  v.rename("eigenvector", v.label())
  v_file << (v, float(i + 1))

del(beta_sq_ref)
beta_sq = Function(space, name = "beta_sq", static = True)
function_assign(beta_sq, 400.0)  # As in GH13 experiment 3

reset()
start_manager()
_, J = forward(beta_sq, ref = ref)
stop_manager()

ddJ = SingleBlockHessian(J)
if debug:
  dJ = compute_gradient(J, beta_sq)
  min_order = taylor_test(lambda beta_sq : forward(beta_sq, ref = ref)[1], beta_sq, J_val = J.value(), dJ = dJ, seed = 1.0e-4)
  min_order = taylor_test(lambda beta_sq : forward(beta_sq, ref = ref)[1], beta_sq, J_val = J.value(), dJ = dJ, ddJ = ddJ, seed = 1.0e-3)