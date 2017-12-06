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
from projectq.ops import (X,
                          H,
                          CNOT,
                          HGate,
                          Toffoli,
                          AllocateQubitGate,
                          DeallocateQubitGate)
from projectq.meta import DirtyQubits


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

    assert counter.max_width == 1, "Dirty Qubit was not remapped"


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

    assert len(dummy.received_commands) == 2, (
           "Commands on dqubit aren't cached")

    del dqubit

    # H^2 was mapped into qubit
    assert len(dummy.received_commands) == 4, "Gates were not sent on"
    assert all(isinstance(cmd.gate, HGate)
               for cmd in dummy.received_commands[1:]), (
               "Gates are not Hadamard Gates")
    assert all(qb.id == 1
               for cmd in dummy.received_commands
               for qreg in cmd.qubits
               for qb in qreg), "Gates don't act on qubit1"


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
           "Clean allocations were cached")

    del dqubit

    assert counter.max_width == 3, "dqubit was remapped"


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

    assert counter.max_width == 2, "dqubit was not remapped"
    # Question: Why here qubits[][]
    assert dummy.received_commands[-1].qubits[0][0].id == 2, (
           "CNOT does not act on qubit2")
    # and here control_qubits[]
    assert dummy.received_commands[-1].control_qubits[0].id == 1, (
           "CNOT is not controlled on qubit1")


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
           "Toffoli does not act on qureg[1]")
    assert dummy.received_commands[-2].control_qubits[0].id == 0, (
           "Toffoli not controlled on qureg[0]")
    assert dummy.received_commands[-2].control_qubits[1].id == 2, (
           "Toffoli not controlled on qureg[2]")

    assert dummy.received_commands[-1].qubits[0][0].id == 2, (
           "Toffoli does not act on qureg[2]")
    assert dummy.received_commands[-1].control_qubits[0].id == 0, (
           "Toffoli not controlled on qureg[0]")
    assert dummy.received_commands[-1].control_qubits[1].id == 1, (
           "Toffoli not controlled on qureg[1]")


def test_flush_works(dqubitmapper_testengine):
    """
    Test that flush works correctly
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    qubit = dqubitmapper_testengine.allocate_qubit()
    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)

    CNOT | (qubit, dqubit)

    dqubitmapper_testengine.flush()

    lastcmd = dummy.received_commands[-1]
    assert (lastcmd.gate == X and
            lastcmd.qubits[0][0].id == 1 and
            lastcmd.control_qubits[0].id == 0), "CNOT gate was not flushed"


def test_dont_remap_partially_cached(dqubitmapper_testengine):
    """
    Test that a dqubit with only a partially cached lifetime does not get
    remapped
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    qubit = dqubitmapper_testengine.allocate_qubit()
    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)

    dqubitmapper_testengine.flush()

    del dqubit

    assert counter.max_width == 2, "dqubit was remapped"


def test_targetting(dqubitmapper_testengine):
    """
    Test that qubits targetted by 'with DirtyQubits' are preferably mapped into
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    qureg = dqubitmapper_testengine.allocate_qureg(2)

    # ALSO TEST WITH OPTIMIZED REMAPPER
    with DirtyQubits(dqubitmapper_testengine, [qureg[1]]):
        dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
        H | dqubit
        del dqubit

    assert dummy.received_commands[-1].qubits[0][0].id == 1, (
           "dqubit was not remapped into target")


def test_ignoring_FastForwardingGates(dqubitmapper_testengine):
    """
    Test that FastForwardingGates are ignored if flag is set, ie. cached
    """
    dqubitmapper_testengine.next_engine._ignore_FF = True
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    qureg = dqubitmapper_testengine.allocate_qureg(2)
    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)

    CNOT | (dqubit, qureg[0])

    del qureg[0]

    assert isinstance(dummy.received_commands[-1].gate, AllocateQubitGate), (
           "Deallocate on clean qubit was not cached")

    del dqubit

    assert counter.max_width == 2, "Dirty qubit was not remapped"


def test_forwarding_FastForwardingGates(dqubitmapper_testengine):
    """
    Test that FastForwardingGates are not ignored, ie. sent on
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    qureg = dqubitmapper_testengine.allocate_qureg(2)
    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)

    CNOT | (dqubit, qureg[0])

    del qureg[0]

    assert isinstance(dummy.received_commands[-1].gate, DeallocateQubitGate), (
           "Deallocate on clean qubit was cached")

    del dqubit

    assert counter.max_width == 3, "Dirty qubit was remapped"
