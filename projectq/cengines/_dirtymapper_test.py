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

# David Ochsner
# 2017-11-01
# ochsnerd@student.ethz.ch

import pytest

from projectq import MainEngine
from projectq.cengines import DirtyQubitMapper, DummyEngine
from projectq.backends import ResourceCounter
from projectq.ops import CNOT, H, Toffoli, HGate, AllocateQubitGate


@pytest.fixture
def dqubitmapper_testengine():
    return MainEngine(DummyEngine(save_commands=True),
                      [DirtyQubitMapper(), ResourceCounter()])


def test_empty_dqubit(dqubitmapper_testengine):
    """
    Test if 'empty' dqubit gets handled correctly
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qubit = dqubitmapper_testengine.allocate_qubit()
    del dqubit

    assert counter.max_width == 1, "Dirty Qubit was remapped"


def test_noninteracting_dqubit(dqubitmapper_testengine):
    """
    Test if a noninteracting dqubit gets handled correctly
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qubit = dqubitmapper_testengine.allocate_qubit()

    H | dqubit
    H | dqubit
    H | qubit

    assert len(dummy.received_commands) == 2, "Commands on dqubit are cached"

    del dqubit

    # H^2 was mapped into qubit
    assert len(dummy.received_commands) == 4, "Gates were sent on"
    assert all(isinstance(cmd.gate, HGate)
               for cmd in dummy.received_commands[1:]), (
               "Gates are Hadamard Gates")
    assert all(qb.id == 1
               for cmd in dummy.received_commands
               for qreg in cmd.qubits
               for qb in qreg), "Gates act on qubit1"


def test_interacting_dqubit_notremap(dqubitmapper_testengine):
    """
    Test if a interacting, non-remappable dqubit gets handled correctly
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qubit1 = dqubitmapper_testengine.allocate_qubit()
    qubit2 = dqubitmapper_testengine.allocate_qubit()

    CNOT | (qubit1, dqubit)
    CNOT | (qubit1, qubit2)
    CNOT | (qubit1, dqubit)

    assert len(dummy.received_commands) == 2, (
           "Clean allocations were not cached")

    del dqubit

    assert counter.max_width == 3, "dqubit was not be remapped"


def test_interacting_dqubit_remap(dqubitmapper_testengine):
    """
    Test if a interacting, remappable dqubit gets handled correctly
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qubit1 = dqubitmapper_testengine.allocate_qubit()
    qubit2 = dqubitmapper_testengine.allocate_qubit()

    CNOT | (qubit1, dqubit)

    del dqubit

    assert counter.max_width == 2, "dqubit was remapped"
    # Question: Why here qubits[][]
    assert dummy.received_commands[-1].qubits[0][0].id == 2, (
           "CNOT acts on qubit2")
    # and here control_qubits[]
    assert dummy.received_commands[-1].control_qubits[0].id == 1, (
           "CNOT controlled on qubit1")


def test_toffoli_remap(dqubitmapper_testengine):
    """
    Test if a Toffoli gate (as example of multi-controlled gate) gets remapped
    correctly
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    qureg = dqubitmapper_testengine.allocate_qureg(3)
    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)

    Toffoli | (dqubit, qureg[0], qureg[1])
    Toffoli | (qureg[:2], dqubit)

    del dqubit

    assert dummy.received_commands[-2].qubits[0][0].id == 1, (
           "Toffoli acts on qureg[1]")
    assert dummy.received_commands[-2].control_qubits[0].id == 0, (
           "Toffoli controlled on qureg[0]")
    assert dummy.received_commands[-2].control_qubits[1].id == 2, (
           "Toffoli controlled on qureg[2]")

    assert dummy.received_commands[-1].qubits[0][0].id == 2, (
           "Toffoli acts on qureg[2]")
    assert dummy.received_commands[-1].control_qubits[0].id == 0, (
           "Toffoli controlled on qureg[0]")
    assert dummy.received_commands[-1].control_qubits[1].id == 1, (
           "Toffoli controlled on qureg[1]")
