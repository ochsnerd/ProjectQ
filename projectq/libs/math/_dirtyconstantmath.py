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

from projectq.ops import  X, CNOT, Toffoli, Tensor
from projectq.meta import DirtyQubits, Compute, Uncompute

def add_constant(eng, c, quint):
    """
    Very good documentation goes here
    """

    """
    Structure:

    if small enough:
        'add' with cnots
        return

    split in qreg_high, qreg_low
    split in c_high, c_low

    g = eng.allocate_qubit(dirty=true)

    increment(qreg_high, control=g)
    CNOT | (g, qreg_high[0])

    carry(qreg = qreg_low, targets = qreg_high, c_low, g)
    increment(qreg_high, control=g)
    carry(qreg = qreg_low, targets = qreg_high, c_low, g)   //reset g

    CNOT | (g, qreg_high[0])

    del g

    add_constant(eng, c_low, qreg_low)
    add_constant(eng, c_high,qreg_high)
    """
    """
    TODO: 'manual' add  # done
    TODO: port carry (with DirtyQubits, signature) # done
    TODO: write incrementer # done?
    TODO: figure out how to split qreg # done
    """
    if len(quint) == 1:
        if (c & 1):
            X | quint[0]
        return


def get_carry(eng, quint, c, anc, g):
    """
    Computes the carry of the addition of quint with the constant c and toggles
    the qubit g if there is a carry.

        Args:
            quint (list<Qubit>): Length: n
                                 Holds the n-bit binary number
            c (int):             Number to add
            anc (list<Qubit>):   Length >= n-1
                                 Holds the dirty qubits used in the
                                 computation
            g (Qubit):           Qubit that gets toggled if there is a carry
    """
    n = len(qureg)

    with DirtyQubits(eng, anc):
        ancilla = eng.allocate_qureg(n-1, dirty=False)

        CNOT | (ancilla[-1], g)

        with Compute(eng):
            for i in range(1,n-1):
                if (c >> (n-i)) & 1:
                    CNOT | (qureg[-i], ancilla[-i])
                    X | qureg[-i]
                Toffoli | (ancilla[-(i+1)], qureg[-i], ancilla[-i])
            if (c >> 1) & 1:
                CNOT | (qureg[1],ancilla[0])
                X | qureg[1]
            if c & 1:
                Toffoli | (qureg[0], qureg[1], ancilla[0])
            for i in range(n-2):
                Toffoli | (ancilla[i], qureg[i+2], ancilla[i+1])

        CNOT | (ancilla[-1], g)

        Uncompute(eng)

        del ancilla


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
        def op1(x,y,z):
            CNOT | (x,y)
            CNOT | (z,x)
            CNOT | (x,z)

        def op2(x,y,z):
            CNOT | (x,z)
            CNOT | (z,x)
            CNOT | (z,y)

        n = len(a)

        for i in range(n-1):
            op1(b[i], a[i], b[i+1])

        CNOT | (b[-1], a[-1])

        for i in range(n-2, -1, -1):
            op2(b[i], a[i], b[i+1])

    n = len(quint)

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

        for _ in range(n):
            del g[-1]
        
"""
    - wait-problem: if we map into an uninvolved qubit, we can send on the
        commands without changing the circuit, BUT we send on allocates of
        dirty qubits and so we destroy optimization possibilities. If we always
        wait we get into a deadlock if we ignore FF-Gates
        -> "Solution" always wait, never ignore FF (Good in this example, and
            from what I've seen so far no FF-Gates are used during lifetime
            of a dirty qubit)
"""

if __name__=='__main__':
    """
    Testing incrementer
    """
    from projectq.ops import Measure
    from projectq import MainEngine
    from projectq.backends import Simulator, ResourceCounter
    from projectq.cengines import (MainEngine,
                                   AutoReplacer,
                                   LocalOptimizer,
                                   TagRemover,
                                   InstructionFilter,
                                   DecompositionRuleSet,
                                   DirtyQubitMapper,
                                   DummyEngine)

    # build compilation engine list
    resource_counter = ResourceCounter()
    dummy_engine = DummyEngine(save_commands=True)
    compilerengines = [DirtyQubitMapper(ignore_FastForwarding=False, verbose=False),
                       #LocalOptimizer(3),
                       dummy_engine,
                       resource_counter]

    # make the compiler and run the circuit on the simulator backend
    eng = MainEngine(Simulator(), compilerengines)

    n = 3
    qureg = eng.allocate_qureg(n)
    ancilla = eng.allocate_qureg(n)

    X | qureg[1]
    X | qureg[2]

    print("v: ", end="")
    for qb in reversed(qureg):
        Measure | qb
        print(int(qb), end="")

    print(" -> ", end="")

    increment(eng,qureg,ancilla)

    print("v: ", end="")
    for qb in reversed(qureg):
        Measure | qb
        print(int(qb), end="")
    print("")

    for cmd in dummy_engine.received_commands:
        print(cmd)

