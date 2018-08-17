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

from .base_equations import *
from .base_interface import *

from collections import OrderedDict
import copy
import numpy

__all__ = \
  [
    "LinearEquation",
    "Matrix",
    "RHS",
    
    "ContractionEquation",
    "ContractionRHS",
    "IdentityRHS",
    "InnerProductEquation",
    "InnerProductRHS",
    "MatrixActionRHS",
    "NormSqEquation",
    "SumEquation",
    "SumRHS"
  ]
  
class RHS:
  def __init__(self, deps, nl_deps = None):
    if len(set(dep.id() for dep in deps)) != len(deps):
      raise EquationException("Duplicate dependency")
    if not nl_deps is None:
      if len(set(dep.id() for dep in nl_deps)) != len(nl_deps):
        raise EquationException("Duplicate non-linear dependency")
    
    self._deps = tuple(deps)
    self._nl_deps = None if nl_deps is None else tuple(nl_deps)
    
  def replace(self, replace_map):
    self._deps = tuple(replace_map.get(dep, dep) for dep in self._deps)
    if not self._nl_deps is None:
      self._nl_deps = tuple(replace_map.get(dep, dep) for dep in self._nl_deps)
  
  def dependencies(self):
    return self._deps
  
  def nonlinear_dependencies(self):
    if self._nl_deps is None:
      return self.dependencies()
    else:
      return self._nl_deps

  def add_forward(self, B, deps):
    raise EquationException("Method not overridden")

  def add_adjoint_derivative_action(self, b, nl_deps, dep_index, adj_X):
    raise EquationException("Method not overridden")

  def tangent_linear_rhs(self, M, dM, tlm_map):
    raise EquationException("Method not overridden")

class IdentityRHS(RHS):
  def __init__(self, x):
    RHS.__init__(self, [x])

  def add_forward(self, b, deps):
    x, = deps
    b.vector()[:] += x.vector()

  def add_adjoint_derivative_action(self, b, nl_deps, dep_index, adj_x):
    if dep_index == 0:
      b.vector()[:] += adj_x.vector()

  def tangent_linear_rhs(self, M, dM, tlm_map):
    x, = self.dependencies()
    tau_x = None
    for m, dm in zip(M, dM):
      if m == x:
        tau_x = dm
    if tau_x is None:
      tau_x = tlm_map[x]
    if tau_x is None:
      return None
    else: 
      return IdentityRHS(tau_x)

class Matrix:
  def __init__(self, nl_deps = []):
    if len(set(dep.id() for dep in nl_deps)) != len(nl_deps):
      raise EquationException("Duplicate non-linear dependency")
      
    self._nl_deps = tuple(nl_deps)
  
  def replace(self, replace_map):
    self._nl_deps = tuple(replace_map.get(dep, dep) for dep in self._nl_deps)
  
  def nonlinear_dependencies(self):
    return self._nl_deps
  
  def add_forward_action(self, B, nl_deps, X):
    raise EquationException("Method not overridden")
  
  def reset_add_forward_action(self):
    pass
  
  def forward_solve(self, B, nl_deps):
    raise EquationException("Method not overridden")
  
  def reset_forward_solve(self):
    pass
  
  def adjoint_jacobian_action(self, nl_deps, adj_X):
    raise EquationException("Method not overridden")
  
  def reset_adjoint_jacobian_action(self):
    pass
  
  def subtract_adjoint_derivative_action(self, b, nl_deps, nl_dep_index, adj_X, X):
    raise EquationException("Method not overridden")
  
  def reset_subtract_adjoint_derivative_action(self):
    pass
  
  def adjoint_jacobian_solve(self, B, nl_deps):
    raise EquationException("Method not overridden")
  
  def reset_adjoint_jacobian_solve(self):
    pass
  
  def tangent_linear_rhs(self, M, dM, tlm_map):
    raise EquationException("Method not overridden")

class MatrixActionRHS(RHS):
  def __init__(self, A, X):
    if is_function(X):
      X = (X,)
    A_nl_deps = A.nonlinear_dependencies()
    if len(A_nl_deps) == 0:
      x_indices = list(range(len(X)))
      RHS.__init__(self, list(X), nl_deps = [])
    else:
      nl_deps = list(A_nl_deps)
      nl_dep_ids = {dep.id():i for i, dep in enumerate(nl_deps)}
      x_indices = []
      for x in X:
        x_id = x.id()
        if not x_id in nl_deps:
          nl_deps.append(x)
          nl_dep_ids[x_id] = len(nl_deps) - 1
        x_indices.append(nl_dep_ids[x_id])
      RHS.__init__(self, nl_deps, nl_deps = nl_deps)
      
    self._A = A
    self._x_indices = x_indices
  
  def add_forward(self, B, deps):
    X = [deps[j] for j in self._x_indices]
    self._A.add_forward_action(B, deps[:len(self._A.nonlinear_dependencies())], X[0] if len(X) == 1 else X)

  def add_adjoint_derivative_action(self, b, nl_deps, dep_index, adj_X):
    A_nl_deps = self._A.nonlinear_dependencies()
    if dep_index < len(A_nl_deps):
      X = [nl_deps[j] for j in self._x_indices]
      self._A.subtract_adjoint_derivative_action(b, nl_deps[:len(A_nl_deps)], dep_index, adj_X, X[0] if len(X) == 1 else X)
    elif dep_index < len(self.dependencies()):
      X = [None for x in self.X()]
      i = self._x_indices.index(dep_index)
      X[i] = adj_X[i]
      raise NotImplementedError

class LinearEquation(Equation):
  def __init__(self, B, X, A = None):
    if isinstance(B, RHS):
      B = (B,)
    if is_function(X):
      X = (X,)
  
    deps = []
    dep_ids = {}
    nl_deps = []
    nl_dep_ids = {}
    
    for x in X:
      x_id = x.id()
      if x_id in dep_ids:
        raise EquationException("Duplicate solve")
      deps.append(x)
      dep_ids[x_id] = len(deps) - 1
    
    b_dep_indices = [[] for b in B]
    b_nl_dep_indices = [[] for b in B]
    
    for i, b in enumerate(B):
      for dep in b.dependencies():
        dep_id = dep.id()
        if not dep_id in dep_ids:
          deps.append(dep)
          dep_ids[dep_id] = len(deps) - 1
        b_dep_indices[i].append(dep_ids[dep_id])
      for dep in b.nonlinear_dependencies():
        dep_id = dep.id()
        if not dep_id in nl_dep_ids:
          nl_deps.append(dep)
          nl_dep_ids[dep_id] = len(nl_deps) - 1
        b_nl_dep_indices[i].append(nl_dep_ids[dep_id])
    
    if not A is None:
      A_dep_indices = []
      A_nl_dep_indices = []
      for dep in A.nonlinear_dependencies():
        dep_id = dep.id()
        if not dep_id in dep_ids:
          deps.append(dep)
          dep_ids[dep_id] = len(deps) - 1
        A_dep_indices.append(dep_ids[dep_id])
        if not dep_id in nl_dep_ids:
          nl_deps.append(dep)
          nl_dep_ids[dep_id] = len(nl_deps) - 1
        A_nl_dep_indices.append(nl_dep_ids[dep_id])
      A_x_indices = []
      if len(A.nonlinear_dependencies()) > 0:
        for x in X:
          x_id = x.id()
          if not x_id in nl_dep_ids:
            nl_deps.append(x)
            nl_dep_ids[x_id] = len(nl_deps) - 1
          A_x_indices.append(nl_dep_ids[x_id])
    
    del(dep_ids, nl_dep_ids)
    
    Equation.__init__(self, X, deps, nl_deps = nl_deps)
    self._B = copy.copy(B)
    self._b_dep_indices = b_dep_indices
    self._b_nl_dep_indices = b_nl_dep_indices
    self._A = A
    if not A is None:
      self._A_dep_indices = A_dep_indices
      self._A_nl_dep_indices = A_nl_dep_indices
      self._A_x_indices = A_x_indices
    self._deps_map = {dep.id():i for i, dep in enumerate(self.nonlinear_dependencies())}
    
  def _replace(self, replace_map):
    Equation._replace(self, replace_map)
    for b in self._B:
      b.replace(replace_map)
    if not self._A is None:
      self._A.replace(replace_map)
    
  def forward_solve(self, X, deps = None):
    if is_function(X):
      X = (X,)      
    if deps is None:
      deps = self.dependencies()
      
    for x in X:
      function_zero(x)    
    for i, b in enumerate(self._B):
      b.add_forward(X[0] if len(X) == 1 else X, [deps[j] for j in self._b_dep_indices[i]])
    if not self._A is None:
      if len(X) == 1:
        X_new = [self._A.forward_solve(X[0], [deps[j] for j in self._A_dep_indices])]
      else:
        X_new = self._A.forward_solve(X, [deps[j] for j in self._A_dep_indices])
      for x, x_new in zip(X, X_new):
        function_assign(x, x_new)
      
  def adjoint_jacobian_solve(self, nl_deps, B):
    if self._A is None:
      return B
    else:
      return self._A.adjoint_jacobian_solve(B, [nl_deps[j] for j in self._A_nl_dep_indices])
      
  def adjoint_derivative_action(self, nl_deps, dep_index, adj_X):
    if is_function(adj_X):
      adj_X = (adj_X,)
      
    if dep_index < len(self.X()):
      if self._A is None:
        return adj_X[dep_index]
      else:
        X = [None for adj_x in adj_X]
        X[dep_index] = adj_X[dep_index]
        self._A.adjoint_jacobian_action([nl_deps[j] for j in self._A_nl_dep_indices], X[0] if len(X) == 1 else X)
    else:
      F = function_new(self.dependencies()[dep_index])
      for i, b in enumerate(self._B):
        try:
          b_dep_index = b.dependencies().index(self.dependencies()[dep_index])
        except ValueError:
          continue
        b.add_adjoint_derivative_action(F,
          [nl_deps[j] for j in self._b_nl_dep_indices[i]],
          b_dep_index,
          adj_X[0] if len(adj_X) == 1 else adj_X)
      if not self._A is None:
        dep_id = self.dependencies()[dep_index].id()
        if dep_id in self._deps_map:
          X = [nl_deps[j] for j in self._A_x_indices]
          self._A.subtract_adjoint_derivative_action(F,
            [nl_deps[j] for j in self._A_nl_dep_indices],
            self._deps_map[dep_id],
            adj_X[0] if len(adj_X) == 1 else adj_X,
            X[0] if len(X) == 1 else X)
      return (-1.0, F)

  def tangent_linear(self, M, dM, tlm_map):
    X = self.X()
    for x in X:
      if x in M:
        raise EquationException("Invalid tangent-linear parameter")
    
    if self._A is None:
      tlm_B = []
    else:
      tlm_B = self._A.tangent_linear_rhs(M, dM, tlm_map, X[0] if len(X) == 1 else X)
      if tlm_B is None:
        tlm_B = []
      elif isinstance(tlm_B, RHS):
        tlm_B = [tlm_B]
    for b in self._B:
      tlm_b = b.tangent_linear_rhs(M, dM, tlm_map)
      if not tlm_b is None:
        if isinstance(tlm_b, RHS):
          tlm_B.append(tlm_b)
        else:
          tlm_B += list(tlm_b)
          
    if len(tlm_B) == 0:
      return None
    else:
      return LinearEquation(tlm_B, [tlm_map[x] for x in self.X()], A = self._A)

class SumRHS(RHS):
  def __init__(self, x):
    RHS.__init__(self, [x], nl_deps = [])
    
  def add_forward(self, b, deps):
    y, = deps
    b.vector()[:] += y.vector().sum()
    
  def add_adjoint_derivative_action(self, b, nl_deps, dep_index, adj_x):
    if dep_index == 0:
      b.vector()[:] += adj_x.vector().sum()
      
  def tangent_linear_rhs(self, M, dM, tlm_map):
    y, = self.dependencies()
    
    tau_y = None
    for i, m in enumerate(M):
      if m == y:
        tau_y = dM[i]
    if tau_y is None:
      tau_y = tlm_map[y]
    
    if tau_y is None:
      return None
    else:
      return SumRHS(tau_y)

class SumEquation(LinearEquation):
  def __init__(self, y, x):
    LinearEquation.__init__(self, SumRHS(y), x)

class InnerProductRHS(RHS):
  def __init__(self, y, z, alpha = 1.0, M = None):
    RHS.__init__(self, [y, z], nl_deps = [y, z])
    self._alpha = alpha
    self._M = M
    if M is None:
      if alpha == 1.0:
        self._dot = lambda x : x
      else:
        self._dot = lambda x : alpha * x
    elif alpha == 1.0:
      self._dot = lambda x : M.dot(X)
    else:
      self._dot = lambda x : alpha * M.dot(x)
    
  def add_forward(self, b, deps):
    y, z = deps
    b.vector()[:] += y.vector().dot(self._dot(z.vector()))
    
  def add_adjoint_derivative_action(self, b, nl_deps, dep_index, adj_x):
    if dep_index == 0:
      b.vector()[:] += adj_x.vector().sum() * self._dot(nl_deps[1].vector())
    elif dep_index == 1:
      b.vector()[:] += adj_x.vector().sum() * self._dot(nl_deps[0].vector())
      
  def tangent_linear_rhs(self, M, dM, tlm_map):
    y, z = self.dependencies()
    
    tau_y = None
    tau_z = None
    for i, m in enumerate(M):
      if m == y:
        tau_y = dM[i]
      elif m == z:
        tau_z = dM[i]
    if tau_y is None:
      tau_y = tlm_map[y]
    if tau_z is None:
      tau_z = tlm_map[z]
    
    tlm_B = []
    if not tau_y is None:
      tlm_B.append(InnerProductRHS(tau_y, z, alpha = self._alpha, M = self._M))
    if not tau_z is None:
      tlm_B.append(InnerProductRHS(y, tau_z, alpha = self._alpha, M = self._M))
    return tlm_B

class InnerProductEquation(LinearEquation):
  def __init__(self, y, z, x, alpha = 1.0, M = None):
    LinearEquation.__init__(self, InnerProductRHS(y, z, alpha = alpha, M = M), x)

class NormSqEquation(Equation):
  def __init__(self, y, x, M = None):
    Equation.__init__(self, x, [x, y], nl_deps = [y])
    self._M = M
    if M is None:
      self._dot = lambda x : x
    else:
      self._dot = lambda x : M.dot(x)
  
  def forward_solve(self, x, deps = None):
    _, y = self.dependencies() if deps is None else deps
    x.vector()[:] = y.vector().dot(self._dot(y.vector()))
  
  def adjoint_jacobian_solve(self, nl_deps, b):
    return b
    
  def adjoint_derivative_action(self, nl_deps, dep_index, adj_x):
    if dep_index == 0:
      return adj_x
    elif dep_index == 1:
      return (-2.0, adj_x.vector().sum() * self._dot(nl_deps[0].vector()))
    else:
      return None
    
  def tangent_linear(self, M, dM, tlm_map):
    x, y = self.dependencies()
    
    tau_y = None
    for i, m in enumerate(M):
      if m == x:
        raise EquationException("Invalid tangent-linear parameter")
      elif m == y:
        tau_y = dM[i]
    if tau_y is None:
      tau_y = tlm_map[y]
    
    if tau_y is None:
      return None
    else:
      return InnerProductEquation(y, tau_y, tlm_map[x], alpha = 2.0, M = self._M)

class ContractionMatrix:
  def __init__(self, A, I, A_T = None, alpha = 1.0):  
    self._A = A
    self._A_T = A_T
    self._I = tuple(I)
    self._alpha = alpha
  
  def A(self):
    return self._A
  
  def A_T(self):
    return self._A.T if self._A_T is None else self._A_T
  
  def I(self):
    return self._I
    
  def alpha(self):
    return self._alpha
  
  def value(self, X):
    if len(self._A.shape) == 2 and self._I == (0,):
      v = self.A_T().dot(X[0].vector())
    elif len(self._A.shape) == 2 and self._I == (1,):
      v = self._A.dot(X[0].vector())
    else:
      v = self._A
      for i, (j, x) in enumerate(zip(self._I, X)):
        v = numpy.tensordot(v, x.vector(), axes = ((j - i,), (0,)))
    if self._alpha != 1.0:
      if len(X) > 0:
        v *= self._alpha
      else:
        v = v * self._alpha
    return v
    
class ContractionRHS(RHS):
  def __init__(self, A, I, Y, A_T = None, alpha = 1.0):
    if len(Y) != len(A.shape) - 1:
      raise EquationException("Contraction does not result in a vector")
    j = set(range(len(A.shape)))
    for i in I:
      j.remove(i)  # Raises an error if there is a duplicate element in I
    j = j.pop()
    
    RHS.__init__(self, Y, nl_deps = [] if len(Y) == 1 else Y)
    self._A_T = A_T
    self._c = ContractionMatrix(A, I, A_T = A_T, alpha = alpha)
    self._j = j

  def add_forward(self, b, deps):
    b.vector()[:] += self._c.value(deps)

  def add_adjoint_derivative_action(self, b, nl_deps, dep_index, adj_x):
    if dep_index < len(self._c.I()):
      A, I, alpha = self._c.A(), self._c.I(), self._c.alpha()
      Y = [None for i in range(len(A.shape))]
      k = I[dep_index]
      for j, (i, nl_dep) in enumerate(zip(I, nl_deps)):
        if j != dep_index:
          Y[i] = nl_dep
      Y[self._j] = adj_x
      
      b.vector()[:] += ContractionMatrix(A, list(range(k)) + list(range(k + 1, len(A.shape))), A_T = self._A_T, alpha = alpha).value(Y[:k] + Y[k + 1:])

  def tangent_linear_rhs(self, M, dM, tlm_map):
    Y = list(self.dependencies())
    A, I, alpha = self._c.A(), self._c.I(), self._c.alpha()
    m_map = OrderedDict([(m, dm) for m, dm in zip(M, dM)])
    
    J = []
    for j, y in enumerate(Y):
      if y in m_map:
        Y[j] = m_map[y]
      else:
        J.append(j)

    if len(J) == 0:
      return [ContractionRHS(A, I, Y, A_T = self._A_T, alpha = alpha)]
    else:
      tlm_B = []
      for j in J:
        tau_y = tlm_map[Y[j]]
        if not tau_y is None:
          tlm_B.append(ContractionRHS(A, I, Y[:j] + [tau_y] + Y[j + 1:], A_T = self._A_T, alpha = alpha))
      return tlm_B

class ContractionEquation(LinearEquation):
  def __init__(self, A, I, Y, x, A_T = None, alpha = 1.0):
    LinearEquation.__init__(self, ContractionRHS(A, I, Y, A_T = A_T, alpha = alpha), x)