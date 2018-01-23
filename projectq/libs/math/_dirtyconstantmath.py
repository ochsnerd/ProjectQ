#   Copyright 2017 ProjectQ-Framework (www.projectq.ch)
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import math
try:
    from math import gcd
except ImportError:
    from fractions import gcd

from projectq.ops import X, CNOT, Toffoli, Tensor, Swap
from projectq.meta import DirtyQubits, Compute, Uncompute, Control
from ._gates import AddConstant, SubConstant, AddConstantModN, SubConstantModN


def add_constant_modN(eng, c, N, quint, anc=None):
    """
    Adds the constant c to the number stored in quantum register quint and
    takes modulo N of the result
    (quint) -> ((quint+c)modN)

    If supplied, a dirty ancilla quibt (in a general state before the
    computation, in the same state after) will be used during the computation
    Described in https://arxiv.org/abs/1611.07995
        Args:
            c (int)             : Constant. 0 =< c < N
                                  The lowest n bits (length of quint)
                                  will be added to quint
            N (int)             : Constant. The result of the addition will
                                  be modulo N
            quint (list<Qubit>) : length: n
                                  Quantum register to which the constant will
                                  be added
            anc (list<Qubit>)   : optional
                                  length >= n
                                  Dirty ancilla quantum register to be used as
                                  scratch space during the computation. Can be
                                  in any state and will be in the same state
                                  after the computation
    """
    assert c >= 0 and c < N, "c has to be nonnegative and smaller than N"
    if c == 0:
        return

    indicator = eng.allocate_qubit()

    # b is number in quint
    # !(b < N - c) <=> b + c >= N -> the addition will "overflow" (mod N)
    # and we have to subtract N after
    carry(eng, quint, -N + c, indicator, anc)
    X | indicator

    add_constant(eng, c, quint, anc)

    with Control(eng, indicator):
        add_constant(eng, -N, quint)

    # uncompute indicator for it to be in |0> again
    carry(eng, quint, -c, indicator, anc)


def add_constant(eng, c, quint, anc=None):
    """
    Adds the constant c to the number stored in quantum register quint.
    If supplied, a dirty ancilla quibt (in a general state before the
    computation, in the same state after) will be used during the computation
    Described in https://arxiv.org/abs/1611.07995

        Args:
            c (int)             : Constant. The lowest n bits (length of quint)
                                  will be added to quint
            quint (list<Qubit>) : Quantum register to which the constant will
                                  be added
            anc (Qubit)         : optional
                                  Dirty ancilla qubit to be used as scratch
                                  space during the computation. Can be in a
                                  general state and will be in the same state
                                  after the computation
    """
    n = len(quint)

    if n == 1:
        # We reached the base-case, "adding" to a 1-bit number
        if (c & 1):
            X | quint[0]
        return

    if anc is None:
        # No ancilla supplied - make our own
        anc = eng.allocate_qubit(dirty=True)
        allocated_ancilla = True
    else:
        allocated_ancilla = False
        if len(anc) > 1:
            anc = anc[0]

    # Split quint and c
    n_l = n - n // 2

    x_l = quint[:n_l]  # lowest bit at quint[0]
    x_h = quint[n_l:]

    c &= (1 << n) - 1  # we only add as many bits as quint has qubits
    c_l = c & ((1 << n_l) - 1)
    c_h = c >> n_l

    controlled_increment(eng, x_h, anc, x_l)

    #~ with Control(eng, anc):
        #~ Tensor(X) | x_h

    CNOT | (anc, x_h[0])

    carry(eng, x_l, c_l, anc, x_h)

    controlled_increment(eng, x_h, anc, x_l)

    carry(eng, x_l, c_l, anc, x_h)

    CNOT | (anc, x_h[0])

    #~ with Control(eng, anc):
        #~ Tensor(X) | x_h

    add_constant(eng, c_l, x_l, anc=anc)

    add_constant(eng, c_h, x_h, anc=anc)

    if allocated_ancilla:
        del anc


def carry(eng, quint, c, g, anc=None):
    """
    Computes the carry of the addition of quint with the constant c and toggles
    the qubit g if there is a carry.

        Args:
            quint (list<Qubit>): Length: n
                                 Holds the n-bit binary number
            c (int):             Number to add (lowest n bits)
            g (Qubit):           Qubit that gets toggled if there is a carry
            anc (list<Qubit>):   optional
                                 Length >= n-1
                                 Holds the dirty qubits used in the
                                 computation
    """
    if c < 0:
        Tensor(X) | quint
        carry(eng, quint, -c, g, anc)
        Tensor(X) | quint
        return

    n = len(quint)

    assert n > 0, "Need some qubits to add to"

    if n == 1:
        if c & 1:
            CNOT | (quint[0], g)
        return

    if anc is None:
        needed = n - 1
    elif len(anc) < n - 1:
        needed = n - 1 - len(anc)
    else:
        needed = 0

    if needed > 0:
        allocated_ancilla = True
        add_anc = eng.allocate_qureg(needed, dirty=False)
        if anc is None:
            new_anc = add_anc
        else:
            new_anc = add_anc + anc
    else:
        allocated_ancilla = False
        new_anc = anc

    with DirtyQubits(eng, new_anc):
        ancilla = eng.allocate_qureg(n-1, dirty=False)

        CNOT | (ancilla[-1], g)

        with Compute(eng):
            for i in range(1, n-1):
                if (c >> (n-i)) & 1:
                    CNOT | (quint[-i], ancilla[-i])
                    X | quint[-i]
                Toffoli | (ancilla[-(i+1)], quint[-i], ancilla[-i])
            if (c >> 1) & 1:
                CNOT | (quint[1], ancilla[0])
                X | quint[1]
            if c & 1:
                Toffoli | (quint[0], quint[1], ancilla[0])
            for i in range(n-2):
                Toffoli | (ancilla[i], quint[i+2], ancilla[i+1])

        CNOT | (ancilla[-1], g)

        Uncompute(eng)

        del ancilla

    if allocated_ancilla:
        del add_anc


def controlled_increment(eng, quint, control, anc):
    """
    Increments the value in quint, controlled on control, using anc
    as dirty qubits
    (q, c, a) -> (q+a, c, a)
    Args:
            quint (list<Qubit>) : Length: n
                                  Holds the n-bit binary number to increment
            control (Qubit)     : Controls the increment-operation
            anc (list<Qubit>)   : Length >= n+1
                                  Holds the dirty qubits in used in the
                                  computation
    """
    needed = len(quint) - len(anc) + 1
    if needed > 0:
        # need one more ancilla than qubits to increment
        add_anc = eng.allocate_qureg(needed, dirty=False)
        new_anc = anc + add_anc
        allocated_ancilla = True
    else:
        new_anc = anc
        allocated_ancilla = False

    qureg = [control] + quint

    increment(eng, qureg, new_anc)

    X | control

    if allocated_ancilla:
        del add_anc


def increment(eng, quint, anc):
    """
    Increments the value in quint, using anc as dirty qubits
    Adapted from
    algassert.com/circuits/2015/06/12/Constructing-Large-Increment-Gates.html
    http://cs.stackexchange.com/questions/40933/

        Args:
            quint (list<Qubit>) : Length: n
                                  Holds the n-bit binary number to increment
            anc (list<Qubit>)   : Length >= n
                                  Holds the dirty qubits in used in the
                                  computation
    """
    def subtract(a, b):
        """
        (a,b) -> (a-b, b)
            Args:
                a (list<Qubit>) : Length: n
                b (list<Qubit>) : Length: n
        """
        assert len(a) == len(b), "Quregs must have the same length"

        def op1(x, y, z):
            CNOT | (x, y)
            # Controlled swap with last CNOT cancelled with
            # first CNOT in controlled swap in op2
            CNOT | (z, x)
            Toffoli | (x, y, z)

        def op2(x, y, z):
            # Second controlled swap
            Toffoli | (x, y, z)
            CNOT | (z, x)
            CNOT | (z, y)

        n = len(a)

        for i in range(n-1):
            op1(b[i], a[i], b[i+1])

        CNOT | (b[-1], a[-1])

        for i in range(n-2, -1, -1):
            op2(b[i], a[i], b[i+1])

    n = len(quint)

    assert n == len(anc), "Need an ancilla qubit for each bit"

    with DirtyQubits(eng, anc):
        g = eng.allocate_qureg(n, dirty=True)

        # Cancel garbage state of the carry bit
        for qb in quint[:-1]:
            CNOT | (g[0], qb)

        # Cancel garbage of the other dirty qubits
        Tensor(X) | g[1:]

        # "Special" handling of highest bit
        X | quint[-1]

        # "subtract"
        subtract(quint, g)

        # Cancel garbage of the other dirty qubits
        Tensor(X) | g[1:]

        # "subtract"
        subtract(quint, g)

        # Cancel garbage state of the carry bit
        for qb in quint[:-1]:
            CNOT | (g[0], qb)

        del g


# Modular multiplication by modular addition & shift, followed by uncompute
# from https://arxiv.org/abs/quant-ph/0205095
def mul_by_constant_modN(eng, c, N, quint_in):
    """
    Multiplies a quantum integer by a classical number a modulo N, i.e.,

    |x> -> |a*x mod N>

    (only works if a and N are relative primes, otherwise the modular inverse
    does not exist).
    """
    assert(c < N and c >= 0)
    assert(gcd(c, N) == 1)

    n = len(quint_in)
    quint_out = eng.allocate_qureg(n + 1)

    for i in range(n):
        with Control(eng, quint_in[i]):
            AddConstantModN((c << i) % N, N) | quint_out

    for i in range(n):
        Swap | (quint_out[i], quint_in[i])

    cinv = inv_mod_N(c, N)

    for i in range(n):
        with Control(eng, quint_in[i]):
            SubConstantModN((cinv << i) % N, N) | quint_out
    del quint_out


# calculates the inverse of a modulo N
def inv_mod_N(a, N):
    s = 0
    old_s = 1
    r = N
    old_r = a
    while r != 0:
        q = int(old_r / r)
        tmp = r
        r = old_r - q * r
        old_r = tmp
        tmp = s
        s = old_s - q * s
        old_s = tmp
    return (old_s + N) % N
