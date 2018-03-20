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
import pytest

from projectq import MainEngine
from projectq.cengines import DirtyQubitMapper, DummyEngine
from projectq.backends import ResourceCounter
from projectq.ops import (X,
                          XGate,
                          H,
                          HGate,
                          BasicGate,
                          ControlledGate,
                          CNOT,
                          Toffoli,
                          AllocateQubitGate,
                          DeallocateQubitGate)
from projectq.meta import CarrierQubits


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
    del qubit

    # H^2 was mapped into qubit
    assert len(dummy.received_commands) == 5, "Gates were not sent on"
    assert all(isinstance(cmd.gate, HGate)
               for cmd in dummy.received_commands[1:-1]), (
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
    del qubit1
    del qubit2

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
    del qubit1
    del qubit2

    assert counter.max_width == 2, "dqubit was not remapped"

    assert dummy.received_commands[-3].qubits[0][0].id == 2, (
           "CNOT does not act on qubit2")

    assert dummy.received_commands[-3].control_qubits[0].id == 1, (
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
    del qureg

    assert dummy.received_commands[-5].qubits[0][0].id == 1, (
           "Toffoli does not act on qureg[1]")
    assert dummy.received_commands[-5].control_qubits[0].id == 0, (
           "Toffoli not controlled on qureg[0]")
    assert dummy.received_commands[-5].control_qubits[1].id == 2, (
           "Toffoli not controlled on qureg[2]")

    assert dummy.received_commands[-4].qubits[0][0].id == 2, (
           "Toffoli does not act on qureg[2]")
    assert dummy.received_commands[-4].control_qubits[0].id == 0, (
           "Toffoli not controlled on qureg[0]")
    assert dummy.received_commands[-4].control_qubits[1].id == 1, (
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
    Test that qubits targetted by 'with CarrierQubits' are preferably
    mapped into
    """
    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    qureg = dqubitmapper_testengine.allocate_qureg(2)

    H | qureg[0]
    with CarrierQubits(dqubitmapper_testengine, [qureg[1]]):
        dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
        H | dqubit
        del dqubit

    del qureg

    assert dummy.received_commands[-3].qubits[0][0].id == 1, (
           "dqubit was not remapped into target")


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


def test_manual_targetting(dqubitmapper_testengine):
    """
    Test that manual targetting works
    """
    dummy = dqubitmapper_testengine.backend

    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qubit0 = dqubitmapper_testengine.allocate_qubit()
    qubit1 = dqubitmapper_testengine.allocate_qubit()

    dqubitmapper_testengine.next_engine.set_next_carrier(qubit1)

    H | dqubit
    H | qubit1
    H | qubit1
    H | qubit1

    del dqubit

    assert dqubitmapper_testengine.next_engine._manualmap == -1, (
           "Target ID for was not reset")
    assert dummy.received_commands[-1].qubits[0][0].id == 2, (
           "Dirty qubit was not remapped to the correct target qubit")


def test_cache_limit(dqubitmapper_testengine):
    """
    Test if the limit on the commands cached works
    """
    dummy = dqubitmapper_testengine.backend
    dqubitmapper_testengine.next_engine._cache_limit = 4

    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)

    for _ in range(3):
        H | dqubit

    assert len(dummy.received_commands) == 0, (
           "Commands were not cached")

    H | dqubit

    assert len(dummy.received_commands) == 2, (
           "Commands were not sent on (only sends out half the cache)")


def test_costdict_construction():
    """
    Test if the cost-dict is set correctly
    """
    c1 = {BasicGate: 42}
    t1 = DirtyQubitMapper(gate_costs=c1)
    assert t1._default_cost == 42, "Default cost was not set correctly"

    c2 = {HGate: 42, XGate: 99}
    t2 = DirtyQubitMapper(gate_costs=c2)
    assert t2._gate_costs == c2, "Cost dict was not set correctly"


def test_load_balanced_remapping1(dqubitmapper_testengine):
    """
    Test if dqubit gets remapped into the qubit with the lowest load
    """
    dummy = dqubitmapper_testengine.backend

    dqubit = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qureg = dqubitmapper_testengine.allocate_qureg(2)

    H | qureg[0]
    H | dqubit

    del dqubit
    del qureg

    assert dummy.received_commands[-3].qubits[0][0].id == 2, (
           "Qubit was not remapped into lowest load qubit")


def test_load_balanced_remapping2(dqubitmapper_testengine):
    """
    Test if load gets updated correctly after remap, so that the second remap
    works correctly. Case where load(target) < load(dqubit)
    """

    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    dqubit1 = dqubitmapper_testengine.allocate_qubit(dirty=True)
    dqubit2 = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qureg = dqubitmapper_testengine.allocate_qureg(3)

    CNOT | (dqubit1, qureg[2])
    CNOT | (dqubit1, qureg[2])
    CNOT | (dqubit2, qureg[2])
    CNOT | (dqubit2, qureg[2])

    H | qureg[0]
    H | qureg[0]
    H | qureg[1]

    del dqubit1
    del dqubit2
    del qureg

    assert counter.max_width == 3, "dqubits were not remapped"

    assert dummy.received_commands[-6].control_qubits[0].id == 3, (
           "dqubit1 was not remapped to optimal dqubit")
    assert dummy.received_commands[-4].control_qubits[0].id == 2, (
           "dqubit2 was not remapped to optimal dqubit")


def test_load_balanced_remapping3(dqubitmapper_testengine):
    """
    Test if load gets updated correctly after remap, so that the second remap
    works correctly Case where load(target) > load(dqubit)
    """

    dummy = dqubitmapper_testengine.backend
    counter = dqubitmapper_testengine.next_engine.next_engine

    dqubit1 = dqubitmapper_testengine.allocate_qubit(dirty=True)
    dqubit2 = dqubitmapper_testengine.allocate_qubit(dirty=True)
    qureg = dqubitmapper_testengine.allocate_qureg(3)

    CNOT | (dqubit1, qureg[2])
    CNOT | (dqubit1, qureg[2])
    CNOT | (dqubit2, qureg[2])
    CNOT | (dqubit2, qureg[2])

    for _ in range(4):
        H | qureg[0]
    for _ in range(3):
        H | qureg[1]

    del dqubit1
    del dqubit2
    del qureg

    assert counter.max_width == 3, "dqubits were not remapped"

    assert dummy.received_commands[-6].control_qubits[0].id == 3, (
           "dqubit1 was not remapped to optimal dqubit")
    assert dummy.received_commands[-4].control_qubits[0].id == 2, (
           "dqubit2 was not remapped to optimal dqubit")
